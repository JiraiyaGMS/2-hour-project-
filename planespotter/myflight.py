"""My flight: follow a flight you (or someone you know) are taking.

Add it on the map or by sending "flight UA1234" to your command topic. While
it's in the air planespotter checks on it every few minutes (within adsb.lol's
daily limit) and notifies you, and your family's feed, when it takes off, if
it flies over your house, and when it lands.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
from datetime import date, timedelta

from .adsb import Lookup, get_json, key_header, parse_aircraft
from .airports import airports
from .geo import distance_miles

log = logging.getLogger(__name__)

CHECK_EVERY = 4 * 60
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)

# Flight numbers on tickets use the airline's 2-letter code; planes broadcast the 3-letter one.
IATA_TO_ICAO = {
    "AA": "AAL", "DL": "DAL", "UA": "UAL", "WN": "SWA", "AS": "ASA", "B6": "JBU", "NK": "NKS", "F9": "FFT",
    "HA": "HAL", "G4": "AAY", "SY": "SCX", "MX": "MXY", "XP": "VXP", "QX": "QXE", "OO": "SKW", "YX": "RPA",
    "MQ": "ENY", "9E": "EDV", "OH": "JIA", "YV": "ASH", "ZW": "AWI", "PT": "PDT", "C5": "UCA", "G7": "GJS",
    "AC": "ACA", "WS": "WJA", "PD": "POE", "TS": "TSC", "AM": "AMX", "Y4": "VOI", "VB": "VIV",
    "BA": "BAW", "VS": "VIR", "U2": "EZY", "FR": "RYR", "LH": "DLH", "AF": "AFR", "KL": "KLM", "IB": "IBE",
    "EI": "EIN", "LX": "SWR", "OS": "AUA", "SK": "SAS", "AY": "FIN", "TP": "TAP", "AZ": "ITY", "TK": "THY",
    "EK": "UAE", "QR": "QTR", "EY": "ETD", "SV": "SVA", "LY": "ELY", "QF": "QFA", "VA": "VOZ", "NZ": "ANZ",
    "JL": "JAL", "NH": "ANA", "KE": "KAL", "OZ": "AAR", "CX": "CPA", "SQ": "SIA", "CI": "CAL", "BR": "EVA",
    "CA": "CCA", "MU": "CES", "CZ": "CSN", "PR": "PAL", "AI": "AIC", "LA": "LAN", "AV": "AVA", "CM": "CMP",
    "FX": "FDX", "5X": "UPS", "W8": "CJT", "K4": "CKS", "5Y": "GTI",
}


def callsign_for(text: str) -> str:
    """'UA 1234' / 'ua1234' / 'UAL1234' / 'DL 0573' -> the callsign the plane broadcasts (UAL1234, DAL573)."""
    t = re.sub(r"\s+", "", text.upper())
    m = re.fullmatch(r"([A-Z]{3})0*(\d{1,4}[A-Z]?)", t)
    if m:
        return m[1] + m[2]
    m = re.fullmatch(r"([A-Z0-9]{2})0*(\d{1,4}[A-Z]?)", t)
    if m and m[1] in IATA_TO_ICAO:
        return IATA_TO_ICAO[m[1]] + m[2]
    if re.fullmatch(r"[A-Z0-9]{2,8}", t):
        return t  # a registration (N12345) or an unusual callsign: try it as it is
    raise ValueError(f"couldn't read the flight number {text!r} (try something like UA1234)")


def parse_day(word: str, today: date) -> date:
    w = (word or "").strip().lower()
    if not w or w == "today":
        return today
    if w == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(w)


def add_flight(store, text: str, day: date, now: float) -> dict:
    callsign = callsign_for(text)
    store.db.execute(
        "INSERT OR REPLACE INTO my_flights (callsign, label, day, added, state, over_home) VALUES (?, ?, ?, ?, 'waiting', 0)",
        (callsign, text.strip().upper(), day.isoformat(), now))
    store.db.commit()
    return {"callsign": callsign, "label": text.strip().upper(), "day": day.isoformat()}


def remove_flight(store, callsign: str) -> None:
    store.db.execute("DELETE FROM my_flights WHERE callsign = ?", (callsign,))
    store.db.commit()


def my_flights(store) -> list[dict]:
    return [dict(r) for r in store.db.execute("SELECT * FROM my_flights ORDER BY day, added")]


def place_text(code: str, city: str) -> str:
    return f"{city} ({code})" if city and code else city or code or "somewhere"


class FlightWatch:
    """Checks on your flights and sends the notifications."""

    def __init__(self, cfg, store, notifier, budget=None, fetch=None, lookup=None, clock=time.time):
        self.cfg, self.store, self.notifier, self.budget, self.clock = cfg, store, notifier, budget, clock
        self.fetch = fetch or (lambda cs: get_json(f"{cfg.adsb_api}/v2/callsign/{cs}", 15, key_header(cfg.adsb_api_key)))
        self.lookup = lookup or Lookup(store, cfg.adsbdb_api)

    def tell(self, title: str, message: str, hex_code: str = "", tags=("airplane",)) -> None:
        if self.notifier is None or not self.notifier.enabled:
            return
        click = self.notifier.link(plane=hex_code) if hex_code else ""
        self.notifier.send(title, message, click=click, tags=tags)
        self.notifier.family(title, message, tags=tags)

    def check(self) -> list[str]:
        """One look at every active flight. Returns what happened (for the log and tests)."""
        now = self.clock()
        today = self.cfg.local(now).date()
        events = []
        for f in my_flights(self.store):
            day = date.fromisoformat(f["day"])
            if f["state"] in ("landed", "done") or day > today + timedelta(days=1):
                if f["state"] == "landed" and now - (f["landed"] or now) > 12 * 3600:
                    self.set(f["callsign"], state="done")
                continue
            if day < today - timedelta(days=1) and f["state"] == "waiting":
                self.set(f["callsign"], state="done")  # it never showed up
                continue
            if self.budget is not None and not self.budget.allow("positions"):
                break
            if self.budget is not None:
                self.budget.record("positions")
            raw = self.fetch(f["callsign"]).get("ac") or []
            events += self.update(f, raw, now)
        return events

    def update(self, f: dict, raw: list[dict], now: float) -> list[str]:
        cs, name = f["callsign"], f["label"]
        flying = [ac for ac in (parse_aircraft(r) for r in raw) if ac]
        on_ground = any(r.get("alt_baro") == "ground" for r in raw)
        events = []
        if flying:
            ac = flying[0]
            if f["state"] == "waiting":
                info = self.lookup.info(ac, now)
                origin = airports().nearest(ac.lat, ac.lon, max_km=40)
                start = place_text(info.origin, info.origin_city) if info.origin else (
                    place_text(origin.code, origin.city) if origin else "")
                dest = place_text(info.destination, info.destination_city) if info.destination else ""
                text = f"🛫 {name} has taken off" + (f" from {start}" if start else "")
                self.tell(text, f"Heading for {dest}." if dest else "planespotter will tell you when it lands.",
                          ac.hex, tags=("airplane_departure",))
                self.set(cs, state="airborne", took_off=now, hex=ac.hex, origin=info.origin, destination=info.destination,
                         origin_city=info.origin_city, destination_city=info.destination_city)
                events.append(text)
            self.set(cs, last_lat=ac.lat, last_lon=ac.lon, last_alt=ac.alt_ft, last_seen=now, hex=ac.hex,
                     state="airborne")
            near = distance_miles(self.cfg.home_lat, self.cfg.home_lon, ac.lat, ac.lon)
            if near <= max(10.0, self.cfg.radius_miles * 3) and not f["over_home"]:
                text = f"✈ {name} is flying over {self.cfg.home_name} right now"
                self.tell(text, f"{near:.0f} mi away at {ac.alt_ft or 0:,} ft. Look up and wave!", ac.hex)
                self.set(cs, over_home=1)
                events.append(text)
            return events
        if f["state"] != "airborne":
            return events
        lost = f["last_seen"] and now - f["last_seen"] > 20 * 60 and (f["last_alt"] or 99999) < 4000
        if on_ground or lost:
            airport = airports().at(f["last_lat"], f["last_lon"], 0, max_km=20) if f["last_lat"] is not None else None
            where = place_text(airport.code, airport.city) if airport else place_text(f["destination"], f["destination_city"])
            text = f"🛬 {name} has landed" + (f" in {where}" if where != "somewhere" else "")
            self.tell(text, "Safe and sound.", f["hex"] or "", tags=("airplane_arriving",))
            self.set(cs, state="landed", landed=now)
            events.append(text)
        return events

    def set(self, callsign: str, **fields) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        self.store.db.execute(f"UPDATE my_flights SET {', '.join(f'{k} = ?' for k in fields)} WHERE callsign = ?",
                              (*fields.values(), callsign))
        self.store.db.commit()


def start_flight_worker(cfg, budget, notifier) -> threading.Thread:
    from .store import Store

    def run():
        store = Store(cfg.db_path)
        watch = FlightWatch(cfg, store, notifier, budget)
        while True:
            try:
                for text in watch.check():
                    log.info("my flight: %s", text)
            except FETCH_ERRORS as exc:
                log.info("couldn't check your flights: %s", exc)
            except Exception:
                log.exception("checking your flights failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=run, name="my-flights", daemon=True)
    thread.start()
    return thread

