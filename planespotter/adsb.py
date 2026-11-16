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

from .geo import bearing_deg, distance_miles

log = logging.getLogger(__name__)

USER_AGENT = "planespotter/1.0 (hobby plane-spotting script)"


def get_json(url: str, timeout: float = 15, headers: dict | None = None) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def counted(budget, kind: str, call):
    """Run one request to adsb.lol, and count it in the day's budget only if adsb.lol answered. A connection
    that failed, or a Wi-Fi sign-in page answering instead, used none of the allowance: counting those would
    slow the alerts down for the rest of the day after an hour offline."""
    try:
        result = call()
    except urllib.error.HTTPError:  # it answered (too many requests, not found...): that counts
        if budget is not None:
            budget.record(kind)
        raise
    except (OSError, ValueError):  # never reached adsb.lol (offline, timed out, or not its answer)
        raise
    except Exception:  # some other answer from it
        if budget is not None:
            budget.record(kind)
        raise
    if budget is not None:
        budget.record(kind)
    return result


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
    vr: float | None = None  # climbing (+) or descending (-), feet a minute

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
    if str(raw.get("category") or "").startswith("C"):
        return None  # airport vehicles and obstacles with transponders: not aircraft

    alt = raw.get("alt_baro")
    if not isinstance(alt, (int, float)):
        alt = raw.get("alt_geom")
    alt_ft = int(alt) if isinstance(alt, (int, float)) else None

    def num(key: str) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    def plain(key: str, size: int) -> str:
        # Only letters, digits and dashes: these come from what aircraft broadcast (anyone can transmit
        # anything), and end up in web pages, notifications and the database.
        return "".join(c for c in str(raw.get(key) or "").strip() if c.isascii() and (c.isalnum() or c == "-"))[:size]

    return Aircraft(
        # (adsb.lol puts "~" in front of addresses that aren't real aircraft ones: radar-only targets. Kept.)
        hex=("~" if str(raw.get("hex") or "").strip().startswith("~") else "") + plain("hex", 8).lower(),
        callsign=plain("flight", 8).upper(),
        registration=plain("r", 10).upper(),
        type_code=plain("t", 6).upper(),
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        alt_ft=alt_ft,
        speed_kt=num("gs"),
        track=num("track") if num("track") is not None else num("true_heading"),
        squawk=plain("squawk", 4),
        seen_pos=float(raw.get("seen_pos") or 0),
        category=str(raw.get("category") or ""),
        db_flags=int(raw.get("dbFlags") or 0) if str(raw.get("dbFlags") or 0).isdigit() else 0,
        emergency="" if raw.get("emergency") in (None, "none") else str(raw.get("emergency")),
        vr=num("baro_rate") if num("baro_rate") is not None else num("geom_rate"),
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
        data = counted(self.budget, "poll", lambda: get_json(url, self.timeout, key_header(self.api_key)))
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
        data = counted(self.budget, "positions", lambda: get_json(url, timeout=15, headers=key_header(self.api_key)))
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
    # How the route was arrived at (see routes.Route): "track" and "shows" are checked against the plane
    # itself, "agree" is two sources saying the same, "fits" is one source the plane's position fits.
    route_basis: str = ""

    @property
    def route_checked(self) -> bool:
        return self.route_basis in ("track", "shows")


def route_is_plausible(aircraft: Aircraft, origin: dict, dest: dict) -> bool:
    """Is the plane roughly on the way from origin to destination?

    Route databases map a flight number to a single leg, so flight numbers
    that fly several legs a day often come back with the wrong route.
    Reject any route that would put the plane way off its path. (routes.fit is the full check.)
    """
    from .routes import Leg, Place, fit

    try:
        o = Place("", "", "", float(origin["latitude"]), float(origin["longitude"]), float(origin.get("elevation") or 0))
        d = Place("", "", "", float(dest["latitude"]), float(dest["longitude"]), float(dest.get("elevation") or 0))
    except (KeyError, TypeError, ValueError):
        return False
    return fit(aircraft, Leg(o, d, "")) is not None


class Lookup:
    """Routes (from adsb.lol's standing data and adsbdb.com, checked against what the plane is doing) and
    airframe details (adsbdb.com), cached in the database."""

    ROUTE_TTL = 86400            # the standing data is rebuilt every day
    AIRCRAFT_TTL = 30 * 86400
    MISS_TTL = 12 * 3600
    ROUTES_URL = "https://vrs-standing-data.adsb.lol"

    def __init__(self, store, base_url: str = "https://api.adsbdb.com", timeout: float = 8,
                 routes_url: str | None = None):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.routes_url = (self.ROUTES_URL if routes_url is None else routes_url).rstrip("/")

    @classmethod
    def for_config(cls, store, cfg) -> "Lookup":
        return cls(store, cfg.adsbdb_api, routes_url=cfg.routes_api)

    def _cached_fetch(self, key: str, url: str, ttl: float, now: float, unwrap: str | None = "response") -> dict | None:
        cached = self.store.cache_get(key, now)
        if cached is not None:
            return cached or None  # {} means "known miss"
        try:
            body = get_json(url, self.timeout)
            if unwrap:
                body = body.get(unwrap) if isinstance(body, dict) else None
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

    def legs(self, callsign: str, now: float) -> list:
        """Every leg the route sources list for a callsign (both sources, each as it spells the callsign)."""
        from .routes import callsign_spellings, legs_from_adsbdb, legs_from_standing

        standing, adsbdb = [], []
        for cs in callsign_spellings(callsign):
            if not standing and self.routes_url:
                standing = legs_from_standing(self._cached_fetch(
                    f"standing:{cs}", f"{self.routes_url}/routes/{cs[:2]}/{cs}.json", self.ROUTE_TTL, now, unwrap=None))
            if not adsbdb and self.base_url:
                adsbdb = legs_from_adsbdb(self._cached_fetch(
                    f"route:{cs}", f"{self.base_url}/v0/callsign/{cs}", self.ROUTE_TTL, now))
        return standing + adsbdb

    def route(self, aircraft: Aircraft, now: float, took_off_from=None):
        """The route this plane is flying (routes.Route). ``took_off_from``: the airport (airports.Airport or
        routes.Place) its recorded track left the ground at, when that's known."""
        from .routes import Place, Route, airline_of, choose, place_from_airport

        if took_off_from is not None and not isinstance(took_off_from, Place):
            took_off_from = place_from_airport(took_off_from)
        legs = self.legs(aircraft.callsign, now) if aircraft.callsign else []
        found = choose(aircraft, legs, took_off_from)
        if not found.airline:
            found = Route(found.origin, found.destination, airline_of(aircraft.callsign), found.basis, found.source)
        return found

    def info(self, aircraft: Aircraft, now: float, took_off_from=None) -> FlightInfo:
        fields: dict[str, str] = {}

        route = self.route(aircraft, now, took_off_from)
        fields["airline"] = route.airline
        if route.origin is not None:
            fields.update(origin=route.origin.code, origin_city=route.origin.city)
        if route.destination is not None:
            fields.update(destination=route.destination.code, destination_city=route.destination.city)
        if route.origin is not None or route.destination is not None:
            fields["route_basis"] = route.basis

        if aircraft.hex and not aircraft.hex.startswith("~") and self.base_url:
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
