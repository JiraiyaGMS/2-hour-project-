"""Your collection: every airline, aircraft type and country you've spotted, plus badges."""

from __future__ import annotations

from collections import Counter

from .countries import country_of, flag
from .logos import airline_code

# key, emoji, name, how to earn it
BADGES = [
    ("first", "🛫", "First plane", "Spot your first plane"),
    ("century", "💯", "Century", "Spot 100 planes"),
    ("thousand", "🏆", "Thousand club", "Spot 1,000 planes"),
    ("airlines10", "🎫", "Frequent flyer", "Spot 10 different airlines"),
    ("airlines25", "🌐", "Airline collector", "Spot 25 different airlines"),
    ("types25", "🛩️", "Type spotter", "Spot 25 different aircraft types"),
    ("countries10", "🗺️", "Globetrotter", "Planes registered in 10 countries"),
    ("a380", "🐋", "Superjumbo", "An Airbus A380 over your zone"),
    ("b747", "👑", "Queen of the skies", "A Boeing 747 over your zone"),
    ("low", "🔊", "Low and loud", "A plane under 1,000 ft"),
    ("overhead", "🎯", "Right overhead", "A plane within a quarter mile"),
    ("night", "🦉", "Night owl", "A plane between midnight and 4 am"),
    ("early", "🌅", "Early bird", "A plane between 5 and 6 am"),
    ("rush", "⏱️", "Rush hour", "20 planes in a single hour"),
    ("military", "🎖️", "Top brass", "A military aircraft nearby"),
    ("helicopter", "🚁", "Chopper", "A helicopter over your zone"),
    ("emergency", "🚨", "Mayday", "A plane squawking an emergency nearby"),
    ("eyes1", "👀", "Eyes on the sky", "Tap “I saw it” on a plane you spotted"),
    ("streak7", "🔥", "On a roll", "Spot a plane with your own eyes 7 days running"),
    ("photo1", "📸", "Shutterbug", "Add your own photo of a plane"),
]


def build_collection(store, cfg) -> dict:
    rows = store.db.execute(
        "SELECT id, callsign, registration, type_code, aircraft_desc, airline, first_seen, closest_miles, "
        "closest_alt_ft FROM passes ORDER BY first_seen").fetchall()

    airlines: dict[str, dict] = {}
    types: dict[str, dict] = {}
    countries: dict[str, dict] = {}
    hours, weekdays = [0] * 24, [0] * 7
    per_hour = Counter()
    earned: dict[str, float] = {}

    def earn(key: str, when: float) -> None:
        earned.setdefault(key, when)

    for i, r in enumerate(rows, start=1):
        t = r["first_seen"]
        local = cfg.local(t)
        hours[local.hour] += 1
        weekdays[local.weekday()] += 1
        per_hour[(local.date(), local.hour)] += 1
        code = airline_code(r["callsign"])
        if code:
            a = airlines.setdefault(code, {"code": code, "name": r["airline"] or code, "count": 0, "first_seen": t})
            a["count"] += 1
            if r["airline"]:
                a["name"] = r["airline"]
        if r["type_code"]:
            ty = types.setdefault(r["type_code"], {"code": r["type_code"], "name": r["aircraft_desc"] or r["type_code"],
                                                   "count": 0, "first_seen": t})
            ty["count"] += 1
            if r["aircraft_desc"]:
                ty["name"] = r["aircraft_desc"]
        c = country_of(r["registration"])
        if c:
            entry = countries.setdefault(c[0], {"code": c[0], "name": c[1], "flag": flag(c[0]), "count": 0, "first_seen": t})
            entry["count"] += 1

        # Badges (the first moment each was earned).
        earn("first", t)
        if i == 100:
            earn("century", t)
        if i == 1000:
            earn("thousand", t)
        if len(airlines) >= 10:
            earn("airlines10", t)
        if len(airlines) >= 25:
            earn("airlines25", t)
        if len(types) >= 25:
            earn("types25", t)
        if len(countries) >= 10:
            earn("countries10", t)
        if r["type_code"] == "A388":
            earn("a380", t)
        if (r["type_code"] or "").startswith("B74"):
            earn("b747", t)
        if r["closest_alt_ft"] is not None and r["closest_alt_ft"] < 1000:
            earn("low", t)
        if r["closest_miles"] <= 0.25:
            earn("overhead", t)
        if 0 <= local.hour < 4:
            earn("night", t)
        if local.hour == 5:
            earn("early", t)
        if per_hour[(local.date(), local.hour)] >= 20:
            earn("rush", t)

    for reason, key in (("military", "military"), ("helicopter", "helicopter"), ("emergency", "emergency")):
        row = store.db.execute("SELECT MIN(at) FROM special_seen WHERE reason = ?", (reason,)).fetchone()
        if row[0]:
            earn(key, row[0])

    eyes = [r[0] for r in store.db.execute("SELECT at FROM eyes ORDER BY at")]
    if eyes:
        earn("eyes1", eyes[0])
        run, prev = 0, None
        for t in eyes:
            day = cfg.local(t).date()
            if day == prev:
                continue
            run = run + 1 if prev and (day - prev).days == 1 else 1
            prev = day
            if run >= 7:
                earn("streak7", t)
                break
    first_photo = store.db.execute("SELECT MIN(added_at) FROM photos").fetchone()[0]
    if first_photo:
        earn("photo1", first_photo)

    # Remember badges for good (special-plane records are tidied away after a month).
    store.db.executemany("INSERT OR IGNORE INTO badges (key, earned_at) VALUES (?, ?)", earned.items())
    store.db.commit()
    kept = dict(store.db.execute("SELECT key, earned_at FROM badges").fetchall())

    total = len(rows)
    progress = {"century": (total, 100), "thousand": (total, 1000), "airlines10": (len(airlines), 10),
                "airlines25": (len(airlines), 25), "types25": (len(types), 25), "countries10": (len(countries), 10)}
    badges = []
    for key, emoji, name, how in BADGES:
        b = {"key": key, "emoji": emoji, "name": name, "how": how, "earned_at": kept.get(key)}
        if key in progress and not b["earned_at"]:
            have, need = progress[key]
            b["progress"] = f"{have:,} / {need:,}"
        badges.append(b)

    by_count = lambda d: sorted(d.values(), key=lambda x: (-x["count"], x["name"]))  # noqa: E731
    return {
        "totals": {"planes": total, "airlines": len(airlines), "types": len(types), "countries": len(countries),
                   "badges": sum(1 for b in badges if b["earned_at"])},
        "since": rows[0]["first_seen"] if rows else None,
        "airlines": by_count(airlines), "types": by_count(types), "countries": by_count(countries),
        "hours": hours, "weekdays": weekdays, "badges": badges,
    }
