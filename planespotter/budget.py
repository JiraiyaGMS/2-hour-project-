"""Keeps planespotter inside adsb.lol's daily request limit.

adsb.lol counts requests to its live-data and flight-history API ("metered"
paths) per internet connection: 500 a day without a key, 1,000 with a free
key, 7,200 for people who feed data to adsb.lol from their own receiver. Going
over means "429 Too Many Requests" until the window resets, which would also
stop the email alerts, so everything that talks to adsb.lol shares one budget:

* checking for planes over home gets most of it, spread evenly over the day,
  and faster while someone is looking at the live map;
* whole-flight lookups get a quarter;
* where planes are now (for the map) gets a little;
* the offline log (planes that came over while planespotter was off) gets up
  to a fifth, once, when it starts again.

Usage is stored in the database, so restarts and a separate `map` window
share the same count.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

POLL_SHARE = 0.70      # spread evenly over the day for the alerts
TRACE_SHARE = 0.25     # whole-flight lookups
CATCHUP_SHARE = 0.20   # the offline log
VIEWING_FAST = 30      # seconds between checks while someone is watching the live map
VIEWING_UNTIL = 0.60   # ...as long as less than this much of the day's budget is used
NEARLY_OUT = 0.97      # keep a little back for the person clicking around on the map


class ApiBudget:
    def __init__(self, db_path: str | Path, daily_limit: int, min_poll: int = 20, clock=time.time):
        self.limit = max(50, int(daily_limit))
        self.min_poll = max(10, int(min_poll))
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS api_usage (minute INTEGER NOT NULL, kind TEXT NOT NULL, "
            "n INTEGER NOT NULL, PRIMARY KEY (minute, kind))")
        self.db.commit()
        self.viewing_until = 0.0

    # --- counting -----------------------------------------------------------------

    def record(self, kind: str) -> None:
        minute = int(self.clock() // 60)
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO api_usage (minute, kind, n) VALUES (?, ?, 0)", (minute, kind))
            self.db.execute("UPDATE api_usage SET n = n + 1 WHERE minute = ? AND kind = ?", (minute, kind))
            self.db.execute("DELETE FROM api_usage WHERE minute < ?", (minute - 2 * 1440,))
            self.db.commit()

    def used(self, kind: str | None = None) -> int:
        """Requests in the last 24 hours (of one kind, or all)."""
        since = int(self.clock() // 60) - 1440
        with self.lock:
            if kind:
                row = self.db.execute("SELECT SUM(n) FROM api_usage WHERE minute > ? AND kind = ?", (since, kind))
            else:
                row = self.db.execute("SELECT SUM(n) FROM api_usage WHERE minute > ?", (since,))
            return int(row.fetchone()[0] or 0)

    # --- deciding -----------------------------------------------------------------

    def viewing(self) -> bool:
        return self.clock() < self.viewing_until

    def mark_viewing(self, seconds: float = 60) -> None:
        """Someone has the live map open."""
        self.viewing_until = self.clock() + seconds

    @property
    def base_interval(self) -> float:
        """Seconds between checks that spread the polling share evenly over 24 hours."""
        return max(self.min_poll, 86400 / (self.limit * POLL_SHARE))

    def poll_interval(self) -> float:
        used = self.used()
        if used >= self.limit * NEARLY_OUT:
            return max(self.base_interval, 900)  # nearly out: once every 15 minutes
        if self.viewing() and used < self.limit * VIEWING_UNTIL:
            return max(self.min_poll, VIEWING_FAST)
        return self.base_interval

    def allow(self, kind: str) -> bool:
        """May we make one more request of this kind now?"""
        used = self.used()
        if kind == "trace":
            return used < self.limit * 0.90 and self.used("trace") < self.limit * TRACE_SHARE
        if kind == "catchup":
            return used < self.limit * 0.85 and self.used("catchup") < self.limit * CATCHUP_SHARE
        if kind == "positions":
            return used < self.limit * 0.85
        return used < self.limit * NEARLY_OUT  # "click": something the person asked for on the map

    def status(self) -> dict:
        return {"used": self.used(), "limit": self.limit, "interval_s": round(self.poll_interval())}
