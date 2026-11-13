"""Follow a flight: a phone notification when it lands, wherever that is.

Tap Follow on a plane on the map. Every few minutes planespotter asks adsb.lol
where the planes you follow are (one request for all of them, within the
daily budget). Once one is seen on the ground, or disappears low down next to
an airport, it sends "DAL573 has landed in Atlanta (ATL)".
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error

from .airports import airports

log = logging.getLogger(__name__)

CHECK_EVERY = 4 * 60
GIVE_UP_S = 24 * 3600
LOST_S = 20 * 60          # not heard from for this long, low down by an airport: it has landed
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def follow(store, hex_code: str, callsign: str, now: float) -> None:
    store.db.execute("INSERT OR REPLACE INTO follows (hex, callsign, since, airborne) VALUES (?, ?, ?, 0)",
                     (hex_code, callsign, now))
    store.db.commit()


def unfollow(store, hex_code: str) -> None:
    store.db.execute("DELETE FROM follows WHERE hex = ?", (hex_code,))
    store.db.commit()


def following(store) -> list[dict]:
    return [dict(r) for r in store.db.execute("SELECT * FROM follows ORDER BY since")]


def landed_text(row, airport) -> str:
    name = row["callsign"] or row["hex"].upper()
    if airport is None:
        return f"🛬 {name} has landed"
    where = airport.city or airport.name
    return f"🛬 {name} has landed in {where} ({airport.code})"


def check(store, positions, notifier, now: float) -> list[str]:
    """Look up every followed plane once; notify about the ones that have landed. Returns the messages."""
    rows = following(store)
    if not rows:
        return []
    budget = getattr(positions, "budget", None)
    if budget is not None and not budget.allow("positions"):
        return []  # nearly at adsb.lol's daily limit: try again later
    hexes = [r["hex"] for r in rows]
    flying, ground = positions.fetch(hexes)
    by_hex = {ac.hex: ac for ac in flying}
    sent = []
    for r in rows:
        ac = by_hex.get(r["hex"])
        if ac is not None:
            store.db.execute("UPDATE follows SET airborne = 1, last_lat = ?, last_lon = ?, last_alt = ?, last_seen = ? "
                             "WHERE hex = ?", (ac.lat, ac.lon, ac.alt_ft, now, r["hex"]))
            continue
        landed_at = None
        if r["hex"] in ground and r["airborne"]:
            landed_at = "ground"
        elif r["airborne"] and r["last_seen"] and now - r["last_seen"] > LOST_S and (r["last_alt"] or 99999) < 3000:
            landed_at = "lost-low"  # out of range on the ground
        if landed_at:
            airport = None
            if r["last_lat"] is not None:
                airport = airports().at(r["last_lat"], r["last_lon"], 0, max_km=15)
            text = landed_text(r, airport)
            if notifier is not None and notifier.enabled:
                notifier.send(text, "You asked to know when it landed. It's no longer followed.",
                              click=notifier.link(plane=r["hex"]), tags=("airplane_arriving",))
            sent.append(text)
            unfollow(store, r["hex"])
        elif now - r["since"] > GIVE_UP_S:
            unfollow(store, r["hex"])
    store.db.commit()
    return sent


def start_follow_worker(cfg, positions, notifier) -> threading.Thread:
    """Check on followed flights every few minutes, in the background."""
    from .store import Store

    def run():
        store = Store(cfg.db_path)
        while True:
            try:
                for text in check(store, positions, notifier, time.time()):
                    log.info("followed flight: %s", text)
            except FETCH_ERRORS as exc:
                log.info("couldn't check followed flights: %s", exc)
            except Exception:
                log.exception("following flights failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=run, name="follow", daemon=True)
    thread.start()
    return thread
