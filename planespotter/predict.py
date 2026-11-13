"""Which planes will come over next, and when, if they keep flying straight.

Used by rush alerts, the Siri shortcut and the home-screen widget. (The map
does the same sums in the browser, so planes keep moving between updates.)
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import NamedTuple

from .geo import bearing_deg, compass, distance_miles, offset

FT = 0.3048
MI = 1609.344
KT = 0.514444
HOVER_KT = 40   # slower than this inside the zone: hovering or parked, not "coming over"
DIRECTION_WORDS = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east", "S": "south",
                   "SW": "south-west", "W": "west", "NW": "north-west"}


class Crossing(NamedTuple):
    ac: object          # the Aircraft
    eta: float          # seconds until it's closest to home (0 = over your zone now)
    miles: float        # how far away it is now
    az: float           # which way to look (compass bearing from home)
    up: float           # how high to look (degrees above the horizon)


def crossings(cfg, planes, ground_ft: int = 0, horizon: float = 900) -> list[Crossing]:
    """Planes over the zone now or due to cross it within ``horizon`` seconds, soonest first."""
    out = []
    for ac in planes:
        if ac.lat is None or ac.lon is None:
            continue
        too_high = cfg.max_altitude_ft is not None and ac.alt_ft is not None and ac.alt_ft > cfg.max_altitude_ft
        miles = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        az = bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        eta = None
        if miles <= cfg.radius_miles and not too_high:
            if ac.speed_kt is not None and ac.speed_kt >= HOVER_KT:
                eta = 0.0  # (a helicopter hovering, or a plane parked with its transponder on, isn't coming over)
        elif ac.speed_kt and ac.track is not None and not too_high:
            x, y = math.sin(math.radians(az)) * miles * MI, math.cos(math.radians(az)) * miles * MI
            v = ac.speed_kt * KT
            vx, vy = math.sin(math.radians(ac.track)) * v, math.cos(math.radians(ac.track)) * v
            tc = -(x * vx + y * vy) / (v * v)
            if 0 < tc <= horizon and math.hypot(x + vx * tc, y + vy * tc) / MI <= cfg.radius_miles:
                eta = tc
        if eta is None:
            continue
        height = max(0.0, ((ac.alt_ft or 0) - ground_ft) * FT)
        up = math.degrees(math.atan2(height, max(1.0, miles * MI)))
        out.append(Crossing(ac, eta, miles, az, up))
    return sorted(out, key=lambda c: (c.eta, c.miles))


def say_name(callsign: str, label: str, airline: str = "", city: str = "") -> str:
    """'Delta flight 573 from Atlanta' (or the callsign, if we don't know the airline): how Siri says a flight."""
    airline = (airline or "").strip()
    for suffix in (" Air Lines", " Airlines", " Airways"):
        if airline.endswith(suffix):
            airline = airline[: -len(suffix)]
    callsign = callsign or ""
    if label.startswith("~"):
        return "An unidentified plane"  # seen by radar only, with no identity
    number = callsign[3:] if len(callsign) > 3 and callsign[:3].isalpha() and callsign[3:4].isdigit() else ""
    if airline and number:
        name = f"{airline} flight {number}"
    else:
        name = f"{airline}, {label}" if airline else label
    return f"{name} from {city}" if city else name


def spoken_name(ac, info) -> str:
    """'Delta flight 573' (or the callsign, if we don't know the airline)."""
    return say_name(ac.callsign, ac.label, getattr(info, "airline", "") or "")


def describe(c: Crossing, info=None, sky: str = "") -> str:
    """One sentence about a plane, for Siri or the widget."""
    origin = getattr(info, "origin_city", "") if info else ""
    name = spoken_name(c.ac, info) + (f" from {origin}" if origin else "")
    if c.eta == 0:
        when = " is over your zone now"
    elif c.eta < 60:
        when = f" is coming over in about {max(5, round(c.eta / 5) * 5)} seconds"
    else:
        when = f" is coming over in about {round(c.eta / 60)} minute{'s' if round(c.eta / 60) != 1 else ''}"
    where = ("Look straight up." if c.up >= 70
             else f"Look {DIRECTION_WORDS[compass(c.az)]}, {round(c.up)} degrees up.")
    alt = f" It's at {round((c.ac.alt_ft or 0) / 100) * 100:,} feet." if c.ac.alt_ft else ""
    note = ""
    if sky:
        note = " " + sky.split(" ", 1)[-1].replace(": ", ", ") + "."
    return f"{name}{when}. {where}{alt}{note}"


def glide(ac, seconds: float):
    """Where a plane is ``seconds`` after its last position report, if it flew straight on."""
    if not ac.speed_kt or ac.track is None or seconds <= 0:
        return ac
    lat, lon = offset(ac.lat, ac.lon, ac.track, ac.speed_kt * 1.15078 * min(seconds, 300) / 3600)
    return replace(ac, lat=lat, lon=lon)
