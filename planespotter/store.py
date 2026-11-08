"""SQLite storage: every pass over home, lookup cache, and a little bookkeeping.

A *pass* is one aircraft flying through the zone. Sightings of the same
aircraft less than ``gap`` seconds apart belong to the same pass.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .adsb import Aircraft, FlightInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS passes (
    id               INTEGER PRIMARY KEY,
    hex              TEXT NOT NULL,
    callsign         TEXT NOT NULL DEFAULT '',
    registration     TEXT NOT NULL DEFAULT '',
    type_code        TEXT NOT NULL DEFAULT '',
    first_seen       REAL NOT NULL,
    last_seen        REAL NOT NULL,
    closest_miles    REAL NOT NULL,
    closest_alt_ft   INTEGER,
    closest_bearing  REAL,
    speed_kt         REAL,
    track            REAL,
    alerted          INTEGER NOT NULL DEFAULT 0,
    airline          TEXT NOT NULL DEFAULT '',
    origin           TEXT NOT NULL DEFAULT '',
    origin_city      TEXT NOT NULL DEFAULT '',
    destination      TEXT NOT NULL DEFAULT '',
    destination_city TEXT NOT NULL DEFAULT '',
    aircraft_desc    TEXT NOT NULL DEFAULT '',
    owner            TEXT NOT NULL DEFAULT '',
    photo_url        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS passes_by_hex ON passes (hex, last_seen);
CREATE INDEX IF NOT EXISTS passes_by_time ON passes (first_seen);

CREATE TABLE IF NOT EXISTS lookups (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_emails (sent_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Heat map: how many planes flew through each grid cell, per local day.
CREATE TABLE IF NOT EXISTS heat (
    day TEXT    NOT NULL,
    cx  INTEGER NOT NULL,
    cy  INTEGER NOT NULL,
    n   INTEGER NOT NULL,
    PRIMARY KEY (day, cx, cy)
);
"""


MAX_PATH_POINTS = 240


