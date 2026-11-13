"""The main loop: poll for planes, email alerts, send the daily digest."""

from __future__ import annotations

import logging
import smtplib
import sqlite3
import threading
import time
import urllib.error
from datetime import date, timedelta
from typing import Callable

from .adsb import Aircraft, LiveFeed, Lookup
from .config import Config, fmt_clock_range
from .digest import build_forecast, build_recap
from .emails import alert_email, digest_email, fmt_alt, fmt_dist, special_email
from .catchup import gap_to_check, start_catchup
from .fullpath import OverBudget, save_flight, start_full_path_worker
from .heat import HeatRecorder
from .weekly import build_week, weekly_email
from .logos import airline_code
from .specials import FIRSTS_AFTER_DAYS, special_reasons
from .heat import backfill as backfill_heat
from .geo import bearing_deg, compass, distance_miles, miles_to_nm, segment_min_distance_miles
from .flow import observe as observe_flow
from .flow import flow_report, reference_airport
from .mailer import Mailer
from .predict import crossings
from .records import plane_of_the_day
from .sky import sky_note
from .space import when_text
from .store import Store

log = logging.getLogger(__name__)

FETCH_ERRORS = (urllib.error.URLError, TimeoutError, ValueError, OSError)
SEND_ERRORS = (smtplib.SMTPException, OSError)
FASTEST_PLANE_KT = 650
RUSH_WINDOW = 15 * 60   # "N planes coming over in the next 15 minutes"
RUSH_NM = 60            # look this far out for them (a jet covers ~60 nm in 15 minutes)


