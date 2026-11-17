"""Talking to the free flight-data APIs.

* Live positions: adsb.lol (community ADS-B network, no key needed), and adsb.fi when adsb.lol can't be reached.
* Flight routes: adsb.lol's standing data and adsbdb.com, checked against the plane (see routes.py).
* Aircraft details: adsbdb.com (no key needed).
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
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


class FeedBusy(OSError):
    """The fast feed can't be asked just now: it was asked a moment ago, or it's being left alone for a while."""


class _Pace:
    """One pace for everything in this program that asks the same service (accounts, other places, the map and
    flights being tracked all count together towards its limit)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.next_at = 0.0     # when the next request may go
        self.down_until = 0.0  # left alone until then, after a failure or "too many requests"
        self.strikes = 0


class FastFeed:
    """adsb.fi's open data: the same planes, in the same format as adsb.lol, with no key and no daily
    allowance (at most one request a second). adsb.lol's allowance works out at a check every 2 to 4 minutes,
    so planespotter asks adsb.fi in between: where planes are is then seconds old instead of minutes. It's
    also what's used when adsb.lol can't be reached at all.

    Kept well inside its limit: ``MIN_GAP`` seconds between any two requests from this program, and when it
    says "too many requests", or doesn't answer, it's left alone for a while (longer each time).
    """

    MIN_GAP = 1.5    # seconds between any two requests (adsb.fi allows one a second)
    MAX_WAIT = 2.5   # rather than queue for longer than this, skip the request: the next will do
    _paces: dict[str, _Pace] = {}

    def __init__(self, base_url: str, timeout: float = 8.0, monotonic=time.monotonic, sleep=time.sleep):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.monotonic, self.sleep = monotonic, sleep
        self.pace = FastFeed._paces.setdefault(self.base_url, _Pace())

    @classmethod
    def forget(cls) -> None:
        """Start every pace afresh (for the tests)."""
        cls._paces.clear()

    def resting(self) -> bool:
        """Being left alone just now (after a failure, or because it asked us to slow down)."""
        return self.monotonic() < self.pace.down_until

    def get(self, path: str) -> dict:
        """One request. Raises FeedBusy (not asked), or what went wrong (an OSError or ValueError)."""
        pace = self.pace
        with pace.lock:
            now = self.monotonic()
            if now < pace.down_until:
                raise FeedBusy("the fast feed is being left alone for a while")
            wait = pace.next_at - now
            if wait > self.MAX_WAIT:
                raise FeedBusy("the fast feed was asked a moment ago")
            pace.next_at = max(now, pace.next_at) + self.MIN_GAP
        if wait > 0:
            self.sleep(wait)
        try:
            data = get_json(self.base_url + path, self.timeout)
            if not isinstance(data, dict) or not isinstance(data.get("ac") or data.get("aircraft") or [], list):
                raise ValueError("not the fast feed's answer")  # a sign-in page, say
        except urllib.error.HTTPError as exc:
            self._failed(max(60.0 if exc.code == 429 else 30.0, retry_after_seconds(exc)))
            raise
        except (OSError, ValueError):
            self._failed(20.0)
            raise
        with pace.lock:
            pace.strikes = 0
        return data

    def _failed(self, wait: float) -> None:
        pace = self.pace
        with pace.lock:
            pace.strikes += 1
            pace.down_until = self.monotonic() + min(900.0, wait * 2 ** min(pace.strikes - 1, 4))

    @staticmethod
    def rows(data: dict) -> list[dict]:
        return [r for r in (data.get("ac") or data.get("aircraft") or []) if isinstance(r, dict)]

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        radius = max(1, min(250, math.ceil(radius_nm)))
        found = (parse_aircraft(raw) for raw in self.rows(self.get(f"/v3/lat/{lat:.5f}/lon/{lon:.5f}/dist/{radius}")))
        return [ac for ac in found if ac and ac.hex]

    def by_hex(self, hexes: list[str]) -> list[dict]:
        """Where these aircraft are, as the feed sends them (up to 60 addresses in one request)."""
        return self.rows(self.get("/v2/hex/" + ",".join(hexes[:60])))


