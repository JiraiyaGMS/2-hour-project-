"""The Moon, the planets and the brightest stars: where they are in your sky.

For two things: telling you when a plane is about to cross in front of the
Moon (or the Sun), and showing the planets and bright stars in the sky view,
because Venus and Jupiter fool everyone into thinking they're planes.

The positions use Paul Schlyter's well-known simplified orbits ("How to
compute planetary positions"): good to a minute of arc or two, which is far
finer than you can point a phone. The Moon is corrected for being seen from
your house rather than the Earth's centre (that shifts it by up to 1°).
"""

from __future__ import annotations

import math
from typing import NamedTuple

from .space import gmst, look_angles

EARTH_RADIUS_KM = 6378.135
AU_KM = 149597870.7
MOON_RADIUS_DEG = 0.259     # the Moon (and Sun) are about half a degree across
SUN_RADIUS_DEG = 0.267

# The brightest stars: name, right ascension (hours), declination (degrees), magnitude (smaller = brighter).
STARS = [
    ("Sirius", 6.7525, -16.7161, -1.46), ("Canopus", 6.3992, -52.6957, -0.74), ("Arcturus", 14.2610, 19.1824, -0.05),
    ("Vega", 18.6156, 38.7837, 0.03), ("Capella", 5.2782, 45.9980, 0.08), ("Rigel", 5.2423, -8.2016, 0.13),
    ("Procyon", 7.6550, 5.2250, 0.34), ("Betelgeuse", 5.9195, 7.4071, 0.42), ("Achernar", 1.6286, -57.2367, 0.46),
    ("Altair", 19.8464, 8.8683, 0.76), ("Aldebaran", 4.5987, 16.5093, 0.86), ("Antares", 16.4901, -26.4320, 0.96),
    ("Spica", 13.4199, -11.1613, 0.97), ("Pollux", 7.7553, 28.0262, 1.14), ("Fomalhaut", 22.9608, -29.6222, 1.16),
    ("Deneb", 20.6905, 45.2803, 1.25), ("Regulus", 10.1395, 11.9672, 1.35), ("Castor", 7.5767, 31.8883, 1.58),
    ("Polaris", 2.5303, 89.2641, 1.98),
]


class SkyObject(NamedTuple):
    name: str
    kind: str       # "moon", "planet", "star", "sun"
    az: float
    el: float       # apparent elevation (with refraction), degrees
    mag: float      # brightness: smaller is brighter


DELTA_T = 69.0  # seconds between Earth-rotation time and the steady time orbits run on (2020s)


def _days(ts: float) -> float:
    """Schlyter's day number: days since 2000 Jan 0.0, in the steady time scale orbits use."""
    return (ts + DELTA_T) / 86400.0 + 2440587.5 - 2451543.5


def _rev(x: float) -> float:
    return x % 360.0


def _sind(x):
    return math.sin(math.radians(x))


def _cosd(x):
    return math.cos(math.radians(x))


def _kepler(m: float, e: float) -> float:
    """Eccentric anomaly (degrees) for mean anomaly m (degrees)."""
    E = m + math.degrees(e * _sind(m) * (1 + e * _cosd(m)))
    for _ in range(8):
        E = E - (E - math.degrees(e * _sind(E)) - m) / (1 - e * _cosd(E))
    return E


def _orbit(N, i, w, a, e, M):
    """Position in the orbit's own frame, turned into ecliptic x, y, z."""
    E = _kepler(_rev(M), e)
    xv = a * (_cosd(E) - e)
    yv = a * math.sqrt(1 - e * e) * _sind(E)
    v = math.degrees(math.atan2(yv, xv))
    r = math.hypot(xv, yv)
    x = r * (_cosd(N) * _cosd(v + w) - _sind(N) * _sind(v + w) * _cosd(i))
    y = r * (_sind(N) * _cosd(v + w) + _cosd(N) * _sind(v + w) * _cosd(i))
    z = r * _sind(v + w) * _sind(i)
    return x, y, z


