"""Whole flights for the planes that crossed your zone.

planespotter only sees planes near home. To draw the rest of a flight (say
Portland → Los Angeles), it asks adsb.lol for that aircraft's recorded
positions once the flight is over, cuts out the one flight that went over
home, and keeps a simplified copy. This runs in the background, one request
at a time, so it never slows down the alerts.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable, NamedTuple

from .adsb import USER_AGENT
from .heat import route_cells

log = logging.getLogger(__name__)

LIVE_TRACE_URL = "https://globe.adsb.lol/data/traces/{xx}/trace_full_{hex}.json"
HISTORY_TRACE_URL = ("https://globe.adsb.lol/globe_history/{d.year:04d}/{d.month:02d}/{d.day:02d}"
                     "/traces/{xx}/trace_full_{hex}.json")
LIVE_HOURS = 20            # adsb.lol's "full" trace holds roughly the last 24 hours
LEG_GAP = 30 * 60          # no positions for this long = a separate flight
SETTLE = 10 * 60           # wait this long after a plane leaves before looking it up
RETRY = 30 * 60            # how often to check a flight that's still in the air
MAX_TRIES = 40             # ~20 hours of checking, then keep what we have
MAX_POINTS = 250
PAUSE = 10                 # seconds between lookups when there's a backlog

# Pass states for the full_state column.
WAITING, DONE, UNAVAILABLE = 0, 1, 2


class TraceNotFound(Exception):
    """adsb.lol has no recorded positions for this aircraft on that day."""


class Point(NamedTuple):
    t: float
    lat: float
    lon: float
    alt: int | None
    ground: bool
    new_leg: bool


def get_trace_json(url: str, timeout: float = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise TraceNotFound(url) from None
        raise
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    try:
        data = json.loads(body)
    except ValueError:
        raise TraceNotFound(url) from None  # e.g. a web page instead of data
    if not isinstance(data, dict) or not isinstance(data.get("trace"), list):
        raise TraceNotFound(url)
    return data


def live_url(hex_code: str) -> str:
    return LIVE_TRACE_URL.format(xx=hex_code[-2:], hex=hex_code)


def history_url(hex_code: str, day: date) -> str:
    return HISTORY_TRACE_URL.format(d=day, xx=hex_code[-2:], hex=hex_code)


def utc_day(ts: float) -> date:
    return datetime.fromtimestamp(ts, timezone.utc).date()


def parse_trace(data: dict) -> list[Point]:
    """adsb.lol (readsb) trace format: [seconds after "timestamp", lat, lon, alt, gs, track, flags, ...]."""
    base = float(data.get("timestamp") or 0)
    points = []
    for p in data["trace"]:
        if len(p) < 4 or not isinstance(p[1], (int, float)) or not isinstance(p[2], (int, float)):
            continue
        ground = p[3] == "ground"
        alt = 0 if ground else (int(p[3]) if isinstance(p[3], (int, float)) else None)
        flags = p[6] if len(p) > 6 and isinstance(p[6], int) else 0
        points.append(Point(base + float(p[0]), float(p[1]), float(p[2]), alt, ground, bool(flags & 2)))
    return points


def split_legs(points: list[Point]) -> list[list[Point]]:
    """One list of points per flight."""
    legs: list[list[Point]] = []
    current: list[Point] = []
    for p in sorted(points, key=lambda p: p.t):
        if current and (p.new_leg or p.t - current[-1].t > LEG_GAP):
            legs.append(current)
            current = []
        current.append(p)
    if current:
        legs.append(current)
    return [leg for leg in legs if not all(p.ground for p in leg)]


def find_leg(legs: list[list[Point]], t_from: float, t_to: float) -> int | None:
    """Index of the flight that was in the air while we saw it near home."""
    best, best_overlap = None, -600.0
    for i, leg in enumerate(legs):
        overlap = min(leg[-1].t, t_to + 300) - max(leg[0].t, t_from - 300)
        if overlap > best_overlap:
            best, best_overlap = i, overlap
    return best


def merge(*point_lists: list[Point]) -> list[Point]:
    seen, out = set(), []
    for p in sorted((p for pts in point_lists for p in pts), key=lambda p: p.t):
        if round(p.t) not in seen:
            seen.add(round(p.t))
            out.append(p)
    return out


def simplify(points: list[Point], tolerance_m: float = 150, max_points: int = MAX_POINTS) -> list[Point]:
    """Fewer points, same shape (Ramer–Douglas–Peucker), so whole flights stay small."""
    if len(points) <= 2:
        return points
    lat0 = math.radians(sum(p.lat for p in points) / len(points))
    xy = [(math.radians(p.lon) * math.cos(lat0) * 6371000, math.radians(p.lat) * 6371000) for p in points]

    def rdp(tol: float) -> list[int]:
        keep = {0, len(points) - 1}
        stack = [(0, len(points) - 1)]
        while stack:
            a, b = stack.pop()
            (x1, y1), (x2, y2) = xy[a], xy[b]
            dx, dy = x2 - x1, y2 - y1
            norm = math.hypot(dx, dy) or 1e-9
            far, far_d = None, tol
            for i in range(a + 1, b):
                x, y = xy[i]
                d = abs(dy * (x - x1) - dx * (y - y1)) / norm if norm > 1e-6 else math.hypot(x - x1, y - y1)
                if d > far_d:
                    far, far_d = i, d
            if far is not None:
                keep.add(far)
                stack.extend([(a, far), (far, b)])
        return sorted(keep)

    tol = tolerance_m
    keep = rdp(tol)
    while len(keep) > max_points:
        tol *= 2
        keep = rdp(tol)
    return [points[i] for i in keep]


def as_path(points: list[Point]) -> list[list]:
    """The [lat, lon, alt_ft, time] format used for paths everywhere else."""
    return [[round(p.lat, 4), round(p.lon, 4), p.alt, round(p.t)] for p in points]


class FlightLookup:
    """Finds the whole flight that an aircraft was on at a given time."""

    def __init__(self, fetch: Callable[[str], dict] = get_trace_json, clock: Callable[[], float] = time.time):
        self.fetch = fetch
        self.clock = clock
        self.cache: dict[str, tuple[float, list[Point] | None]] = {}

    def points(self, url: str) -> list[Point] | None:
        now = self.clock()
        hit = self.cache.get(url)
        if hit and now - hit[0] < 120:
            return hit[1]
        try:
            pts = parse_trace(self.fetch(url))
        except TraceNotFound:
            pts = None
        if len(self.cache) > 40:
            self.cache.clear()
        self.cache[url] = (now, pts)
        return pts

    def day_points(self, hex_code: str, day: date) -> list[Point]:
        now = self.clock()
        pts: list[Point] = []
        if day >= utc_day(now - LIVE_HOURS * 3600):
            pts = self.points(live_url(hex_code)) or []
        if day < utc_day(now):
            pts = merge(pts, self.points(history_url(hex_code, day)) or [])
        return pts

    def flight(self, hex_code: str, t_from: float, t_to: float) -> tuple[list[Point] | None, bool]:
        """(points of the flight, whether it has finished), or (None, False) if not found."""
        now = self.clock()
        if now - t_to < LIVE_HOURS * 3600:
            pts = self.points(live_url(hex_code)) or []
        else:
            pts = self.points(history_url(hex_code, utc_day(t_from))) or []
        legs = split_legs(pts)
        i = find_leg(legs, t_from, t_to)
        if i is None:
            return None, False

        # Flights that cross midnight UTC are split over two days of records: join them up.
        leg = legs[i]
        if i == 0 and not leg[0].ground:
            earlier = self.day_points(hex_code, utc_day(leg[0].t - 3600))
            if earlier:
                pts = merge(pts, earlier)
        if i == len(legs) - 1 and not leg[-1].ground and now - leg[-1].t > 3600:
            later_day = utc_day(leg[-1].t) + timedelta(days=1)
            if later_day <= utc_day(now):
                pts = merge(pts, self.day_points(hex_code, later_day))
        legs = split_legs(pts)
        i = find_leg(legs, t_from, t_to)
        leg = legs[i]

        finished = (
            leg[-1].ground                    # it landed
            or i < len(legs) - 1              # it has flown again since
            or now - leg[-1].t > 2 * 3600     # nothing heard for hours (out of coverage)
        )
        return leg, finished

    def current_flight(self, hex_code: str) -> list[Point]:
        """The flight a plane in the air right now is on, so far."""
        legs = split_legs(self.points(live_url(hex_code)) or [])
        if legs and self.clock() - legs[-1][-1].t < 15 * 60:
            return legs[-1]
        return []


class FullPathFetcher:
    """Background worker: looks up whole flights for passes, one at a time."""

    def __init__(self, cfg, store, lookup: FlightLookup | None = None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.lookup = lookup or FlightLookup(clock=clock)
        self.clock = clock
        self.blocked_until = 0.0
        self.backoff = 0.0

    def step(self) -> bool:
        """Do one lookup if one is due. Returns True if it did some work."""
        now = self.clock()
        if now < self.blocked_until:
            return False
        row = self.store.claim_full_path_job(now, settle=SETTLE, hold=15 * 60)
        if row is None:
            return False
        try:
            points, finished = self.lookup.flight(row["hex"], row["first_seen"], row["last_seen"])
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # The service is busy or unreachable: try again later, more slowly each time.
            self.backoff = min(3600.0, max(120.0, self.backoff * 2))
            retry = getattr(exc, "headers", None) and exc.headers.get("Retry-After")
            try:
                wait = max(self.backoff, float(retry or 0))
            except ValueError:
                wait = self.backoff
            self.blocked_until = now + wait
            self.store.update_full_path(row["id"], next_try=now + wait)
            log.info("couldn't look up whole flights (%s); trying again in %d min", exc, wait // 60)
            return True
        self.backoff = 0.0

        tries = row["full_tries"] + 1
        if points is None:
            state = UNAVAILABLE if tries >= 3 else WAITING
            self.store.update_full_path(row["id"], state=state, tries=tries, next_try=now + RETRY)
            return True
        path = as_path(simplify(points))
        if finished or tries >= MAX_TRIES:
            day = self.cfg.local(row["first_seen"]).date().isoformat()
            self.store.update_full_path(row["id"], path=path, state=DONE, tries=tries,
                                        route_heat=(day, route_cells(self.cfg, path)))
        else:
            # Still flying: keep what we have so far, and look again later.
            self.store.update_full_path(row["id"], path=path, tries=tries, next_try=now + RETRY)
        return True

    def run_forever(self) -> None:
        while True:
            try:
                did_work = self.step()
            except Exception:  # never let a bad record stop the worker
                log.exception("whole-flight lookup failed")
                did_work = False
            time.sleep(PAUSE if did_work else 30)


def start_full_path_worker(cfg) -> threading.Thread:
    """Run the whole-flight lookups in a background thread with its own database connection."""
    from .store import Store

    def run():
        FullPathFetcher(cfg, Store(cfg.db_path)).run_forever()

    thread = threading.Thread(target=run, name="whole-flights", daemon=True)
    thread.start()
    return thread
