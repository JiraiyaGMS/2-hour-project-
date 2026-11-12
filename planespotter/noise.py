"""The noise log: low and late flights, the microphone noise meter, and a spreadsheet export.

The noise meter runs in the map page (it needs the browser's permission to use
the microphone). The page sends one level per second; once a plane has gone by,
the loudest second near its closest approach is compared with the background
level around it. Microphones aren't calibrated, so the result is "how much
louder than usual", in decibels, rather than an absolute sound level.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import time as clock_time

from .config import Config
from .emails import label
from .geo import compass, distance_miles

LOW_FT = 2000             # "low" means less than this above home
WINDOW_S = 75             # the plane's own noise: this long either side of its closest approach
BACKGROUND_S = 600        # the background: the 10 minutes either side, apart from the plane itself
MIN_SAMPLES = 30          # need at least this much recording around a plane to say anything
SETTLE_S = 180            # wait this long after a plane has gone before measuring it
DEFAULT_NIGHT = (clock_time(23, 0), clock_time(7, 0))


def night_hours(cfg: Config) -> tuple[clock_time, clock_time]:
    return cfg.quiet_hours or DEFAULT_NIGHT


def is_night(cfg: Config, ts: float) -> bool:
    start, end = night_hours(cfg)
    t = cfg.local(ts).time()
    return (start <= t or t < end) if start > end else (start <= t < end)


def is_low(alt_ft: int | None, ground_ft: int) -> bool:
    """Less than LOW_FT above home (but off the ground)."""
    return alt_ft is not None and alt_ft > 0 and alt_ft - ground_ft < LOW_FT


def closest_time(cfg: Config, row) -> float:
    """When the plane was closest to home, from its recorded path (else the middle of its pass)."""
    path = json.loads(row["path"] or "[]")
    if path:
        return min(path, key=lambda p: distance_miles(cfg.home_lat, cfg.home_lon, p[0], p[1]))[3]
    return (row["first_seen"] + row["last_seen"]) / 2


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def measure(levels: list[tuple[int, float]], t0: float) -> tuple[float, float] | None:
    """(peak, background) in dB around time t0, or None if the microphone wasn't on then."""
    window = [db for t, db in levels if abs(t - t0) <= WINDOW_S]
    if len(window) < MIN_SAMPLES:
        return None
    around = [db for t, db in levels if WINDOW_S < abs(t - t0) <= BACKGROUND_S]
    # The quieter moments are the background (a 20th percentile ignores dogs, cars, doors...).
    background = percentile(around if len(around) >= MIN_SAMPLES else window, 0.2)
    return round(max(window), 1), round(background, 1)


def settle(store, cfg: Config, now: float) -> int:
    """Measure every plane that's finished going by. Returns how many got a measurement."""
    first_level = store.db.execute("SELECT MIN(t) FROM noise_levels").fetchone()[0]
    if first_level is None:
        return 0
    rows = store.db.execute(
        "SELECT id, path, first_seen, last_seen FROM passes WHERE noise_state = 0 AND last_seen <= ? "
        "AND last_seen >= ? ORDER BY first_seen", (now - SETTLE_S, first_level - BACKGROUND_S)).fetchall()
    measured = 0
    for r in rows:
        t0 = closest_time(cfg, r)
        result = measure(store.noise_levels(t0 - BACKGROUND_S, t0 + BACKGROUND_S), t0)
        if result:
            store.set_noise(r["id"], result[0], result[1], 1)
            measured += 1
        elif now - t0 > BACKGROUND_S + SETTLE_S:
            store.set_noise(r["id"], None, None, 2)  # the microphone wasn't listening then
    return measured


def loudness(row) -> float | None:
    """How many dB louder than the background it was."""
    if row["noise_state"] != 1 or row["noise_peak"] is None or row["noise_bg"] is None:
        return None
    return round(row["noise_peak"] - row["noise_bg"], 1)


