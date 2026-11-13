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


def pick_aeroapi(flights: list[dict], now: float) -> dict | None:
    """The flight that matters now: in the air, else one that arrived in the last 12 hours, else the next one."""
    in_air = [f for f in flights if (f.get("actual_off") and not f.get("actual_on"))
              or (f.get("actual_out") and not f.get("actual_in") and not f.get("actual_on"))]
    if in_air:
        return in_air[0]
    arrived = [f for f in flights if _when(f.get("actual_in") or f.get("actual_on"))]
    arrived.sort(key=lambda f: _when(f.get("actual_in") or f.get("actual_on")), reverse=True)
    if arrived and now - _when(arrived[0].get("actual_in") or arrived[0].get("actual_on")) < 12 * 3600:
        return arrived[0]
    upcoming = [f for f in flights if not f.get("actual_out") and _when(f.get("scheduled_out"))]
    upcoming.sort(key=lambda f: _when(f.get("scheduled_out")))
    return upcoming[0] if upcoming else (arrived[0] if arrived else (flights[0] if flights else None))


def from_aeroapi(body: dict, now: float) -> dict | None:
    f = pick_aeroapi(body.get("flights") or [], now)
    if not f:
        return None
    o, d = f.get("origin") or {}, f.get("destination") or {}
    return {"dep": {"code": o.get("code_iata") or o.get("code") or "", "gate": f.get("gate_origin") or "",
                    "terminal": f.get("terminal_origin") or ""},
            "arr": {"code": d.get("code_iata") or d.get("code") or "", "city": d.get("city") or "",
                    "gate": f.get("gate_destination") or "", "terminal": f.get("terminal_destination") or "",
                    "baggage": f.get("baggage_claim") or ""},
            "status": f.get("status") or ""}


def pick_aviationstack(data: list[dict], today: str) -> dict | None:
    """The flight that matters now: in the air, else today's, else the first."""
    for f in data:
        if f.get("flight_status") == "active":
            return f
    todays = [f for f in data if f.get("flight_date") == today]
    return todays[0] if todays else (data[0] if data else None)


def from_aviationstack(body: dict, today: str) -> dict | None:
    f = pick_aviationstack(body.get("data") or [], today)
    if not f:
        return None
    dep, arr = f.get("departure") or {}, f.get("arrival") or {}
    return {"dep": {"code": dep.get("iata") or "", "gate": dep.get("gate") or "", "terminal": dep.get("terminal") or ""},
            "arr": {"code": arr.get("iata") or "", "city": arr.get("airport") or "", "gate": arr.get("gate") or "",
                    "terminal": arr.get("terminal") or "", "baggage": arr.get("baggage") or ""},
            "status": f.get("flight_status") or ""}


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
               urllib.parse.urlencode({"access_key": key, "flight_icao": cs}))
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
    today = datetime.fromtimestamp(now).date().isoformat()
    info = from_aeroapi(body, now) if api == "aeroapi" else from_aviationstack(body, today)
    return info or {"none": True}


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
        return "the service doesn't know this flight right now"
    def gate(side, verb):
        g, t = side.get("gate"), side.get("terminal")
        return f"{verb} gate {g}" + (f" (terminal {t})" if t else "") if g else f"{verb} gate: not known yet"
    dep, arr = info["dep"], info["arr"]
    parts = [f"{dep['code'] or '?'} → {arr['code'] or '?'}", gate(dep, "leaves from"), gate(arr, "arrives at")]
    if arr.get("baggage"):
        parts.append(f"bags at carousel {arr['baggage']}")
    if info.get("status"):
        parts.append(f"status: {info['status']}")
    return " · ".join(parts)
