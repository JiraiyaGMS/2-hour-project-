"""Travel mode: watch wherever you are for a day or two (a hotel, a friend's house, an airport).

It starts by itself: the planespotter script on your phone (the widget and
Siri) sends the phone's location when it has moved, and once you're more than
a few miles from home (and your other places) planespotter watches where you
are too, moving the zone as you go and switching back when you're home.

You can also start it from the map ("Watch from here"), or by sending
"travel 33.81,-117.92" to your private ntfy command topic. It runs like one of
your other places: its own zone, alerts and map, sharing adsb.lol's daily
limit, and switches itself off after two days (or when you send "home").
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
from .config import parse_places
from .geo import distance_miles
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_HOURS = 48
TRAVEL_PORT_OFFSET = 9   # the travel map is on MAP_PORT + 9 (8774 by default)
FOLLOW_AWAY_MILES = 5    # farther than this from home (and your other places): watch where your phone is
FOLLOW_MOVE_MILES = 3    # move the zone once your phone is this far from its middle
FOLLOW_HOURS = 12        # a followed trip ends this long after your phone last said where it is


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

    def follow(self, lat: float, lon: float) -> str:
        """Your phone says where it is: watch there if you're away, stop when you're back. Returns news, or ''."""
        if not self.cfg.follow_phone:
            return ""
        spots = [(self.cfg.home_lat, self.cfg.home_lon)] + [(la, lo) for _, la, lo, _ in parse_places(self.cfg.places)]
        near = min(distance_miles(lat, lon, a, b) for a, b in spots)
        now = self.clock()
        with self.lock:
            trip = self.trip
            if near <= FOLLOW_AWAY_MILES:
                if trip and trip.get("auto"):
                    self._stop()
                    self._save("")
                    return f"🏠 Welcome back: just watching {self.cfg.home_name} (and your other places) again."
                return ""
            if trip and distance_miles(lat, lon, trip["lat"], trip["lon"]) <= FOLLOW_MOVE_MILES:
                if trip.get("auto"):  # still here: keep watching
                    trip["until"] = max(trip["until"], now + FOLLOW_HOURS * 3600)
                    self._save(json.dumps(trip))
                return ""
            moving_on = bool(trip and trip.get("auto"))
            self._stop()
            new = {"lat": round(lat, 5), "lon": round(lon, 5), "name": place_name(lat, lon),
                   "until": now + FOLLOW_HOURS * 3600, "auto": True}
            self._run(new)
            self._save(json.dumps(new))
            log.info("following your phone: watching %s", new["name"])
            if moving_on:
                return ""  # moving along with you: no need to say so every time
            return (f"📍 You're near {new['name']}: planespotter is watching there too, so plane alerts follow you. "
                    "It switches back by itself when you're home.")

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
        return {"on": True, "auto": False, **self.trip, "port": self.cfg.map_port + TRAVEL_PORT_OFFSET,
                "until_text": self.cfg.local(self.trip["until"]).strftime("%a %I:%M %p").replace(" 0", " ")}