def _sun(d: float):
    """The Sun's ecliptic position seen from the Earth (AU) and its mean longitude and anomaly."""
    w = 282.9404 + 4.70935e-5 * d
    e = 0.016709 - 1.151e-9 * d
    M = _rev(356.0470 + 0.9856002585 * d)
    E = _kepler(M, e)
    xv, yv = _cosd(E) - e, math.sqrt(1 - e * e) * _sind(E)
    v = math.degrees(math.atan2(yv, xv))
    r = math.hypot(xv, yv)
    lon = _rev(v + w)
    return r * _cosd(lon), r * _sind(lon), 0.0, _rev(M + w), M


def _moon_ecliptic(d: float):
    """The Moon's geocentric ecliptic longitude, latitude (degrees) and distance (Earth radii)."""
    N = 125.1228 - 0.0529538083 * d
    i, w, a, e = 5.1454, 318.0634 + 0.1643573223 * d, 60.2666, 0.054900
    M = 115.3654 + 13.0649929509 * d
    x, y, z = _orbit(N, i, w, a, e, M)
    lon = math.degrees(math.atan2(y, x))
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    r = math.sqrt(x * x + y * y + z * z)
    _, _, _, Ls, Ms = _sun(d)
    Mm, Lm = _rev(M), _rev(N + w + M)
    D, F = Lm - Ls, Lm - N
    lon += (-1.274 * _sind(Mm - 2 * D) + 0.658 * _sind(2 * D) - 0.186 * _sind(Ms) - 0.059 * _sind(2 * Mm - 2 * D)
            - 0.057 * _sind(Mm - 2 * D + Ms) + 0.053 * _sind(Mm + 2 * D) + 0.046 * _sind(2 * D - Ms)
            + 0.041 * _sind(Mm - Ms) - 0.035 * _sind(D) - 0.031 * _sind(Mm + Ms) - 0.015 * _sind(2 * F - 2 * D)
            + 0.011 * _sind(Mm - 4 * D))
    lat += (-0.173 * _sind(F - 2 * D) - 0.055 * _sind(Mm - F - 2 * D) - 0.046 * _sind(Mm + F - 2 * D)
            + 0.033 * _sind(F + 2 * D) + 0.017 * _sind(2 * Mm + F))
    r += -0.58 * _cosd(Mm - 2 * D) - 0.46 * _cosd(2 * D)
    return lon, lat, r


def _to_equatorial(x: float, y: float, z: float, d: float):
    """Ecliptic -> equatorial (of date) coordinates."""
    ecl = 23.4393 - 3.563e-7 * d
    return x, y * _cosd(ecl) - z * _sind(ecl), y * _sind(ecl) + z * _cosd(ecl)


def refraction(el: float) -> float:
    """How much the air lifts something near the horizon (degrees), Saemundsson's formula."""
    if el < -1.5:
        return 0.0
    return 1.02 / math.tan(math.radians(el + 10.3 / (el + 5.11))) / 60.0


def _apparent(vec_km, ts, lat, lon) -> tuple[float, float]:
    az, el, _ = look_angles(vec_km, ts, lat, lon)
    return az, el + refraction(el)


def moon(ts: float, lat: float, lon: float) -> tuple[float, float, float]:
    """(azimuth, apparent elevation, illuminated fraction 0..1) of the Moon from (lat, lon)."""
    d = _days(ts)
    mlon, mlat, r = _moon_ecliptic(d)
    x, y, z = r * _cosd(mlon) * _cosd(mlat), r * _sind(mlon) * _cosd(mlat), r * _sind(mlat)
    vec = tuple(c * EARTH_RADIUS_KM for c in _to_equatorial(x, y, z, d))
    az, el = _apparent(vec, ts, lat, lon)
    sx, sy, _, _, _ = _sun(d)
    elong = math.degrees(math.acos(max(-1.0, min(1.0, _cosd(mlat) * _cosd(mlon - math.degrees(math.atan2(sy, sx)))))))
    return az, el, (1 - _cosd(elong)) / 2


