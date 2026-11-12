"""Talking to the free flight-data APIs.

* Live positions: adsb.lol (community ADS-B network, no key needed).
* Flight routes and aircraft details: adsbdb.com (no key needed).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .geo import distance_miles

log = logging.getLogger(__name__)

USER_AGENT = "planespotter/1.0 (hobby plane-spotting script)"


def get_json(url: str, timeout: float = 15, headers: dict | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def key_header(api_key: str) -> dict:
    """adsb.lol reads an (optional) API key from the x-api-key header."""
    return {"x-api-key": api_key} if api_key else {}


@dataclass(frozen=True)
class Aircraft:
    hex: str
    callsign: str
    registration: str
    type_code: str
    lat: float
    lon: float
    alt_ft: int | None
    speed_kt: float | None
    track: float | None
    squawk: str = ""
    seen_pos: float = 0.0  # seconds since the position was received
    category: str = ""     # ADS-B emitter category: A1 light ... A5 heavy, A7 helicopter
    db_flags: int = 0      # adsb.lol's aircraft database: 1 = military
    emergency: str = ""    # "general", "lifeguard", "nordo"... when the crew declares one

    @property
    def label(self) -> str:
        return self.callsign or self.registration or self.hex.upper()


def parse_aircraft(raw: dict[str, Any]) -> Aircraft | None:
    """Turn one entry from the API's ``ac`` list into an Aircraft.

    Returns None for planes on the ground, without a position, or whose
    position is stale.
    """
    if not isinstance(raw.get("lat"), (int, float)) or not isinstance(raw.get("lon"), (int, float)):
        return None
    if raw.get("alt_baro") == "ground":
        return None
    if (raw.get("seen_pos") or 0) > 60:
        return None

    alt = raw.get("alt_baro")
    if not isinstance(alt, (int, float)):
        alt = raw.get("alt_geom")
    alt_ft = int(alt) if isinstance(alt, (int, float)) else None

    def num(key: str) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return Aircraft(
        hex=str(raw.get("hex", "")).lower(),
        callsign=str(raw.get("flight") or "").strip(),
        registration=str(raw.get("r") or "").strip(),
        type_code=str(raw.get("t") or "").strip(),
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        alt_ft=alt_ft,
        speed_kt=num("gs"),
        track=num("track") if num("track") is not None else num("true_heading"),
        squawk=str(raw.get("squawk") or ""),
        seen_pos=float(raw.get("seen_pos") or 0),
        category=str(raw.get("category") or ""),
        db_flags=int(raw.get("dbFlags") or 0) if str(raw.get("dbFlags") or 0).isdigit() else 0,
        emergency="" if raw.get("emergency") in (None, "none") else str(raw.get("emergency")),
    )


class LiveFeed:
    """Aircraft currently near a point, from adsb.lol."""

    def __init__(self, base_url: str = "https://api.adsb.lol", timeout: float = 15, api_key: str = "", budget=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.budget = budget

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        radius = max(1, min(250, math.ceil(radius_nm)))
        url = f"{self.base_url}/v2/point/{lat:.5f}/{lon:.5f}/{radius}"
        if self.budget:
            self.budget.record("poll")
        data = get_json(url, self.timeout, key_header(self.api_key))
        found = (parse_aircraft(raw) for raw in data.get("ac") or [])
        return [ac for ac in found if ac and ac.hex]


def retry_after_seconds(exc: urllib.error.HTTPError) -> float:
    """The server's Retry-After header in seconds, if it sent one (capped at 6 hours)."""
    try:
        return min(6 * 3600.0, float((exc.headers or {}).get("Retry-After") or 0))
    except (TypeError, ValueError):
        return 0.0


