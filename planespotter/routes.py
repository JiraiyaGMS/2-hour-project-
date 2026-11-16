"""Where a flight is going, worked out from more than one place and checked against the plane itself.

A flight number is not a route. Route databases map a callsign to "the" route, but the same number flies
different legs on different days, there-and-back flights share one number, and the databases age. Measured
against the airport each plane had really taken off from, a single database was right for about one flight
in five, so planespotter:

* asks two free sources, adsb.lol's standing data (rebuilt every day) and adsbdb.com;
* splits a route with stops (SEA-SNA-SEA) into its legs;
* keeps only the legs this plane can be flying, from where it is, which way it points, how high it is and
  whether it's climbing or coming down, and picks the one that fits best;
* lets the plane's recorded track overrule all of it: the airport it left the ground at is its origin.

No answer is better than a wrong one: when nothing fits, the route is left blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .airlines import BY_ICAO
from .airports import Airport, airports
from .geo import bearing_deg, distance_miles

STANDING = "adsb.lol"   # adsb.lol's standing data (the VRS route list, rebuilt daily)
ADSBDB = "adsbdb"
SOURCE_ORDER = {STANDING: 0.0, ADSBDB: 0.15}  # measured: the standing data is right far more often

SAME_CITY_MILES = 25.0
LOW_FT = 10000          # below this (above the airport) a plane is near one end of its trip
CLIMB_FPM = 400
DESCEND_FPM = -400


@dataclass(frozen=True)
class Place:
    """One end of a route."""

    code: str            # IATA where there is one (LAX), otherwise ICAO
    icao: str = ""
    city: str = ""
    lat: float | None = None
    lon: float | None = None
    elevation_ft: float = 0.0

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True)
class Leg:
    origin: Place
    destination: Place
    source: str
    airline: str = ""


@dataclass(frozen=True)
class Route:
    """What we believe about a flight's route. Either end may be missing."""

    origin: Place | None = None
    destination: Place | None = None
    airline: str = ""
    # "track": the origin is where its recorded track left the ground; "shows": it's plainly taking off
    # from / landing at that airport; "agree": both sources say so; "fits": one source, and the plane is
    # where that leg would put it; "": nothing known.
    basis: str = ""
    source: str = ""

    @property
    def sure(self) -> bool:
        """Checked against the plane itself, or two sources that agree."""
        return self.basis in ("track", "shows", "agree")


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def place_from_airport(a: Airport | None) -> Place | None:
    if a is None:
        return None
    return Place(a.code, a.icao, a.city or a.name, a.lat, a.lon, float(a.elevation_ft or 0))


def _place(code: str, icao: str, city: str, lat, lon, elevation) -> Place | None:
    """An airport from a route source, tidied up against our own airport list (one spelling of each code,
    and a position even when the source left it out)."""
    code, icao = (code or "").strip().upper(), (icao or "").strip().upper()
    known = airports().get(icao) or airports().get(code)
    lat, lon = _num(lat), _num(lon)
    if known is not None:
        return Place(known.code, known.icao or icao, city or known.city or known.name,
                     lat if lat is not None else known.lat, lon if lon is not None else known.lon,
                     _num(elevation) if _num(elevation) is not None else float(known.elevation_ft or 0))
    if not (code or icao):
        return None
    return Place(code or icao, icao, city or "", lat, lon, _num(elevation) or 0.0)


def legs_from_standing(body: dict | None) -> list[Leg]:
    """adsb.lol's standing data: {"airport_codes": "KSEA-KSNA-KSEA", "_airports": [{...}, ...]} -> one leg
    for each hop."""
    if not isinstance(body, dict):
        return []
    stops = []
    for a in body.get("_airports") or []:
        if not isinstance(a, dict):
            return []
        p = _place(a.get("iata") or "", a.get("icao") or "", a.get("location") or "", a.get("lat"), a.get("lon"),
                   a.get("alt_feet"))
        if p is None:
            return []
        stops.append(p)
    if len(stops) < 2:
        # No airport details: just the codes.
        codes = [c for c in str(body.get("airport_codes") or "").split("-") if c]
        stops = [p for p in (_place("", c, "", None, None, None) for c in codes) if p is not None]
    airline = BY_ICAO.get(str(body.get("airline_code") or "").upper(), "")
    return [Leg(a, b, STANDING, airline) for a, b in zip(stops, stops[1:]) if a.code != b.code]


def legs_from_adsbdb(body: dict | None) -> list[Leg]:
    """adsbdb.com: {"flightroute": {"airline": {...}, "origin": {...}, "destination": {...}}} (and sometimes
    a "midpoint" for a flight with a stop)."""
    route = (body or {}).get("flightroute") if isinstance(body, dict) else None
    if not isinstance(route, dict):
        return []

    def end(a) -> Place | None:
        if not isinstance(a, dict):
            return None
        return _place(a.get("iata_code") or "", a.get("icao_code") or "", a.get("municipality") or "",
                      a.get("latitude"), a.get("longitude"), a.get("elevation"))

    stops = [p for p in (end(route.get("origin")), end(route.get("midpoint")), end(route.get("destination")))
             if p is not None]
    airline = (route.get("airline") or {}).get("name") or ""
    if end(route.get("origin")) is None or end(route.get("destination")) is None:
        return []
    return [Leg(a, b, ADSBDB, airline) for a, b in zip(stops, stops[1:]) if a.code != b.code]


