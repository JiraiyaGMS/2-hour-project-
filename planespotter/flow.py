"""Which way is the airport landing today? And what that means for your house.

Airports land and take off into the wind, so a busy airport switches between
"flows" (e.g. landing towards the west, or towards the east) as the wind
changes, and a house under one approach gets lots of planes in one flow and
almost none in the other. planespotter learns this:

* It watches planes low near your nearest big airport (on the live map, and
  from the whole flights of planes that crossed your zone) to see which way
  they're landing and taking off.
* It counts how many planes came over you per hour in each flow.
* So it can say: "LAX is landing towards the west. In this flow, about 6
  planes an hour come over you (0.4 an hour when it lands towards the east)."
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict

from .airports import airports
from .geo import bearing_deg, compass, distance_miles

NEAR_KM = 10            # a plane this close to the airport...
LOW_FT = 2500           # ...and this low above it is landing or taking off
RECENT_S = 45 * 60      # the flow "now" is from the last 45 minutes of planes
LEARN_DAYS = 30
MIN_HOURS = 3           # need this many hours in a flow before saying how busy it usually is


def reference_airport(store, cfg):
    """The big airport most of your planes come from or go to (else the nearest big one)."""
    db = airports()
    counts = Counter()
    for r in store.db.execute("SELECT origin, destination FROM passes ORDER BY first_seen DESC LIMIT 3000"):
        for code in r:
            a = db.get(code) if code else None
            if a and a.kind in ("L", "M") and distance_miles(cfg.home_lat, cfg.home_lon, a.lat, a.lon) <= 60:
                counts[a.code] += 1
    if counts:
        return db.get(counts.most_common(1)[0][0])
    big = db.nearest(cfg.home_lat, cfg.home_lon, max_km=60)
    return big if big and big.kind in ("L", "M") else None


def low_near(airport, lat: float, lon: float, alt_ft) -> bool:
    if alt_ft is None:
        return False
    km = distance_miles(lat, lon, airport.lat, airport.lon) * 1.609344
    return km <= NEAR_KM and 0 < alt_ft - airport.elevation_ft < LOW_FT


def observe(store, airport, planes, now: float) -> int:
    """Remember which way planes low near the airport are heading (landing or taking off). Returns how many."""
    if airport is None:
        return 0
    rows = [(now, airport.code, round(ac.track, 1)) for ac in planes
            if ac.track is not None and ac.speed_kt and ac.speed_kt > 80 and low_near(airport, ac.lat, ac.lon, ac.alt_ft)]
    if rows:
        store.db.executemany("INSERT INTO flow_obs (t, airport, heading) VALUES (?, ?, ?)", rows)
        store.db.execute("DELETE FROM flow_obs WHERE t < ?", (now - 90 * 86400,))
        store.db.commit()
    return len(rows)


def path_observations(store, airport, since: float) -> list[tuple[float, float]]:
    """(time, heading) of planes that crossed your zone and then landed at (or took off from) the airport."""
    out = []
    for r in store.db.execute("SELECT full_path FROM passes WHERE first_seen >= ? AND full_path != '[]'", (since,)):
        path = json.loads(r[0])
        for a, b in zip(path, path[1:]):
            if b[3] - a[3] > 300 or a[2] in (None, 0) or b[2] in (None, 0):
                continue
            if low_near(airport, b[0], b[1], b[2]) and low_near(airport, a[0], a[1], a[2]):
                out.append((b[3], bearing_deg(a[0], a[1], b[0], b[1])))
                break  # one heading per flight end is plenty
    return out


def dominant(headings: list[float]) -> float | None:
    """The direction most of these headings agree on (within 50°), averaged."""
    if not headings:
        return None
    best, support = None, []
    for h in headings:
        near = [x for x in headings if abs(((x - h + 180) % 360) - 180) <= 50]
        if len(near) > len(support):
            best, support = h, near
    sx = sum(math.sin(math.radians(x)) for x in support)
    cy = sum(math.cos(math.radians(x)) for x in support)
    return (math.degrees(math.atan2(sx, cy)) + 360) % 360


def sector(heading: float) -> str:
    """Flows are named by compass direction: landing towards the 'W'."""
    return compass(heading)


def flow_report(store, cfg, now: float, weather: dict | None = None) -> dict:
    airport = reference_airport(store, cfg)
    if airport is None:
        return {"airport": None}
    since = now - LEARN_DAYS * 86400
    obs = [(r[0], r[1]) for r in store.db.execute(
        "SELECT t, heading FROM flow_obs WHERE airport = ? AND t >= ?", (airport.code, since))]
    obs += path_observations(store, airport, since)

    current = dominant([h for t, h in obs if now - t <= RECENT_S])
    # Learn: each hour's flow, and how many planes came over you in that hour.
    by_hour: dict[int, list[float]] = defaultdict(list)
    for t, h in obs:
        by_hour[int(t // 3600)].append(h)
    passes = Counter(int(r[0] // 3600) for r in store.db.execute(
        "SELECT first_seen FROM passes WHERE first_seen >= ?", (since,)))
    hours_in: dict[str, list[int]] = defaultdict(list)
    for hour, headings in by_hour.items():
        if len(headings) >= 2 and hour < int(now // 3600):
            hours_in[sector(dominant(headings))].append(passes.get(hour, 0))
    usual = sorted(({"towards": k, "per_hour": round(sum(v) / len(v), 1), "hours": len(v)}
                    for k, v in hours_in.items() if len(v) >= MIN_HOURS), key=lambda u: -u["hours"])

    out = {"airport": {"code": airport.code, "name": airport.name, "city": airport.city},
           "now": None, "usual": usual, "samples": len(obs)}
    if current is not None:
        towards = sector(current)
        rate = next((u for u in usual if u["towards"] == towards), None)
        others = [u for u in usual if u["towards"] != towards]
        out["now"] = {"heading": round(current), "towards": towards,
                      "per_hour": rate["per_hour"] if rate else None,
                      "other": others[0] if others else None}
    if weather and weather.get("wind_dir") is not None and weather.get("wind_kt"):
        out["wind"] = {"from": compass(weather["wind_dir"]), "kt": weather["wind_kt"]}
    out["text"] = flow_text(out)
    return out


def flow_text(f: dict) -> str:
    """One line for the map and the morning email."""
    if not f.get("airport"):
        return ""
    code = f["airport"]["code"]
    now = f.get("now")
    if not now:
        return ""
    words = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east", "S": "south", "SW": "south-west",
             "W": "west", "NW": "north-west"}
    text = f"{code} is landing towards the {words[now['towards']]}"
    if f.get("wind"):
        text += f" (into a {f['wind']['kt']} kt {words[f['wind']['from']]} wind)"
    if now["per_hour"] is not None:
        text += f". That usually means about {now['per_hour']:g} plane{'s' if now['per_hour'] != 1 else ''} an hour over you"
        other = now.get("other")
        if other:
            text += f" ({other['per_hour']:g} an hour when it lands towards the {words[other['towards']]})"
    return text + "."
