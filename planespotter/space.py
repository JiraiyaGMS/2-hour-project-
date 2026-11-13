"""When the space station (and other bright satellites) fly over your house, and whether you'll see them.

Orbits come from CelesTrak (free "two-line elements", refreshed twice a day).
Positions are worked out here with SGP4, the standard model those elements are
made for. A pass is *visible* when the satellite is high enough, still in
sunlight, and your sky is dark: the space station then shows up as a very
bright, steady, fast-moving star.
"""

from __future__ import annotations

import logging
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from .adsb import USER_AGENT
from .geo import compass

log = logging.getLogger(__name__)

SATELLITES = {25544: "Space station (ISS)", 48274: "Chinese space station (Tiangong)", 20580: "Hubble"}
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={n}&FORMAT=TLE"
ISS_FALLBACK = "https://api.wheretheiss.at/v1/satellites/25544/tles"
REFRESH_S = 12 * 3600
MIN_EL = 10.0           # a pass starts and ends 10° above the horizon
STEP_S = 20

# WGS-72 constants, as SGP4 expects.
MU = 398600.8
RE = 6378.135
XKE = 60.0 / math.sqrt(RE ** 3 / MU)
J2, J3, J4 = 0.001082616, -0.00000253881, -0.00000165597
J3OJ2 = J3 / J2
TWO_PI = 2 * math.pi
AU_KM = 149597870.7


# --- two-line elements ----------------------------------------------------------------

def _implied(field: str) -> float:
    """'15334-3' -> 0.15334e-3 (the TLE's way of writing small numbers)."""
    field = field.strip()
    if not field:
        return 0.0
    sign = -1.0 if field[0] == "-" else 1.0
    field = field.lstrip("+-")
    mantissa, exponent = field[:-2], field[-2:]
    return sign * float(f"0.{mantissa}") * 10 ** int(exponent)