def same_place(a: Place | None, b: Place | None) -> bool:
    """The same airport, or two airports of one city (Heathrow and Gatwick are both "London")."""
    if a is None or b is None:
        return False
    if a.code and a.code == b.code or (a.icao and a.icao == b.icao):
        return True
    if a.located and b.located:
        return distance_miles(a.lat, a.lon, b.lat, b.lon) <= SAME_CITY_MILES
    return False


def _reach(feet_above: float) -> float:
    """How far (miles) from an airport a plane this far above it can be while climbing out or coming in:
    generous, about three times a normal descent, plus room to be steered around."""
    return 30.0 + max(0.0, feet_above) / 1000.0 * 9.0


def off_line_miles(lat: float, lon: float, o: Place, d: Place) -> float:
    """How far a point is from the shortest path between two airports (the great circle), in miles."""
    import math

    R = 3958.8
    d13 = distance_miles(o.lat, o.lon, lat, lon) / R
    t13 = math.radians(bearing_deg(o.lat, o.lon, lat, lon))
    t12 = math.radians(bearing_deg(o.lat, o.lon, d.lat, d.lon))
    return abs(math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(t13 - t12)))) * R)


def fit(ac, leg: Leg) -> float | None:
    """How well this plane fits this leg: None when it can't be flying it, otherwise lower is better
    (0 = right on the line, pointing at where it's going).

    ``ac`` needs lat and lon; track, alt_ft and vr (feet a minute) are used when known.
    """
    o, d = leg.origin, leg.destination
    if not (o.located and d.located):
        return None
    direct = distance_miles(o.lat, o.lon, d.lat, d.lon)
    if direct < 15:
        return None  # a local flight (training, sightseeing): nothing to check a position against
    gone = distance_miles(o.lat, o.lon, ac.lat, ac.lon)
    left = distance_miles(ac.lat, ac.lon, d.lat, d.lon)
    if gone + left - direct > direct * 0.15 + 60:
        return None  # it would have to go a long way round
    # Long flights bend with the winds, short ones are steered around on the way in and out: some room,
    # but a plane hundreds of miles off the line between two airports is flying between two others.
    room = 35 + direct * 0.09
    aside = off_line_miles(ac.lat, ac.lon, o, d)
    if aside > room:
        return None
    score = aside / room

    alt, vr, track = getattr(ac, "alt_ft", None), getattr(ac, "vr", None), getattr(ac, "track", None)
    above_o = alt - o.elevation_ft if alt is not None else None
    above_d = alt - d.elevation_ft if alt is not None else None
    if track is not None and left > 12:
        off = abs((bearing_deg(ac.lat, ac.lon, d.lat, d.lon) - track + 540) % 360 - 180)
        if gone > 60 and left > 100 and off > 75:
            return None  # out in the middle of the trip it points roughly at where it's going
        if above_d is not None and above_d > 12000 and off > 90:
            return None  # still high up and flying away from it: it isn't on its way in there
        score += off / 180 * 0.6  # there-and-back flights: the leg it's pointing along
    if alt is not None:
        # Planes come down about 1,000 ft every 3 miles and climb a little steeper: one at cruising height
        # right over an airport is neither landing there nor has it just left.
        if above_d > 6000 + left * 700 or above_o > 9000 + gone * 800:
            return None
        climbing = vr is not None and vr > CLIMB_FPM
        descending = vr is not None and vr < DESCEND_FPM
        if climbing and above_o < LOW_FT and gone > _reach(above_o):
            return None  # low and climbing, but nowhere near where this leg starts
        if descending and above_d < LOW_FT and left > _reach(above_d):
            return None  # low and coming down, but nowhere near where this leg ends
        # Low and climbing right by the destination, or coming down right by the origin: the other direction.
        if climbing and above_d < LOW_FT and left < _reach(above_d) < gone:
            return None
        if descending and above_o < LOW_FT and gone < _reach(above_o) < left:
            return None
    return score


