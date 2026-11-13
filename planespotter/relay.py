"""Your status, for the widget and Siri, wherever your phone is.

The phone can only reach your Mac on your home Wi-Fi. So planespotter also
posts a small status message (the next planes due, the last one over, the
space station...) to a private ntfy topic, and the widget and Siri read the
latest one from there: at a hotel, on mobile data, anywhere. Reading is free;
posting counts towards ntfy's daily limit, so it posts only when the phone's
picture would otherwise be wrong, spread evenly over the day, and never at the
expense of plane alerts.

The phone works out what to show (and what Siri says) when it reads the status,
dropping planes whose time has passed: so a status stays right until a new
plane turns up or one changes course.
"""

from __future__ import annotations

import json
import logging
import math

from .geo import compass
from .notify import EXTRA_CAPS
from .predict import DIRECTION_WORDS, say_name, spoken_name

log = logging.getLogger(__name__)

MIN_GAP = 4 * 60          # seconds between posts at least
HEARTBEAT = 30 * 60       # post at least this often (allowance permitting), so "last updated" stays fresh
BURST = 2                 # posts that can be saved up for a busy moment
RETRY = 2 * 60            # wait this long after a post that didn't go through (ntfy busy, no network)
GRACE = 90                # a plane still counts as "coming over now" this long after its time
MAX_BYTES = 3800          # ntfy turns longer messages into attachments
NEXT_PLANES = 6


def clock(cfg, ts: float) -> str:
    return cfg.local(ts).strftime("%I:%M %p").lstrip("0").lower()


UPGRADE = "For answers that are always up to date, change your Siri shortcut to Scriptable's Run Script."


def siri_sentence(cfg, nxt: list[dict], last: dict | None, extra: list[str], now: float) -> str:
    """For older Siri shortcuts that read this sentence as it was written. It starts with the time, since
    it may be read much later (with the Mac asleep, nothing new is posted). The planespotter script on the
    phone works out its own answer when you ask instead, and never reads this."""
    if nxt:
        p = nxt[0]
        when = "was over your zone" if p["eta_s"] == 0 else f"was due over about {clock(cfg, p['at'])}"
        text = f"{p['spoken']}{(' from ' + p['from']) if p.get('from') else ''} {when}, " \
               f"coming from the {DIRECTION_WORDS[p['direction']]}."
        if len(nxt) > 1:
            text += f" {len(nxt) - 1} more after that."
    else:
        text = f"No planes were due over {cfg.home_name}."
        if last:
            text += f" The last one was {last['label']} at {clock(cfg, last['at'])}."
    return " ".join([f"At {clock(cfg, now)}: {text}", *extra, UPGRADE])


def build_status(cfg, crossings, infos: dict, last_row, now: float, extras: dict | None = None) -> dict:
    """The status message: the next planes with clock times (so it stays right as it ages)."""
    extras = extras or {}
    nxt = []
    for c in crossings[:NEXT_PLANES]:
        info = infos.get(c.ac.hex)
        route = ""
        if info and (info.origin or info.destination):
            route = f"{info.origin or '?'} → {info.destination or '?'}"
        spoken, city = spoken_name(c.ac, info), getattr(info, "origin_city", "") or ""
        nxt.append({"label": c.ac.label, "airline": getattr(info, "airline", "") or "", "route": route,
                    "at": round(now + c.eta), "eta_s": round(c.eta), "direction": compass(c.az), "up": round(c.up),
                    "alt_ft": c.ac.alt_ft, "say": f"{spoken} from {city}" if city else spoken,
                    "spoken": spoken, "from": city})
    last = None
    if last_row is not None:
        label = last_row["callsign"] or last_row["registration"] or last_row["hex"].upper()
        last = {"label": label, "at": round(last_row["first_seen"]),
                "route": f"{last_row['origin']} → {last_row['destination']}"
                if last_row["origin"] and last_row["destination"] else "",
                "say": say_name(last_row["callsign"], label, last_row["airline"], last_row["origin_city"])}
    siri_extra = [x for x in (extras.get("space"), extras.get("transit")) if x]
    status = {"v": 1, "home": cfg.home_name, "updated": round(now), "next": nxt, "last": last,
              "siri": siri_sentence(cfg, nxt, last, siri_extra, now)}
    for key in ("sky", "best", "space", "flight", "transit", "hour_count", "regulars"):
        if extras.get(key):
            status[key] = extras[key]
    if "hour_count" in extras:
        status["hour_count"] = extras["hour_count"]  # 0 is worth showing too
    for p in status["next"]:
        p.pop("spoken", None)
        p.pop("from", None)
    return status


def compact(status: dict) -> str:
    """The status as JSON, leaving out the nice-to-haves if it would be too long for one ntfy message."""
    body = json.dumps(status, separators=(",", ":"))
    for key in ("best", "sky", "space", "regulars"):
        if len(body.encode()) <= MAX_BYTES:
            break
        status = {k: v for k, v in status.items() if k != key}
        body = json.dumps(status, separators=(",", ":"))
    return body


def upcoming(status: dict, now: float) -> list[dict]:
    """The planes the phone shows from this status at ``now``: the ones whose time hasn't passed."""
    return [p for p in status.get("next") or [] if p["at"] > now - GRACE]


