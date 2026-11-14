"""Track a flight on the Mac's map, the same as the phone's ✈︎ Track a flight.

Find flights anywhere in the world by flight number ("UA 1234", "Alaska 1349"), registration (N12345),
airline ("Southwest") or airport ("PDX", "Portland", "Alaska PDX"), straight from adsb.lol, within the
Mac's daily allowance. Then follow one: live while it flies, and before take-off where it waits, with its
gate, take-off time and delay from the gate service (if you've set one up).
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
from typing import Any

from .adsb import get_json, key_header, parse_aircraft
from .airlines import AIRLINES, IATA_TO_ICAO
from .airports import Airport, airports
from .geo import distance_miles

ICAO_AIRLINES = {a[0] for a in AIRLINES}
NOISE_WORDS = {"airlines", "airline", "airways", "air", "lines", "flight", "the", "at", "near", "from", "to", "in"}
REG = re.compile(r"^(N[0-9][0-9A-Z]{0,4}|[A-Z]{1,2}-[A-Z0-9]{3,5}|C-?[FGI][A-Z]{3})$")


def flown_as(callsign: str) -> list[str]:
    """Other callsigns the same flight uses on the radio: Alaska's 2000s are flown by Horizon as QXE."""
    m = re.fullmatch(r"(ASA|QXE)(\d+)", callsign or "")
    if not m:
        return []
    if m[1] == "QXE":
        return ["ASA" + m[2]]
    return ["QXE" + m[2]] if 2000 <= int(m[2]) <= 2999 else []


def _airline_named(words: list[str]):
    """The airline these words name ("alaska", "southwest", "united airlines"), if one does."""
    words = [w for w in words if w not in NOISE_WORDS]
    if not words:
        return None
    best = None
    for icao, iata, name in AIRLINES:
        names = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w and w not in NOISE_WORDS]
        if names and all(w in names for w in words) and names[0] == words[0]:
            if best is None or len(name) < len(best[2]):
                best = (icao, iata, name)
    return best


MAIN_AIRPORT = {"chicago": "ORD", "new york": "JFK", "washington": "IAD", "houston": "IAH", "dallas": "DFW",
                "london": "LHR", "paris": "CDG", "tokyo": "HND", "san francisco": "SFO", "los angeles": "LAX",
                "miami": "MIA", "seattle": "SEA", "detroit": "DTW", "toronto": "YYZ", "montreal": "YUL"}


def _airport_named(words: list[str], home: tuple[float, float] | None = None) -> Airport | None:
    """The airport these words name: a code (PDX, KPDX), or a city or airport name (Portland, John Wayne).
    Several for one city: its main one, else an international one, else the nearest to home."""
    text = " ".join(words)
    if len(words) == 1 and len(text) in (3, 4):
        a = airports().get(text)
        if a and a.kind in "LM":
            return a
    if not text or len(text) < 4:
        return None
    if text in MAIN_AIRPORT:
        return airports().get(MAIN_AIRPORT[text])
    hits = [a for a in airports().by_code.values() if a.kind in "LM" and len(a.code) == 3 and a.code == a.code.upper()
            and (a.city.lower() == text or text in a.name.lower())]
    if not hits:
        return None
    near = (lambda a: distance_miles(home[0], home[1], a.lat, a.lon)) if home else (lambda a: 0)
    return min(set(hits), key=lambda a: (a.kind, "international" not in a.name.lower(), near(a), a.code))


