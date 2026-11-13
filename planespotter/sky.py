"""Can you actually see it? Clouds from the nearest weather station, and daylight.

Weather comes from aviationweather.gov (the US National Weather Service's free
aviation weather service, worldwide): the latest METAR report from the airport
weather station nearest home, checked at most every 15 minutes. The sun's
position is worked out here, no internet needed.
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .adsb import USER_AGENT
from .geo import distance_miles

log = logging.getLogger(__name__)

METAR_URL = "https://aviationweather.gov/api/data/metar"
CACHE_SECONDS = 15 * 60
RETRY_SECONDS = 10 * 60
CEILING_COVERS = ("BKN", "OVC", "OVX")  # broken or overcast: a solid layer you can't see through
COVER_WORDS = {"FEW": "a few clouds", "SCT": "scattered clouds", "BKN": "broken cloud",
               "OVC": "overcast", "OVX": "fog"}
M_TO_FT = 3.28084


# --- the sun ------------------------------------------------------------------------


def sun_position(lat: float, lon: float, ts: float) -> tuple[float, float]:
    """(altitude, azimuth) of the sun in degrees: altitude above the horizon, azimuth clockwise
    from north. Accurate to well under a degree, which is plenty for "is it dark?"."""
    d = ts / 86400.0 + 2440587.5 - 2451545.0  # days since 1 Jan 2000, noon UTC
    g = math.radians((357.529 + 0.98560028 * d) % 360)  # mean anomaly
    q = (280.459 + 0.98564736 * d) % 360  # mean longitude
    ecl_lon = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    tilt = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(tilt) * math.sin(ecl_lon), math.cos(ecl_lon))
    dec = math.asin(math.sin(tilt) * math.sin(ecl_lon))
    sidereal = math.radians(((18.697374558 + 24.06570982441908 * d) % 24) * 15 + lon)
    hour_angle = sidereal - ra
    phi = math.radians(lat)
    alt = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(hour_angle))
    az = math.atan2(-math.sin(hour_angle) * math.cos(dec),
                    math.sin(dec) * math.cos(phi) - math.cos(dec) * math.sin(phi) * math.cos(hour_angle))
    return math.degrees(alt), (math.degrees(az) + 360) % 360


def daylight(sun_alt: float) -> str:
    """'day', 'twilight' (dawn or dusk) or 'night'."""
    if sun_alt > -0.833:
        return "day"
    if sun_alt > -6:
        return "twilight"
    return "night"


# --- the weather --------------------------------------------------------------------


def fetch_metars(lat: float, lon: float, span: float) -> list[dict]:
    """Every weather report within ``span`` degrees of a point."""
    dlon = span / max(0.2, math.cos(math.radians(lat)))
    box = f"{lat - span:.3f},{lon - dlon:.3f},{lat + span:.3f},{lon + dlon:.3f}"
    url = f"{METAR_URL}?{urllib.parse.urlencode({'bbox': box, 'format': 'json'})}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    return json.loads(body) if body.strip() else []


def _visibility_miles(raw) -> float | None:
    """'10+' -> 10.0, 6 -> 6.0 (statute miles)."""
    if raw is None:
        return None
    try:
        return float(str(raw).rstrip("+"))
    except ValueError:
        return None


def parse_metar(m: dict, home_lat: float, home_lon: float) -> dict[str, Any]:
    layers = []
    for c in m.get("clouds") or []:
        cover, base = c.get("cover"), c.get("base")
        if cover in COVER_WORDS and base is not None:
            layers.append({"cover": cover, "base_ft": int(base)})
    if m.get("vertVis"):  # the sky is hidden (fog): vertical visibility is the ceiling
        layers.append({"cover": "OVX", "base_ft": int(m["vertVis"])})
    layers.sort(key=lambda layer: layer["base_ft"])
    ceiling = next((layer["base_ft"] for layer in layers if layer["cover"] in CEILING_COVERS), None)
    return {
        "station": m.get("icaoId", ""),
        "name": (m.get("name") or "").split(",")[0],
        "distance_miles": round(distance_miles(home_lat, home_lon, m["lat"], m["lon"]), 1),
        "observed": m.get("obsTime"),
        "elevation_ft": round((m.get("elev") or 0) * M_TO_FT),
        "cover": m.get("cover") or "",
        "layers": layers,
        "ceiling_ft": ceiling,  # above the ground
        "visibility_miles": _visibility_miles(m.get("visib")),
        "weather": m.get("wxString") or "",  # e.g. "-RA BR" (light rain, mist)
        "wind_dir": m.get("wdir") if isinstance(m.get("wdir"), (int, float)) else None,  # where it blows from
        "wind_kt": m.get("wspd") if isinstance(m.get("wspd"), (int, float)) else None,
        "raw": m.get("rawOb", ""),
    }


def nearest_report(metars: list[dict], lat: float, lon: float) -> dict | None:
    """The closest station that actually reports the sky (some only send wind and temperature)."""
    usable = [m for m in metars if m.get("lat") is not None and m.get("lon") is not None
              and (m.get("clouds") is not None or m.get("cover") or m.get("vertVis"))]
    if not usable:
        return None
    best = min(usable, key=lambda m: distance_miles(lat, lon, m["lat"], m["lon"]))
    return parse_metar(best, lat, lon)


class SkyWatch:
    """The weather near home (cached) and the sun right now."""

    def __init__(self, cfg, fetch: Callable[[float, float, float], list[dict]] = fetch_metars,
                 clock: Callable[[], float] = time.time):
        self.cfg, self.fetch, self.clock = cfg, fetch, clock
        self._weather: dict | None = None
        self._checked = -1e12
        self._ok = False

    def weather(self) -> dict | None:
        now = self.clock()
        wait = CACHE_SECONDS if self._ok else RETRY_SECONDS
        if now - self._checked >= wait:
            self._checked = now
            lat, lon = self.cfg.home_lat, self.cfg.home_lon
            try:
                report = None
                for span in (0.5, 1.5):  # look nearby first, then farther out
                    report = nearest_report(self.fetch(lat, lon, span), lat, lon)
                    if report:
                        break
                self._weather, self._ok = report, True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
                log.info("couldn't get the weather: %s", exc)
                self._ok = False  # keep the last report we had, try again in a while
        return self._weather

    def report(self) -> dict[str, Any]:
        now = self.clock()
        alt, az = sun_position(self.cfg.home_lat, self.cfg.home_lon, now)
        weather = self.weather()
        if weather and weather.get("observed") and now - weather["observed"] > 3 * 3600:
            weather = None  # too old to say anything about the sky now
        return {"weather": weather,
                "sun": {"altitude": round(alt, 1), "azimuth": round(az, 1), "daylight": daylight(alt)}}


def sky_note(report: dict | None, alt_ft: int | None, distance: float, ground_ft: int = 0) -> str:
    """One line on whether you'll be able to see a plane, for alerts. Blank when it's simply visible."""
    if not report:
        return ""
    weather, sun = report.get("weather"), report.get("sun") or {}
    if weather and alt_ft is not None:
        height = alt_ft - ground_ft
        ceiling = weather.get("ceiling_ft")
        if ceiling is not None and height > ceiling + 200:
            layer = next(x for x in weather["layers"] if x["base_ft"] == ceiling and x["cover"] in CEILING_COVERS)
            what = COVER_WORDS[layer["cover"]]
            if layer["cover"] == "BKN":
                return f"⛅ Above {what} at {ceiling:,} ft: you might catch it through a gap"
            return f"☁️ Above the {what} at {ceiling:,} ft: listen for it"
        vis = weather.get("visibility_miles")
        slant = math.hypot(distance, max(0, height) / 5280)
        if vis is not None and vis < slant:
            return f"🌫️ Visibility is only {vis:g} mi, so it may be hard to see"
    if sun.get("daylight") == "night":
        return "🌙 After dark: look for its flashing lights"
    if sun.get("daylight") == "twilight":
        return "🌆 Getting dark: look for its lights"
    return ""