class Relay:
    """Posts the status when the phone's picture would be wrong, spread over the day.

    A status stays right as it ages (the phone drops planes once their time has passed), so a new one
    is only needed when a new plane is next, the next one changes its time or course, or for the
    half-hourly "still here". Posts come out of an allowance that refills evenly through the day, so
    the status never runs out by lunchtime and goes quiet until tomorrow.
    """

    def __init__(self, notifier, clock_fn, per_day: int | None = None):
        self.notifier, self.clock = notifier, clock_fn
        self.per_day = per_day or EXTRA_CAPS["relay"]
        self.last_sent = self.failed_at = -1e12
        self.tokens, self.tokens_at = float(BURST), None
        self.posted: dict | None = None

    def enabled(self) -> bool:
        return bool(self.notifier and self.notifier.enabled and self.notifier.cfg.relay)

    def stale(self, status: dict, now: float) -> bool:
        """Would the phone, reading the last status we posted, now get the next plane wrong?"""
        if self.posted is None:
            return True
        shown = next(iter(upcoming(self.posted, now)), None)
        actual = status["next"][0] if status["next"] else None
        if shown is None or actual is None:
            return (shown is None) != (actual is None)
        return shown["label"] != actual["label"] or abs(shown["at"] - actual["at"]) > 180

    def maybe_send(self, status: dict) -> bool:
        if not self.enabled():
            return False
        now = self.clock()
        if self.tokens_at is not None:
            self.tokens = min(BURST, self.tokens + (now - self.tokens_at) * self.per_day / 86400)
        self.tokens_at = now
        since = now - self.last_sent
        if since < MIN_GAP or now - self.failed_at < RETRY or self.tokens < 1:
            return False
        if not (self.stale(status, now) or since >= HEARTBEAT):
            return False
        if not self.notifier.publish("relay", message=compact(status)):
            self.failed_at = now
            return False
        if self.posted is None:
            log.info("posted your status for the widget and Siri (it updates whenever the next plane changes)")
        self.tokens -= 1
        self.last_sent, self.posted = now, status
        return True


# --- what Siri says (worked out on the phone, when you ask; this copy is for `phone --test` and the tests) ---

def _round(x: float) -> int:
    return int(math.floor(x + 0.5))  # like JavaScript's Math.round


def in_minutes(seconds: float) -> str:
    m = max(1, _round(seconds / 60))
    return "in about a minute" if m == 1 else f"in about {m} minutes"


def ago(seconds: float) -> str:
    m = _round(seconds / 60)
    if m < 1:
        return "just now"
    if m < 60:
        return "a minute ago" if m == 1 else f"{m} minutes ago"
    h = _round(m / 60)
    return "about an hour ago" if h == 1 else f"about {h} hours ago"


def last_over(status: dict, now: float) -> dict | None:
    """The last plane over: from the status, or a plane it said was due whose time has since passed."""
    gone = [p for p in status.get("next") or [] if p["at"] <= now - GRACE]
    candidates = [x for x in (status.get("last"), gone[-1] if gone else None) if x]
    return max(candidates, key=lambda x: x["at"]) if candidates else None


def spoken_now(status: dict | None, now: float, clock_text) -> str:
    """What Siri says when you ask at ``now`` (the same steps as the Scriptable script)."""
    if not status:
        return "I can't get the planes right now. Check your phone has internet, then try again."
    age = now - (status.get("updated") or now)
    if age > 3 * 3600:
        return f"planespotter's last update was {ago(age)}. Is it still running on your Mac, and is the Mac awake?"
    out = []
    nxt = upcoming(status, now)
    if nxt:
        p = nxt[0]
        wait = p["at"] - now
        over = " is coming over now" + (f" at {p['alt_ft']:,} feet" if isinstance(p.get("alt_ft"), int) else "")
        line = (p.get("say") or p["label"]) + (over if wait <= 30 else " is due " + in_minutes(wait))
        if p.get("direction") in DIRECTION_WORDS:
            line += (", look up to the " if wait <= 30 else ", look to the ") + DIRECTION_WORDS[p["direction"]]
        out.append(line + ".")
        if len(nxt) > 1:
            q = nxt[1]
            out.append(f"Then {q.get('say') or q['label']} {in_minutes(max(60, q['at'] - now))}.")
    else:
        out.append(f"No planes are due over {status.get('home') or 'your house'} in the next few minutes.")
        for h in (status.get("hovering") or [])[:1]:
            out.append(f"{h.get('say') or h['label']} is hovering nearby, about {max(1, _round(h['miles']))} "
                       f"mile{'s' if _round(h['miles']) > 1 else ''} away.")
        r = next((r for r in status.get("regulars") or [] if r["at"] > now - 10 * 60), None)
        if r:
            when = f"at about {clock_text(r['at'])}" if r["at"] > now else "about now"
            out.append(f"Your next regular, {r.get('say') or r['label']}, usually comes over {when}.")
        last = last_over(status, now)
        if last:
            out.append(f"The last one over was {last.get('say') or last['label']}, {ago(now - last['at'])}.")
        elif isinstance(status.get("nearby"), int):
            n = status["nearby"]
            out.append("There's 1 plane within 10 miles." if n == 1 else f"There are {n} planes within 10 miles.")
    if status.get("transit"):
        out.append(status["transit"])
    if not nxt and status.get("space"):
        out.append(status["space"])
    if age > 15 * 60:
        out.append(f"planespotter last updated {ago(age)}.")
    return " ".join(out)
