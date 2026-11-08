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

from .adsb import Aircraft, LiveFeed, Lookup, SharedFeed
from .config import Config
from .geo import bearing_deg, distance_miles, miles_to_nm, offset
from .heat import backfill as backfill_heat
from .heat import heat_json
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
    }


def shared_feed(cfg: Config) -> SharedFeed:
    """The live feed used by both the watcher and the map (one request serves both)."""
    view = miles_to_nm(cfg.view_radius_miles) if cfg.map_enabled else 0
    return SharedFeed(LiveFeed(cfg.adsb_api), min_radius_nm=view)


class LiveCache:
    """Fetches live planes at most every few seconds, however many browsers are open."""

    def __init__(self, cfg: Config, feed: LiveFeed, ttl: float = 4.0, clock: Callable[[], float] = time.time):
        self.cfg, self.feed, self.ttl, self.clock = cfg, feed, ttl, clock
        self.lock = threading.Lock()
        self.data: dict[str, Any] | None = None
        self.fetched_at = 0.0

    def get(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if self.data is None or now - self.fetched_at >= self.ttl:
                cfg = self.cfg
                planes = self.feed.aircraft_near(cfg.home_lat, cfg.home_lon, miles_to_nm(cfg.view_radius_miles))
                aircraft = [plane_json(cfg, ac) for ac in planes]
                aircraft = [a for a in aircraft if a["distance_miles"] <= cfg.view_radius_miles]
                aircraft.sort(key=lambda a: a["distance_miles"])
                self.data = {"time": now, "aircraft": aircraft, "quiet_now": cfg.in_quiet_hours(now)}
                self.fetched_at = now
            return self.data


class MapApp:
    """Everything the request handler needs; one per server."""

    def __init__(self, cfg: Config, feed=None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.clock = clock
        self.live = LiveCache(cfg, feed or shared_feed(cfg), ttl=2.0, clock=clock)
        self._local = threading.local()

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
        }

    def flights_json(self, days: float) -> dict[str, Any]:
        since = 0.0 if days <= 0 else self.clock() - days * 86400
        rows = self.store().recent_passes(since, limit=3000)
        return {"flights": [pass_json(self.cfg, r) for r in rows]}

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

        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
                    self.send_json(app.live.get())
                elif url.path == "/api/flights":
                    self.send_json(app.flights_json(float(query.get("days", "7") or 7)))
                elif url.path == "/api/heat":
                    days = int(float(query.get("days", "7") or 7))
                    self.send_json(heat_json(app.store(), app.cfg, app.clock(), days))
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


def start_map_server(cfg: Config, feed=None, port: int | None = None):
    """Start serving the map in a background thread. Returns (server, list of URLs)."""
    host = "0.0.0.0" if cfg.map_lan else "127.0.0.1"
    store = Store(cfg.db_path)
    backfill_heat(store, cfg)  # first run only: build the heat map from existing history
    store.close()
    port = cfg.map_port if port is None else port
    try:
        server = ThreadingHTTPServer((host, port), make_handler(MapApp(cfg, feed)))
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
