"""Gate numbers (optional): from a flight-status service, with your own free key.

The plane data planespotter uses (adsb.lol, adsbdb) has no gates: those come from airline and
airport schedules. Two services have a free allowance:

* FlightAware AeroAPI, Personal tier: up to $5 of free credit a month, and a flight lookup costs
  $0.005, so about 1,000 lookups a month for free (personal use).
* Aviationstack, Free plan: 100 lookups a month.

The phone looks a gate up only when you ask (tap 🚪 Gate, or "Where's my plane?") and keeps the
answer for two hours, so asking again costs nothing; it stops just short of the free allowance each
month. `python3 -m planespotter gates test UAL123` checks your key from the Mac.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .adsb import USER_AGENT

APIS = {
    "aeroapi": {"name": "FlightAware AeroAPI", "monthly": 900},   # $5 free ÷ $0.005 = 1,000
    "aviationstack": {"name": "Aviationstack", "monthly": 90},    # 100 free
}


def _when(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() if text else None
    except ValueError:
        return None


def _wall_clock(text, tz) -> float | None:
    """"2026-11-14T15:05:00+00:00" read as the time on the clocks in time zone tz, whatever its label says."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(text or ""))
    if not m or not tz:
        return None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(str(tz))
    except Exception:  # no such zone, or no time zone data on this computer
        return None
    y, mo, d, h, mi, sec = (int(x or 0) for x in m.groups())
    return datetime(y, mo, d, h, mi, sec, tzinfo=zone).timestamp()


def aviationstack_clock(data: list[dict], now: float):
    """How to read Aviationstack's times. They're labelled "+00:00" (UTC) but are each airport's local time
    (a pilot of one of the flights pointed this out on their issue tracker, and other apps found the same), so
    they're read as local time, unless this answer itself shows they really are UTC: the times have to fit
    what the flights are doing (the same check as the phone's)."""
    def reader(local):
        def read(text, tz):
            if not text:
                return None
            v = _wall_clock(text, tz) if local else None
            return v if v is not None else _when(text)
        return read

    def misfits(t):
        n = 0
        for f in data:
            d, a = f.get("departure") or {}, f.get("arrival") or {}
            left = t(d.get("actual_runway"), d.get("timezone")) or t(d.get("actual"), d.get("timezone"))
            landed = t(a.get("actual_runway"), a.get("timezone")) or t(a.get("actual"), a.get("timezone"))
            due = t(a.get("estimated"), a.get("timezone")) or t(a.get("scheduled"), a.get("timezone"))
            seen = _when((f.get("live") or {}).get("updated"))  # this one really is UTC
            n += bool(left and left > now + 1200) + bool(landed and landed > now + 1200)
            n += bool(f.get("flight_status") == "active" and due and due < now - 3 * 3600)
            n += bool(left and seen and left > seen + 1200)
        return n

    local, utc = reader(True), reader(False)
    return utc if misfits(utc) < misfits(local) else local


def trips_aeroapi(body: dict) -> list[dict]:
    """Every trip FlightAware lists for the flight number (its times are real UTC)."""
    out = []
    for f in body.get("flights") or []:
        if not isinstance(f, dict):
            continue
        o, d = f.get("origin") or {}, f.get("destination") or {}
        t = {k: _when(f.get(k)) for k in ("scheduled_out", "estimated_out", "actual_out", "scheduled_off", "estimated_off",
                                           "actual_off", "scheduled_on", "estimated_on", "actual_on", "scheduled_in",
                                           "estimated_in", "actual_in")}

        def late(secs, est, sch):
            return round(secs / 60) if isinstance(secs, (int, float)) else round((est - sch) / 60) if est and sch else None
        phase = ("cancelled" if f.get("cancelled") else "diverted" if f.get("diverted") else
                 "landed" if t["actual_in"] or t["actual_on"] else "air" if t["actual_off"] else
                 "taxi" if t["actual_out"] else "scheduled")
        out.append({"day": "", "codeshare": False,
                    "dep": {"code": o.get("code_iata") or o.get("code") or "", "gate": f.get("gate_origin") or "",
                            "terminal": f.get("terminal_origin") or "", "scheduled": t["scheduled_out"],
                            "estimated": t["estimated_out"], "actual": t["actual_out"],
                            "delay": late(f.get("departure_delay"), t["estimated_out"], t["scheduled_out"])},
                    "takeoff": {"scheduled": t["scheduled_off"], "estimated": t["estimated_off"], "actual": t["actual_off"]},
                    "landing": {"scheduled": t["scheduled_on"], "estimated": t["estimated_on"], "actual": t["actual_on"]},
                    "arr": {"code": d.get("code_iata") or d.get("code") or "", "city": d.get("city") or "",
                            "gate": f.get("gate_destination") or "", "terminal": f.get("terminal_destination") or "",
                            "baggage": f.get("baggage_claim") or "", "scheduled": t["scheduled_in"],
                            "estimated": t["estimated_in"], "actual": t["actual_in"],
                            "delay": late(f.get("arrival_delay"), t["estimated_in"], t["scheduled_in"])},
                    "status": f.get("status") or "", "phase": phase})
    return out