class Satellite:
    """One satellite's orbit, ready to say where it is at any time (SGP4, near-Earth orbits)."""

    def __init__(self, line1: str, line2: str, name: str = ""):
        self.name = name
        self.number = int(line1[2:7])
        year = int(line1[18:20])
        year += 2000 if year < 57 else 1900
        day = float(line1[20:32])
        self.epoch = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() + (day - 1) * 86400
        self.bstar = _implied(line1[53:61])
        deg = math.pi / 180
        self.inclo = float(line2[8:16]) * deg
        self.nodeo = float(line2[17:25]) * deg
        self.ecco = float("0." + line2[26:33].strip())
        self.argpo = float(line2[34:42]) * deg
        self.mo = float(line2[43:51]) * deg
        no_kozai = float(line2[52:63]) * TWO_PI / 1440.0  # radians per minute
        self._init(no_kozai)

    def _init(self, no_kozai: float) -> None:
        e, i = self.ecco, self.inclo
        cosio = math.cos(i)
        cosio2 = cosio * cosio
        eccsq = e * e
        omeosq = 1 - eccsq
        rteosq = math.sqrt(omeosq)
        ak = (XKE / no_kozai) ** (2 / 3)
        d1 = 0.75 * J2 * (3 * cosio2 - 1) / (rteosq * omeosq)
        dl = d1 / (ak * ak)
        adel = ak * (1 - dl * dl - dl * (1 / 3 + 134 * dl * dl / 81))
        dl = d1 / (adel * adel)
        self.no = no = no_kozai / (1 + dl)
        ao = (XKE / no) ** (2 / 3)
        sinio = math.sin(i)
        po = ao * omeosq
        con42 = 1 - 5 * cosio2
        self.con41 = con41 = -con42 - cosio2 - cosio2
        posq = po * po
        rp = ao * (1 - e)
        self.isimp = rp < (220 / RE + 1)
        ss = 78 / RE + 1
        qzms2t = ((120 - 78) / RE) ** 4
        sfour, qzms24 = ss, qzms2t
        perige = (rp - 1) * RE
        if perige < 156:
            sfour = perige - 78 if perige >= 98 else 20
            qzms24 = ((120 - sfour) / RE) ** 4
            sfour = sfour / RE + 1
        pinvsq = 1 / posq
        tsi = 1 / (ao - sfour)
        self.eta = eta = ao * e * tsi
        etasq = eta * eta
        eeta = e * eta
        psisq = abs(1 - etasq)
        coef = qzms24 * tsi ** 4
        coef1 = coef / psisq ** 3.5
        cc2 = coef1 * no * (ao * (1 + 1.5 * etasq + eeta * (4 + etasq))
                            + 0.375 * J2 * tsi / psisq * con41 * (8 + 3 * etasq * (8 + etasq)))
        self.cc1 = cc1 = self.bstar * cc2
        cc3 = -2 * coef * tsi * J3OJ2 * no * sinio / e if e > 1e-4 else 0.0
        self.x1mth2 = x1mth2 = 1 - cosio2
        self.cc4 = 2 * no * coef1 * ao * omeosq * (
            eta * (2 + 0.5 * etasq) + e * (0.5 + 2 * etasq)
            - J2 * tsi / (ao * psisq) * (-3 * con41 * (1 - 2 * eeta + etasq * (1.5 - 0.5 * eeta))
                                         + 0.75 * x1mth2 * (2 * etasq - eeta * (1 + etasq)) * math.cos(2 * self.argpo)))
        self.cc5 = 2 * coef1 * ao * omeosq * (1 + 2.75 * (etasq + eeta) + eeta * etasq)
        cosio4 = cosio2 * cosio2
        temp1 = 1.5 * J2 * pinvsq * no
        temp2 = 0.5 * temp1 * J2 * pinvsq
        temp3 = -0.46875 * J4 * pinvsq * pinvsq * no
        self.mdot = no + 0.5 * temp1 * rteosq * con41 + 0.0625 * temp2 * rteosq * (13 - 78 * cosio2 + 137 * cosio4)
        self.argpdot = (-0.5 * temp1 * con42 + 0.0625 * temp2 * (7 - 114 * cosio2 + 395 * cosio4)
                        + temp3 * (3 - 36 * cosio2 + 49 * cosio4))
        xhdot1 = -temp1 * cosio
        self.nodedot = xhdot1 + (0.5 * temp2 * (4 - 19 * cosio2) + 2 * temp3 * (3 - 7 * cosio2)) * cosio
        self.omgcof = self.bstar * cc3 * math.cos(self.argpo)
        self.xmcof = -2 / 3 * coef * self.bstar / eeta if e > 1e-4 else 0.0
        self.nodecf = 3.5 * omeosq * xhdot1 * cc1
        self.t2cof = 1.5 * cc1
        den = 1 + cosio if abs(cosio + 1) > 1.5e-12 else 1.5e-12
        self.xlcof = -0.25 * J3OJ2 * sinio * (3 + 5 * cosio) / den
        self.aycof = -0.5 * J3OJ2 * sinio
        self.delmo = (1 + eta * math.cos(self.mo)) ** 3
        self.sinmao = math.sin(self.mo)
        self.x7thm1 = 7 * cosio2 - 1
        if not self.isimp:
            cc1sq = cc1 * cc1
            self.d2 = d2 = 4 * ao * tsi * cc1sq
            temp = d2 * tsi * cc1 / 3
            self.d3 = d3 = (17 * ao + sfour) * temp
            self.d4 = d4 = 0.5 * temp * ao * tsi * (221 * ao + 31 * sfour) * cc1
            self.t3cof = d2 + 2 * cc1sq
            self.t4cof = 0.25 * (3 * d3 + cc1 * (12 * d2 + 10 * cc1sq))
            self.t5cof = 0.2 * (3 * d4 + 12 * cc1 * d3 + 6 * d2 * d2 + 15 * cc1sq * (2 * d2 + cc1sq))

    def position(self, ts: float) -> tuple[float, float, float]:
        """Where it is at unix time ``ts``: km, in the TEME frame (Earth-centred, not rotating)."""
        t = (ts - self.epoch) / 60.0
        xmdf = self.mo + self.mdot * t
        argpdf = self.argpo + self.argpdot * t
        nodedf = self.nodeo + self.nodedot * t
        argpm, mm = argpdf, xmdf
        t2 = t * t
        nodem = nodedf + self.nodecf * t2
        tempa = 1 - self.cc1 * t
        tempe = self.bstar * self.cc4 * t
        templ = self.t2cof * t2
        if not self.isimp:
            delomg = self.omgcof * t
            delm = self.xmcof * ((1 + self.eta * math.cos(xmdf)) ** 3 - self.delmo)
            temp = delomg + delm
            mm = xmdf + temp
            argpm = argpdf - temp
            t3 = t2 * t
            t4 = t3 * t
            tempa = tempa - self.d2 * t2 - self.d3 * t3 - self.d4 * t4
            tempe = tempe + self.bstar * self.cc5 * (math.sin(mm) - self.sinmao)
            templ = templ + self.t3cof * t3 + t4 * (self.t4cof + t * self.t5cof)
        am = (XKE / self.no) ** (2 / 3) * tempa * tempa
        nm = XKE / am ** 1.5
        em = max(1e-6, self.ecco - tempe)
        mm = mm + self.no * templ
        xlm = mm + argpm + nodem
        nodem %= TWO_PI
        argpm %= TWO_PI
        xlm %= TWO_PI
        mm = (xlm - argpm - nodem) % TWO_PI
        sinim, cosim = math.sin(self.inclo), math.cos(self.inclo)

        axnl = em * math.cos(argpm)
        temp = 1 / (am * (1 - em * em))
        aynl = em * math.sin(argpm) + temp * self.aycof
        xl = mm + argpm + nodem + temp * self.xlcof * axnl
        u = (xl - nodem) % TWO_PI
        eo1, tem5, ktr = u, 9999.9, 1
        sineo1 = coseo1 = 0.0
        while abs(tem5) >= 1e-12 and ktr <= 10:  # Kepler's equation
            sineo1, coseo1 = math.sin(eo1), math.cos(eo1)
            tem5 = 1 - coseo1 * axnl - sineo1 * aynl
            tem5 = (u - aynl * coseo1 + axnl * sineo1 - eo1) / tem5
            tem5 = max(-0.95, min(0.95, tem5))
            eo1 += tem5
            ktr += 1
        ecose = axnl * coseo1 + aynl * sineo1
        esine = axnl * sineo1 - aynl * coseo1
        el2 = axnl * axnl + aynl * aynl
        pl = am * (1 - el2)
        rl = am * (1 - ecose)
        betal = math.sqrt(1 - el2)
        temp = esine / (1 + betal)
        sinu = am / rl * (sineo1 - aynl - axnl * temp)
        cosu = am / rl * (coseo1 - axnl + aynl * temp)
        su = math.atan2(sinu, cosu)
        sin2u = (cosu + cosu) * sinu
        cos2u = 1 - 2 * sinu * sinu
        temp = 1 / pl
        temp1 = 0.5 * J2 * temp
        temp2 = temp1 * temp
        mrt = rl * (1 - 1.5 * temp2 * betal * self.con41) + 0.5 * temp1 * self.x1mth2 * cos2u
        su = su - 0.25 * temp2 * self.x7thm1 * sin2u
        xnode = nodem + 1.5 * temp2 * cosim * sin2u
        xinc = self.inclo + 1.5 * temp2 * cosim * sinim * cos2u
        sinsu, cossu = math.sin(su), math.cos(su)
        snod, cnod = math.sin(xnode), math.cos(xnode)
        sini, cosi = math.sin(xinc), math.cos(xinc)
        xmx, xmy = -snod * cosi, cnod * cosi
        ux = xmx * sinsu + cnod * cossu
        uy = xmy * sinsu + snod * cossu
        uz = sini * sinsu
        return mrt * ux * RE, mrt * uy * RE, mrt * uz * RE


