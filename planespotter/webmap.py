"""The live 3D/2D flight map, served from this computer.

Nothing is published: the page and its data come straight from planespotter
on your computer, to browsers on the same computer or the same Wi-Fi.
"""

from __future__ import annotations

import base64
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
from .geo import bearing_deg, compass, distance_miles, miles_to_nm, offset
from .heat import backfill as backfill_heat
from .heat import heat_json
from .fullpath import WAITING, FlightLookup, Point, as_path, simplify
from .logos import LogoCache
from .flow import flow_report
from .follow import follow, following, unfollow
from .games import bingo, old_friends, profile, quiz_answer, quiz_question, quiz_score, wrapped
from .noise import build_noise, loudness, noise_csv
from .predict import crossings, describe, glide
from .schedule import delays_for, today_board
from .space import SpaceWatch, pass_text
from .noise import settle as settle_noise
from .records import build_records, busiest, plane_of_the_day, streak
from .sky import SkyWatch, sky_note
from .specials import short_label, special_reasons
from .store import Store
from .tls import TLSServer, ensure_certificate

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Only these files are ever served. Nothing else on disk (like .env) is reachable.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sky": ("sky.html", "text/html; charset=utf-8"),
    "/sky.html": ("sky.html", "text/html; charset=utf-8"),
    "/sky.js": ("sky.js", "text/javascript; charset=utf-8"),
    "/kiosk": ("index.html", "text/html; charset=utf-8"),
}
MAX_UPLOAD = 16 * 1024 * 1024  # bytes: a photo (sent as text) or a batch of noise levels
MAX_PHOTO = 8 * 1024 * 1024
MAX_THUMB = 600 * 1024


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
        "noise_db": loudness(row),  # how much louder than the background it was, if the noise meter was on
        "offline": bool(row["offline"]),  # found afterwards by the offline log
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
                 position_lookup=None, logo_cache=None, budget=None, sky=None):
        self.cfg = cfg
        self.sky = sky if sky is not None else (SkyWatch(cfg) if cfg.weather else None)
        self.photo_dir = Path(cfg.db_path).parent / "photos"
        self.secure_url = ""  # the https address for phones, once it's running
        self.space = SpaceWatch(cfg, self.store) if cfg.space_alerts else None
        self.places: list[dict] = []  # the maps of your other places, if any: [{name, url}]
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
            "secure_url": self.secure_url,
            "rush_count": cfg.rush_count,
            "places": self.places,
        }

    def flights_json(self, days: float) -> dict[str, Any]:
        since = 0.0 if days <= 0 else self.clock() - days * 86400
        rows = self.store().recent_passes(since, limit=3000)
        # How late or early your regular flights were, for the last couple of days.
        recent = [r for r in rows if r["first_seen"] >= self.clock() - 2 * 86400]
        delays = delays_for(self.store(), self.cfg, recent)
        return {"flights": [{**pass_json(self.cfg, r), "delay": delays.get(r["id"])} for r in rows]}

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

    def sky_json(self) -> dict[str, Any]:
        """Clouds at the nearest weather station, and where the sun is."""
        if not self.sky:
            from .sky import daylight, sun_position
            alt, az = sun_position(self.cfg.home_lat, self.cfg.home_lon, self.clock())
            return {"weather": None, "sun": {"altitude": round(alt, 1), "azimuth": round(az, 1),
                                             "daylight": daylight(alt)}}
        return self.sky.report()

    def collection_json(self) -> dict[str, Any]:
        """Your collection, records, plane of the day and "I saw it" streak."""
        store, cfg, now = self.store(), self.cfg, self.clock()
        data = build_collection(store, cfg)
        rows = store.db.execute("SELECT * FROM passes").fetchall()
        today = cfg.local(now).date()
        todays = store.passes_between(cfg.day_start(today), now + 1)
        star = plane_of_the_day(todays, store, cfg)
        if star is None:  # nothing yet today: yesterday's
            from datetime import timedelta
            star = plane_of_the_day(store.passes_between(cfg.day_start(today - timedelta(days=1)),
                                                         cfg.day_start(today)), store, cfg)
            if star:
                star["yesterday"] = True
        return {**data, "records": build_records(rows), "busiest": busiest(rows, cfg), "star": star,
                "streak": streak(store.eye_times(), cfg, today), "photos": len(store.photos()),
                "bingo": bingo(store, cfg, now, home_elevation_ft(cfg)), "quiz": quiz_score(store),
                "friends": old_friends(store), "year": str(today.year), "month": today.strftime("%Y-%m")}

    def yours_json(self) -> dict[str, Any]:
        """Your photos and the planes you've seen with your own eyes (for marking them on the map)."""
        store = self.store()
        photos = [{"id": r["id"], "pass_id": r["pass_id"], "hex": r["hex"], "callsign": r["callsign"],
                   "added_at": r["added_at"], "width": r["width"], "height": r["height"],
                   "registration": r["registration"] or "", "airline": r["airline"] or "",
                   "type": r["type_code"] or "", "origin": r["origin"] or "", "destination": r["destination"] or "",
                   "first_seen": r["first_seen"]} for r in store.photos()]
        since = self.clock() - 30 * 86400
        eyes = [[r["hex"], r["at"]] for r in store.db.execute("SELECT hex, at FROM eyes WHERE at >= ?", (since,))]
        return {"photos": photos, "eyes": eyes, "following": following(store),
                "streak": streak(store.eye_times(), self.cfg, self.cfg.local(self.clock()).date())}

    def saw(self, body: dict) -> dict[str, Any]:
        """You tapped "I saw it" (or took it back)."""
        hex_code = clean_hex(body.get("hex", ""))
        if not hex_code:
            raise ValueError("which plane?")
        store, now = self.store(), self.clock()
        at = float(body.get("at") or now)
        if not now - 7 * 86400 <= at <= now + 60:
            raise ValueError("that's too long ago")
        if body.get("undo"):
            store.unsaw(hex_code, at)
        else:
            store.saw(hex_code, str(body.get("callsign", ""))[:10].upper(), at)
        return self.yours_json()

    def add_photo(self, body: dict) -> dict[str, Any]:
        """A photo you took, already shrunk to a sensible size by the browser (JPEG, as text)."""
        full, thumb = jpeg_from_data_url(body.get("full", ""), MAX_PHOTO), jpeg_from_data_url(body.get("thumb", ""), MAX_THUMB)
        store, now = self.store(), self.clock()
        pass_id = body.get("pass_id")
        row = store.get_pass(int(pass_id)) if pass_id else None
        hex_code = clean_hex(body.get("hex", "")) or (row["hex"] if row else "")
        if not hex_code:
            raise ValueError("which plane?")
        if row is None:  # a live plane: the pass it's on now, if it's crossed your zone
            row = store.latest_pass_for(hex_code, now - 6 * 3600)
        callsign = (row["callsign"] if row and row["callsign"] else str(body.get("callsign", "")))[:10].upper()
        photo_id = store.add_photo(row["id"] if row else None, hex_code, callsign, now,
                                   int(body.get("width") or 0), int(body.get("height") or 0))
        self.photo_dir.mkdir(exist_ok=True)
        (self.photo_dir / f"{photo_id}.jpg").write_bytes(full)
        (self.photo_dir / f"{photo_id}-thumb.jpg").write_bytes(thumb)
        return {"id": photo_id, **self.yours_json()}

    def delete_photo(self, body: dict) -> dict[str, Any]:
        photo_id = int(body.get("photo") or 0)
        if self.store().delete_photo(photo_id):
            for name in (f"{photo_id}.jpg", f"{photo_id}-thumb.jpg"):
                (self.photo_dir / name).unlink(missing_ok=True)
        return self.yours_json()

    def photo_file(self, name: str) -> bytes | None:
        """photos/12.jpg or photos/12-thumb.jpg: only names like these, nothing else on disk."""
        stem = name[:-len("-thumb")] if name.endswith("-thumb") else name
        if not stem.isdigit():
            return None
        path = self.photo_dir / f"{name}.jpg"
        return path.read_bytes() if path.is_file() else None

    def add_noise(self, body: dict) -> dict[str, Any]:
        """One level per second from the noise meter in the browser."""
        now = self.clock()
        samples = []
        for item in (body.get("samples") or [])[:900]:
            t, db = float(item[0]), float(item[1])
            if abs(t - now) < 86400 and -160 <= db <= 20:
                samples.append((t, db))
        store = self.store()
        store.add_noise_levels(samples)
        measured = settle_noise(store, self.cfg, now)
        return {"stored": len(samples), "measured": measured}

    def noise_json(self, days: int) -> dict[str, Any]:
        store = self.store()
        settle_noise(store, self.cfg, self.clock())
        return build_noise(store, self.cfg, self.clock(), max(1, min(days, 366)), home_elevation_ft(self.cfg))

    def noise_csv(self, days: int) -> str:
        return noise_csv(self.store(), self.cfg, self.clock(), days, home_elevation_ft(self.cfg))

    def follow(self, body: dict) -> dict[str, Any]:
        """Follow a flight (a notification when it lands), or stop following it."""
        hex_code = clean_hex(body.get("hex", ""))
        if not hex_code:
            raise ValueError("which plane?")
        if body.get("undo"):
            unfollow(self.store(), hex_code)
        else:
            follow(self.store(), hex_code, str(body.get("callsign", ""))[:10].upper(), self.clock())
        return self.yours_json()

    def quiz_new(self, query: dict) -> dict[str, Any]:
        """Ask "which kind of plane is it?" about a live plane."""
        hex_code = clean_hex(query.get("hex", ""))
        planes, _ = self.feed.latest() if hasattr(self.feed, "latest") else ([], 0)
        ac = next((a for a in planes or [] if a.hex == hex_code), None)
        q = quiz_question(self.store(), hex_code, ac.type_code if ac else "", self.clock()) if ac else None
        if q is None:
            raise ValueError("can't make a question about that plane (its type isn't known)")
        return {**q, "score": quiz_score(self.store())}

    def quiz_reply(self, body: dict) -> dict[str, Any]:
        return quiz_answer(self.store(), int(body.get("id") or 0), str(body.get("chosen", ""))[:60])

    def flow_json(self) -> dict[str, Any]:
        weather = self.sky.weather() if self.sky else None
        return flow_report(self.store(), self.cfg, self.clock(), weather)

    def space_json(self) -> dict[str, Any]:
        """Satellite passes over home for the next 3 days, and tracks for any happening now or soon."""
        if not self.space:
            return {"passes": [], "tracks": []}
        now = self.clock()
        upcoming = self.space.upcoming()
        tracks = []
        for p in upcoming:
            if p["start"] - now < 20 * 60 and p["end"] > now:
                start = max(now - 30, p["start"] - 60)
                tracks.append({"number": p["number"], "name": p["name"], "visible": p["visible"],
                               "points": self.space.track(p["number"], start, p["end"] + 60 - start)})
        return {"passes": [{**{k: p[k] for k in ("name", "number", "start", "end", "visible", "visible_from",
                                                    "visible_to", "max_el", "max_t", "start_dir", "max_dir",
                                                    "end_dir", "minutes")},
                            "text": pass_text(p, self.cfg)} for p in upcoming], "tracks": tracks}

    def coming(self, horizon: float = 900) -> list:
        """Planes due over the zone soon (positions moved on to now), with their details."""
        planes, fetched_at = self.feed.latest() if hasattr(self.feed, "latest") else ([], 0)
        now = self.clock()
        ground = home_elevation_ft(self.cfg)
        moved = [glide(ac, now - fetched_at + (ac.seen_pos or 0)) for ac in planes or []]
        return crossings(self.cfg, moved, ground, horizon)

    def next_json(self) -> dict[str, Any]:
        """For the home-screen widget: the next few planes and when they'll be over."""
        now = self.clock()
        lookup = Lookup(self.store(), self.cfg.adsbdb_api)
        report = self.sky.report() if self.sky else None
        out = []
        for c in self.coming()[:4]:
            info = lookup.info(c.ac, now)
            note = sky_note(report, c.ac.alt_ft, c.miles, home_elevation_ft(self.cfg))
            out.append({"hex": c.ac.hex, "label": c.ac.label, "airline": info.airline,
                        "route": f"{info.origin or '?'} → {info.destination or '?'}" if info.origin or info.destination else "",
                        "eta_s": round(c.eta), "at": round(now + c.eta), "direction": compass(c.az), "up": round(c.up),
                        "alt_ft": c.ac.alt_ft, "sky": note, "text": describe(c, info, note)})
        last = self.store().recent_passes(0, limit=1)
        return {"home": self.cfg.home_name, "time": now, "next": out,
                "last": {"label": label_of(last[0]), "at": last[0]["first_seen"]} if last else None}

    def now_text(self) -> str:
        """What Siri says: the plane overhead or coming next."""
        data = self.next_json()
        if data["next"]:
            text = data["next"][0]["text"]
            if len(data["next"]) > 1:
                more = len(data["next"]) - 1
                text += f" {more} more plane{'s' if more != 1 else ''} after that in the next 15 minutes."
            return text
        last = data["last"]
        if last:
            ago = round((self.clock() - last["at"]) / 60)
            when = f"{ago} minutes ago" if ago < 120 else self.cfg.local(last["at"]).strftime("at %I:%M %p").replace(" 0", " ")
            return f"No planes are coming over {self.cfg.home_name} in the next 15 minutes. The last one was {last['label']}, {when}."
        return f"No planes are coming over {self.cfg.home_name} in the next 15 minutes."

    def scriptable(self, host: str) -> str:
        """The widget script for the Scriptable app, with this map's address already filled in."""
        script = (WEB_DIR / "scriptable.js").read_text(encoding="utf-8")
        host = host if host and all(c.isalnum() or c in ".-:[]" for c in host) else f"localhost:{self.cfg.map_port}"
        scheme = "https" if self.secure_url and host in self.secure_url else "http"
        return script.replace("__PLANESPOTTER_URL__", f"{scheme}://{host}")

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