def read_query(text: str, home: tuple[float, float] | None = None) -> dict:
    """What a search asks for: {"callsigns": [...], "reg": "", "airline": (icao, iata, name) | None,
    "airport": Airport | None}."""
    q = " ".join((text or "").strip().split())
    out: dict[str, Any] = {"callsigns": [], "reg": "", "airline": None, "airport": None}
    if not q:
        return out
    up = q.upper()
    if REG.match(up.replace(" ", "")) and not re.fullmatch(r"[A-Z]{2,3}\d+", up.replace(" ", "")):
        out["reg"] = up.replace(" ", "")
        return out
    # A flight number: "UA 1234", "UAL1234", "Alaska 1349", "southwest flight 1672"
    m = re.fullmatch(r"(.*?)\s*(\d{1,4}[A-Z]?)", up)
    if m and m[1]:
        code = m[1].replace(" ", "")
        if re.fullmatch(r"[A-Z]{3}", code) and code in ICAO_AIRLINES:
            out["callsigns"] = [code + m[2].lstrip("0")]
        elif re.fullmatch(r"[A-Z0-9]{2}", code) and code in IATA_TO_ICAO:
            out["callsigns"] = [IATA_TO_ICAO[code] + m[2].lstrip("0")]
        else:
            airline = _airline_named(m[1].lower().split())
            if airline:
                out["callsigns"] = [airline[0] + m[2].lstrip("0")]
        if out["callsigns"]:
            return out
    # An airline and/or an airport: "Alaska", "PDX", "Alaska PDX", "Delta Los Angeles", "New York"
    words = q.lower().split()
    clean = [w for w in words if w not in NOISE_WORDS]
    if len(clean) == 1 and len(clean[0]) in (2, 3):  # a code: an airline's (UA, SWA) or an airport's (LAX, DAL)
        w = clean[0].upper()
        ap = airports().get(w)
        if w in IATA_TO_ICAO and len(w) == 2:
            out["airline"] = next((x for x in AIRLINES if x[0] == IATA_TO_ICAO[w]), None)
        elif ap and ap.kind in "LM" and (w not in ICAO_AIRLINES or (ap.kind == "L" and (
                not home or distance_miles(home[0], home[1], ap.lat, ap.lon) < 1500))):  # LAX, PDX; DAL near home
            out["airport"] = ap
        elif w in ICAO_AIRLINES:  # SWA, ASA
            out["airline"] = next((x for x in AIRLINES if x[0] == w), None)
        return out
    out["airline"] = _airline_named(words)
    if out["airline"]:
        return out
    out["airport"] = _airport_named(clean, home)
    if out["airport"]:
        return out
    for cut in range(len(words) - 1, 0, -1):  # an airline, then an airport
        airline, rest = _airline_named(words[:cut]), [w for w in words[cut:] if w not in NOISE_WORDS]
        airport = _airport_named(rest, home) if airline and rest else None
        if airport:
            out["airline"], out["airport"] = airline, airport
            break
    return out


class Sky:
    """adsb.lol for searches: anywhere in the world, counted in the Mac's daily allowance (as clicks)."""

    def __init__(self, base_url: str, api_key: str = "", budget=None, clock=time.time, ttl: float = 30):
        self.base_url, self.api_key, self.budget, self.clock, self.ttl = base_url.rstrip("/"), api_key, budget, clock, ttl
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, list]] = {}

    def get(self, path: str) -> list[dict] | None:
        """The raw aircraft at this path (kept ``ttl`` seconds), or None when we can't ask now."""
        now = self.clock()
        with self.lock:
            hit = self.cache.get(path)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        if self.budget and not self.budget.allow("click"):
            return None
        if self.budget:
            self.budget.record("click")
        try:
            data = get_json(self.base_url + path, timeout=12, headers=key_header(self.api_key))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None
        raw = [r for r in (data.get("ac") or []) if isinstance(r, dict)]
        with self.lock:
            self.cache[path] = (now, raw)
            for k in [k for k, (t, _) in self.cache.items() if now - t > 600]:
                del self.cache[k]
        return raw


def _result(ac, home: tuple[float, float], score: float, why: str) -> dict:
    return {"hex": ac.hex, "callsign": ac.callsign, "label": ac.label, "registration": ac.registration,
            "type": ac.type_code, "lat": ac.lat, "lon": ac.lon, "alt_ft": ac.alt_ft, "speed_kt": ac.speed_kt,
            "heading": ac.track, "miles": round(distance_miles(home[0], home[1], ac.lat, ac.lon), 1),
            "score": round(score, 1), "why": why}