def sun(ts: float, lat: float, lon: float) -> tuple[float, float]:
    """(azimuth, apparent elevation) of the Sun."""
    d = _days(ts)
    sx, sy, sz, _, _ = _sun(d)
    vec = tuple(c * AU_KM for c in _to_equatorial(sx, sy, sz, d))
    return _apparent(vec, ts, lat, lon)


# Planets: N, i, w, a (AU), e, M at day 0 and their daily changes (Schlyter).
PLANETS = {
    "Mercury": ((48.3313, 3.24587e-5), (7.0047, 5.00e-8), (29.1241, 1.01444e-5), 0.387098, (0.205635, 5.59e-10),
                (168.6562, 4.0923344368)),
    "Venus": ((76.6799, 2.46590e-5), (3.3946, 2.75e-8), (54.8910, 1.38374e-5), 0.723330, (0.006773, -1.302e-9),
              (48.0052, 1.6021302244)),
    "Mars": ((49.5574, 2.11081e-5), (1.8497, -1.78e-8), (286.5016, 2.92961e-5), 1.523688, (0.093405, 2.516e-9),
             (18.6021, 0.5240207766)),
    "Jupiter": ((100.4542, 2.76854e-5), (1.3030, -1.557e-7), (273.8777, 1.64505e-5), 5.20256, (0.048498, 4.469e-9),
                (19.8950, 0.0830853001)),
    "Saturn": ((113.6634, 2.38980e-5), (2.4886, -1.081e-7), (339.3939, 2.97661e-5), 9.55475, (0.055546, -9.499e-9),
               (316.9670, 0.0334442282)),
}


def _planet_helio(name: str, d: float):
    (N0, N1), (i0, i1), (w0, w1), a, (e0, e1), (M0, M1) = PLANETS[name]
    x, y, z = _orbit(N0 + N1 * d, i0 + i1 * d, w0 + w1 * d, a, e0 + e1 * d, M0 + M1 * d)
    if name in ("Jupiter", "Saturn"):
        # The two giants pull on each other noticeably.
        Mj, Ms = _rev(19.8950 + 0.0830853001 * d), _rev(316.9670 + 0.0334442282 * d)
        lon = math.degrees(math.atan2(y, x))
        lat = math.degrees(math.atan2(z, math.hypot(x, y)))
        r = math.sqrt(x * x + y * y + z * z)
        if name == "Jupiter":
            lon += (-0.332 * _sind(2 * Mj - 5 * Ms - 67.6) - 0.056 * _sind(2 * Mj - 2 * Ms + 21)
                    + 0.042 * _sind(3 * Mj - 5 * Ms + 21) - 0.036 * _sind(Mj - 2 * Ms) + 0.022 * _cosd(Mj - Ms)
                    + 0.023 * _sind(2 * Mj - 3 * Ms + 52) - 0.016 * _sind(Mj - 5 * Ms - 69))
        else:
            lon += (0.812 * _sind(2 * Mj - 5 * Ms - 67.6) - 0.229 * _cosd(2 * Mj - 4 * Ms - 2)
                    + 0.119 * _sind(Mj - 2 * Ms - 3) + 0.046 * _sind(2 * Mj - 6 * Ms - 69)
                    + 0.014 * _sind(Mj - 3 * Ms + 32))
            lat += -0.020 * _cosd(2 * Mj - 4 * Ms - 2) + 0.018 * _sind(2 * Mj - 6 * Ms - 49)
        x, y, z = r * _cosd(lon) * _cosd(lat), r * _sind(lon) * _cosd(lat), r * _sind(lat)
    return x, y, z