# --- where to look --------------------------------------------------------------------

def gmst(ts: float) -> float:
    """Greenwich sidereal angle (radians): how far the Earth has turned."""
    jd = ts / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    seconds = (-6.2e-6 * t ** 3 + 0.093104 * t ** 2 + (876600 * 3600 + 8640184.812866) * t + 67310.54841)
    return (seconds * math.pi / 180 / 240) % TWO_PI


def observer_xyz(lat: float, lon: float, height_km: float = 0.0) -> tuple[float, float, float]:
    """Your position, Earth-centred and turning with the Earth (km)."""
    f = 1 / 298.26
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    n = RE / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((n + height_km) * math.cos(phi) * math.cos(lam), (n + height_km) * math.cos(phi) * math.sin(lam),
            (n * (1 - e2) + height_km) * math.sin(phi))


def look_angles(sat_teme, ts: float, lat: float, lon: float, obs=None) -> tuple[float, float, float]:
    """(azimuth, elevation, range km) of a satellite seen from (lat, lon)."""
    g = gmst(ts)
    x, y, z = sat_teme
    ex, ey = math.cos(g) * x + math.sin(g) * y, -math.sin(g) * x + math.cos(g) * y
    ox, oy, oz = obs or observer_xyz(lat, lon)
    rx, ry, rz = ex - ox, ey - oy, z - oz
    phi, lam = math.radians(lat), math.radians(lon)
    east = -math.sin(lam) * rx + math.cos(lam) * ry
    north = -math.sin(phi) * math.cos(lam) * rx - math.sin(phi) * math.sin(lam) * ry + math.cos(phi) * rz
    up = math.cos(phi) * math.cos(lam) * rx + math.cos(phi) * math.sin(lam) * ry + math.sin(phi) * rz
    rng = math.sqrt(rx * rx + ry * ry + rz * rz)
    return (math.degrees(math.atan2(east, north)) + 360) % 360, math.degrees(math.asin(up / rng)), rng