class Store:
    """One connection per thread: the watcher and the map server each open their own."""

    def __init__(self, path: str | Path = "planespotter.db"):
        self.db = sqlite3.connect(str(path), timeout=15)
        self.db.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            # Lets the map read while the watcher writes.
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring databases made by older versions up to date."""
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(passes)")}
        if "path" not in columns:
            # [[lat, lon, alt_ft, unix_time], ...] while the plane was near home
            self.db.execute("ALTER TABLE passes ADD COLUMN path TEXT NOT NULL DEFAULT '[]'")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- passes -------------------------------------------------------------

    def record_sighting(
        self, ac: Aircraft, distance: float, bearing: float, now: float, gap: float
    ) -> tuple[int, bool]:
        """Add a sighting; returns (pass id, True if this started a new pass)."""
        row = self.db.execute(
            "SELECT id, closest_miles FROM passes WHERE hex = ? AND last_seen >= ? "
            "ORDER BY last_seen DESC LIMIT 1",
            (ac.hex, now - gap),
        ).fetchone()

        if row is None:
            cur = self.db.execute(
                "INSERT INTO passes (hex, callsign, registration, type_code, first_seen, "
                "last_seen, closest_miles, closest_alt_ft, closest_bearing, speed_kt, track) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ac.hex, ac.callsign, ac.registration, ac.type_code, now, now,
                 distance, ac.alt_ft, bearing, ac.speed_kt, ac.track),
            )
            self.db.commit()
            return int(cur.lastrowid), True

        self.db.execute("UPDATE passes SET last_seen = ? WHERE id = ?", (now, row["id"]))
        # Fill in a callsign that wasn't broadcast on the first sighting.
        if ac.callsign:
            self.db.execute(
                "UPDATE passes SET callsign = ? WHERE id = ? AND callsign = ''",
                (ac.callsign, row["id"]),
            )
        if distance < row["closest_miles"]:
            self.db.execute(
                "UPDATE passes SET closest_miles = ?, closest_alt_ft = ?, closest_bearing = ?, "
                "speed_kt = ?, track = ? WHERE id = ?",
                (distance, ac.alt_ft, bearing, ac.speed_kt, ac.track, row["id"]),
            )
        self.db.commit()
        return int(row["id"]), False

    def set_info(self, pass_id: int, info: FlightInfo) -> None:
        fields = asdict(info)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE passes SET {assignments} WHERE id = ?", (*fields.values(), pass_id))
        self.db.commit()

    def mark_alerted(self, pass_ids: list[int]) -> None:
        self.db.executemany("UPDATE passes SET alerted = 1 WHERE id = ?", [(i,) for i in pass_ids])
        self.db.commit()

    def get_passes(self, pass_ids: list[int]) -> list[sqlite3.Row]:
        if not pass_ids:
            return []
        marks = ",".join("?" * len(pass_ids))
        return self.db.execute(
            f"SELECT * FROM passes WHERE id IN ({marks}) ORDER BY closest_miles", pass_ids
        ).fetchall()

    def append_path(self, pass_id: int, points: list[list[float]]) -> None:
        """Add positions to a pass's flight path (for drawing it on the map)."""
        if not points:
            return
        row = self.db.execute("SELECT path FROM passes WHERE id = ?", (pass_id,)).fetchone()
        if row is None:
            return
        path = json.loads(row["path"] or "[]")
        for point in points:
            if not path or path[-1][3] < point[3]:
                path.append([round(point[0], 5), round(point[1], 5), point[2], round(point[3])])
        self.db.execute("UPDATE passes SET path = ? WHERE id = ?",
                        (json.dumps(path[-MAX_PATH_POINTS:]), pass_id))
        self.db.commit()

    # --- heat map -------------------------------------------------------------

    def add_heat(self, day: str, cells, commit: bool = True) -> None:
        """Count one plane through each cell (a cell listed twice counts twice)."""
        counts = Counter(cells)
        if counts:
            self.db.executemany("INSERT OR IGNORE INTO heat (day, cx, cy, n) VALUES (?, ?, ?, 0)",
                                [(day, cx, cy) for cx, cy in counts])
            self.db.executemany("UPDATE heat SET n = n + ? WHERE day = ? AND cx = ? AND cy = ?",
                                [(n, day, cx, cy) for (cx, cy), n in counts.items()])
        if commit:
            self.db.commit()

    def heat_cells(self, since_day: str = "") -> list[tuple[int, int, int]]:
        """(cx, cy, total) for every cell with traffic on or after ``since_day`` (YYYY-MM-DD)."""
        return [tuple(r) for r in self.db.execute(
            "SELECT cx, cy, SUM(n) FROM heat WHERE day >= ? GROUP BY cx, cy", (since_day,))]

    def first_heat_day(self) -> str | None:
        return self.db.execute("SELECT MIN(day) FROM heat").fetchone()[0]

    def top_routes(self, since: float, limit: int = 5) -> list[sqlite3.Row]:
        """The most common origin → destination pairs among planes over the zone."""
        return self.db.execute(
            "SELECT origin, destination, MAX(origin_city) AS origin_city, "
            "MAX(destination_city) AS destination_city, COUNT(*) AS n FROM passes "
            "WHERE first_seen >= ? AND origin != '' AND destination != '' "
            "GROUP BY origin, destination ORDER BY n DESC, origin LIMIT ?",
            (since, limit),
        ).fetchall()

    def recent_passes(self, since: float, limit: int = 2000) -> list[sqlite3.Row]:
        """Newest first, for the map's flight list."""
        return self.db.execute(
            "SELECT * FROM passes WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT ?",
            (since, limit),
        ).fetchall()

    def passes_between(self, start: float, end: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM passes WHERE first_seen >= ? AND first_seen < ? ORDER BY first_seen",
            (start, end),
        ).fetchall()

    def first_pass_time(self) -> float | None:
        row = self.db.execute("SELECT MIN(first_seen) AS t FROM passes").fetchone()
        return row["t"]

    # --- alert rate limiting ------------------------------------------------

    def log_alert_email(self, now: float) -> None:
        self.db.execute("INSERT INTO alert_emails (sent_at) VALUES (?)", (now,))
        self.db.execute("DELETE FROM alert_emails WHERE sent_at < ?", (now - 7 * 86400,))
        self.db.commit()

    def alert_emails_since(self, since: float) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM alert_emails WHERE sent_at >= ?", (since,)
        ).fetchone()
        return int(row["n"])

    # --- lookup cache -------------------------------------------------------

    def cache_get(self, key: str, now: float) -> dict | None:
        row = self.db.execute(
            "SELECT value FROM lookups WHERE key = ? AND expires_at > ?", (key, now)
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_put(self, key: str, value: dict, now: float, ttl: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO lookups (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), now + ttl),
        )
        self.db.commit()

    # --- meta ---------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()