def planet(name: str, ts: float, lat: float, lon: float) -> tuple[float, float, float]:
    """(azimuth, apparent elevation, magnitude) of a planet."""
    d = _days(ts)
    hx, hy, hz = _planet_helio(name, d)
    sx, sy, sz, _, _ = _sun(d)
    gx, gy, gz = hx + sx, hy + sy, hz + sz  # from the Earth
    vec = tuple(c * AU_KM for c in _to_equatorial(gx, gy, gz, d))
    az, el = _apparent(vec, ts, lat, lon)
    r = math.sqrt(hx * hx + hy * hy + hz * hz)
    R = math.sqrt(gx * gx + gy * gy + gz * gz)
    s = math.sqrt(sx * sx + sy * sy + sz * sz)
    fv = math.degrees(math.acos(max(-1.0, min(1.0, (r * r + R * R - s * s) / (2 * r * R)))))  # phase angle
    base = 5 * math.log10(r * R)
    mag = {
        "Mercury": -0.36 + base + 0.027 * fv + 2.2e-13 * fv ** 6,
        "Venus": -4.34 + base + 0.013 * fv + 4.2e-7 * fv ** 3,
        "Mars": -1.51 + base + 0.016 * fv,
        "Jupiter": -9.25 + base + 0.014 * fv,
        "Saturn": -9.0 + base + 0.044 * fv,
    }[name]
    return az, el, round(mag, 1)


def precess(ra_h: float, dec: float, ts: float) -> tuple[float, float]:
    """A star's year-2000 position moved on to now (the Earth's axis slowly wobbles)."""
    years = (ts - 946728000) / (365.25 * 86400)
    ra = math.radians(ra_h * 15)
    dra = (3.075 + 1.336 * math.sin(ra) * math.tan(math.radians(dec))) * years  # seconds of time
    ddec = 20.04 * math.cos(ra) * years  # seconds of arc
    return ra_h + dra / 3600, dec + ddec / 3600


def star(ra_h: float, dec: float, ts: float, lat: float, lon: float) -> tuple[float, float]:
    """(azimuth, apparent elevation) of a star."""
    ra_h, dec = precess(ra_h, dec, ts)
    lst = math.degrees(gmst(ts)) + lon
    ha = math.radians(lst - ra_h * 15)
    phi, dec_r = math.radians(lat), math.radians(dec)
    el = math.asin(math.sin(phi) * math.sin(dec_r) + math.cos(phi) * math.cos(dec_r) * math.cos(ha))
    az = math.atan2(-math.sin(ha) * math.cos(dec_r),
                    math.sin(dec_r) * math.cos(phi) - math.cos(dec_r) * math.sin(phi) * math.cos(ha))
    e = math.degrees(el)
    return (math.degrees(az) + 360) % 360, e + refraction(e)


def sky_objects(ts: float, lat: float, lon: float, min_el: float = -2.0) -> list[SkyObject]:
    """Everything bright in the sky right now that's above the horizon."""
    out = []
    az, el, lit = moon(ts, lat, lon)
    out.append(SkyObject("Moon", "moon", az, el, -12.7 * lit))
    az, el = sun(ts, lat, lon)
    out.append(SkyObject("Sun", "sun", az, el, -26.7))
    for name in PLANETS:
        az, el, mag = planet(name, ts, lat, lon)
        out.append(SkyObject(name, "planet", az, el, mag))
    for name, ra, dec, mag in STARS:
        az, el = star(ra, dec, ts, lat, lon)
        out.append(SkyObject(name, "star", az, el, mag))
    return [o for o in out if o.el >= min_el]