def sun_vector(ts: float) -> tuple[float, float, float]:
    """Direction of the sun from the Earth's centre (unit vector, equatorial)."""
    d = ts / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * d)
    return math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam)


def sunlit(sat_teme, ts: float) -> bool:
    """Is the satellite in sunshine (not in the Earth's shadow)?"""
    sx, sy, sz = sun_vector(ts)
    x, y, z = sat_teme
    along = x * sx + y * sy + z * sz
    if along > 0:
        return True  # on the sunny side of the Earth
    perp = math.sqrt(max(0.0, x * x + y * y + z * z - along * along))
    return perp > RE + 20  # beside the Earth's shadow


def passes(sat: Satellite, lat: float, lon: float, start: float, hours: float = 72,
           sun_alt: Callable[[float], float] | None = None) -> list[dict]:
    """Every pass above 10° in the next ``hours``, with whether (and when) you can see it."""
    from .sky import sun_position
    sun_alt = sun_alt or (lambda t: sun_position(lat, lon, t)[0])
    obs = observer_xyz(lat, lon)
    out, current = [], None
    t = start
    end = start + hours * 3600
    while t <= end:
        pos = sat.position(t)
        az, el, _ = look_angles(pos, t, lat, lon, obs)
        if el >= MIN_EL:
            visible = sunlit(pos, t) and sun_alt(t) < -6
            if current is None:
                current = {"name": sat.name, "number": sat.number, "start": t, "start_az": az, "max_el": el,
                           "max_t": t, "max_az": az, "visible_from": None, "visible_to": None}
            if el > current["max_el"]:
                current.update(max_el=el, max_t=t, max_az=az)
            if visible:
                current["visible_from"] = current["visible_from"] or t
                current["visible_to"] = t
            current.update(end=t, end_az=az)
        elif current is not None:
            out.append(_finish(current))
            current = None
        t += STEP_S if el > -15 else 60  # well below the horizon: it can't rise for a minute or more
    if current is not None:
        out.append(_finish(current))
    return out


def _finish(p: dict) -> dict:
    p["visible"] = p["visible_from"] is not None and p["max_el"] >= MIN_EL
    p["max_el"] = round(p["max_el"])
    for k in ("start_az", "max_az", "end_az"):
        p[k.replace("_az", "_dir")] = compass(p[k])
        p[k] = round(p[k])
    p["minutes"] = round((p["end"] - p["start"]) / 60, 1)
    return p