class SharedFeed:
    """One request to adsb.lol serves both the watcher and the live map.

    Only the watcher (or the map's own poller) fetches, on the budget's schedule;
    the map just reads the latest answer with ``latest()``. Results are reused for
    ``ttl`` seconds, and if adsb.lol says we're asking too often (HTTP 429), we back
    off for as long as it asks and keep showing the last answer for a bit.
    """

    def __init__(self, feed: LiveFeed, min_radius_nm: float = 0, ttl: float = 10.0, clock=None):
        self.feed = feed
        self.min_radius_nm = min_radius_nm
        self.ttl = ttl
        self.clock = clock or time.time
        self.lock = threading.Lock()
        self.data: list[Aircraft] | None = None
        self.radius = 0.0
        self.fetched_at = 0.0
        self.blocked_until = 0.0
        self.backoff = 0.0

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        with self.lock:
            now = self.clock()
            fresh = self.data is not None and now - self.fetched_at < self.ttl and radius_nm <= self.radius
            if fresh:
                return self.data
            if now < self.blocked_until:
                if self.data is not None and now - self.fetched_at < 180:
                    return self.data
                minutes = math.ceil((self.blocked_until - now) / 60)
                raise urllib.error.URLError(f"adsb.lol's request limit was reached; trying again in {minutes} min")
            radius = max(radius_nm, self.min_radius_nm)
            try:
                self.data = self.feed.aircraft_near(lat, lon, radius)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    # Wait longer each time it happens in a row: 45 s, 90 s, ... up to 5 minutes.
                    self.backoff = min(300.0, max(45.0, self.backoff * 2))
                    wait = max(self.backoff, retry_after_seconds(exc))
                    self.blocked_until = now + wait
                    log.info("adsb.lol asked us to slow down; pausing live requests for %ds", wait)
                raise
            self.backoff = 0.0
            self.radius = radius
            self.fetched_at = now
            return self.data

    def latest(self) -> tuple[list[Aircraft] | None, float]:
        """The most recent answer and when it arrived, without asking adsb.lol again."""
        with self.lock:
            return self.data, self.fetched_at

    def paused_for(self) -> float:
        """Seconds until we may ask again after a 429, or 0."""
        return max(0.0, self.blocked_until - self.clock())


class ReceiverFeed:
    """Planes from your own ADS-B receiver (readsb, dump1090-fa, tar1090...): unlimited and real-time.

    ``url`` is its aircraft.json, e.g. http://raspberrypi.local/tar1090/data/aircraft.json
    """

    def __init__(self, url: str, timeout: float = 5):
        self.url = url
        self.timeout = timeout

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        data = get_json(self.url, self.timeout)
        found = (parse_aircraft(raw) for raw in (data.get("aircraft") or data.get("ac") or []))
        limit = radius_nm * 1.15078 + 0.5  # nautical miles -> statute miles, a little slack
        return [ac for ac in found if ac and ac.hex and distance_miles(lat, lon, ac.lat, ac.lon) <= limit]


class FeedWithReceiver:
    """Your receiver first; adsb.lol (within its daily budget) only if the receiver can't be reached."""

    def __init__(self, receiver: ReceiverFeed, fallback, clock=time.time):
        self.receiver = receiver
        self.fallback = fallback
        self.clock = clock
        self.lock = threading.Lock()
        self.data: list[Aircraft] | None = None
        self.fetched_at = 0.0
        self.uses_receiver = True
        self.warned = False

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        try:
            data = self.receiver.aircraft_near(lat, lon, radius_nm)
            source = True
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if not self.warned:
                log.warning("your receiver isn't answering (%s); using adsb.lol until it's back", exc)
                self.warned = True
            data = self.fallback.aircraft_near(lat, lon, radius_nm)
            source = False
        with self.lock:
            if source and not self.uses_receiver:
                log.info("your receiver is back")
            self.uses_receiver = source
            self.warned = self.warned and not source
            self.data, self.fetched_at = data, self.clock()
        return data

    def latest(self) -> tuple[list[Aircraft] | None, float]:
        with self.lock:
            return self.data, self.fetched_at

    def paused_for(self) -> float:
        return 0.0 if self.uses_receiver else self.fallback.paused_for()


class PositionLookup:
    """Where specific planes are right now, wherever they are (not just near home).

    Used to draw a spotted flight that's still in the air all the way to its
    live position. One request covers many planes; answers are reused for ``ttl`` s.
    """

    def __init__(self, base_url: str = "https://api.adsb.lol", ttl: float = 60.0, clock=time.time,
                 api_key: str = "", budget=None):
        self.base_url = base_url.rstrip("/")
        self.ttl = ttl
        self.clock = clock
        self.api_key = api_key
        self.budget = budget
        self.lock = threading.Lock()
        self.known: dict[str, tuple[float, Aircraft | None]] = {}
        self.blocked_until = 0.0

    def fetch(self, hexes: list[str]) -> tuple[list[Aircraft], set[str]]:
        """(planes in the air, addresses of planes on the ground) among ``hexes``."""
        url = f"{self.base_url}/v2/hex/{','.join(hexes)}"
        if self.budget:
            self.budget.record("positions")
        data = get_json(url, timeout=15, headers=key_header(self.api_key))
        raw = data.get("ac") or []
        flying = [ac for ac in (parse_aircraft(r) for r in raw) if ac]
        ground = {str(r.get("hex", "")).lower() for r in raw if r.get("alt_baro") == "ground"}
        return flying, ground

    def get(self, hexes: list[str]) -> dict[str, Aircraft]:
        hexes = sorted({h for h in hexes if len(h) == 6 and all(c in "0123456789abcdef" for c in h)})
        with self.lock:
            now = self.clock()
            stale = [h for h in hexes if h not in self.known or now - self.known[h][0] >= self.ttl]
            allowed = self.budget is None or self.budget.allow("positions")
            if stale and now >= self.blocked_until and allowed:
                try:
                    for i in range(0, len(stale), 40):
                        chunk = stale[i:i + 40]
                        flying, ground = self.fetch(chunk)
                        found = {ac.hex: ac for ac in flying}
                        for h in chunk:
                            # An Aircraft, "ground" (it has landed), or None (out of range).
                            self.known[h] = (now, found.get(h) or ("ground" if h in ground else None))
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        self.blocked_until = now + max(120.0, retry_after_seconds(exc))
                    log.info("position lookup failed: %s", exc)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    log.info("position lookup failed: %s", exc)
            for h in [h for h, (t, _) in self.known.items() if now - t > 600]:
                del self.known[h]
            return {h: self.known[h][1] for h in hexes
                    if h in self.known and isinstance(self.known[h][1], Aircraft)}

    def on_ground(self, hexes: list[str]) -> set[str]:
        """Which of these planes adsb.lol last saw on the ground (they've landed)."""
        with self.lock:
            return {h for h in hexes if h in self.known and self.known[h][1] == "ground"}


