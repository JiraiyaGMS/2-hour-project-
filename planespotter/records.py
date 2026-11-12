"""Records (lowest, fastest, longest flight...), the plane of the day, and your "I saw it" streak."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta

from .airports import airports
from .emails import fmt_alt, fmt_dist, label
from .geo import distance_miles
from .specials import SPECIAL_TYPES


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def flight_miles(origin: str, destination: str) -> float | None:
    """Great-circle distance between two airports, if both are known."""
    db = airports()
    a, b = db.get(origin), db.get(destination)
    if not a or not b or a.code == b.code:
        return None
    return distance_miles(a.lat, a.lon, b.lat, b.lon)


def _summary(p, value: str) -> dict:
    return {"id": p["id"], "label": label(p), "callsign": p["callsign"], "value": value,
            "when": p["first_seen"], "airline": p["airline"],
            "route": f"{p['origin']} → {p['destination']}" if p["origin"] and p["destination"] else ""}


def build_records(rows) -> list[dict]:
    """Your all-time records, from every pass."""
    out = []

    def best(key, emoji, name, candidates, pick, value):
        if candidates:
            p = pick(candidates)
            out.append({"key": key, "emoji": emoji, "name": name, **_summary(p, value(p))})

    flying = [p for p in rows if p["closest_alt_ft"] is not None and 0 < p["closest_alt_ft"] < 60000]
    best("lowest", "🔊", "Lowest", flying, lambda c: min(c, key=lambda p: p["closest_alt_ft"]),
         lambda p: fmt_alt(p["closest_alt_ft"]))
    best("closest", "🎯", "Closest", rows, lambda c: min(c, key=lambda p: p["closest_miles"]),
         lambda p: fmt_dist(p["closest_miles"]))
    fast = [p for p in rows if p["speed_kt"] is not None and p["speed_kt"] < 800]
    best("fastest", "💨", "Fastest", fast, lambda c: max(c, key=lambda p: p["speed_kt"]),
         lambda p: f"{round(p['speed_kt']):,} kt ({round(p['speed_kt'] * 1.15078):,} mph)")
    best("highest", "⛰️", "Highest", flying, lambda c: max(c, key=lambda p: p["closest_alt_ft"]),
         lambda p: fmt_alt(p["closest_alt_ft"]))

    trips = []
    for p in rows:
        miles = flight_miles(p["origin"], p["destination"]) if p["origin"] and p["destination"] else None
        if miles:
            trips.append((miles, p))
    if trips:
        miles, p = max(trips, key=lambda t: t[0])
        out.append({"key": "longest", "emoji": "🌍", "name": "Longest flight",
                    **_summary(p, f"{round(miles):,} mi")})

    regulars = Counter(p["hex"] for p in rows if not p["hex"].startswith("~"))
    if regulars:
        hex_code, n = regulars.most_common(1)[0]
        if n >= 3:
            p = max((r for r in rows if r["hex"] == hex_code), key=lambda r: r["first_seen"])
            name = p["registration"] or label(p)
            out.append({"key": "regular", "emoji": "🤝", "name": "Old friend",
                        **_summary(p, f"{name} · {n} times")})
    return out


def busiest(rows, cfg) -> dict:
    days = Counter(cfg.local(p["first_seen"]).date() for p in rows)
    hours = Counter(cfg.local(p["first_seen"]).replace(minute=0, second=0, microsecond=0) for p in rows)
    out = {}
    if days:
        d, n = max(days.items(), key=lambda kv: (kv[1], kv[0]))
        out["day"] = {"date": d.isoformat(), "count": n}
    if hours:
        h, n = max(hours.items(), key=lambda kv: (kv[1], kv[0]))
        out["hour"] = {"start": h.timestamp(), "count": n}
    return out


def plane_of_the_day(passes, store, cfg) -> dict | None:
    """The most interesting plane among ``passes``: a first or special plane, else the rarest type."""
    if not passes:
        return None
    type_counts = Counter(r[0] for r in store.db.execute("SELECT type_code FROM passes WHERE type_code != ''"))
    special_types = getattr(cfg, "special_types", SPECIAL_TYPES)

    def score(p):
        firsts = json.loads(p["firsts"] or "[]")
        special = bool(firsts) or p["type_code"] in special_types
        rarity = type_counts.get(p["type_code"], 10**9) if p["type_code"] else 10**9
        alt = p["closest_alt_ft"] if p["closest_alt_ft"] is not None else 10**6
        return (0 if special else 1, rarity, alt)

    p = min(passes, key=score)
    firsts = json.loads(p["firsts"] or "[]")
    name = p["aircraft_desc"] or p["type_code"]
    if firsts:
        why = firsts[0]
    elif p["type_code"]:
        nth = store.db.execute("SELECT COUNT(*) FROM passes WHERE type_code = ? AND first_seen <= ?",
                               (p["type_code"], p["first_seen"])).fetchone()[0]
        why = (f"Only the {ordinal(nth)} {name} you've spotted" if nth <= 5
               else f"The rarest plane: you've seen {type_counts.get(p['type_code'], nth):,} {name}s")
    else:
        why = f"Lowest at {fmt_alt(p['closest_alt_ft'])}"
    return {**_summary(p, name or ""), "why": why, "aircraft": name, "registration": p["registration"],
            "photo_url": p["photo_url"]}


def streak(eye_times: list[float], cfg, today: date) -> dict:
    """Days in a row you've spotted at least one plane with your own eyes."""
    days = sorted({cfg.local(t).date() for t in eye_times})
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    current = 0
    if days and (today - days[-1]).days <= 1:  # still alive: you have until the end of today
        current = run
    seen_today = sum(1 for t in eye_times if cfg.local(t).date() == today)
    return {"current": current, "best": best, "total": len(eye_times), "today": seen_today,
            "alive_today": bool(days) and days[-1] == today}
