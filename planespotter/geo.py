"""Small geometry helpers. Distances are in statute miles."""

from __future__ import annotations

import math

EARTH_RADIUS_MI = 3958.8
NM_PER_MILE = 0.868976
_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing (0-360) from point 1 towards point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass(deg: float | None) -> str:
    """0 -> 'N', 135 -> 'SE', etc."""
    if deg is None:
        return "?"
    return _COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def _to_local_xy(home_lat: float, home_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """Flat x/y miles relative to home; accurate enough over a few dozen miles."""
    x = math.radians(lon - home_lon) * math.cos(math.radians(home_lat)) * EARTH_RADIUS_MI
    y = math.radians(lat - home_lat) * EARTH_RADIUS_MI
    return x, y


def segment_min_distance_miles(
    home_lat: float, home_lon: float,
    lat1: float, lon1: float, lat2: float, lon2: float,
) -> float:
    """Closest distance from home to the straight line between two positions.

    Used to catch fast planes that cross the zone between two polls.
    """
    x1, y1 = _to_local_xy(home_lat, home_lon, lat1, lon1)
    x2, y2 = _to_local_xy(home_lat, home_lon, lat2, lon2)
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(x1, y1)
    t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_sq))
    return math.hypot(x1 + t * dx, y1 + t * dy)


def offset(lat: float, lon: float, bearing: float, miles: float) -> tuple[float, float]:
    """The point ``miles`` away from (lat, lon) in compass direction ``bearing``."""
    d = miles / EARTH_RADIUS_MI
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def miles_to_nm(miles: float) -> float:
    return miles * NM_PER_MILE