class Watcher:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        feed: LiveFeed,
        lookup: Lookup,
        mailer: Mailer,
        clock: Callable[[], float] = time.time,
        budget=None,
        flights=None,
        notifier=None,
        sky=None,
        ground_ft: int = 0,
        space=None,
    ):
        self.cfg = cfg
        self.share = 1  # how many places share adsb.lol's daily limit (each checks this much less often)
        self.shared: dict | None = None  # {"n": places}, shared by all places so travel mode slows them all
        self.stopped = threading.Event()  # set to end run() (travel mode ending, say)
        self.after_poll = []  # extra jobs after each check: functions of (planes, now)
        self.space = space  # satellite passes, for "the space station flies over in 10 minutes" (None = off)
        self._space_checked = 0.0
        self._flow_airport = (None, -1e12)  # (airport, when we worked it out)
        self.sky = sky  # clouds + daylight, for "you'll hear it, not see it" in alerts (None = off)
        self.ground_ft = ground_ft  # how high home is, since planes report height above sea level
        self.budget = budget  # adsb.lol's daily request limit, shared with the map
        self.flights = flights  # whole-flight lookups (None = off)
        self.notifier = notifier  # phone notifications (None = off)
        self.new_specials: list[dict] = []  # special planes found by poll(), sent by send_specials()
        self.interval = float(cfg.poll_seconds)  # seconds until the next check
        self.store = store
        self.feed = feed
        self.lookup = lookup
        self.mailer = mailer
        self.clock = clock
        # Last known position of every plane near the zone: hex -> (lat, lon, time, alt_ft)
        self.last_positions: dict[str, tuple] = {}
        # Planes whose pass is still being drawn: hex -> pass id
        self.open_paths: dict[str, int] = {}
        self.heat = HeatRecorder(store, cfg)
        self.digest_retry_at = 0.0
        self.catchup = None  # the offline log's background thread, while it runs

    # --- polling ------------------------------------------------------------

    def query_radius_nm(self) -> float:
        """Look far enough beyond the zone that a plane is seen on both sides of it,
        even when checks are minutes apart (to stay within adsb.lol's daily limit)."""
        travel = min(FASTEST_PLANE_KT, 500) * self.interval / 3600
        radius = miles_to_nm(self.cfg.radius_miles) + travel + 1
        if self.rush_enabled:
            radius = max(radius, RUSH_NM)  # to see a rush coming
        return min(250.0, radius)

    @property
    def rush_enabled(self) -> bool:
        return bool(self.cfg.rush_alerts and self.notifier and self.notifier.enabled)

    def in_zone(self, ac: Aircraft, now: float) -> tuple[float, float] | None:
        """(closest distance, bearing) if the plane is in the zone or crossed it since last poll."""
        cfg = self.cfg
        if cfg.max_altitude_ft is not None and ac.alt_ft is not None and ac.alt_ft > cfg.max_altitude_ft:
            return None
        dist = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        prev = self.last_positions.get(ac.hex)
        if prev and now - prev[2] <= max(cfg.poll_seconds, self.interval) * 2.5:
            dist = min(dist, segment_min_distance_miles(
                cfg.home_lat, cfg.home_lon, prev[0], prev[1], ac.lat, ac.lon))
        if dist > cfg.radius_miles:
            return None
        return dist, bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)

    def poll(self, now: float) -> list[int]:
        """Fetch planes once. Returns ids of passes that just started."""
        cfg = self.cfg
        planes = self.feed.aircraft_near(cfg.home_lat, cfg.home_lon, self.query_radius_nm())
        new_passes = []
        positions = {}
        for ac in planes:
            hit = self.in_zone(ac, now)
            prev = self.last_positions.get(ac.hex)
            positions[ac.hex] = (ac.lat, ac.lon, now, ac.alt_ft)
            point = [ac.lat, ac.lon, ac.alt_ft, now]
            if hit is None:
                # Keep drawing the path of a plane that just left the zone.
                if ac.hex in self.open_paths:
                    self.store.append_path(self.open_paths[ac.hex], [point])
                continue
            pass_id, is_new = self.store.record_sighting(
                ac, hit[0], hit[1], now, gap=cfg.pass_gap_minutes * 60)
            path = [[prev[0], prev[1], prev[3], prev[2]], point] if is_new and prev else [point]
            self.store.append_path(pass_id, path)
            self.open_paths[ac.hex] = pass_id
            if is_new:
                info = self.lookup.info(ac, now)
                self.store.set_info(pass_id, info)
                self.check_route(pass_id, ac, now)
                info = self.store.get_pass(pass_id)  # the route may have just been corrected
                self.note_firsts(pass_id, ac, info, now)
                new_passes.append(pass_id)
                route = (f" ({info['origin'] or '?'}→{info['destination'] or '?'})"
                         if info["origin"] or info["destination"] else "")
                log.info("over %s: %s %s %s, %s%s", cfg.home_name, ac.label, fmt_dist(hit[0]),
                         compass(hit[1]), fmt_alt(ac.alt_ft), route)
        self.new_specials.extend(self.find_specials(planes, now))
        self.observe_flow(planes, now)
        self.check_rush(planes, now)
        for job in self.after_poll:
            try:
                job(planes, now)
            except Exception:  # extras (the widget status, Moon crossings...) must never stop alerts
                log.exception("after-check job failed")
        try:
            self.heat.record(planes, self.last_positions, now, max_gap=max(cfg.poll_seconds, self.interval) * 2.5)
        except sqlite3.Error as exc:  # the heat map is a nice-to-have; never let it stop alerts
            log.warning("couldn't update the heat map: %s", exc)
        self.last_positions = positions
        self.open_paths = {h: pid for h, pid in self.open_paths.items() if h in positions}
        self.store.set_meta("last_poll", str(now))  # the offline log starts from here if we stop
        return new_passes

    def observe_flow(self, planes, now: float) -> None:
        """Note which way planes near the airport are landing and taking off (the runway flow)."""
        airport, when = self._flow_airport
        if now - when > 3600:
            airport = reference_airport(self.store, self.cfg)
            self._flow_airport = (airport, now)
        try:
            observe_flow(self.store, airport, planes, now)
        except sqlite3.Error as exc:
            log.info("couldn't note the runway flow: %s", exc)

    def check_rush(self, planes, now: float) -> bool:
        """A phone notification when lots of planes are about to come over (at most one an hour)."""
        cfg = self.cfg
        if not self.rush_enabled or cfg.in_quiet_hours(now):
            return False
        coming = crossings(cfg, planes, self.ground_ft, horizon=RUSH_WINDOW)
        if len(coming) < cfg.rush_count:
            return False
        if now - float(self.store.get_meta("last_rush") or 0) < 3600:
            return False
        self.store.set_meta("last_rush", str(now))
        lines = [f"{c.ac.label} {'now' if c.eta == 0 else f'in {max(1, round(c.eta / 60))} min'}, "
                 f"from the {compass(c.az)}, {fmt_alt(c.ac.alt_ft)}" for c in coming[:8]]
        log.info("rush: %d planes coming over in the next 15 minutes", len(coming))
        return self.notifier.send(f"🛫 Rush: {len(coming)} planes coming over in the next 15 minutes",
                                  "\n".join(lines) + "\n(if they keep flying straight)",
                                  click=self.notifier.link(), tags=("airplane", "stopwatch"))

    def check_space(self, now: float) -> bool:
        """A phone notification 10 minutes before the space station (or another bright one) flies over."""
        if not (self.space and self.cfg.space_alerts and self.notifier and self.notifier.enabled):
            return False
        if now - self._space_checked < 60:
            return False
        self._space_checked = now
        try:
            upcoming = self.space.upcoming()
        except Exception as exc:  # satellites are a fun extra; never let them stop planespotter
            log.info("couldn't work out satellite passes: %s", exc)
            return False
        sent = False
        for p in upcoming:
            if not p["visible"] or p["max_el"] < 20:
                continue
            lead = p["visible_from"] - now
            key = f"space:{p['number']}:{int(p['start'])}"
            if not 0 < lead <= 10 * 60 or self.store.get_meta(key):
                continue
            self.store.set_meta(key, "1")
            if self.cfg.in_quiet_hours(p["visible_from"]):
                continue
            at = self.cfg.local(p["visible_from"]).strftime("%I:%M %p").lstrip("0").lower()
            title = f"🛰️ {p['name']} flies over in {max(1, round(lead / 60))} min"
            message = (f"Look {p['start_dir']} at {at}. It climbs to {p['max_el']}° up in the {p['max_dir']} and "
                       f"crosses the sky in about {round(p['minutes'])} minutes: a bright, steady star moving fast, "
                       "no blinking lights.")
            sent = self.notifier.send(title, message, click=self.notifier.link(), tags=("satellite",), priority=4) or sent
            self.notifier.family(title, message, tags=("satellite",))
        return sent

    def start_offline_log(self, now: float) -> None:
        """If planespotter wasn't watching for a while (stopped, Mac asleep, no internet), look for
        the planes that came over meanwhile, in the background."""
        if self.catchup is not None and self.catchup.is_alive():
            return
        gap = gap_to_check(self.store, now)
        if gap and getattr(self.feed, "uses_receiver", False) is False:
            self.store.set_meta("last_poll", str(now))  # don't search the same gap twice
            self.catchup = start_catchup(self.cfg, self.budget, gap, self.notifier)

    def note_firsts(self, pass_id: int, ac: Aircraft, row, now: float) -> None:
        """Is this the first time we've seen this airline, or this kind of plane?"""
        started = float(self.store.get_meta("watch_started") or now)
        if not self.cfg.special_alerts or now - started < FIRSTS_AFTER_DAYS * 86400:
            return  # in the first few days everything would be a "first"
        firsts = []
        code = airline_code(ac.callsign)
        if code and not self.store.seen_before(pass_id, airline=code):
            firsts.append(f"First {row['airline'] or code} flight you've spotted")
        if ac.type_code and not self.store.seen_before(pass_id, type_code=ac.type_code):
            firsts.append(f"First {row['aircraft_desc'] or ac.type_code} you've spotted")
        if firsts:
            self.store.set_firsts(pass_id, firsts)
            log.info("first! %s", "; ".join(firsts))

    def find_specials(self, planes: list[Aircraft], now: float) -> list[dict]:
        """Special planes anywhere on the live map that we haven't announced in the last 6 hours."""
        cfg = self.cfg
        found = []
        for ac in planes:
            dist = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
            if dist > cfg.view_radius_miles:
                continue
            fresh = [r for r in special_reasons(ac, dist, cfg)
                     if not self.store.special_announced(ac.hex, r[0], now - 6 * 3600)]
            if not fresh:
                continue
            for key, _, _ in fresh:
                self.store.log_special(ac.hex, key, now)
            found.append({"ac": ac, "distance": dist, "bearing": bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon),
                          "reasons": fresh})
            log.info("special: %s %s, %s", ac.label, fmt_dist(dist), ", ".join(r[1] for r in fresh))
        return found

    def send_specials(self, items: list[dict], now: float) -> bool:
        if not items or not self.cfg.alerts_enabled:
            return False
        if self.cfg.in_quiet_hours(now):
            log.info("quiet hours: no alert for %d special plane(s)", len(items))
            return False
        if self.store.specials_since(now - 3600) > 12:
            log.warning("lots of special planes this hour; not sending more alerts for a while")
            return False
        items = [{**it, "info": self.lookup.info(it["ac"], now)} for it in items]
        if self.notifier:
            for it in items:
                ac, info = it["ac"], it["info"]
                reason = it["reasons"][0]
                title = f"{'🚨' if reason[2] else '✨'} {reason[1]}: {ac.label}"
                where = f"{fmt_dist(it['distance'])} {compass(it['bearing'])} of {self.cfg.home_name}, {fmt_alt(ac.alt_ft)}"
                details = " · ".join(x for x in (info.airline or info.owner, info.aircraft_desc) if x)
                self.notifier.special(title, f"{details}\n{where}" if details else where, ac.hex, ac.callsign,
                                      emergency=reason[2], photo=info.photo_url)
                if reason[0] != "helicopter":  # the big ones go to your family's feed too
                    self.notifier.family(title, f"{details}\nOver {self.cfg.home_name}" if details
                                         else f"Over {self.cfg.home_name}", tags=("sparkles", "airplane"))
        try:
            self.mailer.send(special_email(items, self.cfg))
        except SEND_ERRORS as exc:
            log.error("could not send special-plane email: %s", exc)
            return False
        return True

    def check_route(self, pass_id: int, ac: Aircraft, now: float) -> None:
        """Look up where the plane really came from before alerting (route databases are often wrong)."""
        if self.flights is None or ac.hex.startswith("~"):
            return
        try:
            points, finished = self.flights.flight(ac.hex, now - 120, now)
        except OverBudget:
            return  # the background worker will get to it
        except FETCH_ERRORS as exc:
            log.info("couldn't look up %s's flight: %s", ac.label, exc)
            return
        if points:
            save_flight(self.store, self.cfg, pass_id, now, points, finished, tries=1, now=now)

    # --- alerts -------------------------------------------------------------

    def send_alerts(self, pass_ids: list[int], now: float) -> bool:
        if not pass_ids or not self.cfg.alerts_enabled:
            return False
        if self.cfg.in_quiet_hours(now):
            log.info("quiet hours: no alert for %d plane(s), they'll be in the morning report",
                     len(pass_ids))
            return False
        if self.store.alert_emails_since(now - 3600) >= self.cfg.max_alerts_per_hour:
            log.warning("hit MAX_ALERTS_PER_HOUR; %d plane(s) will only appear in the digest",
                        len(pass_ids))
            return False
        passes = self.with_sky_notes(self.store.get_passes(pass_ids))
        pushed = self.notifier.plane_over_home(passes, self.cfg) if self.notifier else False
        try:
            self.mailer.send(alert_email(passes, self.cfg))
        except SEND_ERRORS as exc:
            log.error("could not send alert email: %s", exc)
            if not pushed:
                return False
        self.store.mark_alerted(pass_ids)
        self.store.log_alert_email(now)
        return True

    def with_sky_notes(self, passes: list) -> list:
        """Add a line like "☁️ Above the overcast at 1,800 ft: listen for it" when the sky's hiding it."""
        if not self.sky:
            return passes
        try:
            report = self.sky.report()
        except Exception as exc:  # the weather is a nice extra; never let it stop an alert
            log.info("no sky check: %s", exc)
            return passes
        out = []
        for p in passes:
            note = sky_note(report, p["closest_alt_ft"], p["closest_miles"], self.ground_ft)
            out.append({**dict(p), "highlights": [note]} if note else p)
        return out

    # --- digest -------------------------------------------------------------

    def digest_due(self, now: float) -> date | None:
        """Today's date if the digest should go out now, else None."""
        if not self.cfg.digest_enabled or now < self.digest_retry_at:
            return None
        if self.catchup is not None and self.catchup.is_alive():
            return None  # wait for the offline log, so the report includes what it finds
        local = self.cfg.local(now)
        if local.time() < self.cfg.digest_time:
            return None
        today = local.date()
        if self.store.get_meta("last_digest") == today.isoformat():
            return None
        started = float(self.store.get_meta("watch_started") or now)
        found = self.store.passes_between(self.cfg.at(today - timedelta(days=1), self.cfg.digest_time), now)
        if started >= self.cfg.day_start(today) and not found:
            # Nothing was watched (or found by the offline log) yesterday, so there's nothing to report yet.
            self.store.set_meta("last_digest", today.isoformat())
            return None
        return today

    def weekly_due(self, now: float) -> date | None:
        """Today's date if this is the day (and time) for the weekly email and it hasn't gone yet."""
        cfg = self.cfg
        if not cfg.weekly_report:
            return None
        local = cfg.local(now)
        if local.weekday() != cfg.weekly_day or local.time() < cfg.digest_time:
            return None
        today = local.date()
        if self.store.get_meta("last_weekly") == today.isoformat():
            return None
        started = float(self.store.get_meta("watch_started") or now)
        if now - started < 2 * 86400:
            self.store.set_meta("last_weekly", today.isoformat())  # too new to have a week to report
            return None
        return today

    def send_weekly(self, today: date) -> bool:
        email = weekly_email(build_week(self.store, self.cfg, today), self.cfg)
        calendar = self.week_calendar()
        if calendar:
            email.attachments.append(("planespotter.ics", calendar, "text/calendar"))
        try:
            self.mailer.send(email)
        except SEND_ERRORS as exc:
            log.error("could not send the weekly email: %s", exc)
            return False
        self.store.set_meta("last_weekly", today.isoformat())
        return True

    def week_calendar(self) -> bytes | None:
        """Space station passes, the best times to look up, and your flights, as a calendar file."""
        from .calendar_ics import build_calendar
        from .myflight import my_flights
        from .outlook import Outlook, daily_best

        now = self.clock()
        try:
            passes = self.space.upcoming(hours=24 * 7) if self.space else []
            hours = Outlook(self.cfg, background=False).hours() if self.sky else []  # weather on (not in tests)
            return build_calendar(self.cfg, now, passes, daily_best(self.store, self.cfg, now, hours),
                                  my_flights(self.store))
        except Exception as exc:  # the calendar is an extra: never let it stop the weekly email
            log.info("no calendar for the weekly email: %s", exc)
            return None

    def morning_notes(self, now: float) -> list[str]:
        """The runway outlook and tonight's space station passes, for the top of the morning email."""
        notes = []
        try:
            weather = self.sky.weather() if self.sky else None
            text = flow_report(self.store, self.cfg, now, weather).get("text")
            if text:
                notes.append(f"🛬 {text}")
        except Exception as exc:  # extras: never let them stop the report
            log.info("no runway outlook: %s", exc)
        if self.sky:  # weather checks are on
            try:
                from .outlook import Outlook, best_time, best_time_text, contrail_text
                hours = Outlook(self.cfg, background=False).hours()  # the morning email can wait a moment
                for text in (best_time_text(best_time(self.store, self.cfg, now, hours), self.cfg, now),
                             contrail_text(hours, self.cfg, now)):
                    if text:
                        notes.append(text)
            except Exception as exc:
                log.info("no outlook: %s", exc)
        if self.space:
            try:
                for p in self.space.upcoming(hours=30):
                    if p["visible"] and p["max_el"] >= 20 and p["start"] < now + 24 * 3600:
                        when = when_text(self.cfg, p["visible_from"] or p["start"], now)
                        notes.append(f"🛰️ {p['name']} flies over {when}: look {p['start_dir']}, it climbs to "
                                     f"{p['max_el']}° in the {p['max_dir']} (about {round(p['minutes'])} minutes)")
            except Exception as exc:
                log.info("no satellite passes: %s", exc)
        return notes

    def send_digest(self, today: date) -> bool:
        recap = build_recap(self.store, self.cfg, today)
        recap.star = plane_of_the_day(recap.passes, self.store, self.cfg)
        recap.notes = self.morning_notes(self.clock())
        forecast = build_forecast(self.store, self.cfg, today)
        try:
            self.mailer.send(digest_email(recap, forecast, self.cfg))
        except SEND_ERRORS as exc:
            log.error("could not send digest, will retry in 10 minutes: %s", exc)
            self.digest_retry_at = self.clock() + 600
            return False
        self.store.set_meta("last_digest", today.isoformat())
        return True

    # --- main loop ----------------------------------------------------------

    def run(self) -> None:
        cfg = self.cfg
        if self.store.get_meta("watch_started") is None:
            self.store.set_meta("watch_started", str(self.clock()))
        backfill_heat(self.store, cfg)
        if cfg.whole_flights:
            start_full_path_worker(cfg, self.budget)  # looks up whole flights in the background
        log.info(
            "watching %.1f mi around %s (%.4f, %.4f)%s",
            cfg.radius_miles, cfg.home_name, cfg.home_lat, cfg.home_lon,
            f" — no email login set, emails are saved in {cfg.outbox_dir}" if cfg.dry_run
            else f", emailing {cfg.email_to}",
        )
        if self.budget:
            every = self.budget.base_interval * (self.shared["n"] if self.shared else self.share)
            log.info("checking every %s to stay within adsb.lol's limit of %d requests a day%s",
                     f"{every / 60:.1f} min" if every >= 90 else f"{every:.0f} s", self.budget.limit,
                     "" if cfg.adsb_api_key else " (a free adsb.lol key raises it; see the README)")
        if cfg.quiet_hours:
            log.info("quiet hours %s: no alerts then, planes go in the %s report",
                     fmt_clock_range(cfg.quiet_hours), cfg.digest_time.strftime("%H:%M"))
        failures = 0
        last_check = 0.0
        self.start_offline_log(self.clock())  # planes that came over while planespotter was off
        while not self.stopped.is_set():
            now = self.clock()
            # Check on the budget's schedule; it speeds up while someone has the live map open.
            receiver = getattr(self.feed, "uses_receiver", False)  # your own receiver: no daily limit
            share = self.shared["n"] if self.shared else self.share
            interval = (self.budget.poll_interval() * share if self.budget and not receiver
                        else float(cfg.poll_seconds))
            if failures:
                interval = max(interval, min(300, cfg.poll_seconds * 2 ** min(failures, 4)))
            self.check_space(now)
            if now - last_check < interval:
                time.sleep(2)
                today = self.digest_due(now)
                if today is not None:
                    self.send_digest(today)
                week_day = self.weekly_due(now)
                if week_day is not None:
                    self.send_weekly(week_day)
                continue
            last_check = now
            self.interval = interval
            self.start_offline_log(now)  # e.g. the Mac was asleep
            try:
                new_passes = self.poll(now)
                failures = 0
                self.send_alerts(new_passes, now)
                specials, self.new_specials = self.new_specials, []
                self.send_specials(specials, now)
            except FETCH_ERRORS as exc:
                failures += 1
                log.warning("couldn't fetch planes (attempt %d): %s", failures, exc)
                if failures == 1 and "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    log.error("On a Mac, fix this by double-clicking “Install Certificates.command” "
                              "in Applications → Python 3.x, then start again.")
                self.last_positions = {}
                self.open_paths = {}

            today = self.digest_due(now)
            if today is not None:
                self.send_digest(today)
