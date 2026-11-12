"""The live 3D/2D flight map, served from this computer.

Nothing is published: the page and its data come straight from planespotter
on your computer, to browsers on the same computer or the same Wi-Fi.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .adsb import (Aircraft, FeedWithReceiver, LiveFeed, Lookup, PositionLookup, ReceiverFeed,
                   SharedFeed)
from .airports import airports, route_from_path
from .budget import ApiBudget
from .collection import build_collection
from .config import Config
from .geo import bearing_deg, distance_miles, miles_to_nm, offset
from .heat import backfill as backfill_heat
from .heat import heat_json
from .fullpath import WAITING, FlightLookup, Point, as_path, simplify
from .logos import LogoCache
from .specials import short_label, special_reasons
from .store import Store

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Only these files are ever served. Nothing else on disk (like .env) is reachable.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class MapServerError(Exception):
    pass


def in_zone(cfg: Config, distance: float, alt_ft: int | None) -> bool:
    if distance > cfg.radius_miles:
        return False
    return cfg.max_altitude_ft is None or alt_ft is None or alt_ft <= cfg.max_altitude_ft


def plane_json(cfg: Config, ac: Aircraft) -> dict[str, Any]:
    dist = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
    return {
        "hex": ac.hex, "callsign": ac.callsign, "registration": ac.registration,
        "type": ac.type_code, "lat": ac.lat, "lon": ac.lon, "alt_ft": ac.alt_ft,
        "speed_kt": ac.speed_kt, "heading": ac.track, "squawk": ac.squawk,
        "seen_pos": ac.seen_pos, "distance_miles": round(dist, 2),
        "bearing": round(bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)),
        "in_zone": in_zone(cfg, dist, ac.alt_ft),
        "special": [{"text": text, "short": short_label(key, text), "emergency": emergency}
                    for key, text, emergency in special_reasons(ac, dist, cfg)],
    }


def pass_json(cfg: Config, row) -> dict[str, Any]:
    path = json.loads(row["path"] or "[]")
    if path:
        # Where it came closest, from the recorded path.
        closest = min(path, key=lambda p: distance_miles(cfg.home_lat, cfg.home_lon, p[0], p[1]))
        lat, lon = closest[0], closest[1]
    else:
        # Older records have no path: work it out from distance and direction.
        lat, lon = offset(cfg.home_lat, cfg.home_lon, row["closest_bearing"] or 0, row["closest_miles"])
    return {
        "id": row["id"], "hex": row["hex"], "callsign": row["callsign"],
        "registration": row["registration"], "type": row["type_code"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "closest_miles": round(row["closest_miles"], 2), "closest_alt_ft": row["closest_alt_ft"],
        "bearing": row["closest_bearing"], "speed_kt": row["speed_kt"], "heading": row["track"],
        "lat": round(lat, 5), "lon": round(lon, 5), "path": path,
        "airline": row["airline"], "origin": row["origin"], "origin_city": row["origin_city"],
        "destination": row["destination"], "destination_city": row["destination_city"],
        "aircraft": row["aircraft_desc"], "owner": row["owner"], "photo_url": row["photo_url"],
        "alerted": bool(row["alerted"]), "quiet": cfg.in_quiet_hours(row["first_seen"]),
        "route_checked": bool(row["route_checked"]),
    }


def make_budget(cfg: Config) -> ApiBudget:
    """adsb.lol's daily request limit, shared by everything in planespotter."""
    return ApiBudget(cfg.db_path, cfg.adsb_daily_limit, cfg.poll_seconds)


def shared_feed(cfg: Config, budget: ApiBudget | None = None):
    """The live feed used by both the watcher and the map (one request serves both).

    With your own receiver, that comes first, and adsb.lol is only the backup."""
    view = miles_to_nm(cfg.view_radius_miles) if cfg.map_enabled else 0
    adsb_lol = SharedFeed(LiveFeed(cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget), min_radius_nm=view)
    if cfg.receiver_url:
        return FeedWithReceiver(ReceiverFeed(cfg.receiver_url), adsb_lol)
    return adsb_lol


def home_elevation_ft(cfg: Config) -> int:
    """Roughly how high home is above sea level (the nearest airport's elevation), so we can
    say how high up in the sky to look. Planes report their height above sea level."""
    airport = airports().nearest(cfg.home_lat, cfg.home_lon, max_km=40)
    return airport.elevation_ft if airport else 0


