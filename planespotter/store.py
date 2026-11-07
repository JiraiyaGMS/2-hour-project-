"""SQLite storage: every pass over home, lookup cache, and a little bookkeeping.

A *pass* is one aircraft flying through the zone. Sightings of the same
aircraft less than ``gap`` seconds apart belong to the same pass.
"""

from __future__ import annotations

import json
import sqlite3
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
"""


class Store:
    def __init__(self, path: str | Path = "planespotter.db"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

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