def _flight(cfg: Config, row, ground_ft: int, **extra) -> dict:
    alt = row["closest_alt_ft"]
    return {"id": row["id"], "label": label(row), "callsign": row["callsign"], "when": row["first_seen"],
            "airline": row["airline"], "aircraft": row["aircraft_desc"] or row["type_code"],
            "alt_ft": alt, "above_home_ft": None if alt is None else max(0, alt - ground_ft),
            "miles": round(row["closest_miles"], 2), **extra}


def build_noise(store, cfg: Config, now: float, days: int, ground_ft: int = 0) -> dict:
    """The noise tab: low and night flights, the loudest measured, and night flights per day."""
    since = now - days * 86400
    rows = store.db.execute("SELECT * FROM passes WHERE first_seen >= ? ORDER BY first_seen", (since,)).fetchall()
    low = [r for r in rows if is_low(r["closest_alt_ft"], ground_ft)]
    night = [r for r in rows if is_night(cfg, r["first_seen"])]
    measured = [(loudness(r), r) for r in rows if loudness(r) is not None]
    measured.sort(key=lambda m: -m[0])

    today = cfg.local(now).date()
    per_day = []
    for i in range(days - 1, -1, -1):
        d = today.toordinal() - i
        per_day.append({"date": d, "count": 0})
    index = {e["date"]: e for e in per_day}
    for r in night:
        # A night belongs to the evening it started on, so 1 am counts towards the night before.
        local = cfg.local(r["first_seen"])
        d = local.date().toordinal() - (1 if local.time() < night_hours(cfg)[1] else 0)
        if d in index:
            index[d]["count"] += 1
    for e in per_day:
        e["date"] = type(today).fromordinal(e["date"]).isoformat()

    start, end = night_hours(cfg)
    return {
        "days": days, "low_ft": LOW_FT, "night": [start.strftime("%H:%M"), end.strftime("%H:%M")],
        "total": len(rows), "low_count": len(low), "night_count": len(night),
        "lowest": [_flight(cfg, r, ground_ft) for r in sorted(low, key=lambda r: r["closest_alt_ft"])[:10]],
        "latest_night": [_flight(cfg, r, ground_ft) for r in reversed(night[-10:])],
        "loudest": [_flight(cfg, r, ground_ft, louder_db=db) for db, r in measured[:10]],
        "measured_count": len(measured),
        "nights": per_day,
        "listening": store.db.execute("SELECT MAX(t) FROM noise_levels").fetchone()[0],
    }


CSV_COLUMNS = ["Date", "Time", "Flight", "Registration", "Airline", "Aircraft type", "Aircraft", "From", "To",
               "Closest (miles)", "Direction", "Altitude (ft)", "Height above home (ft)", "Speed (kt)",
               "Low", "Night", "Quiet hours", "Louder than background (dB)", "ICAO address"]


def noise_csv(store, cfg: Config, now: float, days: int, ground_ft: int = 0) -> str:
    """Every plane over your zone as a spreadsheet (opens in Numbers, Excel or Google Sheets)."""
    since = 0 if days <= 0 else now - days * 86400
    rows = store.db.execute("SELECT * FROM passes WHERE first_seen >= ? ORDER BY first_seen", (since,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        local = cfg.local(r["first_seen"])
        alt = r["closest_alt_ft"]
        above = None if alt is None else max(0, alt - ground_ft)
        louder = loudness(r)
        w.writerow([
            local.strftime("%Y-%m-%d"), local.strftime("%H:%M"), r["callsign"], r["registration"], r["airline"],
            r["type_code"], r["aircraft_desc"], r["origin"], r["destination"], f"{r['closest_miles']:.2f}",
            compass(r["closest_bearing"]) if r["closest_bearing"] is not None else "",
            "" if alt is None else alt, "" if above is None else above,
            "" if r["speed_kt"] is None else round(r["speed_kt"]),
            "yes" if is_low(alt, ground_ft) else "no",
            "yes" if is_night(cfg, r["first_seen"]) else "no",
            "yes" if cfg.in_quiet_hours(r["first_seen"]) else "no",
            "" if louder is None else louder, r["hex"],
        ])
    # A byte-order mark so Excel opens it as UTF-8 (airline names with accents, arrows...).
    return "﻿" + out.getvalue()