def choose(ac, legs: Iterable[Leg], took_off_from: Place | None = None) -> Route:
    """The route this plane is flying, from the legs the sources list for its callsign.

    What the plane itself shows comes first: ``took_off_from`` (the airport its recorded track left the
    ground at, when known), or the airport it's plainly climbing out of, and the one it's plainly about to
    land at. Only legs that agree with those count; if none does, the ends the plane shows are still known.
    """
    legs = list(legs)
    airline = next((l.airline for l in legs if l.airline), "")
    left_from = took_off_from if took_off_from is not None else departing_from(ac)
    landing_at = arriving_at(ac, not_at=left_from)
    ranked = []
    for leg in legs:
        score = fit(ac, leg)
        if score is None:
            continue
        if left_from is not None and not same_place(leg.origin, left_from):
            continue
        if landing_at is not None and not same_place(leg.destination, landing_at):
            continue
        agreed = any(other.source != leg.source and same_place(other.origin, leg.origin)
                     and same_place(other.destination, leg.destination) for other in legs)
        ranked.append((score + SOURCE_ORDER.get(leg.source, 0.3) - (0.4 if agreed else 0.0), agreed, leg))
    shown = "track" if took_off_from is not None else "shows" if (left_from or landing_at) else ""
    if ranked:
        _, agreed, leg = min(ranked, key=lambda x: x[0])
        origin = left_from if left_from is not None else leg.origin
        destination = landing_at if landing_at is not None else leg.destination
        if not origin.city:
            origin = leg.origin
        return Route(origin, destination, leg.airline or airline, shown or ("agree" if agreed else "fits"), leg.source)
    if left_from is not None or landing_at is not None:
        # The databases describe some other flight; what the plane shows is still true.
        return Route(left_from, landing_at, airline, shown, "")
    return Route(None, None, airline, "", "")


def _big_airports_near(lat: float, lon: float, miles: float) -> list[tuple[float, Airport]]:
    """Large and medium airports within ``miles``, nearest first."""
    import math

    db, found, seen = airports(), [], set()
    span = int(math.ceil(miles / 55.0)) + 1
    for dlat in range(-span, span + 1):
        for dlon in range(-span * 2, span * 2 + 1):
            for a in db.by_cell.get((math.floor(lat) + dlat, math.floor(lon) + dlon), ()):
                if a.kind not in "LM" or a.code in seen:
                    continue
                dist = distance_miles(lat, lon, a.lat, a.lon)
                if dist <= miles:
                    seen.add(a.code)
                    found.append((dist, a))
    return sorted(found, key=lambda x: x[0])


def arriving_at(ac, not_at: Place | None = None) -> Place | None:
    """The airport a plane is plainly about to land at: low, coming down, lined up with it, and no other
    airport it could as well be going to. None when that isn't clear."""
    alt, vr, track = getattr(ac, "alt_ft", None), getattr(ac, "vr", None), getattr(ac, "track", None)
    if alt is None or vr is None or vr > -300 or track is None:
        return None
    fits = []
    for dist, a in _big_airports_near(ac.lat, ac.lon, 30):
        above = alt - a.elevation_ft
        # Planes come down about 1,000 ft every 3 miles. On the way in to a big airport they pass smaller
        # ones, so a smaller one only counts once the plane is properly low.
        if not 200 < above < (6000 if a.kind == "L" else 3000):
            continue
        if dist > 4 + above / 1000 * 4.5 or dist < above / 1000 * 1.6 - 2:
            continue  # too far out for how low it is, or too high for how close
        off = abs((bearing_deg(ac.lat, ac.lon, a.lat, a.lon) - track + 540) % 360 - 180)
        if off > 25:
            continue
        fits.append((dist, a))
    if not fits:
        return None
    place = place_from_airport(fits[0][1])
    if len(fits) > 1 and fits[1][0] < fits[0][0] * 2.5:
        return None  # two airports ahead of it: can't say which
    return None if same_place(place, not_at) else place


def departing_from(ac) -> Place | None:
    """The airport a plane has plainly just taken off from: low, climbing, heading away from it, close by."""
    alt, vr, track = getattr(ac, "alt_ft", None), getattr(ac, "vr", None), getattr(ac, "track", None)
    if alt is None or vr is None or vr < 500 or track is None:
        return None
    fits = []
    for dist, a in _big_airports_near(ac.lat, ac.lon, 20):
        above = alt - a.elevation_ft
        if not 200 < above < 6000 or dist > 2.5 + above / 1000 * 3.0:
            continue
        away = abs((bearing_deg(a.lat, a.lon, ac.lat, ac.lon) - track + 540) % 360 - 180)
        if dist > 2 and away > 60:
            continue
        fits.append((dist, a))
    if not fits:
        return None
    if len(fits) > 1 and fits[1][0] < fits[0][0] * 2.5:
        return None
    return place_from_airport(fits[0][1])


_CALLSIGN = re.compile(r"^([A-Z]{3})0*(\d[0-9A-Z]*)$")


def callsign_spellings(callsign: str) -> list[str]:
    """Ways a callsign is listed: as broadcast, and without zeros in front of the number (CAL011 -> CAL11)."""
    cs = (callsign or "").strip().upper()
    out = [cs] if cs else []
    m = _CALLSIGN.match(cs)
    if m and m[1] + m[2] != cs:
        out.append(m[1] + m[2])
    return out


def airline_of(callsign: str) -> str:
    """The airline a callsign belongs to, from its first three letters (DAL123 -> Delta Air Lines)."""
    m = re.match(r"^([A-Z]{3})\d", (callsign or "").upper())
    return BY_ICAO.get(m[1], "") if m else ""
