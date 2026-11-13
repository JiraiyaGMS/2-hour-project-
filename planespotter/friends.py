"""Spotting with friends: compare collections, and hear when a friend spots something rare.

Each planespotter posts a small "card" (this week's planes, collection totals,
bingo, quiz score, and anything rare spotted lately) to its own share topic
on ntfy, about once an hour. Your friend code is that topic's name: give it
to friends, and add theirs to FRIENDS in .env. planespotter reads your
friends' latest cards (reading is free) and shows a leaderboard in Collection.
Nothing about where anyone lives is shared.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error

from .emails import label

log = logging.getLogger(__name__)

POST_EVERY = 3600
READ_EVERY = 15 * 60
FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def parse_friends(text: str) -> list[tuple[str, str]]:
    """'Sam=planespotter-share-abc123; Alex=planespotter-share-def456' -> [(name, code)]."""
    out = []
    for part in (text or "").split(";"):
        if "=" in part:
            name, code = (x.strip() for x in part.split("=", 1))
            if name and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", code):
                out.append((name[:30], code))
    return out


def rare_recent(store, cfg, now: float) -> list[dict]:
    """Rare spots in the last day: special types, military, emergencies, and firsts."""
    out = []
    reasons = {"type": "a rare big plane", "military": "a military aircraft", "emergency": "an emergency"}
    for r in store.db.execute("SELECT hex, reason, at FROM special_seen WHERE at >= ? AND reason != 'helicopter' "
                              "ORDER BY at DESC LIMIT 5", (now - 86400,)):
        p = store.db.execute("SELECT * FROM passes WHERE hex = ? ORDER BY ABS(first_seen - ?) LIMIT 1",
                             (r["hex"], r["at"])).fetchone()
        what = (p["aircraft_desc"] or p["type_code"]) if p and r["reason"] == "type" else reasons.get(r["reason"], "")
        out.append({"at": round(r["at"]), "what": what or "something special", "label": label(p) if p else r["hex"].upper()})
    for p in store.db.execute("SELECT * FROM passes WHERE first_seen >= ? AND firsts != '[]' ORDER BY first_seen DESC "
                              "LIMIT 3", (now - 86400,)):
        for f in json.loads(p["firsts"])[:1]:
            out.append({"at": round(p["first_seen"]), "what": f.replace(" you've spotted", "").replace("First ", "their first "),
                        "label": label(p)})
    return sorted(out, key=lambda x: -x["at"])[:5]


def my_card(store, cfg, now: float) -> dict:
    from .games import bingo, quiz_score

    week = store.passes_between(now - 7 * 86400, now + 1)
    totals = store.db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT type_code), COUNT(DISTINCT substr(callsign, 1, 3)) FROM passes").fetchone()
    card = bingo(store, cfg, now)
    quiz = quiz_score(store)
    return {"v": 1, "name": cfg.share_name or cfg.home_name, "updated": round(now),
            "week": len(week), "planes": totals[0], "types": totals[1], "airlines": totals[2],
            "bingo": card["filled"], "lines": card["lines"], "quiz": [quiz["right"], quiz["answered"]],
            "rare": rare_recent(store, cfg, now)}


class Friends:
    def __init__(self, cfg, store, notifier, clock=time.time):
        self.cfg, self.store, self.notifier, self.clock = cfg, store, notifier, clock
        self.cards: dict[str, dict] = {}
        self.last_post, self.last_read, self.last_rare = -1e12, -1e12, None

    def code(self) -> str:
        return self.notifier.topic("share") if self.notifier and self.notifier.enabled else ""

    def post(self, force: bool = False) -> bool:
        """Post your card (hourly, or straight away when you spot something rare)."""
        now = self.clock()
        card = my_card(self.store, self.cfg, now)
        rare_key = json.dumps(card["rare"][:1])
        new_rare = bool(card["rare"]) and rare_key != self.last_rare
        if not (force or new_rare or now - self.last_post >= POST_EVERY):
            return False
        if self.notifier.publish("share", message=json.dumps(card, separators=(",", ":"))):
            self.last_post, self.last_rare = now, rare_key
            return True
        return False

    def read(self) -> list[str]:
        """Read friends' latest cards; tell you about anything rare they've spotted since last time."""
        news = []
        for name, code in parse_friends(self.cfg.friends):
            try:
                body = self.notifier.poll(code, since="latest", raw=True).strip()
                card = json.loads(body.splitlines()[-1]) if body else None
            except (ValueError, IndexError):
                card = None
            if not isinstance(card, dict):
                continue
            card["friend"] = name
            self.cards[code] = card
            seen = float(self.store.get_meta(f"friend_rare:{code}") or 0)
            fresh = [r for r in card.get("rare", []) if r.get("at", 0) > seen]
            if fresh:
                self.store.set_meta(f"friend_rare:{code}", str(max(r["at"] for r in fresh)))
                if seen:  # not on the very first read (that would be old news)
                    r = fresh[0]
                    text = f"🎉 {name} just spotted {r['what']} ({r['label']})"
                    if self.notifier.quota is None or self.notifier.quota.allow("reply"):
                        self.notifier.send(text, "From your friends on planespotter.", tags=("tada",), kind="reply")
                    news.append(text)
        return news

    def board(self, store=None) -> dict:
        """You and your friends, for the Collection tab (``store``: the caller's own database connection)."""
        me = my_card(store or self.store, self.cfg, self.clock())
        rows = [{**me, "friend": "You", "me": True}] + [
            {**c, "me": False} for c in self.cards.values()]
        rows.sort(key=lambda r: (-r.get("week", 0), -r.get("planes", 0)))
        return {"code": self.code(), "rows": rows, "friends": len(parse_friends(self.cfg.friends))}

    def step(self) -> None:
        now = self.clock()
        self.post()
        if now - self.last_read >= READ_EVERY:
            self.last_read = now
            for text in self.read():
                log.info("friends: %s", text)


def start_friends_worker(friends: Friends) -> threading.Thread:
    from .store import Store

    def run():
        friends.store = Store(friends.cfg.db_path)  # this thread's own connection
        time.sleep(120)  # let the startup notifications go first (ntfy dislikes bursts)
        while True:
            try:
                friends.step()
            except FETCH_ERRORS as exc:
                log.debug("couldn't reach friends: %s", exc)
            except Exception:
                log.exception("friends failed")
            time.sleep(60)

    thread = threading.Thread(target=run, name="friends", daemon=True)
    thread.start()
    return thread
