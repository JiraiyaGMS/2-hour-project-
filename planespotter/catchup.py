"""The offline log: planes that flew over while planespotter wasn't running.

When planespotter starts again (or the Mac wakes up), it knows when it last
checked the sky. adsb.lol keeps every aircraft's positions for about the last
day, but you can only ask about one aircraft at a time, so planespotter:

1. asks once for every plane within 250 nautical miles of home right now,
   including ones parked on the ground at nearby airports;
2. picks the ones that could have come over while it was off: planes whose
   course leads back past home, planes that landed nearby, and planes you
   see often;
3. looks up each one's recorded positions (within a share of the day's
   request budget) and keeps every crossing of your zone in the gap.

These "found while offline" planes go in the daily and weekly emails, the map
and your collection, marked 📴. No alert is sent for them (it's too late), just
one summary notification. Planes that have since landed far away or left the
area can't be found this way, and nothing older than about 20 hours.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import urllib.error

from .adsb import Aircraft, get_json, key_header, retry_after_seconds
from .fullpath import TRACE_HOURS, FlightLookup, OverBudget, Point, as_path, save_flight, simplify, split_legs
from .geo import bearing_deg, distance_miles, offset, segment_min_distance_miles
from .heat import path_cells

log = logging.getLogger(__name__)

MIN_GAP = 10 * 60          # shorter gaps are just a slow check, not "offline"
WIDE_NM = 250              # the widest area adsb.lol answers for in one request
MAX_LOOKUPS = 150          # aircraft looked up per catch-up, at most
PAUSE = 1.5                # seconds between lookups, to be gentle on adsb.lol
MAX_REFUSED = 3            # adsb.lol said "too many requests" this many times: stop for now
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def gap_to_check(store, now: float) -> tuple[float, float] | None:
    """(from, to) of the time planespotter wasn't watching that adsb.lol still remembers, or None."""
    last = store.get_meta("last_poll")
    if last is None:
        last = last_known_check(store)  # a database from before the offline log existed
    if last is None:
        return None  # never watched before: nothing was missed
    last = float(last)
    if now - last < MIN_GAP:
        return None
    return last, now


def last_known_check(store) -> float | None:
    """When planespotter last checked the sky, from its request log (or else the last plane it saw)."""
    try:
        row = store.db.execute("SELECT MAX(minute) FROM api_usage WHERE kind = 'poll'").fetchone()
        if row and row[0]:
            return row[0] * 60.0 + 59
    except sqlite3.Error:
        pass  # no request log yet
    row = store.db.execute("SELECT MAX(last_seen) FROM passes").fetchone()
    return row[0] if row and row[0] else None


def candidates(cfg, raw: list[dict], start: float, now: float, regulars: set[str]) -> list[str]:
    """Aircraft that might have crossed your zone since ``start``, most likely first."""
    scored = []
    for r in raw:
        hex_code = str(r.get("hex", "")).lower()
        if not hex_code or hex_code.startswith("~"):
            continue  # no real aircraft address: nothing to look up
        lat, lon = r.get("lat"), r.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        here = distance_miles(cfg.home_lat, cfg.home_lon, lat, lon)
        gs, track = r.get("gs"), r.get("track")
        if r.get("alt_baro") == "ground":
            score = here if here <= 40 else None  # landed nearby: may have come in over you
        elif isinstance(gs, (int, float)) and isinstance(track, (int, float)) and gs > 50:
            # Fly its course backwards for the length of the gap: did that line pass near home?
            back = min(gs * 1.15078 * (now - start) / 3600, 900)
            blat, blon = offset(lat, lon, (track + 180) % 360, back)
            line = segment_min_distance_miles(cfg.home_lat, cfg.home_lon, lat, lon, blat, blon)
            score = line if line <= cfg.radius_miles + 25 else None
        else:
            score = here if here <= 60 else None
        if score is None and hex_code in regulars:
            score = 40.0
        if score is not None:
            scored.append((score, hex_code))
    scored.sort()
    seen, out = set(), []
    for _, hex_code in scored:
        if hex_code not in seen:
            seen.add(hex_code)
            out.append(hex_code)
    return out