def trips_aviationstack(body: dict, now: float) -> list[dict]:
    """Every trip Aviationstack lists for the flight number, its times read in each airport's time zone."""
    data = [f for f in body.get("data") or [] if isinstance(f, dict)]
    t = aviationstack_clock(data, now)
    out = []
    for f in data:
        dep, arr, st = f.get("departure") or {}, f.get("arrival") or {}, f.get("flight_status") or ""
        def dt(k): return t(dep.get(k), dep.get("timezone"))
        def at(k): return t(arr.get(k), arr.get("timezone"))
        phase = ("cancelled" if st == "cancelled" else "diverted" if st == "diverted" else
                 "landed" if st == "landed" or at("actual") or at("actual_runway") else
                 "air" if st in ("active", "incident") or dt("actual_runway") else "taxi" if dt("actual") else "scheduled")
        out.append({"day": f.get("flight_date") or "", "codeshare": bool((f.get("flight") or {}).get("codeshared")),
                    "dep": {"code": dep.get("iata") or dep.get("icao") or "", "gate": dep.get("gate") or "",
                            "terminal": dep.get("terminal") or "", "scheduled": dt("scheduled"),
                            "estimated": dt("estimated"), "actual": dt("actual"), "delay": dep.get("delay")},
                    "takeoff": {"scheduled": None, "estimated": dt("estimated_runway"), "actual": dt("actual_runway")},
                    "landing": {"scheduled": None, "estimated": at("estimated_runway"), "actual": at("actual_runway")},
                    "arr": {"code": arr.get("iata") or arr.get("icao") or "", "city": arr.get("airport") or "",
                            "gate": arr.get("gate") or "", "terminal": arr.get("terminal") or "",
                            "baggage": arr.get("baggage") or "", "scheduled": at("scheduled"), "estimated": at("estimated"),
                            "actual": at("actual"), "delay": arr.get("delay")},
                    "status": st, "phase": phase})
    return out


def trip_span(r: dict) -> tuple[float | None, float | None]:
    d, a, k, l = r["dep"], r["arr"], r.get("takeoff") or {}, r.get("landing") or {}
    start = d["actual"] or k.get("actual") or d["estimated"] or k.get("estimated") or d["scheduled"] or k.get("scheduled")
    end = (a["actual"] or l.get("actual") or a["estimated"] or l.get("estimated") or a["scheduled"] or l.get("scheduled")
           or (start + 6 * 3600 if start else None))
    return start, end