def separation(az1: float, el1: float, az2: float, el2: float) -> float:
    """Degrees between two directions in the sky."""
    c = (math.sin(math.radians(el1)) * math.sin(math.radians(el2))
         + math.cos(math.radians(el1)) * math.cos(math.radians(el2)) * math.cos(math.radians(az1 - az2)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def not_a_plane(ts: float, lat: float, lon: float) -> str:
    """At dusk and at night, the brightest 'plane' people see is usually Venus or Jupiter."""
    s_az, s_el = sun(ts, lat, lon)
    if s_el > -3:
        return ""
    bright = [o for o in sky_objects(ts, lat, lon, min_el=8) if o.kind == "planet" and o.mag <= -1.5]
    if not bright:
        return ""
    b = min(bright, key=lambda o: o.mag)
    from .geo import compass
    return f"✨ The very bright, steady light in the {compass(b.az)} is {b.name}, not a plane"


# --- planes crossing the Moon (or the Sun) ----------------------------------------------------

class Transit(NamedTuple):
    ac: object
    body: str       # "Moon" or "Sun"
    at: float       # when it's closest to the middle of the disc
    miss: float     # how far from the middle, in degrees (under ~0.26° = right across it)
    az: float
    el: float


def transits(cfg, planes, now: float, ground_ft: int = 0, horizon: float = 600, bodies=("Moon",),
             margin: float = 0.35) -> list[Transit]:
    """Planes due to pass in front of (or just beside) the Moon/Sun in the next ``horizon`` seconds,
    if they keep flying straight. Only worth it while the body is well up (and, for the Moon, not a sliver)."""
    from .geo import bearing_deg, distance_miles, offset

    lat, lon = cfg.home_lat, cfg.home_lon
    targets = []
    for body in bodies:
        if body == "Moon":
            a0, e0, lit = moon(now, lat, lon)
            a1, e1, _ = moon(now + horizon, lat, lon)
            if lit < 0.15:
                continue
            radius = MOON_RADIUS_DEG
        else:
            a0, e0 = sun(now, lat, lon)
            a1, e1 = sun(now + horizon, lat, lon)
            radius = SUN_RADIUS_DEG
        if min(e0, e1) < 4:
            continue
        targets.append((body, a0, e0, a1, e1, radius))
    if not targets:
        return []
    def where(ac, height_m, dt):
        """(azimuth, elevation) of the plane dt seconds from now, or None if it's too far or too low."""
        plat, plon = offset(ac.lat, ac.lon, ac.track, ac.speed_kt * 1.15078 * dt / 3600)
        ground = distance_miles(lat, lon, plat, plon) * 1609.344
        if ground > 80000:
            return None  # too far away to see against the Moon
        el = math.degrees(math.atan2(height_m - ground * ground / (2 * 6371000), max(1.0, ground)))
        return None if el < 3 else (bearing_deg(lat, lon, plat, plon), el)

    def gap(pos, target, dt):
        body, a0, e0, a1, e1, radius = target
        k = dt / horizon
        da = ((a1 - a0 + 540) % 360) - 180
        return separation(pos[0], pos[1], (a0 + da * k) % 360, e0 + (e1 - e0) * k)

    out = []
    for ac in planes:
        if not ac.speed_kt or ac.track is None or ac.alt_ft is None or ac.alt_ft <= 0:
            continue
        height_m = (ac.alt_ft - ground_ft) * 0.3048
        best = None
        for target in targets:
            # A close jet crosses the Moon in a fraction of a second: look every 2 s, then zoom in.
            coarse = None
            for step in range(0, int(horizon) + 1, 2):
                pos = where(ac, height_m, step)
                if pos is not None:
                    g = gap(pos, target, step)
                    if g < 4 and (coarse is None or g < coarse[0]):
                        coarse = (g, step)
            if coarse is None:
                continue
            for i in range(-50, 51):
                dt = coarse[1] + i * 0.05
                pos = where(ac, height_m, dt) if dt >= 0 else None
                if pos is None:
                    continue
                g = gap(pos, target, dt)
                if g <= target[5] + margin and (best is None or g < best.miss):
                    best = Transit(ac, target[0], now + dt, g, pos[0], pos[1])
        if best is not None:
            out.append(best)
    return sorted(out, key=lambda t: t.at)
