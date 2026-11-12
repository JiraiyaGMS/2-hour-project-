"""Special planes worth running outside for.

* Emergencies: squawk 7700 (emergency), 7600 (radio failure), 7500 (hijack).
* Military aircraft (adsb.lol marks them in its aircraft database).
* Rare and huge types: A380, 747, An-124, Dreamlifter, Beluga, C-5 Galaxy...
* Helicopters right over your zone.
* Firsts: an airline or aircraft type you've never spotted before.

All but helicopters and firsts are spotted anywhere on the live map (about 15
miles out), not just over your zone.
"""

from __future__ import annotations

EMERGENCY_SQUAWKS = {"7500": "Hijack code (7500)", "7600": "Radio failure (7600)", "7700": "Emergency (7700)"}

SPECIAL_TYPES = {
    "A388": "Airbus A380", "B741": "Boeing 747-100", "B742": "Boeing 747-200", "B743": "Boeing 747-300",
    "B744": "Boeing 747-400", "B748": "Boeing 747-8", "B74S": "Boeing 747SP", "B74R": "Boeing 747SR",
    "A124": "Antonov An-124", "A225": "Antonov An-225", "BLCF": "Boeing 747 Dreamlifter",
    "A3ST": "Airbus Beluga", "A337": "Airbus BelugaXL", "C5M": "Lockheed C-5 Galaxy",
    "B52": "Boeing B-52", "CONC": "Concorde", "A400": "Airbus A400M", "C17": "Boeing C-17",
}

FIRSTS_AFTER_DAYS = 3  # don't call everything a "first" during the first few days of watching


def special_reasons(ac, distance_miles: float, cfg) -> list[tuple[str, str, bool]]:
    """[(key, description, is_emergency)] for anything special about this plane right now."""
    if not cfg.special_alerts:
        return []
    out = []
    emergency = EMERGENCY_SQUAWKS.get(ac.squawk)
    if not emergency and ac.emergency and ac.emergency != "none":
        emergency = f"Emergency ({ac.emergency})"
    if emergency:
        out.append(("emergency", emergency, True))
    if ac.db_flags & 1:
        out.append(("military", "Military aircraft", False))
    if ac.type_code in cfg.special_types:
        out.append(("type", SPECIAL_TYPES.get(ac.type_code, ac.type_code), False))
    if cfg.special_helicopters and ac.category == "A7" and distance_miles <= cfg.radius_miles:
        out.append(("helicopter", "Helicopter over your zone", False))
    return out


def short_label(key: str, text: str) -> str:
    """A few characters for a badge: "7700", "Military", "A380", "Helicopter"."""
    if key == "emergency":
        return text.split("(")[-1].rstrip(")") if "(" in text else "Emergency"
    if key == "type":
        return text.replace("Airbus ", "").replace("Boeing ", "")
    return {"military": "Military", "helicopter": "Helicopter"}.get(key, text)
