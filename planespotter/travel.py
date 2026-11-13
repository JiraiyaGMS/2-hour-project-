"""Travel mode: watch wherever you are for a day or two (a hotel, a friend's house, an airport).

Start it from the map ("Watch from here", using the computer's location), or
from your phone through your private ntfy command topic (a Shortcut sends your
location, or type "travel 33.81,-117.92"). It runs like one of your other
places: its own zone, alerts, map and emails, sharing adsb.lol's daily limit,
and switches itself off after two days (or when you send "home").
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

from .airports import airports
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_HOURS = 48
TRAVEL_PORT_OFFSET = 9   # the travel map is on MAP_PORT + 9 (8774 by default)


def place_name(lat: float, lon: float) -> str:
    """'Anaheim' from the nearest airport's town (or 'Travel' out in the wilds)."""
    a = airports().nearest(lat, lon, max_km=40)
    return (a.city or a.name).split(" (")[0] if a else "Travel"


def parse_where(text: str) -> tuple[float, float, str] | None:
    """'33.81, -117.92 Disneyland Hotel' -> (33.81, -117.92, 'Disneyland Hotel')."""
    m = re.search(r"(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*(.*)$", text.strip())
    if not m:
        return None
    lat, lon = float(m[1]), float(m[2])
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon, m[3].strip()[:40]


class TravelMode:
    """Starts and stops the travel watcher (and remembers it across restarts)."""

    def __init__(self, home_cfg, home_watcher, budget, start_watcher, clock=time.time):
        """``start_watcher(cfg) -> (watcher, server or None)`` builds and runs a watcher for a place."""
        self.cfg, self.home, self.budget, self.start_watcher, self.clock = home_cfg, home_watcher, budget, start_watcher, clock
        self.lock = threading.Lock()
        self.watcher = self.server = None
        self.trip: dict | None = None

    def config_for(self, trip: dict):
        slug = re.sub(r"[^a-z0-9]+", "-", trip["name"].lower()).strip("-") or "travel"
        return replace(self.cfg, home_lat=trip["lat"], home_lon=trip["lon"], home_name=trip["name"],
                       db_path=Path(self.cfg.db_path).with_name(f"planespotter-travel-{slug}.db"),
                       map_port=self.cfg.map_port + TRAVEL_PORT_OFFSET, map_https=False, places="",
                       space_alerts=False, relay=False, family_alerts=False)

    def start(self, lat: float, lon: float, name: str = "", hours: float = DEFAULT_HOURS) -> dict:
        with self.lock:
            self._stop()
            trip = {"lat": round(lat, 5), "lon": round(lon, 5), "name": name or place_name(lat, lon),
                    "until": self.clock() + hours * 3600}
            self._run(trip)
            self._save(json.dumps(trip))
            log.info("travel mode: watching %s until %s", trip["name"], self.cfg.local(trip["until"]).strftime("%a %H:%M"))
            return self.status()

    def _share(self, change: int) -> None:
        """One more (or one fewer) place sharing adsb.lol's daily limit: every place checks less (more) often."""
        shared = getattr(self.home, "shared", None)
        if shared:
            shared["n"] = max(1, shared["n"] + change)
        else:
            self.home.share = max(1, self.home.share + change)

    def _run(self, trip: dict) -> None:
        self._share(+1)
        self.trip = trip
        self.watcher, self.server = self.start_watcher(self.config_for(trip))
        self.watcher.share = self.home.share
        self.watcher.shared = getattr(self.home, "shared", None)

    def stop(self) -> dict:
        with self.lock:
            self._stop()
            self._save("")
            return self.status()

    def _stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stopped.set()
            if self.server is not None:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            self._share(-1)
            log.info("travel mode: stopped watching %s", self.trip["name"] if self.trip else "")
        self.watcher = self.server = self.trip = None

    def restore(self) -> None:
        """After a restart, carry on with a trip that hasn't ended yet."""
        store = Store(self.cfg.db_path)
        saved = store.get_meta("travel")
        store.close()
        if saved:
            trip = json.loads(saved)
            if trip.get("until", 0) > self.clock():
                with self.lock:
                    self._run(trip)

    def _save(self, value: str) -> None:
        """Remember the trip across restarts (its own connection: this runs on the map's or commands' thread)."""
        store = Store(self.cfg.db_path)
        store.set_meta("travel", value)
        store.close()

    def check(self) -> None:
        """Switch off once the trip's time is up."""
        if self.trip and self.clock() > self.trip["until"]:
            self.stop()

    def status(self) -> dict:
        if not self.trip:
            return {"on": False}
        return {"on": True, **self.trip, "port": self.cfg.map_port + TRAVEL_PORT_OFFSET,
                "until_text": self.cfg.local(self.trip["until"]).strftime("%a %I:%M %p").replace(" 0", " ")}