# --- fetching orbits --------------------------------------------------------------------

def fetch_tle(number: int) -> tuple[str, str]:
    req = urllib.request.Request(TLE_URL.format(n=number), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        lines = [x.strip() for x in resp.read().decode("utf-8", "replace").splitlines() if x.strip()]
    one = next(x for x in lines if x.startswith("1 "))
    two = next(x for x in lines if x.startswith("2 "))
    return one, two


def fetch_tle_fallback(number: int) -> tuple[str, str]:
    """The space station's orbit from a second source, if CelesTrak is down."""
    if number != 25544:
        raise OSError("no fallback source")
    import json
    req = urllib.request.Request(ISS_FALLBACK, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return data["line1"], data["line2"]


class SpaceWatch:
    """Satellites' orbits (refreshed twice a day) and their passes over home (worked out hourly)."""

    def __init__(self, cfg, store_factory, fetch=fetch_tle, fallback=fetch_tle_fallback, clock=time.time,
                 satellites: dict[int, str] | None = None):
        self.cfg, self.store_factory, self.fetch, self.fallback, self.clock = cfg, store_factory, fetch, fallback, clock
        self.satellites = satellites or SATELLITES
        self._sats: dict[int, Satellite] = {}
        self._passes: list[dict] = []
        self._passes_at = -1e12

    def satellite(self, number: int) -> Satellite | None:
        now = self.clock()
        sat = self._sats.get(number)
        if sat is not None and now - getattr(sat, "loaded_at", 0) < REFRESH_S:
            return sat
        store = self.store_factory()
        cached = store.cache_get(f"tle:{number}", now)
        lines = tuple(cached["lines"]) if cached else None
        if lines is None:
            for source in (self.fetch, self.fallback):
                try:
                    lines = source(number)
                    store.cache_put(f"tle:{number}", {"lines": list(lines)}, now, REFRESH_S)
                    break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, StopIteration) as exc:
                    log.info("couldn't get the orbit of %s: %s", self.satellites.get(number, number), exc)
        if lines is None:
            return sat  # keep using an older orbit if we have one
        sat = Satellite(lines[0], lines[1], self.satellites.get(number, str(number)))
        sat.loaded_at = now
        self._sats[number] = sat
        return sat

    def upcoming(self, hours: float = 72) -> list[dict]:
        """Passes over home in the next few days, soonest first (worked out at most hourly)."""
        now = self.clock()
        if now - self._passes_at > 3600 or not self._passes:
            found = []
            for number in self.satellites:
                sat = self.satellite(number)
                if sat is not None:
                    found += passes(sat, self.cfg.home_lat, self.cfg.home_lon, now - 900, hours)
            self._passes = sorted(found, key=lambda p: p["start"])
            self._passes_at = now
        return [p for p in self._passes if p["end"] >= now]

    def track(self, number: int, start: float, seconds: float = 900, step: float = 10) -> list[list[float]]:
        """[[time, azimuth, elevation], ...] for drawing a satellite as it crosses the sky."""
        sat = self.satellite(number)
        if sat is None:
            return []
        obs = observer_xyz(self.cfg.home_lat, self.cfg.home_lon)
        out = []
        t = start
        while t <= start + seconds:
            az, el, _ = look_angles(sat.position(t), t, self.cfg.home_lat, self.cfg.home_lon, obs)
            out.append([round(t), round(az, 2), round(el, 2)])
            t += step
        return out


def pass_text(p: dict, cfg) -> str:
    """'Space station (ISS): 7:42 pm, from the WSW, up to 65°, about 6 minutes'."""
    when = cfg.local(p["visible_from"] or p["start"]).strftime("%I:%M %p").lstrip("0").lower()
    return (f"{p['name']}: {when}, appears in the {p['start_dir']}, up to {p['max_el']}° high in the "
            f"{p['max_dir']}, about {round(p['minutes'])} minutes")
