"""Games and stats: spotting bingo, guess the plane, your year (or month) in planes, and plane profiles."""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import date, datetime, timedelta

from .airports import airports
from .countries import country_of, flag
from .emails import fmt_alt, fmt_dist, label
from .geo import distance_miles
from .logos import airline_code
from .records import flight_miles

# --- kinds of plane -------------------------------------------------------------------

FAMILIES = [
    ("Airbus A220", {"BCS1", "BCS3"}),
    ("Airbus A320 family", {"A318", "A319", "A320", "A321", "A19N", "A20N", "A21N"}),
    ("Airbus A330", {"A332", "A333", "A338", "A339"}),
    ("Airbus A340", {"A342", "A343", "A345", "A346"}),
    ("Airbus A350", {"A359", "A35K"}),
    ("Airbus A380", {"A388"}),
    ("Boeing 737", {"B731", "B732", "B733", "B734", "B735", "B736", "B737", "B738", "B739", "B37M", "B38M",
                    "B39M", "B3XM"}),
    ("Boeing 747", {"B741", "B742", "B743", "B744", "B748", "B74S", "BLCF"}),
    ("Boeing 757", {"B752", "B753"}),
    ("Boeing 767", {"B762", "B763", "B764"}),
    ("Boeing 777", {"B772", "B773", "B77L", "B77W", "B778", "B779"}),
    ("Boeing 787", {"B788", "B789", "B78X"}),
    ("Embraer E-Jet", {"E170", "E75L", "E75S", "E190", "E195", "E290", "E295"}),
    ("Bombardier CRJ", {"CRJ1", "CRJ2", "CRJ7", "CRJ9", "CRJX"}),
    ("ATR 42/72", {"AT43", "AT44", "AT45", "AT72", "AT73", "AT75", "AT76"}),
    ("Dash 8", {"DH8A", "DH8B", "DH8C", "DH8D"}),
    ("Business jet", {"GLF4", "GLF5", "GLF6", "GLEX", "GL5T", "GL7T", "CL30", "CL35", "CL60", "C25A", "C25B",
                      "C25C", "C510", "C525", "C550", "C560", "C56X", "C650", "C680", "C68A", "C700", "C750",
                      "E35L", "E50P", "E55P", "E545", "E550", "F2TH", "F900", "FA7X", "FA8X", "LJ35", "LJ45",
                      "LJ60", "LJ75", "H25B", "PC24", "HDJT", "BE40", "G280", "GALX"}),
    ("Helicopter", {"EC35", "EC45", "EC30", "EC55", "EC75", "AS50", "AS55", "AS65", "AS32", "B06", "B407",
                    "B412", "B429", "B505", "R22", "R44", "R66", "S76", "S92", "A109", "A119", "A139", "A169",
                    "H60", "UH1", "MD52", "MD60", "H500", "H160"}),
    ("Small prop plane", {"C150", "C152", "C172", "C177", "C182", "C206", "C210", "P28A", "P28B", "P28R", "PA32",
                          "PA34", "PA44", "PA46", "SR20", "SR22", "SR2T", "BE33", "BE35", "BE36", "BE58", "M20P",
                          "M20T", "DA40", "DA42", "DA62", "C208", "PC12", "TBM7", "TBM8", "TBM9", "BE20", "BE9L"}),
]
FAMILY_OF = {code: name for name, codes in FAMILIES for code in codes}
WIDEBODIES = {c for name, codes in FAMILIES if name.split()[-1] in ("A330", "A340", "A350", "A380", "747", "767",
                                                                    "777", "787") for c in codes} | {"MD11", "A306"}
FOUR_ENGINES = {"A388", "B741", "B742", "B743", "B744", "B748", "B74S", "BLCF", "A342", "A343", "A345", "A346",
                "A124", "IL76", "C17", "B52", "K35R", "E3TF", "C5M", "A400", "BA46", "RJ85", "RJ1H"}
