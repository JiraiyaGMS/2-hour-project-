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

-- Collection badges, kept once earned.
CREATE TABLE IF NOT EXISTS badges (key TEXT PRIMARY KEY, earned_at REAL NOT NULL);

-- Special planes already announced (so each is announced once in a while, not every check).
CREATE TABLE IF NOT EXISTS special_seen (hex TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS special_seen_by_hex ON special_seen (hex, reason, at);

-- Planes you tapped "I saw it" on (for streaks).
CREATE TABLE IF NOT EXISTS eyes (hex TEXT NOT NULL, callsign TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS eyes_by_hex ON eyes (hex, at);

-- Your own photos of planes. pass_id is NULL for a plane that never crossed your zone.
CREATE TABLE IF NOT EXISTS photos (
    id       INTEGER PRIMARY KEY,
    pass_id  INTEGER,
    hex      TEXT NOT NULL,
    callsign TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    width    INTEGER NOT NULL DEFAULT 0,
    height   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS photos_by_pass ON photos (pass_id);

-- Microphone levels from the noise meter: the loudest moment of each second, in dB (full scale).
CREATE TABLE IF NOT EXISTS noise_levels (t INTEGER PRIMARY KEY, db REAL NOT NULL);

-- The same, on a coarser grid, for the whole flights of planes that crossed your zone.
CREATE TABLE IF NOT EXISTS route_heat (
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
        added = {
            # [[lat, lon, alt_ft, unix_time], ...] while the plane was near home
            "path": "TEXT NOT NULL DEFAULT '[]'",
            # The whole flight, looked up afterwards (same format), and the lookup's progress.
            "full_path": "TEXT NOT NULL DEFAULT '[]'",
            "full_state": "INTEGER NOT NULL DEFAULT 0",  # 0 waiting, 1 done, 2 not available
            "full_next": "REAL NOT NULL DEFAULT 0",
            "full_tries": "INTEGER NOT NULL DEFAULT 0",
            # 1 once the route has been checked against the plane's actual flight
            "route_checked": "INTEGER NOT NULL DEFAULT 0",
            # e.g. ["First Qantas flight you've spotted"], shown in its alert
            "firsts": "TEXT NOT NULL DEFAULT '[]'",
            # Noise meter: the loudest level as it passed, and the background level around then
            "noise_peak": "REAL",
            "noise_bg": "REAL",
            "noise_state": "INTEGER NOT NULL DEFAULT 0",  # 0 not yet, 1 measured, 2 no recording
        }
        for name, kind in added.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE passes ADD COLUMN {name} {kind}")
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

    def add_heat(self, day: str, cells, commit: bool = True, table: str = "heat") -> None:
        """Count one plane through each cell (a cell listed twice counts twice)."""
        assert table in ("heat", "route_heat")
        counts = Counter(cells)
        if counts:
            self.db.executemany(f"INSERT OR IGNORE INTO {table} (day, cx, cy, n) VALUES (?, ?, ?, 0)",
                                [(day, cx, cy) for cx, cy in counts])
            self.db.executemany(f"UPDATE {table} SET n = n + ? WHERE day = ? AND cx = ? AND cy = ?",
                                [(n, day, cx, cy) for (cx, cy), n in counts.items()])
        if commit:
            self.db.commit()

    def heat_cells(self, since_day: str = "", table: str = "heat") -> list[tuple[int, int, int]]:
        """(cx, cy, total) for every cell with traffic on or after ``since_day`` (YYYY-MM-DD)."""
        assert table in ("heat", "route_heat")
        return [tuple(r) for r in self.db.execute(
            f"SELECT cx, cy, SUM(n) FROM {table} WHERE day >= ? GROUP BY cx, cy", (since_day,))]

    # --- whole flights ----------------------------------------------------------

    def claim_full_path_job(self, now: float, settle: float, hold: float) -> sqlite3.Row | None:
        """The newest pass whose whole flight is due a lookup, reserved so no one else takes it."""
        while True:
            row = self.db.execute(
                "SELECT * FROM passes WHERE full_state = 0 AND full_next <= ? AND first_seen <= ? "
                "ORDER BY first_seen DESC LIMIT 1", (now, now - settle)).fetchone()
            if row is None:
                return None
            if row["hex"].startswith("~"):  # no real aircraft address: nothing to look up
                self.db.execute("UPDATE passes SET full_state = 2 WHERE id = ?", (row["id"],))
                self.db.commit()
                continue
            claimed = self.db.execute(
                "UPDATE passes SET full_next = ? WHERE id = ? AND full_next = ?",
                (now + hold, row["id"], row["full_next"])).rowcount
            self.db.commit()
            if claimed:
                return row

    def update_full_path(self, pass_id: int, path=None, state: int | None = None, tries: int | None = None,
                         next_try: float | None = None, route_heat=None) -> None:
        fields = {"full_path": None if path is None else json.dumps(path), "full_state": state,
                  "full_tries": tries, "full_next": next_try}
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            self.db.execute(f"UPDATE passes SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            (*fields.values(), pass_id))
        if route_heat:
            day, cells = route_heat
            self.add_heat(day, cells, commit=False, table="route_heat")
        self.db.commit()

    def set_route(self, pass_id: int, origin, destination) -> None:
        """Use the airports a flight really left from / arrived at (from its path)."""
        if origin is None and destination is None:
            return
        row = self.get_pass(pass_id)
        if row is None:
            return
        fields = {"route_checked": 1}
        if origin is not None:
            fields.update(origin=origin.code, origin_city=origin.city or origin.name)
        if destination is not None:
            fields.update(destination=destination.code, destination_city=destination.city or destination.name)
        elif origin is not None and row["origin"] and row["origin"] != origin.code:
            # The route database was describing a different flight, so its destination is suspect too.
            fields.update(destination="", destination_city="")
        self.db.execute(f"UPDATE passes SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                        (*fields.values(), pass_id))
        self.db.commit()

    # --- special planes ---------------------------------------------------------

    def special_announced(self, hex_code: str, reason: str, since: float) -> bool:
        return self.db.execute("SELECT 1 FROM special_seen WHERE hex = ? AND reason = ? AND at >= ?",
                               (hex_code, reason, since)).fetchone() is not None

    def log_special(self, hex_code: str, reason: str, at: float) -> None:
        self.db.execute("INSERT INTO special_seen (hex, reason, at) VALUES (?, ?, ?)", (hex_code, reason, at))
        self.db.execute("DELETE FROM special_seen WHERE at < ?", (at - 30 * 86400,))
        self.db.commit()

    def specials_since(self, since: float) -> int:
        return self.db.execute("SELECT COUNT(*) FROM special_seen WHERE at >= ?", (since,)).fetchone()[0]

    def seen_before(self, pass_id: int, airline: str | None = None, type_code: str | None = None) -> bool:
        """Has an earlier pass had this airline (ICAO code) / aircraft type?"""
        if airline:
            row = self.db.execute(
                "SELECT 1 FROM passes WHERE id != ? AND substr(callsign, 1, 3) = ? "
                "AND callsign GLOB '[A-Z][A-Z][A-Z][0-9]*' LIMIT 1", (pass_id, airline)).fetchone()
        else:
            row = self.db.execute("SELECT 1 FROM passes WHERE id != ? AND type_code = ? LIMIT 1",
                                  (pass_id, type_code)).fetchone()
        return row is not None

    def set_firsts(self, pass_id: int, firsts: list[str]) -> None:
        self.db.execute("UPDATE passes SET firsts = ? WHERE id = ?", (json.dumps(firsts), pass_id))
        self.db.commit()

    def latest_pass_for(self, hex_code: str, since: float) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM passes WHERE hex = ? AND last_seen >= ? ORDER BY last_seen DESC LIMIT 1",
                               (hex_code, since)).fetchone()

    # --- "I saw it" ---------------------------------------------------------------

    def saw(self, hex_code: str, callsign: str, at: float) -> None:
        """Remember that you spotted this plane with your own eyes (once per plane per hour)."""
        if not self.saw_recently(hex_code, at - 3600, at + 3600):
            self.db.execute("INSERT INTO eyes (hex, callsign, at) VALUES (?, ?, ?)", (hex_code, callsign, at))
            self.db.commit()

    def unsaw(self, hex_code: str, around: float) -> None:
        self.db.execute("DELETE FROM eyes WHERE hex = ? AND at BETWEEN ? AND ?", (hex_code, around - 3600, around + 3600))
        self.db.commit()

    def saw_recently(self, hex_code: str, start: float, end: float) -> bool:
        return self.db.execute("SELECT 1 FROM eyes WHERE hex = ? AND at BETWEEN ? AND ?",
                               (hex_code, start, end)).fetchone() is not None

    def eye_times(self) -> list[float]:
        return [r[0] for r in self.db.execute("SELECT at FROM eyes ORDER BY at")]

    # --- your photos ----------------------------------------------------------------

    def add_photo(self, pass_id: int | None, hex_code: str, callsign: str, at: float, width: int, height: int) -> int:
        cur = self.db.execute("INSERT INTO photos (pass_id, hex, callsign, added_at, width, height) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (pass_id, hex_code, callsign, at, width, height))
        self.db.commit()
        return int(cur.lastrowid)

    def delete_photo(self, photo_id: int) -> bool:
        gone = self.db.execute("DELETE FROM photos WHERE id = ?", (photo_id,)).rowcount
        self.db.commit()
        return bool(gone)

    def photos(self, limit: int = 500) -> list[sqlite3.Row]:
        """Newest first, with the flight each belongs to (when it crossed your zone)."""
        return self.db.execute(
            "SELECT photos.*, passes.registration, passes.type_code, passes.airline, passes.origin, "
            "passes.destination, passes.first_seen FROM photos LEFT JOIN passes ON passes.id = photos.pass_id "
            "ORDER BY photos.added_at DESC LIMIT ?", (limit,)).fetchall()

    # --- noise meter ------------------------------------------------------------------

    def add_noise_levels(self, samples, keep_seconds: float = 3 * 86400) -> None:
        """[(unix second, dB), ...] from the microphone; older recordings are tidied away."""
        rows = [(int(t), float(db)) for t, db in samples]
        if not rows:
            return
        self.db.executemany("INSERT OR REPLACE INTO noise_levels (t, db) VALUES (?, ?)", rows)
        self.db.execute("DELETE FROM noise_levels WHERE t < ?", (max(t for t, _ in rows) - keep_seconds,))
        self.db.commit()

    def noise_levels(self, start: float, end: float) -> list[tuple[int, float]]:
        return [tuple(r) for r in self.db.execute(
            "SELECT t, db FROM noise_levels WHERE t BETWEEN ? AND ? ORDER BY t", (start, end))]

    def set_noise(self, pass_id: int, peak: float | None, background: float | None, state: int) -> None:
        self.db.execute("UPDATE passes SET noise_peak = ?, noise_bg = ?, noise_state = ? WHERE id = ?",
                        (peak, background, state, pass_id))
        self.db.commit()

    def get_pass(self, pass_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM passes WHERE id = ?", (pass_id,)).fetchone()

    def full_path_counts(self, since: float) -> tuple[int, int]:
        """(whole flights mapped, still waiting) among passes since ``since``."""
        row = self.db.execute(
            "SELECT SUM(full_state = 1), SUM(full_state = 0) FROM passes WHERE first_seen >= ?",
            (since,)).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

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