class LiveFeed:
    """Aircraft currently near a point, from adsb.lol.

    ``backup_url`` (adsb.fi's open data API, which answers in the same format): asked instead when adsb.lol
    can't be reached, is having trouble, or says we've asked too often. Then adsb.lol is left alone for a
    couple of minutes (or as long as it asked) before it's tried again. Without one, its errors are raised.
    """

    def __init__(self, base_url: str = "https://api.adsb.lol", timeout: float = 15, api_key: str = "", budget=None,
                 backup_url: str = "", clock=time.time):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.budget = budget
        self.backup_url = (backup_url or "").rstrip("/")
        self.backup = FastFeed(self.backup_url) if self.backup_url else None
        self.clock = clock
        self.using_backup = False
        self.main_down_until = 0.0

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        radius = max(1, min(250, math.ceil(radius_nm)))
        if self.backup_url and self.clock() < self.main_down_until:
            try:
                return self._from_backup(lat, lon, radius)
            except (OSError, ValueError):
                pass  # the backup's gone too: see if the main one is back
        url = f"{self.base_url}/v2/point/{lat:.5f}/{lon:.5f}/{radius}"
        try:
            data = counted(self.budget, "poll", lambda: get_json(url, self.timeout, key_header(self.api_key)))
            if not isinstance(data, dict):
                raise ValueError("not adsb.lol's answer")
        except urllib.error.HTTPError as exc:
            if not self.backup_url or (exc.code != 429 and exc.code < 500):
                raise
            return self._fall_back(lat, lon, radius, exc, max(120.0, retry_after_seconds(exc)))
        except (OSError, ValueError) as exc:  # no connection, timed out, or not its answer
            if not self.backup_url:
                raise
            return self._fall_back(lat, lon, radius, exc, 120.0)
        if self.using_backup:
            log.info("adsb.lol is answering again")
        self.using_backup = False
        found = (parse_aircraft(raw) for raw in data.get("ac") or [])
        return [ac for ac in found if ac and ac.hex]

    def _from_backup(self, lat: float, lon: float, radius: int) -> list[Aircraft]:
        return self.backup.aircraft_near(lat, lon, radius)

    def _fall_back(self, lat: float, lon: float, radius: int, exc: Exception, wait: float) -> list[Aircraft]:
        try:
            planes = self._from_backup(lat, lon, radius)
        except (OSError, ValueError):
            raise exc from None  # neither answered: it's adsb.lol's trouble that gets reported
        if not self.using_backup:
            log.warning("adsb.lol isn't answering (%s); using adsb.fi until it's back", exc)
        self.using_backup = True
        self.main_down_until = self.clock() + wait
        return planes


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

    With a ``fast`` feed (adsb.fi), ``aircraft_fast`` asks that in between, and what comes back from
    either is one picture: each plane where it was most recently seen, by whichever saw it last.
    """

    FAST_KEEP = 90.0   # the fast feed's answer counts for this long (it's asked every few seconds)
    MAIN_KEEP = 330.0  # ...and adsb.lol's for this long (its next turn can be 4 minutes away)

    def __init__(self, feed: LiveFeed, min_radius_nm: float = 0, ttl: float = 10.0, clock=None,
                 fast: FastFeed | None = None):
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
        self.fast = fast
        self.fast_data: list[Aircraft] | None = None
        self.fast_at = 0.0
        self.fast_radius = 0.0
        self.fast_saw: dict[str, float] = {}  # hex -> when the fast feed last had it

    def aircraft_fast(self, lat: float, lon: float, radius_nm: float, every: float = 10.0) -> list[Aircraft] | None:
        """The planes again, a few seconds on: from the fast feed (unless it was asked within ``every``
        seconds: several accounts share one answer), blended with adsb.lol's last answer. None when there's no
        fast feed or it can't be asked just now: carry on with what you have."""
        if self.fast is None:
            return None
        with self.lock:
            now = self.clock()
            if self.fast_data is not None and now - self.fast_at < every and radius_nm <= self.fast_radius:
                return self._now()
            radius = max(radius_nm, self.min_radius_nm)
            try:
                planes = self.fast.aircraft_near(lat, lon, radius)
            except FeedBusy:
                return None
            except (OSError, ValueError) as exc:
                log.debug("the fast feed didn't answer: %s", exc)
                return None
            self.fast_data, self.fast_at, self.fast_radius = planes, now, radius
            budget = getattr(self.feed, "budget", None)
            if budget is not None and hasattr(budget, "mark_fast"):
                budget.mark_fast()  # no need to spend adsb.lol's allowance on speed while this is answering
            for ac in planes:
                self.fast_saw[ac.hex] = now
            for hex_code in [h for h, t in self.fast_saw.items() if now - t > 900]:
                del self.fast_saw[hex_code]
            return self._now()

    def _blend(self) -> tuple[list[Aircraft] | None, float]:
        """(the planes, as of when): adsb.lol's last answer and the fast feed's as one. Each plane is where
        whichever feed saw it most recently put it, and ``seen_pos`` says how long before "as of" that was.
        A plane the fast feed has been following that's no longer in its answer has left: adsb.lol's older
        answer doesn't bring it back."""
        now = self.clock()
        if self.fast_data is None or now - self.fast_at > self.FAST_KEEP:
            return self.data, self.fetched_at
        if self.data is None or self.fast_at - self.fetched_at > self.MAIN_KEEP:
            return self.fast_data, self.fast_at
        at = max(self.fetched_at, self.fast_at)
        best: dict[str, tuple[float, Aircraft]] = {}
        for ac in self.data:
            seen_at = self.fetched_at - ac.seen_pos
            if self.fast_saw.get(ac.hex, 0.0) <= seen_at:  # (else: followed since by the fast feed, and gone)
                best[ac.hex] = (seen_at, ac)
        for ac in self.fast_data:
            seen_at = self.fast_at - ac.seen_pos
            if ac.hex not in best or seen_at >= best[ac.hex][0]:
                best[ac.hex] = (seen_at, ac)
        return [replace(ac, seen_pos=round(max(0.0, at - seen_at), 1)) for seen_at, ac in best.values()], at

    def _now(self) -> list[Aircraft] | None:
        """The same picture for whoever just asked: ``seen_pos`` counted back from this moment (an answer
        kept for a while, for several accounts to share, is that much older than when it arrived)."""
        planes, at = self._blend()
        late = self.clock() - at
        if planes is None or late < 1:
            return planes
        return [replace(ac, seen_pos=round(ac.seen_pos + late, 1)) for ac in planes]

    def aircraft_near(self, lat: float, lon: float, radius_nm: float) -> list[Aircraft]:
        with self.lock:
            now = self.clock()
            fresh = self.data is not None and now - self.fetched_at < self.ttl and radius_nm <= self.radius
            if fresh:
                return self._now()
            if now < self.blocked_until:
                if self.data is not None and now - self.fetched_at < 180:
                    return self._now()
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
            return self._now()

    def latest(self) -> tuple[list[Aircraft] | None, float]:
        """The most recent picture and when it's from, without asking anyone again."""
        with self.lock:
            return self._blend()

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

    FAST_TTL = 12.0  # with a fast feed, asked this often instead (it has no daily allowance)

    def __init__(self, base_url: str = "https://api.adsb.lol", ttl: float = 60.0, clock=time.time,
                 api_key: str = "", budget=None, fast: FastFeed | None = None):
        self.base_url = base_url.rstrip("/")
        self.ttl = ttl
        self.clock = clock
        self.api_key = api_key
        self.budget = budget
        self.fast = fast
        self.lock = threading.Lock()
        self.known: dict[str, tuple[float, Aircraft | None]] = {}
        self.blocked_until = 0.0

    def _ask_fast(self, hexes: list[str], now: float) -> None:
        """Bring the planes the fast feed can see up to date. The ones it can't are left as they were, for
        adsb.lol to answer on its own (slower) schedule."""
        due = [h for h in hexes if h not in self.known or now - self.known[h][0] >= self.FAST_TTL]
        try:
            for i in range(0, len(due), 60):
                raw = self.fast.by_hex(due[i:i + 60])
                for r in raw:
                    hex_code = str(r.get("hex", "")).lower()
                    ac = parse_aircraft(r)
                    if ac:
                        self.known[hex_code] = (now, ac)
                    elif r.get("alt_baro") == "ground":
                        self.known[hex_code] = (now, "ground")
        except (OSError, ValueError) as exc:
            if not isinstance(exc, FeedBusy):
                log.debug("the fast feed didn't answer about positions: %s", exc)

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
            if self.fast is not None and hexes:
                self._ask_fast(hexes, now)
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
            # (An answer kept for a while is that much older than when it arrived: seen_pos says so, and the
            # map moves the plane on by it.)
            return {h: replace(self.known[h][1], seen_pos=round(self.known[h][1].seen_pos + now - self.known[h][0], 1))
                    for h in hexes if h in self.known and isinstance(self.known[h][1], Aircraft)}

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

    def _ask(self, key: str, url: str, unwrap: str | None):
        """One request: ("found", body), ("missing", None) when the service has nothing for it, or
        ("failed", None) when it couldn't be asked (so it's worth asking again soon). Touches no database:
        safe to run on another thread."""
        try:
            body = get_json(url, self.timeout)
            if unwrap:
                body = body.get(unwrap) if isinstance(body, dict) else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "missing", None
            log.info("lookup %s failed: HTTP %s", key, exc.code)
            return "failed", None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            log.info("lookup %s failed: %s", key, exc)
            return "failed", None
        return ("found", body) if isinstance(body, dict) else ("missing", None)

    def _remember(self, key: str, answer, ttl: float, now: float) -> dict | None:
        state, body = answer
        if state == "found":
            self.store.cache_put(key, body, now, ttl)
            return body
        if state == "missing":
            self.store.cache_put(key, {}, now, self.MISS_TTL)  # {} means "known miss"
        return None

    def _cached_fetch(self, key: str, url: str, ttl: float, now: float, unwrap: str | None = "response") -> dict | None:
        cached = self.store.cache_get(key, now)
        if cached is not None:
            return cached or None
        return self._remember(key, self._ask(key, url, unwrap), ttl, now)

    def _cached_fetch_both(self, jobs: list[tuple], ttl: float, now: float) -> list:
        """Several (key, url, unwrap) lookups, asked at the same time when more than one isn't remembered:
        a new plane's alert shouldn't wait for two slow services one after the other."""
        out = [self.store.cache_get(key, now) for key, _, _ in jobs]
        todo = [i for i, cached in enumerate(out) if cached is None]
        if len(todo) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                answers = list(pool.map(lambda i: self._ask(*jobs[i]), todo))
        else:
            answers = [self._ask(*jobs[i]) for i in todo]
        for i, answer in zip(todo, answers):
            out[i] = self._remember(jobs[i][0], answer, ttl, now)
        return [body or None for body in out]

    def legs(self, callsign: str, now: float) -> list:
        """Every leg the route sources list for a callsign (both sources, each as it spells the callsign)."""
        from .routes import callsign_spellings, legs_from_adsbdb, legs_from_standing

        standing, adsbdb = [], []
        for cs in callsign_spellings(callsign):
            jobs = []
            if not standing and self.routes_url:
                jobs.append((f"standing:{cs}", f"{self.routes_url}/routes/{cs[:2]}/{cs}.json", None))
            if not adsbdb and self.base_url:
                jobs.append((f"route:{cs}", f"{self.base_url}/v0/callsign/{cs}", "response"))
            for (key, _, _), body in zip(jobs, self._cached_fetch_both(jobs, self.ROUTE_TTL, now)):
                if key.startswith("standing:"):
                    standing = legs_from_standing(body)
                else:
                    adsbdb = legs_from_adsbdb(body)
        return standing + adsbdb

    def route(self, aircraft: Aircraft, now: float, took_off_from=None):
        """The route this plane is flying (routes.Route). ``took_off_from``: the airport (airports.Airport or
        routes.Place) its recorded track left the ground at, when that's known."""
        from .routes import Place, Route, airline_of, choose, is_airline_callsign, place_from_airport

        if took_off_from is not None and not isinstance(took_off_from, Place):
            took_off_from = place_from_airport(took_off_from)
        legs = self.legs(aircraft.callsign, now) if aircraft.callsign else []
        found = choose(aircraft, legs, took_off_from, airliner=is_airline_callsign(aircraft.callsign))
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