def search(text: str, sky: Sky, home: tuple[float, float], route_for=None) -> dict:
    """The planes a search asks for, best match first (up to 100; the map shows 10 at a time).
    ``route_for(result)`` -> {"airline", "origin", "destination"} (checked routes), for airport searches."""
    ask = read_query(text, home)
    found: dict[str, dict] = {}
    not_flying: list[str] = []

    def add(r):
        if r["hex"] not in found or found[r["hex"]]["score"] < r["score"]:
            found[r["hex"]] = r

    def flying(raw):
        return [ac for ac in (parse_aircraft(r) for r in raw or []) if ac]

    limited = False
    for cs in ask["callsigns"][:3]:  # exact flight numbers, anywhere in the world
        raw, why = sky.get("/v2/callsign/" + cs), f"Flight {cs}"
        limited = limited or raw is None
        for alt in flown_as(cs):  # Horizon flies Alaska's 2000s as QXE, same number
            if flying(raw):
                break
            again = sky.get("/v2/callsign/" + alt)
            if flying(again):
                raw, why = again, f"Flight {cs} (flown as {alt})"
        planes = flying(raw)
        for ac in planes:
            add(_result(ac, home, 100, why))
        if not planes and raw is not None:
            not_flying.append(cs)
    if ask["reg"]:
        raw = sky.get("/v2/reg/" + ask["reg"])
        limited = limited or raw is None
        planes = flying(raw)
        for ac in planes:
            add(_result(ac, home, 98, f"Registration {ask['reg']}"))
        if not planes and raw is not None:
            not_flying.append(ask["reg"])
    airline, airport = ask["airline"], ask["airport"]
    understood = ("Flight " + " or ".join(ask["callsigns"]) if ask["callsigns"] else
                  f"Registration {ask['reg']}" if ask["reg"] else "")
    if airport or (airline and not ask["callsigns"]):
        centre = (airport.lat, airport.lon) if airport else home
        nm = 60 if airport else 200
        raw = sky.get(f"/v2/point/{centre[0]:.4f}/{centre[1]:.4f}/{nm}")
        limited = limited or raw is None
        for ac in flying(raw):
            if airline and not ac.callsign.startswith(airline[0]):
                continue
            d = distance_miles(centre[0], centre[1], ac.lat, ac.lon)
            score = 80 - d * 0.5 - (ac.alt_ft or 0) / 1500 if airport else 70 - d / 4
            where = f"near {airport.code}" if airport else "near home"
            add(_result(ac, home, score, f"{airline[2]} {where}" if airline else f"Near {airport.code}"))
        understood = understood or ((airline[2] if airline else "Planes") +
                                    (f" near {airport.code} ({airport.city or airport.name})" if airport else " near home"))
    results = sorted(found.values(), key=lambda r: -r["score"])
    if route_for:
        for r in results[:20]:
            rt = route_for(r) or {}
            r["airline"] = rt.get("airline") or ""
            if rt.get("origin") and rt.get("destination"):
                r["route"] = f"{rt['origin']} → {rt['destination']}"
            if airport and airport.code in (rt.get("origin"), rt.get("destination")):
                r["score"] += 40
                r["why"] = ("Flying to " if rt.get("destination") == airport.code else "Flying from ") + airport.code
        results.sort(key=lambda r: -r["score"])
    if not understood:
        understood = "Couldn't tell what that is: try a flight number (UA 1234), an airline or an airport"
    return {"query": text, "understood": understood, "results": results[:100], "not_flying": not_flying,
            "limited": limited and not results}


def where_now(callsign: str, hex_code: str, sky: Sky, positions=None) -> dict:
    """Where a flight you're tracking is: {"flying": plane} or {"ground": {...}} or {} (not seen)."""
    names = [callsign, *flown_as(callsign)] if callsign else []
    if hex_code and positions is not None:
        ac = positions.get([hex_code]).get(hex_code)
        if ac and (not callsign or not ac.callsign or ac.callsign in names):
            return {"flying": ac}
    for cs in names:
        raw = sky.get("/v2/callsign/" + cs)
        if not raw:
            continue
        mine = [r for r in raw if str(r.get("flight") or "").strip() in names]
        for r in mine:
            ac = parse_aircraft(r)
            if ac:
                return {"flying": ac}
        for r in mine:
            if r.get("alt_baro") == "ground" and isinstance(r.get("lat"), (int, float)):
                ap = airports().at(r["lat"], r["lon"], 0, max_km=8)
                return {"ground": {"lat": r["lat"], "lon": r["lon"], "gs": r.get("gs") or 0, "hex": str(r.get("hex") or ""),
                                   "airport": {"code": ap.code, "icao": ap.icao, "city": ap.city or ap.name} if ap else None}}
    return {}