def raw_points(data: dict) -> list[dict]:
    """The trace as dicts: t, lat, lon, alt (0 on the ground), gs, track, callsign (where known)."""
    base = float(data.get("timestamp") or 0)
    out, callsign = [], ""
    for p in data.get("trace") or []:
        if len(p) < 4 or not isinstance(p[1], (int, float)) or not isinstance(p[2], (int, float)):
            continue
        details = p[8] if len(p) > 8 and isinstance(p[8], dict) else None
        if details and details.get("flight"):
            callsign = str(details["flight"]).strip()
        ground = p[3] == "ground"
        out.append({"t": base + float(p[0]), "lat": float(p[1]), "lon": float(p[2]),
                    "alt": 0 if ground else (int(p[3]) if isinstance(p[3], (int, float)) else None),
                    "ground": ground, "gs": p[4] if len(p) > 4 and isinstance(p[4], (int, float)) else None,
                    "track": p[5] if len(p) > 5 and isinstance(p[5], (int, float)) else None,
                    "callsign": callsign})
    out.sort(key=lambda q: q["t"])
    return out


def crossings(cfg, points: list[dict], start: float, end: float) -> list[dict]:
    """Every time this aircraft went through your zone between ``start`` and ``end``."""
    found, current, prev = [], None, None
    gap = cfg.pass_gap_minutes * 60
    for p in points:
        if p["t"] > end:
            break
        if p["t"] >= start and not p["ground"]:
            d = distance_miles(cfg.home_lat, cfg.home_lon, p["lat"], p["lon"])
            if prev is not None and p["t"] - prev["t"] <= 300 and not prev["ground"]:
                d = min(d, segment_min_distance_miles(cfg.home_lat, cfg.home_lon, prev["lat"], prev["lon"],
                                                      p["lat"], p["lon"]))
            too_high = cfg.max_altitude_ft is not None and p["alt"] is not None and p["alt"] > cfg.max_altitude_ft
            if d <= cfg.radius_miles and not too_high:
                if current is None or p["t"] - current["last_seen"] > gap:
                    current = {"first_seen": p["t"], "last_seen": p["t"], "closest_miles": d, "point": p,
                               "prev": prev}
                    found.append(current)
                current["last_seen"] = p["t"]
                if d <= current["closest_miles"]:
                    current["closest_miles"], current["point"] = d, p
        prev = p
    return found


