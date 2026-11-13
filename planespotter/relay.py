"""Your status, for the widget and Siri, wherever your phone is.

The phone can only reach your Mac on your home Wi-Fi. So planespotter also
posts a small status message (the next planes due, the last one over, the
space station...) to a private ntfy topic, and the widget and Siri read the
latest one from there: at a hotel, on mobile data, anywhere. Reading is free;
posting counts towards ntfy's daily limit, so it posts only when something
changed, at most every few minutes, and never at the expense of plane alerts.
"""

from __future__ import annotations

import json
import logging

from .geo import compass
from .predict import DIRECTION_WORDS, spoken_name

log = logging.getLogger(__name__)

MIN_GAP = 5 * 60          # seconds between posts at least
HEARTBEAT = 30 * 60       # post at least this often, so "last updated" stays fresh


def clock(cfg, ts: float) -> str:
    return cfg.local(ts).strftime("%I:%M %p").lstrip("0").lower()


def siri_sentence(cfg, nxt: list[dict], last: dict | None, extra: list[str]) -> str:
    """What Siri reads out: written with clock times, since it may be read a few minutes later."""
    if nxt:
        p = nxt[0]
        when = "is over your zone now" if p["eta_s"] == 0 else f"is due over about {clock(cfg, p['at'])}"
        text = f"{p['spoken']}{(' from ' + p['from']) if p.get('from') else ''} {when}, " \
               f"coming from the {DIRECTION_WORDS[p['direction']]}."
        if len(nxt) > 1:
            text += f" {len(nxt) - 1} more after that."
    else:
        text = f"No planes are due over {cfg.home_name} in the next few minutes."
        if last:
            text += f" The last one was {last['label']} at {clock(cfg, last['at'])}."
    return " ".join([text, *extra])


def build_status(cfg, crossings, infos: dict, last_row, now: float, extras: dict | None = None) -> dict:
    """The status message: the next planes with clock times (so it stays right as it ages)."""
    extras = extras or {}
    nxt = []
    for c in crossings[:4]:
        info = infos.get(c.ac.hex)
        route = ""
        if info and (info.origin or info.destination):
            route = f"{info.origin or '?'} → {info.destination or '?'}"
        nxt.append({"label": c.ac.label, "airline": getattr(info, "airline", "") or "", "route": route,
                    "at": round(now + c.eta), "eta_s": round(c.eta), "direction": compass(c.az), "up": round(c.up),
                    "alt_ft": c.ac.alt_ft, "spoken": spoken_name(c.ac, info),
                    "from": getattr(info, "origin_city", "") or ""})
    last = None
    if last_row is not None:
        label = last_row["callsign"] or last_row["registration"] or last_row["hex"].upper()
        last = {"label": label, "at": round(last_row["first_seen"]),
                "route": f"{last_row['origin']} → {last_row['destination']}"
                if last_row["origin"] and last_row["destination"] else ""}
    siri_extra = [x for x in (extras.get("space"), extras.get("transit")) if x]
    status = {"v": 1, "home": cfg.home_name, "updated": round(now), "next": nxt, "last": last,
              "siri": siri_sentence(cfg, nxt, last, siri_extra)}
    for key in ("sky", "best", "space", "flight", "transit"):
        if extras.get(key):
            status[key] = extras[key]
    for p in status["next"]:
        p.pop("spoken", None)
        p.pop("from", None)
    return status


class Relay:
    """Posts the status when it changes (within ntfy's allowance)."""

    def __init__(self, notifier, clock_fn):
        self.notifier, self.clock = notifier, clock_fn
        self.last_sent, self.last_key = -1e12, None

    def enabled(self) -> bool:
        return bool(self.notifier and self.notifier.enabled and self.notifier.cfg.relay)

    def maybe_send(self, status: dict) -> bool:
        if not self.enabled():
            return False
        now = self.clock()
        # What matters to someone glancing at the widget: which planes are next, and the last one.
        key = json.dumps([[p["label"], p["at"] // 120] for p in status["next"]] + [status.get("last")],
                         sort_keys=True)
        since = now - self.last_sent
        if since < MIN_GAP or (key == self.last_key and since < HEARTBEAT):
            return False
        if self.notifier.publish("relay", message=json.dumps(status, separators=(",", ":"))):
            self.last_sent, self.last_key = now, key
            return True
        return False