TURBOPROPS = {c for name, codes in FAMILIES if name in ("ATR 42/72", "Dash 8") for c in codes} | {
    "SF34", "B190", "JS41", "D328", "C208", "PC12", "BE20", "BE9L", "C130", "AT8T"}
CARGO_AIRLINES = {"FDX", "UPS", "GTI", "CKS", "ABX", "ATN", "CLX", "BOX", "CAO", "ABW", "NCA", "PAC", "AJT", "GEC",
                  "DHK", "BCS", "DHX", "MXY", "KYE", "SRR", "NPT", "CKK", "AHK", "FRH"}
PRIVATE_OPERATORS = {"EJA", "LXJ", "XSR", "VJT", "WUP", "JTL", "EJM", "FFL", "GAJ", "TWY"}


def family(type_code: str) -> str | None:
    return FAMILY_OF.get((type_code or "").upper())


# --- spotting bingo -------------------------------------------------------------------

class Context:
    """What the bingo squares need to know beyond the plane itself."""

    def __init__(self, store, cfg, ground_ft: int):
        self.cfg, self.ground_ft = cfg, ground_ft
        home = airports().nearest(cfg.home_lat, cfg.home_lon, max_km=300)
        self.home_country = home.country if home else ""
        self.type_counts = Counter(r[0] for r in store.db.execute("SELECT type_code FROM passes WHERE type_code != ''"))


def _abroad(p, ctx) -> bool:
    c = country_of(p["registration"])
    return bool(c and ctx.home_country and c[0] != ctx.home_country)


def _from_abroad(p, ctx) -> bool:
    a = airports().get(p["origin"]) if p["origin"] else None
    b = airports().get(p["destination"]) if p["destination"] else None
    return any(x and ctx.home_country and x.country != ctx.home_country for x in (a, b))


def _far(p, miles) -> bool:
    d = flight_miles(p["origin"], p["destination"]) if p["origin"] and p["destination"] else None
    return bool(d and d >= miles)


def _hour(p, ctx) -> int:
    return ctx.cfg.local(p["first_seen"]).hour


# key, emoji, text, test(pass, context)
SQUARES = [
    ("b787", "✈️", "A Boeing 787", lambda p, c: family(p["type_code"]) == "Boeing 787"),
    ("b777", "✈️", "A Boeing 777", lambda p, c: family(p["type_code"]) == "Boeing 777"),
    ("b737", "✈️", "A Boeing 737", lambda p, c: family(p["type_code"]) == "Boeing 737"),
    ("a320", "✈️", "An Airbus A320 family", lambda p, c: family(p["type_code"]) == "Airbus A320 family"),
    ("a350", "✈️", "An Airbus A350", lambda p, c: family(p["type_code"]) == "Airbus A350"),
    ("a220", "✈️", "An Airbus A220", lambda p, c: family(p["type_code"]) == "Airbus A220"),
    ("wide", "🛫", "A widebody (twin-aisle) jet", lambda p, c: p["type_code"] in WIDEBODIES),
    ("four", "🪶", "A four-engine plane", lambda p, c: p["type_code"] in FOUR_ENGINES),
    ("regional", "🛩️", "A regional jet (CRJ or E-Jet)",
     lambda p, c: family(p["type_code"]) in ("Embraer E-Jet", "Bombardier CRJ")),
    ("prop", "🌀", "A turboprop", lambda p, c: p["type_code"] in TURBOPROPS),
    ("heli", "🚁", "A helicopter", lambda p, c: family(p["type_code"]) == "Helicopter"),
    ("bizjet", "💼", "A private jet",
     lambda p, c: family(p["type_code"]) == "Business jet" or (airline_code(p["callsign"]) or "") in PRIVATE_OPERATORS),
    ("small", "🐝", "A small prop plane", lambda p, c: family(p["type_code"]) == "Small prop plane"),
    ("cargo", "📦", "A cargo plane", lambda p, c: (airline_code(p["callsign"]) or "") in CARGO_AIRLINES),
    ("abroad", "🌍", "A plane registered in another country", _abroad),
    ("intl", "🛂", "A flight to or from another country", _from_abroad),
    ("long", "🧭", "A flight of 2,000+ miles", lambda p, c: _far(p, 2000)),
    ("early", "🌅", "A plane before 7 am", lambda p, c: _hour(p, c) < 7),
    ("late", "🌙", "A plane after 10 pm", lambda p, c: _hour(p, c) >= 22),
    ("low", "🔊", "A plane under 2,000 ft above you",
     lambda p, c: p["closest_alt_ft"] is not None and 0 < p["closest_alt_ft"] - c.ground_ft < 2000),
    ("close", "🎯", "A plane within half a mile", lambda p, c: p["closest_miles"] <= 0.5),
    ("rare", "💎", "A type you'd seen fewer than 3 times",
     lambda p, c: bool(p["type_code"]) and c.type_counts.get(p["type_code"], 0) <= 3),
    ("first", "✨", "A first (new airline or type)", lambda p, c: json.loads(p["firsts"] or "[]") != []),
]
SQUARE = {key: (key, emoji, text, test) for key, emoji, text, test in SQUARES}
LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
FREE = 4  # the middle square


