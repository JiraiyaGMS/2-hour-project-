"""Late or early? Your regular flights compared with their usual time over your house."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from .digest import build_forecast
from .emails import label

ON_TIME_MIN = 10   # within 10 minutes of its usual time counts as on time


def minute_of_day(cfg, ts: float) -> int:
    local = cfg.local(ts)
    return local.hour * 60 + local.minute


def status_text(delay: int) -> str:
    if abs(delay) <= ON_TIME_MIN:
        return "on time"
    return f"{abs(delay)} min {'late' if delay > 0 else 'early'}"


def today_board(store, cfg, now: float) -> dict:
    """Today's regular flights: on time, late, early, still due, or not seen."""
    today = cfg.local(now).date()
    forecast = build_forecast(store, cfg, today)
    seen_today = defaultdict(list)
    for p in store.passes_between(cfg.day_start(today), cfg.day_start(today + timedelta(days=1))):
        if p["callsign"]:
            seen_today[p["callsign"]].append(p)
    now_min = minute_of_day(cfg, now)
    flights = []
    for f in forecast.flights:
        row = {"callsign": f.callsign, "usual": f.typical_time, "airline": f.airline, "origin": f.origin,
               "destination": f.destination, "days_seen": f.days_seen}
        hits = seen_today.get(f.callsign)
        if hits:
            p = min(hits, key=lambda p: abs(minute_of_day(cfg, p["first_seen"]) - f.typical_minute))
            delay = minute_of_day(cfg, p["first_seen"]) - f.typical_minute
            row.update(state="seen", delay=delay, text=status_text(delay), id=p["id"], at=p["first_seen"])
        elif now_min > f.typical_minute + 90:
            row.update(state="missing", text="not seen today")
        elif now_min > f.typical_minute + ON_TIME_MIN:
            row.update(state="late", delay=now_min - f.typical_minute,
                       text=f"late: not here yet ({now_min - f.typical_minute} min)")
        else:
            row.update(state="due", text=f"due about {f.typical_time}")
        flights.append(row)
    return {"day": today.isoformat(), "learning": forecast.learning, "flights": flights}


def delays_for(store, cfg, passes) -> dict[int, int]:
    """{pass id: minutes late (negative = early)} for passes by regular flights, vs. the week before each."""
    out = {}
    by_callsign = defaultdict(list)
    for p in passes:
        if p["callsign"]:
            by_callsign[p["callsign"]].append(p)
    for callsign, ps in by_callsign.items():
        first, last = min(p["first_seen"] for p in ps), max(p["first_seen"] for p in ps)
        history = [r[0] for r in store.db.execute(
            "SELECT first_seen FROM passes WHERE callsign = ? AND first_seen >= ? AND first_seen < ?",
            (callsign, first - 8 * 86400, last))]
        for p in ps:
            before = [t for t in history if p["first_seen"] - 8 * 86400 <= t < p["first_seen"] - 3600]
            if len({cfg.local(t).date() for t in before}) < 2:
                continue  # not a regular (yet)
            usual = statistics.median(minute_of_day(cfg, t) for t in before)
            delay = int(minute_of_day(cfg, p["first_seen"]) - usual)
            if abs(delay) <= 180:  # beyond that it's a different flight with the same number
                out[p["id"]] = delay
    return out


def punctuality(store, cfg, start: float, end: float) -> dict:
    """For the weekly email: the most punctual and the latest of your regular flights."""
    passes = store.passes_between(start, end)
    delays = delays_for(store, cfg, passes)
    by_callsign = defaultdict(list)
    names = {}
    for p in passes:
        if p["id"] in delays:
            by_callsign[p["callsign"]].append(delays[p["id"]])
            names[p["callsign"]] = label(p)
    stats = [{"callsign": c, "average": round(statistics.mean(v)), "flights": len(v),
              "on_time": sum(1 for d in v if abs(d) <= ON_TIME_MIN)}
             for c, v in by_callsign.items() if len(v) >= 2]
    if not stats:
        return {}
    return {"best": min(stats, key=lambda s: (abs(s["average"]), -s["flights"])),
            "worst": max(stats, key=lambda s: (s["average"], s["flights"])),
            "flights": sum(s["flights"] for s in stats),
            "on_time": sum(s["on_time"] for s in stats)}

