"""Daily digest: yesterday's recap plus a forecast of flights expected today.

There's no free source of exact flight paths, so the forecast is learned:
scheduled flights reuse the same flight number at roughly the same time
every day, so any callsign that has passed over on several recent days is
likely to pass over again today.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Config
from .store import Store


@dataclass
class Recap:
    """The 24 hours leading up to this morning's report (so overnight planes are included)."""

    start: float
    end: float
    passes: list[sqlite3.Row]
    busiest_hour: tuple[int, int] | None = None  # (hour, count)
    lowest: sqlite3.Row | None = None
    closest: sqlite3.Row | None = None
    top_airlines: list[tuple[str, int]] = field(default_factory=list)
    quiet_count: int = 0  # planes that arrived during quiet hours


@dataclass
class Expected:
    callsign: str
    typical_minute: int  # minutes after local midnight
    days_seen: int
    window_days: int
    airline: str
    origin: str
    origin_city: str
    destination: str
    destination_city: str
    aircraft: str
    typical_alt_ft: int | None

    @property
    def typical_time(self) -> str:
        return f"{self.typical_minute // 60:02d}:{self.typical_minute % 60:02d}"


@dataclass
class Forecast:
    day: date
    days_of_history: int
    window_days: int
    flights: list[Expected]

    @property
    def learning(self) -> bool:
        return self.days_of_history < 2


def build_recap(store: Store, cfg: Config, today: date) -> Recap:
    """Everything from yesterday's report time up to today's report time."""
    start = cfg.at(today - timedelta(days=1), cfg.digest_time)
    end = cfg.at(today, cfg.digest_time)
    passes = store.passes_between(start, end)
    recap = Recap(start=start, end=end, passes=passes)
    if not passes:
        return recap

    recap.quiet_count = sum(1 for p in passes if cfg.in_quiet_hours(p["first_seen"]))

    hours = Counter(cfg.local(p["first_seen"]).hour for p in passes)
    recap.busiest_hour = max(hours.items(), key=lambda kv: (kv[1], -kv[0]))
    with_alt = [p for p in passes if p["closest_alt_ft"] is not None]
    recap.lowest = min(with_alt, key=lambda p: p["closest_alt_ft"]) if with_alt else None
    recap.closest = min(passes, key=lambda p: p["closest_miles"])
    recap.top_airlines = Counter(p["airline"] for p in passes if p["airline"]).most_common(3)
    return recap


def build_forecast(
    store: Store, cfg: Config, today: date, window_days: int = 7, min_days: int = 2
) -> Forecast:
    window_start = cfg.day_start(today - timedelta(days=window_days))
    today_start = cfg.day_start(today)

    first = store.first_pass_time()
    if first is None or first >= today_start:
        history = 0
    else:
        history = min(window_days, math.ceil((today_start - first) / 86400))

    by_callsign: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for p in store.passes_between(window_start, today_start):
        if p["callsign"]:
            by_callsign[p["callsign"]].append(p)

    flights = []
    for callsign, passes in by_callsign.items():
        days = {cfg.local(p["first_seen"]).date() for p in passes}
        if len(days) < min_days:
            continue
        minutes = [cfg.local(p["first_seen"]).hour * 60 + cfg.local(p["first_seen"]).minute for p in passes]
        latest = max(passes, key=lambda p: p["first_seen"])
        alts = [p["closest_alt_ft"] for p in passes if p["closest_alt_ft"] is not None]
        flights.append(Expected(
            callsign=callsign,
            typical_minute=int(statistics.median(minutes)),
            days_seen=len(days),
            window_days=window_days,
            airline=latest["airline"],
            origin=latest["origin"],
            origin_city=latest["origin_city"],
            destination=latest["destination"],
            destination_city=latest["destination_city"],
            aircraft=latest["aircraft_desc"] or latest["type_code"],
            typical_alt_ft=int(statistics.median(alts)) if alts else None,
        ))

    flights.sort(key=lambda f: (f.typical_minute, f.callsign))
    return Forecast(day=today, days_of_history=history, window_days=window_days, flights=flights)
