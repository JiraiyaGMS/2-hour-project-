"""Which airport a flight really took off from and landed at.

Route databases map a flight number to a route, and are often wrong (the same
number flies different legs on different days). Once planespotter has a
plane's actual flight path, the airport nearest to where it left the ground,
and to where it came back down, is the real answer.

Airports come from OurAirports (public domain); scripts/update_airports.py
rebuilds the list.
"""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from .geo import distance_miles

DATA = Path(__file__).resolve().parent / "data" / "airports.csv"
NEAR_KM = 8.0          # a takeoff/landing point this close to an airport is "at" it
LOW_FT = 2500          # ...and this low above it
SIZE_BONUS_KM = {"L": 2.0, "M": 1.0, "S": 0.0}  # prefer the big airport next to a small field


class Airport(NamedTuple):
    code: str        # IATA (LAX) where there is one, otherwise ICAO
    icao: str
    kind: str        # L large, M medium, S small
    name: str
    city: str
    country: str
    lat: float
    lon: float
    elevation_ft: int


class Airports:
    def __init__(self, path: Path = DATA):
        self.by_cell: dict[tuple[int, int], list[Airport]] = {}
        self.by_code: dict[str, Airport] = {}
        with open(path, encoding="utf-8") as fh:
            lines = (line for line in fh if not line.startswith("#"))
            for r in csv.DictReader(lines):
                try:
                    a = Airport(r["code"], r["icao"], r["kind"], r["name"], r["city"], r["country"],
                                float(r["lat"]), float(r["lon"]), int(float(r["elevation_ft"] or 0)))
                except (KeyError, ValueError):
                    continue
                self.by_cell.setdefault((math.floor(a.lat), math.floor(a.lon)), []).append(a)
                for code in (a.code, a.icao):
                    if code and (code not in self.by_code or a.kind < self.by_code[code].kind):
                        self.by_code[code] = a  # "L" < "M" < "S": the bigger airport wins a shared code

    def get(self, code: str) -> Airport | None:
        """An airport by its IATA (LAX) or ICAO (KLAX) code."""
        return self.by_code.get((code or "").upper())

    def nearest(self, lat: float, lon: float, max_km: float = NEAR_KM) -> Airport | None:
        best, best_score = None, max_km
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for a in self.by_cell.get((math.floor(lat) + dlat, math.floor(lon) + dlon), ()):
                    km = distance_miles(lat, lon, a.lat, a.lon) * 1.609344
                    if km > max_km:
                        continue
                    score = km - SIZE_BONUS_KM.get(a.kind, 0)
                    if best is None or score < best_score:
                        best, best_score = a, score
        return best

    def at(self, lat: float, lon: float, alt_ft, max_km: float = NEAR_KM, low_ft: float = LOW_FT) -> Airport | None:
        """The airport a plane at this point is taking off from or landing at, if any."""
        airport = self.nearest(lat, lon, max_km)
        if airport is None:
            return None
        on_the_ground = alt_ft == 0 or alt_ft is None
        low = alt_ft is not None and alt_ft - airport.elevation_ft < low_ft
        return airport if on_the_ground or low else None


@lru_cache(maxsize=1)
def airports() -> Airports:
    """Loaded once, on first use (about 12,000 airports)."""
    return Airports()


def route_from_path(path: list, landed: bool) -> tuple[Airport | None, Airport | None]:
    """(origin, destination) from a flight path of [lat, lon, alt_ft, time] points.

    The origin is known when the path starts at an airport (coverage can start
    mid-air, over the ocean say); the destination only once it has landed.
    """
    if not path:
        return None, None
    db = airports()
    first, last = path[0], path[-1]
    origin = db.at(first[0], first[1], first[2])
    if landed:
        destination = db.at(last[0], last[1], last[2])
    else:
        # Not down yet, but about to be: descending, very low, right by an airport it didn't leave from.
        earlier = next((p for p in reversed(path) if last[3] - p[3] >= 90), None)
        descending = (earlier is not None and last[2] is not None and earlier[2] is not None
                      and earlier[2] > last[2] + 200)
        near = db.at(last[0], last[1], last[2], max_km=5, low_ft=1000) if descending else None
        destination = near if near and (origin is None or near.code != origin.code) else None
    if origin and destination and origin.code == destination.code and len(path) < 3:
        destination = None  # a few points near one airport: not enough to tell
    return origin, destination