def pick(trips: list[dict], now: float) -> dict | None:
    """The trip that fits now: under way, about to leave, or just arrived (the phone's rule). None when none
    is anywhere near now: no answer is better than another day's."""
    if any(not r["codeshare"] for r in trips):
        trips = [r for r in trips if not r["codeshare"]]  # the airline that flies it
    ranked = []
    for r in trips:
        start, end = trip_span(r)
        if not start or start - now > 26 * 3600 or now - end > 12 * 3600:
            continue
        away = ((start - 3 * 3600 - now) if now < start - 3 * 3600 else (now - end - 3600) if now > end + 3600 else 0) / 3600
        gone = bool(r["dep"]["actual"] or r["takeoff"]["actual"]) or r["phase"] in ("air", "landed", "diverted")
        done = r["phase"] in ("landed", "diverted", "cancelled") or bool(r["arr"]["actual"] or r["landing"]["actual"])
        ranked.append(((away, 0 if gone and not done else 1 if not gone else 2, abs(start - now)), r))
    if ranked:
        return min(ranked, key=lambda x: x[0])[1]
    days = {datetime.fromtimestamp(now).date().isoformat(), datetime.fromtimestamp(now - 86400).date().isoformat()}
    untimed = [r for r in trips if not trip_span(r)[0] and r["day"] in days]
    untimed.sort(key=lambda r: r["day"], reverse=True)
    return untimed[0] if untimed else None


def listed_as(callsign: str) -> str:
    """The flight number schedules list it under: Horizon flies Alaska's 2000s as QXE, same number."""
    return "ASA" + callsign[3:] if re.fullmatch(r"QXE\d+", callsign) else callsign


def lookup(api: str, key: str, callsign: str, now: float | None = None, fetch=None) -> dict:
    """{"dep": ..., "arr": ..., "status": ...}, or {"error": "key" | "limit" | "busy" | "other", "detail": ...},
    or {"none": True} when the service doesn't know the flight."""
    now = now or time.time()
    fetch = fetch or _fetch
    cs = callsign.strip().upper()
    if api == "aeroapi":
        url = "https://aeroapi.flightaware.com/aeroapi/flights/" + urllib.parse.quote(cs)
        status, body = fetch(url, {"x-apikey": key})
    elif api == "aviationstack":
        url = ("https://api.aviationstack.com/v1/flights?" +
               urllib.parse.urlencode({"access_key": key, "flight_icao": listed_as(cs)}))
        status, body = fetch(url, {})
    else:
        return {"error": "other", "detail": f"unknown gate service {api!r}"}
    if not isinstance(body, dict):
        return {"error": "other", "detail": f"HTTP {status}"}
    err = body.get("error")
    if status in (401, 403) or (isinstance(err, dict) and str(err.get("code")) in (
            "invalid_access_key", "missing_access_key", "inactive_user", "101", "https_access_restricted", "105")):
        return {"error": "key", "detail": json.dumps(err or body)[:200]}
    if status == 429 or (isinstance(err, dict) and str(err.get("code")) in (
            "usage_limit_reached", "104", "rate_limit_reached", "106")):
        return {"error": "limit", "detail": json.dumps(err or body)[:200]}
    if status >= 400 or err:
        return {"error": "other", "detail": json.dumps(err or body)[:200]}
    trips = trips_aeroapi(body) if api == "aeroapi" else trips_aviationstack(body, now)
    return pick(trips, now) or {"none": True}


def _fetch(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, None


def describe(info: dict) -> str:
    """'LAX → SFO · leaves from gate 71A (terminal 7) · arrives at gate F12 (terminal 3)'."""
    if info.get("none"):
        return "the service doesn't list a trip for this flight around now"
    def gate(side, verb):
        g, t = side.get("gate"), side.get("terminal")
        return f"{verb} gate {g}" + (f" (terminal {t})" if t else "") if g else f"{verb} gate: not known yet"
    dep, arr = info["dep"], info["arr"]
    parts = [f"{dep['code'] or '?'} → {arr['code'] or '?'}", gate(dep, "leaves from"), gate(arr, "arrives at")]
    leaves = (info.get("takeoff") or {}).get("estimated") or dep.get("estimated") or dep.get("scheduled")
    if leaves:
        parts.append("leaves " + datetime.fromtimestamp(leaves).strftime("%a %H:%M"))
    if isinstance(dep.get("delay"), (int, float)):
        d = dep["delay"]
        parts.append(f"{d} min late" if d >= 5 else f"{-d} min early" if d <= -5 else "on time")
    if arr.get("baggage"):
        parts.append(f"bags at carousel {arr['baggage']}")
    if info.get("phase") == "diverted":
        parts.append("DIVERTED: the arrival gate is for the airport it was going to")
    if info.get("status"):
        parts.append(f"status: {info['status']}")
    return " · ".join(parts)