@dataclass(frozen=True)
class FlightInfo:
    """Extra details that make an alert interesting. Every field may be blank."""

    airline: str = ""
    origin: str = ""
    origin_city: str = ""
    destination: str = ""
    destination_city: str = ""
    aircraft_desc: str = ""
    owner: str = ""
    photo_url: str = ""


def route_is_plausible(aircraft: Aircraft, origin: dict, dest: dict) -> bool:
    """Is the plane roughly on the way from origin to destination?

    Route databases map a flight number to a single leg, so flight numbers
    that fly several legs a day often come back with the wrong route.
    Reject any route that would put the plane way off its path.
    """
    try:
        olat, olon = float(origin["latitude"]), float(origin["longitude"])
        dlat, dlon = float(dest["latitude"]), float(dest["longitude"])
    except (KeyError, TypeError, ValueError):
        return False
    direct = distance_miles(olat, olon, dlat, dlon)
    via_plane = (distance_miles(olat, olon, aircraft.lat, aircraft.lon)
                 + distance_miles(aircraft.lat, aircraft.lon, dlat, dlon))
    return via_plane <= direct * 1.15 + 60


class Lookup:
    """Route and airframe lookups from adsbdb.com, cached in the database."""

    ROUTE_TTL = 3 * 86400
    AIRCRAFT_TTL = 30 * 86400
    MISS_TTL = 86400

    def __init__(self, store, base_url: str = "https://api.adsbdb.com", timeout: float = 8):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _cached_fetch(self, key: str, url: str, ttl: float, now: float) -> dict | None:
        cached = self.store.cache_get(key, now)
        if cached is not None:
            return cached or None  # {} means "known miss"
        try:
            body = get_json(url, self.timeout).get("response")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.store.cache_put(key, {}, now, self.MISS_TTL)
            else:
                log.info("lookup %s failed: HTTP %s", key, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            log.info("lookup %s failed: %s", key, exc)
            return None
        if not isinstance(body, dict):
            self.store.cache_put(key, {}, now, self.MISS_TTL)
            return None
        self.store.cache_put(key, body, now, ttl)
        return body

    def info(self, aircraft: Aircraft, now: float) -> FlightInfo:
        fields: dict[str, str] = {}

        if aircraft.callsign:
            body = self._cached_fetch(
                f"route:{aircraft.callsign}",
                f"{self.base_url}/v0/callsign/{aircraft.callsign}",
                self.ROUTE_TTL, now,
            )
            route = (body or {}).get("flightroute") or {}
            origin = route.get("origin") or {}
            dest = route.get("destination") or {}
            fields["airline"] = (route.get("airline") or {}).get("name") or ""
            if route_is_plausible(aircraft, origin, dest):
                fields.update(
                    origin=origin.get("iata_code") or origin.get("icao_code") or "",
                    origin_city=origin.get("municipality") or "",
                    destination=dest.get("iata_code") or dest.get("icao_code") or "",
                    destination_city=dest.get("municipality") or "",
                )

        if aircraft.hex and not aircraft.hex.startswith("~"):
            body = self._cached_fetch(
                f"aircraft:{aircraft.hex}",
                f"{self.base_url}/v0/aircraft/{aircraft.hex}",
                self.AIRCRAFT_TTL, now,
            )
            plane = (body or {}).get("aircraft") or {}
            maker = plane.get("manufacturer") or ""
            model = plane.get("type") or ""
            desc = model if maker and model.lower().startswith(maker.lower()) else f"{maker} {model}"
            fields.update(
                aircraft_desc=desc.strip(),
                owner=plane.get("registered_owner") or "",
                photo_url=plane.get("url_photo_thumbnail") or "",
            )

        return FlightInfo(**fields)