class CatchUp:
    """Looks for the planes that came over while planespotter was off."""

    def __init__(self, cfg, store, lookup=None, flights: FlightLookup | None = None, budget=None,
                 fetch_wide=None, clock=time.time, pause: float = PAUSE):
        self.cfg, self.store, self.lookup, self.budget, self.clock, self.pause = cfg, store, lookup, budget, clock, pause
        self.flights = flights or FlightLookup(api_base=cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget)
        self.fetch_wide = fetch_wide or self._fetch_wide

    def _fetch_wide(self) -> list[dict]:
        cfg = self.cfg
        if self.budget is not None:
            self.budget.record("catchup")
        url = f"{cfg.adsb_api}/v2/point/{cfg.home_lat:.5f}/{cfg.home_lon:.5f}/{WIDE_NM}"
        return get_json(url, 30, key_header(cfg.adsb_api_key)).get("ac") or []

    def regulars(self) -> set[str]:
        """Aircraft you've seen at least twice: likely to have come over again."""
        rows = self.store.db.execute("SELECT hex FROM passes GROUP BY hex HAVING COUNT(*) >= 2")
        return {r[0] for r in rows}

    def run(self, gap_start: float, gap_end: float) -> dict:
        """Search the gap. Returns a summary: what it looked at and the pass ids it added."""
        now = self.clock()
        start = max(gap_start, now - TRACE_HOURS * 3600)
        summary = {"start": gap_start, "end": gap_end, "checked_from": start, "found": [], "looked_up": 0}
        if start >= gap_end:
            self.log_gap(summary)
            return summary  # it's all too long ago for adsb.lol
        try:
            raw = self.fetch_wide()
        except FETCH_ERRORS as exc:
            log.info("offline log: couldn't ask adsb.lol (%s); will try again later", exc)
            summary["error"] = str(exc)
            return summary
        hexes = candidates(self.cfg, raw, start, now, self.regulars())
        log.info("offline log: planespotter was off for %s; checking %d planes that might have come over",
                 _duration(gap_end - gap_start), min(len(hexes), MAX_LOOKUPS))
        refused = 0
        for hex_code in hexes[:MAX_LOOKUPS]:
            try:
                found = self.check(hex_code, start, gap_end)
            except OverBudget:
                log.info("offline log: stopping early to stay within adsb.lol's daily limit")
                break
            except urllib.error.HTTPError as exc:
                found = []
                if exc.code != 429:
                    log.info("offline log: couldn't look up %s: %s", hex_code, exc)
                else:
                    # adsb.lol wants us to slow down: wait as long as it asks, and stop if it keeps asking.
                    refused += 1
                    if refused >= MAX_REFUSED:
                        log.info("offline log: adsb.lol is busy; stopping here for now")
                        break
                    wait = min(300.0, max(30.0, retry_after_seconds(exc)))
                    log.info("offline log: adsb.lol asked us to slow down; waiting %d s", wait)
                    time.sleep(wait if self.pause else 0)
            except FETCH_ERRORS as exc:
                log.info("offline log: couldn't look up %s: %s", hex_code, exc)
                found = []
            summary["looked_up"] += 1
            summary["found"].extend(found)
            if self.pause:
                time.sleep(self.pause)
        self.log_gap(summary)
        return summary

    def check(self, hex_code: str, start: float, end: float) -> list[int]:
        """Look one aircraft up and save each crossing of the zone in [start, end]."""
        data = self.flights.fetch_raw(hex_code, kind="catchup")
        if not data:
            return []
        points = raw_points(data)
        added = []
        for c in crossings(self.cfg, points, start, end):
            if self.already_have(hex_code, c["first_seen"], c["last_seen"]):
                continue
            added.append(self.save(hex_code, data, points, c))
        return added

    def already_have(self, hex_code: str, first: float, last: float) -> bool:
        """planespotter saw it itself (just before it stopped, say)."""
        return self.store.db.execute(
            "SELECT 1 FROM passes WHERE hex = ? AND last_seen >= ? AND first_seen <= ?",
            (hex_code, first - 1800, last + 1800)).fetchone() is not None

    def save(self, hex_code: str, data: dict, points: list[dict], c: dict) -> int:
        cfg, store, now = self.cfg, self.store, self.clock()
        p = c["point"]
        ac = Aircraft(hex=hex_code, callsign=p["callsign"], registration=str(data.get("r") or ""),
                      type_code=str(data.get("t") or ""), lat=p["lat"], lon=p["lon"], alt_ft=p["alt"],
                      speed_kt=p["gs"], track=p["track"])
        bearing = bearing_deg(cfg.home_lat, cfg.home_lon, p["lat"], p["lon"])
        near = [q for q in points if c["first_seen"] - 600 <= q["t"] <= c["last_seen"] + 600 and not q["ground"]]
        path = [[round(q["lat"], 5), round(q["lon"], 5), q["alt"], round(q["t"])] for q in near][-240:]
        pass_id = store.add_offline_pass(ac, c["first_seen"], c["last_seen"], c["closest_miles"], bearing, path)
        if self.lookup is not None:
            try:
                store.set_info(pass_id, self.lookup.info(ac, now))
            except Exception as exc:  # the airline and route are extras; keep the plane regardless
                log.info("offline log: no details for %s: %s", ac.label, exc)
        if not store.get_pass(pass_id)["aircraft_desc"] and data.get("desc"):
            store.db.execute("UPDATE passes SET aircraft_desc = ? WHERE id = ?", (str(data["desc"]).title(), pass_id))
            store.db.commit()
        # Its whole flight, from the same lookup: where it really came from (and went).
        legs = split_legs([Point(q["t"], q["lat"], q["lon"], q["alt"], q["ground"], False) for q in points])
        leg = next((lg for lg in legs if lg[0].t - 300 <= c["first_seen"] <= lg[-1].t + 300), None)
        if leg:
            finished = leg[-1].ground or leg is not legs[-1] or now - leg[-1].t > 2 * 3600
            save_flight(store, cfg, pass_id, c["first_seen"], leg, finished, 1, now)
        day = cfg.local(c["first_seen"]).date().isoformat()
        store.add_heat(day, path_cells(path))
        return pass_id

    def log_gap(self, summary: dict) -> None:
        self.store.db.execute(
            "INSERT INTO offline_gaps (start, end, checked_from, found, looked_up, done_at) VALUES (?, ?, ?, ?, ?, ?)",
            (summary["start"], summary["end"], summary["checked_from"], len(summary["found"]),
             summary["looked_up"], self.clock()))
        self.store.db.commit()


