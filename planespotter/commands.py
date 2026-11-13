"""Commands from your phone, from anywhere, through your private ntfy command topic.

Open the topic's page in Safari (planespotter's setup email has the link) and
type a message, or use a Shortcut:

    travel 33.8090,-117.9265 Disneyland   watch where you are for two days
    home                                  stop travel mode
    flight UA1234                         follow your flight (add "tomorrow" or a date)
    flights off                           stop following your flights
    status                                what planespotter is doing
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error

from .myflight import add_flight, my_flights, parse_day, remove_flight
from .travel import parse_where

log = logging.getLogger(__name__)

POLL_EVERY = 60
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def handle(text: str, cfg, store, travel, now: float) -> str:
    """Do one command. Returns the reply."""
    words = text.strip().split(maxsplit=1)
    if not words:
        return ""
    verb, rest = words[0].lower(), (words[1] if len(words) > 1 else "")
    if verb in ("travel", "here", "watch"):
        if rest.lower() in ("off", "stop", "home"):
            verb = "home"
        else:
            where = parse_where(rest)
            if where is None:
                return "📍 Send it like: travel 33.8090,-117.9265 (and a name if you like)"
            if travel is None:
                return "Travel mode isn't available here."
            s = travel.start(*where)
            return f"🧳 Travel mode: watching {s['name']} until {s['until_text']}. Send \"home\" to stop."
    if verb in ("home", "stop"):
        if travel is not None:
            travel.stop()
        return "🏠 Travel mode is off. Just watching home."
    if verb in ("flight", "track", "follow"):
        if rest.lower() in ("off", "stop", "none", "clear"):
            for f in my_flights(store):
                remove_flight(store, f["callsign"])
            return "✈ Not following any flights now."
        parts = rest.split()
        if not parts:
            return "✈ Send it like: flight UA1234 (or flight UA1234 tomorrow)"
        day_word = parts[-1] if len(parts) > 1 and (parts[-1].lower() in ("today", "tomorrow") or parts[-1][:2] == "20") else ""
        number = " ".join(parts[:-1] if day_word else parts)
        try:
            f = add_flight(store, number, parse_day(day_word, cfg.local(now).date()), now)
        except ValueError as exc:
            return f"✈ {exc}"
        return (f"✈ Following {f['label']} ({f['callsign']}) on {f['day']}. You'll hear when it takes off, "
                "if it flies over home, and when it lands.")
    if verb in ("flights", "status"):
        if verb == "flights" and rest.lower() in ("off", "stop", "clear"):
            return handle("flight off", cfg, store, travel, now)
        lines = []
        t = travel.status() if travel is not None else {"on": False}
        lines.append(f"🧳 Travel mode: {t['name']} until {t['until_text']}" if t["on"] else "🏠 Watching home")
        for f in my_flights(store):
            if f["state"] != "done":
                lines.append(f"✈ {f['label']}: {f['state']}")
        return "\n".join(lines)
    return 'Commands: "travel <lat>,<lon>", "home", "flight UA1234", "flights off", "status"'


class Commands:
    """Reads new messages on the command topic once a minute and answers on your main topic."""

    def __init__(self, cfg, store, notifier, travel=None, clock=time.time):
        self.cfg, self.store, self.notifier, self.travel, self.clock = cfg, store, notifier, travel, clock

    def step(self) -> list[str]:
        if not self.notifier.enabled:
            return []
        since = self.store.get_meta("cmd_since") or str(int(self.clock()) - 120)
        messages = self.notifier.poll("cmd", since=since)
        replies = []
        for m in messages:
            if m.get("event") != "message":
                continue
            self.store.set_meta("cmd_since", m["id"])
            reply = handle(m.get("message", ""), self.cfg, self.store, self.travel, self.clock())
            if reply:
                log.info("command %r: %s", m.get("message", ""), reply.splitlines()[0])
                if self.notifier.quota is None or self.notifier.quota.allow("reply"):
                    self.notifier.send("planespotter", reply, tags=("robot",), kind="reply")
                replies.append(reply)
        if not messages and not self.store.get_meta("cmd_since"):
            self.store.set_meta("cmd_since", str(int(self.clock())))
        return replies


def start_command_worker(cfg, notifier, travel) -> threading.Thread:
    from .store import Store

    def run():
        store = Store(cfg.db_path)
        commands = Commands(cfg, store, notifier, travel)
        while True:
            try:
                commands.step()
                if travel is not None:
                    travel.check()
            except FETCH_ERRORS as exc:
                log.debug("couldn't read commands: %s", exc)
            except Exception:
                log.exception("phone commands failed")
            time.sleep(POLL_EVERY)

    thread = threading.Thread(target=run, name="commands", daemon=True)
    thread.start()
    return thread
