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


def get_json(url: str, timeout: float = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


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
    )


class LiveFeed:
    """Aircraft currently near a point, from adsb.lol."""

    def __init__(self, base_url: str = "https://api.adsb.lol", timeout: float = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        radius = max(1, min(250, math.ceil(radius_nm)))
        url = f"{self.base_url}/v2/point/{lat:.5f}/{lon:.5f}/{radius}"
        data = get_json(url, self.timeout)
        found = (parse_aircraft(raw) for raw in data.get("ac") or [])
        return [ac for ac in found if ac and ac.hex]


def retry_after_seconds(exc: urllib.error.HTTPError) -> float:
    """The server's Retry-After header in seconds, if it sent one (capped at 15 minutes)."""
    try:
        return min(900.0, float((exc.headers or {}).get("Retry-After") or 0))
    except (TypeError, ValueError):
        return 0.0


class SharedFeed:
    """One request to adsb.lol serves both the watcher and the live map.

    Results are reused for ``ttl`` seconds, and if adsb.lol says we're asking
    too often (HTTP 429), we back off and keep showing the last answer for a bit.
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
                raise urllib.error.URLError("adsb.lol asked us to slow down; trying again shortly")
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