class LiveCache:
    """The planes near home from the watcher's latest check. The map never asks adsb.lol itself."""

    def __init__(self, cfg: Config, feed, ttl: float = 2.0, clock: Callable[[], float] = time.time):
        self.cfg, self.feed, self.ttl, self.clock = cfg, feed, ttl, clock
        self.lock = threading.Lock()
        self.data: dict[str, Any] | None = None
        self.built_from = -1.0

    def get(self) -> dict[str, Any]:
        with self.lock:
            cfg = self.cfg
            planes, fetched_at = self.feed.latest()
            if self.data is None or fetched_at != self.built_from:
                aircraft = [plane_json(cfg, ac) for ac in planes or []]
                aircraft = [a for a in aircraft if a["distance_miles"] <= cfg.view_radius_miles]
                aircraft.sort(key=lambda a: a["distance_miles"])
                self.data = {"aircraft": aircraft, "updated": fetched_at, "waiting": planes is None}
                self.built_from = fetched_at
            now = self.clock()
            return {**self.data, "time": now, "quiet_now": cfg.in_quiet_hours(now)}


def start_feed_poller(cfg: Config, feed: SharedFeed, budget: ApiBudget) -> threading.Thread:
    """For `map` on its own (no watcher): keep the live planes fresh, on the budget's schedule."""
    def run():
        last = 0.0
        while True:
            pace = cfg.poll_seconds if getattr(feed, "uses_receiver", False) else budget.poll_interval()
            if time.time() - last >= pace:
                last = time.time()
                try:
                    feed.aircraft_near(cfg.home_lat, cfg.home_lon, miles_to_nm(cfg.view_radius_miles))
                except (OSError, ValueError) as exc:
                    log.info("couldn't fetch planes: %s", exc)
            time.sleep(2)

    thread = threading.Thread(target=run, name="map-feed", daemon=True)
    thread.start()
    return thread


