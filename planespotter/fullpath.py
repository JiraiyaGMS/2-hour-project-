"""Whole flights for the planes that crossed your zone.

planespotter only sees planes near home. To draw the rest of a flight (say
Portland → Los Angeles), it asks adsb.lol's API for that aircraft's recorded
positions for the day, cuts out the one flight that went over home, and keeps
a simplified copy: first while it's overhead (where it came from), then again
later until it has landed. This runs in the background, one request at a
time, within the daily request budget, so it never slows down the alerts.
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
from datetime import date, datetime, timezone
from typing import Callable, NamedTuple

from .adsb import USER_AGENT, key_header
from .airports import route_from_path
from .heat import route_cells

log = logging.getLogger(__name__)

TRACE_HOURS = 20           # the API's trace covers about the last day; older flights can't be looked up
LEG_GAP = 30 * 60          # no positions for this long = a separate flight
SETTLE = 30                # first look 30 s after a plane appears over home: where it came from
RETRIES = [20 * 60, 40 * 60, 2 * 3600, 3 * 3600]  # then look again until it has landed
MAX_TRIES = len(RETRIES) + 1
MAX_POINTS = 250
PAUSE = 10                 # seconds between lookups when there's a backlog

# Pass states for the full_state column.
WAITING, DONE, UNAVAILABLE = 0, 1, 2


def retry_delay(tries: int) -> float:
    """How long to wait before checking a flight again: soon at first, then less often."""
    return RETRIES[min(max(tries, 1), len(RETRIES)) - 1]


class OverBudget(Exception):
    """Today's share of adsb.lol requests for whole flights is used up; try later."""


class TraceNotFound(Exception):
    """adsb.lol has no recorded positions for this aircraft on that day."""


class Point(NamedTuple):
    t: float
    lat: float
    lon: float
    alt: int | None
    ground: bool
    new_leg: bool


def get_trace_json(url: str, timeout: float = 25, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
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


def trace_url(hex_code: str, api_base: str = "https://api.adsb.lol") -> str:
    """adsb.lol's API for an aircraft's recorded positions (the "full trace" for the day)."""
    return f"{api_base.rstrip('/')}/1/trace/full/{hex_code}"


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

    def __init__(self, fetch: Callable[[str], dict] | None = None, clock: Callable[[], float] = time.time,
                 api_base: str = "https://api.adsb.lol", api_key: str = "", budget=None):
        self.fetch = fetch or (lambda url: get_trace_json(url, headers=key_header(api_key)))
        self.clock = clock
        self.api_base = api_base
        self.budget = budget
        self.cache: dict[str, tuple[float, list[Point] | None]] = {}

    def points(self, hex_code: str, kind: str = "trace") -> list[Point] | None:
        """The aircraft's recorded positions for about the last day (one API request, cached 2 min)."""
        now = self.clock()
        url = trace_url(hex_code, self.api_base)
        hit = self.cache.get(url)
        if hit and now - hit[0] < 120:
            return hit[1]
        data = self.fetch_raw(hex_code, kind)
        pts = parse_trace(data) if data else None
        if len(self.cache) > 40:
            self.cache.clear()
        self.cache[url] = (now, pts)
        return pts

    def fetch_raw(self, hex_code: str, kind: str = "trace") -> dict | None:
        """The aircraft's trace as adsb.lol sends it (one request), or None if it has none."""
        if self.budget is not None:
            if not self.budget.allow(kind):
                raise OverBudget(kind)
            self.budget.record("catchup" if kind == "catchup" else "trace")
        try:
            return self.fetch(trace_url(hex_code, self.api_base))
        except TraceNotFound:
            return None

    def flight(self, hex_code: str, t_from: float, t_to: float) -> tuple[list[Point] | None, bool]:
        """(points of the flight, whether it has finished), or (None, False) if not found."""
        now = self.clock()
        legs = split_legs(self.points(hex_code) or [])
        i = find_leg(legs, t_from, t_to)
        if i is None:
            return None, False
        leg = legs[i]
        finished = (
            leg[-1].ground                    # it landed
            or i < len(legs) - 1              # it has flown again since
            or now - leg[-1].t > 2 * 3600     # nothing heard for hours (out of coverage)
        )
        return leg, finished

    def current_flight(self, hex_code: str) -> list[Point]:
        """The flight a plane in the air right now is on, so far (when someone clicks it on the map)."""
        try:
            legs = split_legs(self.points(hex_code, kind="click") or [])
        except OverBudget:
            return []
        if legs and self.clock() - legs[-1][-1].t < 15 * 60:
            return legs[-1]
        return []


def save_flight(store, cfg, pass_id: int, first_seen: float, points: list[Point], finished: bool,
                tries: int, now: float) -> None:
    """Store a flight's path (so far), and its real departure/arrival airports."""
    # Where it started, plus its last five minutes (enough to tell if it's coming in to land).
    tail = [p for p in points[1:] if points[-1].t - p.t <= 300]
    origin, destination = route_from_path(as_path(points[:1] + tail), landed=finished)
    store.set_route(pass_id, origin, destination)
    path = as_path(simplify(points))
    if finished or tries >= MAX_TRIES:
        day = cfg.local(first_seen).date().isoformat()
        store.update_full_path(pass_id, path=path, state=DONE, tries=tries,
                               route_heat=(day, route_cells(cfg, path)))
    else:
        # Still flying: keep what we have so far, and look again later.
        store.update_full_path(pass_id, path=path, tries=tries, next_try=now + retry_delay(tries))


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
        budget = self.lookup.budget
        if budget is not None and not budget.allow("trace"):
            return False  # today's share is used up; flights wait their turn
        row = self.store.claim_full_path_job(now, settle=SETTLE, hold=15 * 60)
        if row is None:
            return False
        if now - row["last_seen"] > TRACE_HOURS * 3600:
            # adsb.lol's API only has about the last day, so older flights can't be looked up.
            self.store.update_full_path(row["id"], state=UNAVAILABLE)
            return True
        try:
            points, finished = self.lookup.flight(row["hex"], row["first_seen"], row["last_seen"])
        except OverBudget:
            self.store.update_full_path(row["id"], next_try=now + 30 * 60)
            return False
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
            state = UNAVAILABLE if tries >= 2 else WAITING
            self.store.update_full_path(row["id"], state=state, tries=tries, next_try=now + retry_delay(tries))
            return True
        save_flight(self.store, self.cfg, row["id"], row["first_seen"], points, finished, tries, now)
        return True

    def run_forever(self) -> None:
        while True:
            try:
                did_work = self.step()
            except Exception:  # never let a bad record stop the worker
                log.exception("whole-flight lookup failed")
                did_work = False
            time.sleep(PAUSE if did_work else 30)


def start_full_path_worker(cfg, budget=None) -> threading.Thread:
    """Run the whole-flight lookups in a background thread with its own database connection."""
    from .store import Store

    def run():
        lookup = FlightLookup(api_base=cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget)
        FullPathFetcher(cfg, Store(cfg.db_path), lookup).run_forever()

    thread = threading.Thread(target=run, name="whole-flights", daemon=True)
    thread.start()
    return thread
