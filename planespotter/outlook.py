"""Today's outlook: will planes leave contrails, and when's the best time to look up?

The forecast comes from Open-Meteo (free, no key): temperature and humidity
up where airliners cruise (about 30,000-39,000 ft), and cloud cover, hour by
hour. planespotter only sends your position rounded to about 10 km.

Contrails form when the air up there is colder than about -40°C. They last
(and spread into long white streaks) when that air is moist enough to be
saturated over ice; in drier air they vanish within seconds.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import time as clock_time
from datetime import timedelta

from .adsb import USER_AGENT

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LEVELS = (300, 250, 200)  # hPa: about 30,000, 34,000 and 39,000 ft
CACHE_S = 3600
CONTRAIL_T = -40.0        # °C: colder than this, engine exhaust freezes into a contrail
HISTORY_DAYS = 14


def fetch_forecast(lat: float, lon: float) -> dict:
    hourly = [f"{k}_{lvl}hPa" for lvl in LEVELS for k in ("temperature", "relative_humidity")]
    hourly += ["cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"]
    query = urllib.parse.urlencode({"latitude": f"{lat:.1f}", "longitude": f"{lon:.1f}", "hourly": ",".join(hourly),
                                    "forecast_days": 3, "timeformat": "unixtime"})
    req = urllib.request.Request(f"{FORECAST_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def ice_humidity(rh_water: float, t_c: float) -> float:
    """Relative humidity over ice (%), from the usual humidity over water (Magnus formulas)."""
    over_water = 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))
    over_ice = 6.112 * math.exp(22.46 * t_c / (272.62 + t_c))
    return rh_water * over_water / over_ice


def contrail_kind(hour: dict) -> str:
    """'long' (persistent, spreading), 'some' (lasting a while), 'short' (gone in seconds) or 'none'."""
    best = "none"
    rank = {"none": 0, "short": 1, "some": 2, "long": 3}
    for lvl in LEVELS:
        t, rh = hour.get(f"temperature_{lvl}hPa"), hour.get(f"relative_humidity_{lvl}hPa")
        if t is None or rh is None or t > CONTRAIL_T:
            continue
        rhi = ice_humidity(rh, t)
        kind = "long" if rhi >= 100 else "some" if rhi >= 70 else "short"
        if rank[kind] > rank[best]:
            best = kind
    return best


class Outlook:
    """The hourly forecast near home (cached for an hour).

    With ``background`` (the default) it downloads in a separate thread and answers straight away
    with what it has, so a slow network (a hotel's, say) never holds anything up."""

    def __init__(self, cfg, fetch=fetch_forecast, clock=time.time, background: bool = True):
        self.cfg, self.fetch, self.clock, self.background = cfg, fetch, clock, background
        self._data, self._at = None, -1e12
        self._busy = threading.Lock()
        self._failing = False

    def _refresh(self) -> None:
        try:
            self._data = self.fetch(self.cfg.home_lat, self.cfg.home_lon)
            self._failing = False
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # Say so once; keep trying quietly (every 10 minutes) after that.
            (log.debug if self._failing else log.info)("couldn't get the forecast (will keep trying): %s", exc)
            self._failing = True
        finally:
            if self._busy.locked():
                self._busy.release()

    def hours(self) -> list[dict]:
        """[{t, temperature_250hPa, ..., cloud_cover_low, ...}, ...] for the next 3 days (or [] if offline)."""
        now = self.clock()
        if now - self._at > (CACHE_S if self._data else 600) and self._busy.acquire(blocking=False):
            self._at = now
            if self.background:
                threading.Thread(target=self._refresh, name="forecast", daemon=True).start()
            else:
                self._refresh()
        if not self._data:
            return []
        h = self._data.get("hourly") or {}
        times = h.get("time") or []
        return [{"t": t, **{k: v[i] for k, v in h.items() if k != "time" and i < len(v)}} for i, t in enumerate(times)]


def contrail_text(hours: list[dict], cfg, now: float) -> str:
    """'Long-lasting contrails likely this afternoon' (daytime hours left today), or ''."""
    today = cfg.local(now).date()
    parts = {"morning": range(6, 12), "afternoon": range(12, 18), "evening": range(18, 22)}
    found = {}
    for h in hours:
        local = cfg.local(h["t"])
        if local.date() != today or h["t"] < now - 3600:
            continue
        for name, span in parts.items():
            if local.hour in span:
                found.setdefault(name, Counter())[contrail_kind(h)] += 1
    if not found:
        return ""
    words = {"long": "Long-lasting contrails likely", "some": "Contrails that linger for a while",
             "short": "Only short contrails (gone in seconds)", "none": "No contrails expected"}
    first = next(iter(found))
    kind = found[first].most_common(1)[0][0]
    when = "this " + first if first != "evening" else "tonight"
    later = [n for n in list(found)[1:] if found[n].most_common(1)[0][0] != kind]
    text = f"{words[kind]} {when}"
    if later:
        k2 = found[later[0]].most_common(1)[0][0]
        text += f"; {words[k2][0].lower() + words[k2][1:]} {'tonight' if later[0] == 'evening' else 'this ' + later[0]}"
    return "✈️ " + text


def hourly_rates(store, cfg, now: float) -> dict[int, float]:
    """Average planes over your zone in each hour of the day, from the last two weeks."""
    since = now - HISTORY_DAYS * 86400
    rows = [r[0] for r in store.db.execute("SELECT first_seen FROM passes WHERE first_seen >= ?", (since,))]
    if not rows:
        return {}
    days = max(1, len({cfg.local(t).date() for t in rows}))
    counts = Counter(cfg.local(t).hour for t in rows)
    return {h: counts[h] / days for h in range(24)}


def visibility(hour: dict | None, sun_alt: float) -> tuple[float, str]:
    """How easy planes are to see in this hour (0..1) and a word for the sky."""
    clouds = 0
    if hour:
        clouds = max(hour.get("cloud_cover_low") or 0, (hour.get("cloud_cover_mid") or 0) * 0.8)
    if clouds >= 80:
        factor, word = 0.25, "cloudy"
    elif clouds >= 45:
        factor, word = 0.7, "some cloud"
    else:
        factor, word = 1.0, "clear" if clouds < 20 else "mostly clear"
    if sun_alt < -6:
        factor *= 0.6
        word += ", dark"
    return factor, word


def best_time(store, cfg, now: float, hours: list[dict], day=None) -> dict | None:
    """The best hour to look up on ``day`` (today by default): most planes you'd actually be able to see."""
    from .sky import sun_position

    rates = hourly_rates(store, cfg, now)
    if not rates or not any(rates.values()):
        return None
    day = day or cfg.local(now).date()
    by_hour = {cfg.local(h["t"]).replace(minute=0, second=0, microsecond=0): h for h in hours}
    best = None
    for hour in range(6, 23):
        start = cfg.at(day, clock_time(hour))
        if start + 3600 <= now:
            continue
        fc = by_hour.get(cfg.local(start))
        sun_alt = sun_position(cfg.home_lat, cfg.home_lon, start + 1800)[0]
        factor, word = visibility(fc, sun_alt)
        score = rates.get(hour, 0) * factor
        if best is None or score > best["score"]:
            best = {"start": start, "end": start + 3600, "hour": hour, "planes": round(rates.get(hour, 0)),
                    "sky": word, "score": score}
    if best is None or best["planes"] < 1:
        return None
    return best


def best_time_text(best: dict | None, cfg, now: float) -> str:
    if not best:
        return ""
    fmt = lambda ts: cfg.local(ts).strftime("%I %p").lstrip("0").lower().replace(" ", "")  # noqa: E731
    span = f"{fmt(best['start'])}–{fmt(best['end'])}"
    if best["start"] <= now:
        span = f"now until {fmt(best['end'])}"
    return f"👀 Best time to look up today: {span} (usually about {best['planes']} plane{'s' if best['planes'] != 1 else ''}, {best['sky']})"


def daily_best(store, cfg, now: float, hours: list[dict], days: int = 3) -> list[dict]:
    """The best hour for each of the next few days (for the calendar)."""
    out = []
    today = cfg.local(now).date()
    for i in range(days):
        b = best_time(store, cfg, now, hours, today + timedelta(days=i))
        if b:
            out.append(b)
    return out