class MapApp:
    """Everything the request handler needs; one per server."""

    def __init__(self, cfg: Config, feed=None, clock: Callable[[], float] = time.time, flight_lookup=None,
                 position_lookup=None, logo_cache=None, budget=None):
        self.cfg = cfg
        self.clock = clock
        self.budget = budget
        self.feed = feed or shared_feed(cfg, budget)
        self.live = LiveCache(cfg, self.feed, clock=clock)
        key = cfg.adsb_api_key
        self.flights = flight_lookup or FlightLookup(clock=clock, api_base=cfg.adsb_api, api_key=key, budget=budget)
        self.positions = position_lookup or PositionLookup(cfg.adsb_api, clock=clock, api_key=key, budget=budget)
        self.logos = logo_cache or LogoCache(Path(cfg.db_path).parent / "logo-cache")
        self._local = threading.local()
        self._whole_cache: dict[float, tuple[float, list]] = {}

    def live_json(self) -> dict[str, Any]:
        """Live planes, plus how fresh they are and how the day's request budget is going."""
        if self.budget:
            self.budget.mark_viewing()  # someone's watching: check more often for a while
        data = self.live.get()
        paused = self.feed.paused_for() if hasattr(self.feed, "paused_for") else 0
        receiver = getattr(self.feed, "uses_receiver", False)
        budget = self.budget.status() if self.budget else None
        if receiver and budget:
            budget["interval_s"] = self.cfg.poll_seconds
        return {**data, "paused_s": round(paused), "budget": budget,
                "source": "receiver" if receiver else "adsb.lol"}

    def store(self) -> Store:
        """SQLite connections can't be shared between threads, so each gets its own."""
        if not hasattr(self._local, "store"):
            self._local.store = Store(self.cfg.db_path)
        return self._local.store

    def config_json(self) -> dict[str, Any]:
        cfg = self.cfg
        return {
            "home": {"lat": cfg.home_lat, "lon": cfg.home_lon, "name": cfg.home_name},
            "radius_miles": cfg.radius_miles,
            "view_radius_miles": cfg.view_radius_miles,
            "max_altitude_ft": cfg.max_altitude_ft,
            "quiet_hours": [t.strftime("%H:%M") for t in cfg.quiet_hours] if cfg.quiet_hours else None,
            "home_elevation_ft": home_elevation_ft(cfg),
        }

    def flights_json(self, days: float) -> dict[str, Any]:
        since = 0.0 if days <= 0 else self.clock() - days * 86400
        rows = self.store().recent_passes(since, limit=3000)
        return {"flights": [pass_json(self.cfg, r) for r in rows]}

    def flight_json(self, pass_id: int) -> dict[str, Any]:
        """One spotted flight, with its whole path when that's been looked up."""
        row = self.store().get_pass(pass_id)
        if row is None:
            raise ValueError("no such flight")
        whole = json.loads(row["full_path"] or "[]")
        return {**pass_json(self.cfg, row), "whole_path": whole,
                "whole_state": {0: "waiting", 1: "done", 2: "unavailable"}[row["full_state"]]}

    def whole_json(self, days: float) -> dict[str, Any]:
        """The whole flight of every spotted plane: takeoff to landing, or to where it is now."""
        now = self.clock()
        hit = self._whole_cache.get(days)
        if hit and now - hit[0] < 20:
            flights = hit[1]
        else:
            since = 0.0 if days <= 0 else now - days * 86400
            rows = self.store().db.execute(
                "SELECT id, hex, full_state, full_path FROM passes WHERE first_seen >= ? AND full_path != '[]' "
                "ORDER BY first_seen DESC LIMIT 1500", (since,)).fetchall()
            flights, newest_per_plane = [], set()
            for r in rows:
                path = json.loads(r["full_path"])
                if len(path) < 2:
                    continue
                last = path[-1]
                # Still in the air: not finished, last heard recently, not on the ground,
                # and the newest pass for that aircraft (older ones are earlier flights).
                flying = (r["full_state"] == WAITING and r["hex"] not in newest_per_plane
                          and now - last[3] < 6 * 3600 and last[2] != 0)
                newest_per_plane.add(r["hex"])
                points = [Point(p[3], p[0], p[1], p[2], False, False) for p in path]
                overview = simplify(points, tolerance_m=400, max_points=120)  # plenty for drawing many at once
                flights.append({"id": r["id"], "hex": r["hex"], "flying": flying,
                                "path": [[round(q.lon, 4), round(q.lat, 4), q.alt] for q in overview]})
            self._whole_cache = {days: (now, flights)}
        flying_hexes = [f["hex"] for f in flights if f["flying"]][:120]
        positions = self.positions.get(flying_hexes)
        landed = self.positions.on_ground(flying_hexes)
        out = []
        for f in flights:
            if f["flying"] and f["hex"] in landed:
                # adsb.lol sees it on the ground: it has landed. Fetch its final path soon.
                f = {**f, "flying": False, "landed": True}
                self.store().db.execute(
                    "UPDATE passes SET full_next = ? WHERE id = ? AND full_state = 0 AND full_next > ?",
                    (now, f["id"], now))
                self.store().db.commit()
            ac = positions.get(f["hex"]) if f["flying"] else None
            if ac:
                f = {**f, "now": {"lat": ac.lat, "lon": ac.lon, "alt_ft": ac.alt_ft, "heading": ac.track,
                                  "speed_kt": ac.speed_kt, "seen_pos": ac.seen_pos}}
            out.append(f)
        return {"flights": out}

    def replay_json(self, day: str) -> dict[str, Any]:
        """Every spotted flight of one local day, with timed paths, for the replay."""
        from datetime import date as _date, timedelta
        d = _date.fromisoformat(day)
        start, end = self.cfg.day_start(d), self.cfg.day_start(d + timedelta(days=1))
        flights = []
        for r in self.store().passes_between(start, end):
            path = json.loads(r["full_path"] or "[]")
            if len(path) < 2:
                path = json.loads(r["path"] or "[]")  # no whole flight yet: the part near home
            if len(path) < 2:
                continue
            flights.append({
                "id": r["id"], "hex": r["hex"], "callsign": r["callsign"] or r["registration"] or r["hex"].upper(),
                "airline": r["airline"], "origin": r["origin"], "destination": r["destination"],
                "first_seen": r["first_seen"],
                # [lon, lat, alt_ft, seconds after the start of the day]
                "path": [[p[1], p[0], p[2], round(p[3] - start)] for p in path],
            })
        return {"day": day, "start": start, "end": end, "flights": flights}

    def trace_json(self, hex_code: str) -> dict[str, Any]:
        """The flight a live plane is on, from takeoff until now."""
        hex_code = "".join(c for c in hex_code.lower() if c in "0123456789abcdef")[:6]
        if len(hex_code) != 6:
            return {"path": []}
        try:
            points = self.flights.current_flight(hex_code)
        except (OSError, ValueError) as exc:
            log.info("couldn't get the flight so far for %s: %s", hex_code, exc)
            points = []
        if not points:
            return {"path": []}
        origin, _ = route_from_path(as_path(points[:1]), landed=False)
        return {"path": as_path(simplify(points)),
                "origin": {"code": origin.code, "city": origin.city, "name": origin.name} if origin else None}

    def info_json(self, query: dict[str, str]) -> dict[str, Any]:
        try:
            lat, lon = float(query.get("lat", "")), float(query.get("lon", ""))
        except ValueError:
            lat, lon = self.cfg.home_lat, self.cfg.home_lon
        ac = Aircraft(hex=query.get("hex", "").lower()[:10], callsign=query.get("callsign", "").upper()[:10],
                      registration="", type_code="", lat=lat, lon=lon, alt_ft=None, speed_kt=None, track=None)
        info = Lookup(self.store(), self.cfg.adsbdb_api).info(ac, self.clock())
        return asdict(info)


