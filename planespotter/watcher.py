"""The main loop: poll for planes, email alerts, send the daily digest."""

from __future__ import annotations

import logging
import smtplib
import time
import urllib.error
from datetime import date
from typing import Callable

from .adsb import Aircraft, LiveFeed, Lookup
from .config import Config, fmt_clock_range
from .digest import build_forecast, build_recap
from .emails import alert_email, digest_email, fmt_alt, fmt_dist
from .geo import bearing_deg, compass, distance_miles, miles_to_nm, segment_min_distance_miles
from .mailer import Mailer
from .store import Store

log = logging.getLogger(__name__)

FETCH_ERRORS = (urllib.error.URLError, TimeoutError, ValueError, OSError)
SEND_ERRORS = (smtplib.SMTPException, OSError)
FASTEST_PLANE_KT = 650


class Watcher:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        feed: LiveFeed,
        lookup: Lookup,
        mailer: Mailer,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.store = store
        self.feed = feed
        self.lookup = lookup
        self.mailer = mailer
        self.clock = clock
        # Last known position of every plane near the zone: hex -> (lat, lon, time)
        self.last_positions: dict[str, tuple[float, float, float]] = {}
        self.digest_retry_at = 0.0

    # --- polling ------------------------------------------------------------

    def query_radius_nm(self) -> float:
        """Look a little beyond the zone, so planes are seen before they enter it."""
        travel = FASTEST_PLANE_KT * self.cfg.poll_seconds / 3600
        return miles_to_nm(self.cfg.radius_miles) + travel + 1

    def in_zone(self, ac: Aircraft, now: float) -> tuple[float, float] | None:
        """(closest distance, bearing) if the plane is in the zone or crossed it since last poll."""
        cfg = self.cfg
        if cfg.max_altitude_ft is not None and ac.alt_ft is not None and ac.alt_ft > cfg.max_altitude_ft:
            return None
        dist = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        prev = self.last_positions.get(ac.hex)
        if prev and now - prev[2] <= cfg.poll_seconds * 3:
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
            positions[ac.hex] = (ac.lat, ac.lon, now)
            if hit is None:
                continue
            pass_id, is_new = self.store.record_sighting(
                ac, hit[0], hit[1], now, gap=cfg.pass_gap_minutes * 60)
            if is_new:
                info = self.lookup.info(ac, now)
                self.store.set_info(pass_id, info)
                new_passes.append(pass_id)
                route = f" ({info.origin}→{info.destination})" if info.origin or info.destination else ""
                log.info("overhead: %s %s %s, %s%s", ac.label, fmt_dist(hit[0]),
                         compass(hit[1]), fmt_alt(ac.alt_ft), route)
        self.last_positions = positions
        return new_passes

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
        email = alert_email(self.store.get_passes(pass_ids), self.cfg)
        try:
            self.mailer.send(email)
        except SEND_ERRORS as exc:
            log.error("could not send alert email: %s", exc)
            return False
        self.store.mark_alerted(pass_ids)
        self.store.log_alert_email(now)
        return True

    # --- digest -------------------------------------------------------------

    def digest_due(self, now: float) -> date | None:
        """Today's date if the digest should go out now, else None."""
        if not self.cfg.digest_enabled or now < self.digest_retry_at:
            return None
        local = self.cfg.local(now)
        if local.time() < self.cfg.digest_time:
            return None
        today = local.date()
        if self.store.get_meta("last_digest") == today.isoformat():
            return None
        started = float(self.store.get_meta("watch_started") or now)
        if started >= self.cfg.day_start(today):
            # Nothing was watched yesterday, so there's nothing to report yet.
            self.store.set_meta("last_digest", today.isoformat())
            return None
        return today

    def send_digest(self, today: date) -> bool:
        recap = build_recap(self.store, self.cfg, today)
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
        log.info(
            "watching %.1f mi around %s (%.4f, %.4f), every %ds%s",
            cfg.radius_miles, cfg.home_name, cfg.home_lat, cfg.home_lon, cfg.poll_seconds,
            f" — no email login set, emails are saved in {cfg.outbox_dir}" if cfg.dry_run
            else f", emailing {cfg.email_to}",
        )
        if cfg.quiet_hours:
            log.info("quiet hours %s: no alerts then, planes go in the %s report",
                     fmt_clock_range(cfg.quiet_hours), cfg.digest_time.strftime("%H:%M"))
        failures = 0
        while True:
            started = time.monotonic()
            now = self.clock()
            try:
                new_passes = self.poll(now)
                failures = 0
                self.send_alerts(new_passes, now)
            except FETCH_ERRORS as exc:
                failures += 1
                log.warning("couldn't fetch planes (attempt %d): %s", failures, exc)
                if failures == 1 and "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    log.error("On a Mac, fix this by double-clicking “Install Certificates.command” "
                              "in Applications → Python 3.x, then start again.")
                self.last_positions = {}

            today = self.digest_due(now)
            if today is not None:
                self.send_digest(today)

            delay = cfg.poll_seconds if failures == 0 else min(300, cfg.poll_seconds * 2 ** min(failures, 4))
            time.sleep(max(1.0, delay - (time.monotonic() - started)))