def week_start(cfg, ts: float) -> date:
    d = cfg.local(ts).date()
    return d - timedelta(days=d.weekday())  # Monday


def choose_squares(store, cfg, monday: date, ground_ft: int = 0) -> list[str]:
    """Eight squares for the week: things that happen near you, but not every day. Kept for the week."""
    key = f"bingo:{monday.isoformat()}"
    saved = store.get_meta(key)
    if saved:
        return json.loads(saved)
    ctx = Context(store, cfg, ground_ft)
    since = cfg.day_start(monday) - 28 * 86400
    history = store.db.execute("SELECT * FROM passes WHERE first_seen >= ? AND first_seen < ?",
                               (since, cfg.day_start(monday))).fetchall()
    days = {k: set() for k in SQUARE}
    for p in history:
        for k, (_, _, _, test) in SQUARE.items():
            if test(p, ctx):
                days[k].add(cfg.local(p["first_seen"]).date())
    rng = random.Random(f"{monday.isoformat()}:{cfg.home_lat:.2f}:{cfg.home_lon:.2f}")
    # Happened on 1 to 20 of the last 28 days: possible this week, but not a given.
    # The less common ones first, so the card takes the week (not an afternoon) to fill.
    good = [k for k in SQUARE if 1 <= len(days[k]) <= 20]
    rng.shuffle(good)
    good.sort(key=lambda k: len(days[k]) // 4)  # rough buckets keep some variety from week to week
    chosen = good[:8]
    if len(chosen) < 8:  # not much history yet: fill up with the classics
        fallback = [k for k in ("b737", "a320", "wide", "early", "late", "low", "abroad", "regional", "cargo",
                                "close", "intl", "long") if k not in chosen]
        chosen += fallback[:8 - len(chosen)]
    store.set_meta(key, json.dumps(chosen))
    return chosen


def bingo(store, cfg, now: float, ground_ft: int = 0) -> dict:
    """This week's card: which squares are filled (and by which plane), and any lines."""
    monday = week_start(cfg, now)
    keys = choose_squares(store, cfg, monday, ground_ft)
    ctx = Context(store, cfg, ground_ft)
    passes = store.passes_between(cfg.day_start(monday), cfg.day_start(monday + timedelta(days=7)))
    squares = []
    for k in keys:
        _, emoji, text, test = SQUARE[k]
        hit = next((p for p in passes if test(p, ctx)), None)
        squares.append({"key": k, "emoji": emoji, "text": text,
                        "by": {"id": hit["id"], "label": label(hit), "when": hit["first_seen"]} if hit else None})
    squares.insert(FREE, {"key": "free", "emoji": "⭐", "text": "Free: any plane",
                          "by": {"id": passes[0]["id"], "label": label(passes[0]), "when": passes[0]["first_seen"]}
                          if passes else None})
    lines = [line for line in LINES if all(squares[i]["by"] for i in line)]
    return {"week_of": monday.isoformat(), "squares": squares, "lines": len(lines), "line_cells": lines,
            "filled": sum(1 for s in squares if s["by"]), "full_house": all(s["by"] for s in squares)}


# --- guess the plane --------------------------------------------------------------------

def quiz_question(store, hex_code: str, type_code: str, now: float, rng: random.Random | None = None) -> dict | None:
    """Which kind of plane is it? Four choices: the right one, and three you see around here."""
    answer = family(type_code)
    if not answer:
        return None
    rng = rng or random.Random()
    local = Counter(family(r[0]) for r in store.db.execute(
        "SELECT type_code FROM passes WHERE type_code != '' ORDER BY first_seen DESC LIMIT 2000"))
    pool = [name for name, _ in local.most_common() if name and name != answer]
    common = ["Boeing 737", "Airbus A320 family", "Boeing 787", "Embraer E-Jet", "Boeing 777", "Airbus A220",
              "Bombardier CRJ", "Airbus A330", "Business jet", "Boeing 757"]
    pool += [n for n in common if n not in pool and n != answer]
    others = rng.sample(pool[:7], 3) if len(pool[:7]) >= 3 else pool[:3]
    options = others + [answer]
    rng.shuffle(options)
    cur = store.db.execute("INSERT INTO quiz (hex, at, answer, correct, chosen) VALUES (?, ?, ?, NULL, NULL)",
                           (hex_code, now, answer))
    store.db.commit()
    return {"id": int(cur.lastrowid), "options": options}


def quiz_answer(store, quiz_id: int, chosen: str) -> dict:
    row = store.db.execute("SELECT * FROM quiz WHERE id = ?", (quiz_id,)).fetchone()
    if row is None:
        raise ValueError("no such question")
    if row["chosen"] is None:
        store.db.execute("UPDATE quiz SET chosen = ?, correct = ? WHERE id = ?",
                         (chosen, int(chosen == row["answer"]), quiz_id))
        store.db.commit()
        row = store.db.execute("SELECT * FROM quiz WHERE id = ?", (quiz_id,)).fetchone()
    return {"correct": bool(row["correct"]), "answer": row["answer"], "chosen": row["chosen"], "score": quiz_score(store)}


def quiz_score(store, since: float = 0) -> dict:
    rows = store.db.execute("SELECT correct FROM quiz WHERE chosen IS NOT NULL AND at >= ? ORDER BY at",
                            (since,)).fetchall()
    run = best = 0
    for r in rows:
        run = run + 1 if r["correct"] else 0
        best = max(best, run)
    right = sum(r["correct"] for r in rows)
    return {"answered": len(rows), "right": right, "run": run, "best_run": best,
            "percent": round(100 * right / len(rows)) if rows else None}


# --- your year (or month) in planes ---------------------------------------------------------

def period_bounds(cfg, period: str) -> tuple[float, float, str]:
    """'2026' or '2026-11' -> (start, end, a name like "2026" or "November 2026")."""
    if len(period) == 4 and period.isdigit():
        y = int(period)
        return cfg.day_start(date(y, 1, 1)), cfg.day_start(date(y + 1, 1, 1)), str(y)
    y, m = (int(x) for x in period.split("-"))
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return cfg.day_start(date(y, m, 1)), cfg.day_start(nxt), datetime(y, m, 1).strftime("%B %Y")


def wrapped(store, cfg, period: str, ground_ft: int = 0) -> dict:
    start, end, name = period_bounds(cfg, period)
    rows = store.passes_between(start, end)
    earlier = store.db.execute("SELECT callsign, type_code, registration FROM passes WHERE first_seen < ?",
                               (start,)).fetchall()
    out: dict = {"period": period, "name": name, "planes": len(rows), "year": len(period) == 4}
    if not rows:
        return out
    airlines = Counter(airline_code(r["callsign"]) for r in rows if airline_code(r["callsign"]))
    airline_names = {airline_code(r["callsign"]): r["airline"] for r in rows if r["airline"] and airline_code(r["callsign"])}
    types = Counter((r["aircraft_desc"] or r["type_code"]) for r in rows if r["type_code"])
    routes = Counter(f"{r['origin']} → {r['destination']}" for r in rows if r["origin"] and r["destination"])
    countries = Counter(country_of(r["registration"]) for r in rows if country_of(r["registration"]))
    old_airlines = {airline_code(r["callsign"]) for r in earlier} - {None}
    old_types = {r["type_code"] for r in earlier}
    new_airlines = {airline_code(r["callsign"]) for r in rows} - old_airlines - {None}
    new_types = {r["type_code"] for r in rows if r["type_code"]} - old_types
    ever = Counter(r[0] for r in store.db.execute("SELECT type_code FROM passes WHERE type_code != ''"))
    typed = [r for r in rows if r["type_code"]]
    rarest = min(typed, key=lambda r: (ever[r["type_code"]], r["first_seen"])) if typed else None
    db = airports()
    origins = []
    for r in rows:
        a = db.get(r["origin"]) if r["origin"] else None
        if a:
            origins.append((distance_miles(cfg.home_lat, cfg.home_lon, a.lat, a.lon), a, r))
    farthest = max(origins, key=lambda o: o[0]) if origins else None
    days = Counter(cfg.local(r["first_seen"]).date() for r in rows)
    hours = Counter(cfg.local(r["first_seen"]).hour for r in rows)
    regular = Counter(r["hex"] for r in rows).most_common(1)[0]
    reg_row = max((r for r in rows if r["hex"] == regular[0]), key=lambda r: r["first_seen"])
    eyes = store.db.execute("SELECT COUNT(*) FROM eyes WHERE at >= ? AND at < ?", (start, end)).fetchone()[0]
    photos = store.db.execute("SELECT COUNT(*) FROM photos WHERE added_at >= ? AND added_at < ?", (start, end)).fetchone()[0]
    if out["year"]:
        y = int(period)
        chart = [sum(n for d, n in days.items() if d.month == m) for m in range(1, 13)]
        ticks = [datetime(y, m, 1).strftime("%b") for m in range(1, 13)]
    else:
        first = cfg.local(start).date()
        n_days = (cfg.local(end - 1).date() - first).days + 1
        chart = [days.get(first + timedelta(days=i), 0) for i in range(n_days)]
        ticks = [str(i + 1) for i in range(n_days)]
    with_alt = [r for r in rows if r["closest_alt_ft"] is not None and r["closest_alt_ft"] > 0]
    lowest = min(with_alt, key=lambda r: r["closest_alt_ft"]) if with_alt else None
    out.update({
        "airlines": len(airlines), "types": len(types), "countries": len(countries),
        "top_airline": ([airline_names.get(airlines.most_common(1)[0][0], airlines.most_common(1)[0][0]),
                         airlines.most_common(1)[0][1]] if airlines else None),
        "top_airline_code": airlines.most_common(1)[0][0] if airlines else None,
        "top_type": types.most_common(1)[0] if types else None,
        "top_route": routes.most_common(1)[0] if routes else None,
        "new_airlines": len(new_airlines), "new_types": len(new_types),
        "rarest": {"id": rarest["id"], "label": label(rarest), "aircraft": rarest["aircraft_desc"] or rarest["type_code"],
                   "times": ever[rarest["type_code"]]} if rarest else None,
        "farthest": {"id": farthest[2]["id"], "label": label(farthest[2]), "city": farthest[1].city or farthest[1].name,
                     "code": farthest[1].code, "miles": round(farthest[0])} if farthest else None,
        "busiest_day": {"date": max(days.items(), key=lambda kv: kv[1])[0].isoformat(), "count": max(days.values())},
        "favourite_hour": max(hours.items(), key=lambda kv: kv[1])[0],
        "regular": {"label": reg_row["registration"] or label(reg_row), "times": regular[1], "id": reg_row["id"]},
        "lowest": {"id": lowest["id"], "label": label(lowest), "alt": fmt_alt(lowest["closest_alt_ft"])} if lowest else None,
        "night": sum(1 for r in rows if cfg.local(r["first_seen"]).hour >= 22 or cfg.local(r["first_seen"]).hour < 6),
        "offline": sum(1 for r in rows if r["offline"]),
        "eyes": eyes, "photos": photos, "quiz": quiz_score(store, start),
        "chart": chart, "ticks": ticks,
        "top_countries": [[flag(iso), n, name] for (iso, name), n in countries.most_common(3)],
    })
    return out


# --- plane profiles -------------------------------------------------------------------------

def profile(store, cfg, hex_code: str) -> dict:
    """Everything about one aircraft you've seen: each visit, its routes, when it usually comes."""
    rows = store.db.execute("SELECT * FROM passes WHERE hex = ? ORDER BY first_seen DESC", (hex_code,)).fetchall()
    if not rows:
        raise ValueError("never seen that plane")
    latest = rows[0]
    reg = next((r["registration"] for r in rows if r["registration"]), "")
    country = country_of(reg)
    routes = Counter(f"{r['origin']} → {r['destination']}" for r in rows if r["origin"] and r["destination"])
    airlines = Counter(airline_code(r["callsign"]) for r in rows if airline_code(r["callsign"]))
    airline_names = {airline_code(r["callsign"]): r["airline"] for r in rows if r["airline"] and airline_code(r["callsign"])}
    hours = Counter(cfg.local(r["first_seen"]).hour for r in rows)
    return {
        "hex": hex_code, "registration": reg, "type": latest["type_code"],
        "aircraft": next((r["aircraft_desc"] for r in rows if r["aircraft_desc"]), latest["type_code"]),
        "family": family(latest["type_code"]), "owner": next((r["owner"] for r in rows if r["owner"]), ""),
        "airline": airlines.most_common(1)[0][0] if airlines else "",
        "callsign": latest["callsign"], "country": {"name": country[1], "flag": flag(country[0])} if country else None,
        "photo_url": next((r["photo_url"] for r in rows if r["photo_url"]), ""),
        "times": len(rows), "first_seen": rows[-1]["first_seen"], "last_seen": latest["first_seen"],
        "usual_hour": hours.most_common(1)[0][0],
        "routes": [[r, n] for r, n in routes.most_common(6)],
        "visits": [{"id": r["id"], "when": r["first_seen"], "callsign": r["callsign"],
                    "route": f"{r['origin']} → {r['destination']}" if r["origin"] and r["destination"] else "",
                    "alt": fmt_alt(r["closest_alt_ft"]), "miles": fmt_dist(r["closest_miles"]),
                    "offline": bool(r["offline"])} for r in rows[:100]],
    }


def old_friends(store, limit: int = 10) -> list[dict]:
    """The aircraft you've seen most often (at least 3 times)."""
    rows = store.db.execute(
        "SELECT hex, COUNT(*) AS n, MAX(registration) AS reg, MAX(type_code) AS type, MAX(aircraft_desc) AS aircraft, "
        "MAX(airline) AS airline, MAX(callsign) AS callsign, MAX(first_seen) AS last FROM passes "
        "WHERE hex NOT LIKE '~%' GROUP BY hex HAVING n >= 3 ORDER BY n DESC, last DESC LIMIT ?", (limit,)).fetchall()
    return [{"hex": r["hex"], "times": r["n"], "registration": r["reg"], "type": r["type"],
             "aircraft": r["aircraft"] or r["type"], "airline": r["airline"], "callsign": r["callsign"]} for r in rows]