def label_of(row) -> str:
    return row["callsign"] or row["registration"] or row["hex"].upper()


def clean_hex(text: str) -> str:
    """A plane's ICAO address (6 hex digits), or '' if it isn't one."""
    text = str(text).lower().strip()
    return text if len(text) == 6 and all(c in "0123456789abcdef" for c in text) else ""


def jpeg_from_data_url(data_url: str, limit: int) -> bytes:
    """The picture in a data:image/jpeg;base64,... string (and nothing that isn't a JPEG)."""
    prefix = "data:image/jpeg;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("expected a JPEG picture")
    try:
        data = base64.b64decode(data_url[len(prefix):], validate=True)
    except ValueError:
        raise ValueError("that picture is damaged") from None
    if not data.startswith(b"\xff\xd8\xff") or len(data) > limit:
        raise ValueError("expected a JPEG picture under %d MB" % (limit // 1024 // 1024 or 1))
    return data


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
                    self.send_json(app.collection_json())
                elif url.path == "/api/sky":
                    self.send_json(app.sky_json())
                elif url.path == "/api/bingo":
                    self.send_json(bingo(app.store(), app.cfg, app.clock(), home_elevation_ft(app.cfg)))
                elif url.path == "/api/quiz":
                    self.send_json(app.quiz_new(query))
                elif url.path == "/api/wrapped":
                    self.send_json(wrapped(app.store(), app.cfg, query.get("period", "")[:7] or
                                           str(app.cfg.local(app.clock()).year), home_elevation_ft(app.cfg)))
                elif url.path == "/api/plane":
                    self.send_json(profile(app.store(), app.cfg, clean_hex(query.get("hex", ""))))
                elif url.path == "/api/today":
                    self.send_json(today_board(app.store(), app.cfg, app.clock()))
                elif url.path == "/api/flow":
                    self.send_json(app.flow_json())
                elif url.path == "/api/space":
                    self.send_json(app.space_json())
                elif url.path == "/api/next":
                    self.send_json(app.next_json())
                elif url.path == "/api/now.txt":
                    self.send(200, app.now_text().encode(), "text/plain; charset=utf-8")
                elif url.path == "/scriptable.js":
                    self.send(200, app.scriptable(self.headers.get("Host", "")).encode(), "text/plain; charset=utf-8")
                elif url.path == "/api/yours":
                    self.send_json(app.yours_json())
                elif url.path == "/api/noise":
                    self.send_json(app.noise_json(int(float(query.get("days", "30") or 30))))
                elif url.path == "/api/noise.csv":
                    days = int(float(query.get("days", "30") or 30))
                    body = app.noise_csv(days).encode("utf-8")
                    stamp = app.cfg.local(app.clock()).strftime("%Y-%m-%d")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="planespotter-noise-log-{stamp}.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path.startswith("/photo/") and url.path.endswith(".jpg"):
                    jpg = app.photo_file(url.path[len("/photo/"):-len(".jpg")])
                    if jpg:
                        self.send(200, jpg, "image/jpeg", cache="private, max-age=31536000, immutable")
                    else:
                        self.send_json({"error": "not found"}, 404)
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

        def same_site(self) -> bool:
            """Only this map's own pages may send things (not some other website you have open)."""
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            routes = {"/api/saw": app.saw, "/api/photo": app.add_photo, "/api/photo/delete": app.delete_photo,
                      "/api/noise/levels": app.add_noise, "/api/follow": app.follow, "/api/quiz": app.quiz_reply}
            try:
                if url.path not in routes:
                    self.send_json({"error": "not found"}, 404)
                    return
                size = int(self.headers.get("Content-Length") or 0)
                if not self.same_site():
                    self.send_json({"error": "not allowed"}, 403)
                    return
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                    self.send_json({"error": "expected JSON"}, 415)
                    return
                if not 0 < size <= MAX_UPLOAD:
                    self.send_json({"error": "too big"}, 413)
                    return
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                self.send_json(routes[url.path](body))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, ValueError, TypeError, KeyError) as exc:
                log.info("map upload %s failed: %s", url.path, exc)
                try:
                    self.send_json({"error": str(exc)}, 400)
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
                     position_lookup=None, logo_cache=None, budget=None, sky=None, https_port: int | None = None):
    """Start serving the map in a background thread. Returns (server, list of URLs).

    With MAP_LAN and MAP_HTTPS on, the last URL is a secure (https) one for phones."""
    host = "0.0.0.0" if cfg.map_lan else "127.0.0.1"
    store = Store(cfg.db_path)
    backfill_heat(store, cfg)  # first run only: build the heat map from existing history
    store.close()
    port = cfg.map_port if port is None else port
    try:
        app = MapApp(cfg, feed, flight_lookup=flight_lookup, position_lookup=position_lookup,
                     logo_cache=logo_cache, budget=budget, sky=sky)
        server = ThreadingHTTPServer((host, port), make_handler(app))
    except OSError as exc:
        raise MapServerError(
            f"Couldn't start the map on port {port} ({exc.strerror or exc}). "
            f"It may already be running: try http://localhost:{port}"
        ) from None
    server.daemon_threads = True
    server.app = app  # so the caller can tell it about other places' maps
    threading.Thread(target=server.serve_forever, name="map-server", daemon=True).start()
    port = server.server_address[1]
    urls = [f"http://localhost:{port}"]
    ip = lan_address() if cfg.map_lan else None
    if ip:
        urls.append(f"http://{ip}:{port}")
    if ip and cfg.map_https:
        secure = start_https(cfg, app, host, ip, cfg.map_https_port if https_port is None else https_port)
        if secure:
            app.secure_url = secure
            urls.append(secure)
    return server, urls


def start_https(cfg: Config, app: MapApp, host: str, ip: str, port: int) -> str:
    """The same map over https, for phones (camera, compass, microphone, share sheet). '' if it can't."""
    files = ensure_certificate(Path(cfg.db_path).parent / "https-cert", [ip])
    if not files:
        return ""
    try:
        server = TLSServer((host, port), make_handler(app), *files)
    except (OSError, ValueError) as exc:
        log.info("no https address for phones (port %s): %s", port, exc)
        return ""
    threading.Thread(target=server.serve_forever, name="map-https", daemon=True).start()
    return f"https://{ip}:{server.server_address[1]}"