def _duration(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 1:
        return f"{round(seconds / 60)} min"
    if hours < 48:
        return f"{hours:.1f} hours".replace(".0 ", " ")
    return f"{hours / 24:.1f} days".replace(".0 ", " ")


def summary_message(store, ids: list[int], gap: float) -> tuple[str, str]:
    """The phone notification after a catch-up: title, message."""
    rows = store.get_passes(ids)
    rows = sorted(rows, key=lambda r: r["first_seen"])
    title = f"📴 {len(rows)} plane{'s' if len(rows) != 1 else ''} came over while planespotter was off"
    lines = []
    from .emails import label  # avoid a circular import
    for r in rows[:5]:
        route = f"{r['origin'] or '?'} → {r['destination'] or '?'}" if r["origin"] or r["destination"] else ""
        lines.append(" · ".join(x for x in (label(r), r["airline"], route) if x))
    if len(rows) > 5:
        lines.append(f"…and {len(rows) - 5} more")
    lines.append(f"(off for {_duration(gap)}; they're in your reports, marked 📴)")
    return title, "\n".join(lines)


def offline_summary(store, start: float, end: float) -> dict:
    """For the weekly email: how long it was off, how much could be checked, and what was found."""
    gaps = store.db.execute("SELECT * FROM offline_gaps WHERE end > ? AND start < ?", (start, end)).fetchall()
    off = sum(min(g["end"], end) - max(g["start"], start) for g in gaps)
    checked = sum(max(0, min(g["end"], end) - max(g["checked_from"], start)) for g in gaps)
    found = store.db.execute("SELECT * FROM passes WHERE offline = 1 AND first_seen >= ? AND first_seen < ? "
                             "ORDER BY first_seen", (start, end)).fetchall()
    return {"gaps": len(gaps), "off_s": off, "checked_s": checked, "found": found}



def start_catchup(cfg, budget, gap: tuple[float, float], notifier=None) -> threading.Thread:
    """Run the offline log in the background (with its own database connection), then tell your phone."""
    from .adsb import Lookup
    from .store import Store

    def run():
        store = Store(cfg.db_path)
        try:
            summary = CatchUp(cfg, store, Lookup(store, cfg.adsbdb_api), budget=budget).run(*gap)
            ids = summary["found"]
            log.info("offline log: found %d plane(s) that came over while planespotter was off", len(ids))
            if ids and notifier is not None and notifier.enabled:
                title, message = summary_message(store, ids, gap[1] - gap[0])
                notifier.send(title, message, click=notifier.link(), tags=("mobile_phone_off", "airplane"))
        except Exception:  # never let the offline log stop planespotter
            log.exception("the offline log failed")
        finally:
            store.close()

    thread = threading.Thread(target=run, name="offline-log", daemon=True)
    thread.start()
    return thread