def make_handler(app: MapApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "planespotter"

        def log_message(self, fmt, *args):  # keep the terminal tidy
            log.debug("map: " + fmt, *args)

        def send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data: Any, status: int = 200) -> None:
            self.send(status, json.dumps(data, separators=(",", ":")).encode(), "application/json")

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                if url.path in STATIC_FILES:
                    name, ctype = STATIC_FILES[url.path]
                    self.send(200, (WEB_DIR / name).read_bytes(), ctype)
                elif url.path == "/api/config":
                    self.send_json(app.config_json())
                elif url.path == "/api/live":
                    self.send_json(app.live_json())
                elif url.path == "/api/flights":
                    self.send_json(app.flights_json(float(query.get("days", "7") or 7)))
                elif url.path == "/api/heat":
                    days = int(float(query.get("days", "7") or 7))
                    self.send_json(heat_json(app.store(), app.cfg, app.clock(), days))
                elif url.path.startswith("/logo/") and url.path.endswith(".png"):
                    png = app.logos.get(url.path[len("/logo/"):-len(".png")])
                    if png:
                        self.send(200, png, "image/png", cache="max-age=604800")
                    else:
                        self.send(404, b"", "image/png", cache="max-age=86400")
                elif url.path == "/api/replay":
                    self.send_json(app.replay_json(query.get("day", "")))
                elif url.path == "/api/collection":
                    self.send_json(build_collection(app.store(), app.cfg))
                elif url.path == "/api/whole":
                    self.send_json(app.whole_json(float(query.get("days", "1") or 1)))
                elif url.path == "/api/flight":
                    self.send_json(app.flight_json(int(query.get("id", "0") or 0)))
                elif url.path == "/api/trace":
                    self.send_json(app.trace_json(query.get("hex", "")))
                elif url.path == "/api/info":
                    self.send_json(app.info_json(query))
                else:
                    self.send_json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser went away mid-answer; nothing to do
            except (OSError, ValueError) as exc:
                log.info("map request %s failed: %s", url.path, exc)
                try:
                    self.send_json({"error": str(exc)}, 502)
                except OSError:
                    pass

    return Handler


def lan_address() -> str | None:
    """This computer's address on the home network, for opening the map on a phone."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # nothing is sent; this just picks the right interface
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def start_map_server(cfg: Config, feed=None, port: int | None = None, flight_lookup=None,
                     position_lookup=None, logo_cache=None, budget=None):
    """Start serving the map in a background thread. Returns (server, list of URLs)."""
    host = "0.0.0.0" if cfg.map_lan else "127.0.0.1"
    store = Store(cfg.db_path)
    backfill_heat(store, cfg)  # first run only: build the heat map from existing history
    store.close()
    port = cfg.map_port if port is None else port
    try:
        app = MapApp(cfg, feed, flight_lookup=flight_lookup, position_lookup=position_lookup,
                     logo_cache=logo_cache, budget=budget)
        server = ThreadingHTTPServer((host, port), make_handler(app))
    except OSError as exc:
        raise MapServerError(
            f"Couldn't start the map on port {port} ({exc.strerror or exc}). "
            f"It may already be running: try http://localhost:{port}"
        ) from None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="map-server", daemon=True).start()
    port = server.server_address[1]
    urls = [f"http://localhost:{port}"]
    if cfg.map_lan and (ip := lan_address()):
        urls.append(f"http://{ip}:{port}")
    return server, urls
