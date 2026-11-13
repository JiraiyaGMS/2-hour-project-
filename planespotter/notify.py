"""Phone notifications through ntfy (https://ntfy.sh): a free app, no account needed.

You pick a long, random "topic" name (setup makes one); the ntfy app on your
phone subscribes to it, and planespotter posts each alert there. Anyone who
knows the topic name could read it, so keep it to yourself; nothing in the
messages says where you live.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import string
import threading
import time
import urllib.error
import urllib.request

from .adsb import USER_AGENT
from .logos import airline_code

log = logging.getLogger(__name__)

LOGO_URL = "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{code}.png"


def new_topic() -> str:
    """A hard-to-guess topic name, like planespotter-k3m9x2q7w4c8."""
    alphabet = string.ascii_lowercase + string.digits
    return "planespotter-" + "".join(secrets.choice(alphabet) for _ in range(14))


def derived_topic(main_topic: str, kind: str) -> str:
    """A second private topic made from your main one: "relay" (the widget and Siri read it),
    "cmd" (commands from your phone), "family" (highlights for family), "share" (your friend code)."""
    digest = hashlib.sha256(f"{main_topic}:{kind}".encode()).hexdigest()
    return f"planespotter-{kind}-{digest[:14]}"


# ntfy.sh allows 250 messages a day from one internet connection (reading is free). Plane alerts
# always come first; the extras below only use what's left, and never more than these each day.
EXTRA_CAPS = {"relay": 96, "family": 20, "share": 30, "reply": 20}
KEEP_FOR_ALERTS = 80   # stop the extras when ntfy says fewer than this are left today


class NtfyQuota:
    """Counts what planespotter sends to ntfy, and asks ntfy how many messages are left today."""

    def __init__(self, db_path, server: str, clock=time.time, fetch_remaining=None):
        self.clock = clock
        self.server = server.rstrip("/")
        self.fetch_remaining = fetch_remaining or self._remaining
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS ntfy_sent (t REAL NOT NULL, kind TEXT NOT NULL)")
        self.db.commit()
        self._left, self._left_at = None, -1e12

    def _remaining(self) -> int | None:
        req = urllib.request.Request(f"{self.server}/v1/account", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(json.loads(resp.read())["stats"]["messages_remaining"])

    def remaining(self) -> int | None:
        """Messages ntfy will still accept today (checked every 20 minutes); None if unknown."""
        now = self.clock()
        if now - self._left_at > 1200:
            self._left_at = now
            try:
                self._left = self.fetch_remaining()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
                self._left = None  # a private ntfy server, or offline: just use our own caps
        return self._left

    def sent(self, kind: str) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM ntfy_sent WHERE kind = ? AND t >= ?",
                                   (kind, self.clock() - 86400)).fetchone()[0]

    def record(self, kind: str) -> None:
        now = self.clock()
        with self.lock:
            self.db.execute("INSERT INTO ntfy_sent (t, kind) VALUES (?, ?)", (now, kind))
            self.db.execute("DELETE FROM ntfy_sent WHERE t < ?", (now - 2 * 86400,))
            self.db.commit()
        if self._left is not None:
            self._left -= 1

    def allow(self, kind: str) -> bool:
        if kind == "alert":
            return True
        left = self.remaining()
        if left is not None and left < KEEP_FOR_ALERTS:
            return False
        return self.sent(kind) < EXTRA_CAPS.get(kind, 10)


class PushNotifier:
    def __init__(self, cfg, map_url: str = "", post=None, quota: NtfyQuota | None = None):
        self.cfg = cfg
        self.map_url = map_url.rstrip("/")
        self.post = post or self._post
        self.quota = quota
        self.retry_wait = 8.0  # seconds before trying again when ntfy says "too many requests"
        self.refused_at: dict[str, float] = {}  # when we last logged holding back each kind of extra

    def topic(self, kind: str) -> str:
        """The private topic for the widget ("relay"), commands ("cmd"), family ("family") or friends ("share")."""
        return derived_topic(self.cfg.ntfy_topic, kind) if self.cfg.ntfy_topic else ""

    def topic_url(self, kind: str) -> str:
        return f"{self.cfg.ntfy_server.rstrip('/')}/{self.topic(kind)}"

    def publish(self, kind: str, title: str = "", message: str = "", **extra) -> bool:
        """Send to one of the extra topics, within the day's allowance. Returns False if not sent."""
        if not self.enabled:
            return False
        if self.quota is not None and not self.quota.allow(kind):
            if time.time() - self.refused_at.get(kind, -1e12) > 3600:  # say so once an hour, not every check
                self.refused_at[kind] = time.time()
                log.info("not sending to ntfy (%s): saving today's messages for plane alerts", kind)
            return False
        payload = {"topic": self.topic(kind), "message": message, **{k: v for k, v in extra.items() if v}}
        if title:
            payload["title"] = title
        try:
            self.post(self.cfg.ntfy_server.rstrip("/") + "/", payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.info("couldn't send to ntfy (%s): %s", kind, exc)
            return False
        if self.quota is not None:
            self.quota.record(kind)
        return True

    def family(self, title: str, message: str, tags=("airplane",), click: str = "") -> bool:
        """A highlight for your family's feed (special planes, the space station, your flight)."""
        if not self.cfg.family_alerts:
            return False
        return self.publish("family", title, message, tags=list(tags), click=click)

    def poll(self, kind_or_topic: str, since: str = "latest", raw: bool = False):
        """Read a topic (free: reading doesn't count against ntfy's daily limit)."""
        topic = self.topic(kind_or_topic) if kind_or_topic in ("relay", "cmd", "family", "share") else kind_or_topic
        url = f"{self.cfg.ntfy_server.rstrip('/')}/{topic}/{'raw' if raw else 'json'}?poll=1&since={since}"
        headers = {"User-Agent": USER_AGENT}
        if self.cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {self.cfg.ntfy_token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
        if raw:
            return body
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.ntfy_topic)

    def _post(self, url: str, payload: dict) -> None:
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if self.cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {self.cfg.ntfy_token}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    def send(self, title: str, message: str, click: str = "", tags=("airplane",), priority: int = 3,
             icon: str = "", image: str = "", kind: str = "alert") -> bool:
        """Post one notification. Returns False (and logs why) if it couldn't be sent."""
        if not self.enabled:
            return False
        payload = {"topic": self.cfg.ntfy_topic, "title": title, "message": message,
                   "tags": list(tags), "priority": priority}
        if click:
            payload["click"] = click
        if icon:
            payload["icon"] = icon
        if image:
            payload["attach"] = image
        for attempt in (1, 2):
            try:
                self.post(self.cfg.ntfy_server.rstrip("/") + "/", payload)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 1:
                    time.sleep(self.retry_wait)  # ntfy wants a moment between messages: try once more
                    continue
                log.error("couldn't send the phone notification: %s", exc)
                return False
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.error("couldn't send the phone notification: %s", exc)
                return False
        if self.quota is not None:
            self.quota.record(kind)
        return True

    # --- what planespotter sends ---------------------------------------------------

    def link(self, **params) -> str:
        if not self.map_url:
            return ""
        query = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
        return f"{self.map_url}/?{query}" if query else self.map_url

    def plane_over_home(self, passes, cfg) -> bool:
        """One notification for the plane(s) that just came over."""
        from .emails import field, fmt_alt, fmt_dist, highlights, label, route  # avoid a circular import
        from .geo import compass

        first = passes[0]
        if len(passes) == 1:
            title = f"✈ {label(first)} over {cfg.home_name}"
        else:
            title = f"✈ {len(passes)} planes over {cfg.home_name}"
        lines = []
        for p in passes[:5]:
            bits = [label(p), p["airline"], route(p)]
            where = f"{fmt_dist(p['closest_miles'])} {compass(p['closest_bearing'])}, {fmt_alt(p['closest_alt_ft'])}"
            lines.append(" · ".join(b for b in bits if b) + f"\n{where}")
            lines.extend(highlights(p))
        if any(json.loads(field(p, "firsts", "[]") or "[]") for p in passes):
            title = "✨" + title[1:]  # a first: an airline or aircraft type you've never spotted
        code = airline_code(first["callsign"])
        return self.send(
            title, "\n".join(lines),
            click=self.link(flight=first["id"]),
            icon=LOGO_URL.format(code=code) if code else "",
            image=first["photo_url"] if len(passes) == 1 else "",
        )

    def special(self, title: str, message: str, hex_code: str, callsign: str, emergency: bool = False,
                photo: str = "") -> bool:
        code = airline_code(callsign)
        return self.send(
            title, message, click=self.link(plane=hex_code),
            tags=("rotating_light",) if emergency else ("sparkles", "airplane"),
            priority=5 if emergency else 4,
            icon=LOGO_URL.format(code=code) if code else "", image=photo,
        )
