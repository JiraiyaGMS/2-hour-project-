"""Phone notifications through ntfy (https://ntfy.sh): a free app, no account needed.

You pick a long, random "topic" name (setup makes one); the ntfy app on your
phone subscribes to it, and planespotter posts each alert there. Anyone who
knows the topic name could read it, so keep it to yourself; nothing in the
messages says where you live.
"""

from __future__ import annotations

import json
import logging
import secrets
import string
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


class PushNotifier:
    def __init__(self, cfg, map_url: str = "", post=None):
        self.cfg = cfg
        self.map_url = map_url.rstrip("/")
        self.post = post or self._post

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
             icon: str = "", image: str = "") -> bool:
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
        try:
            self.post(self.cfg.ntfy_server.rstrip("/") + "/", payload)
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.error("couldn't send the phone notification: %s", exc)
            return False

    # --- what planespotter sends ---------------------------------------------------

    def link(self, **params) -> str:
        if not self.map_url:
            return ""
        query = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
        return f"{self.map_url}/?{query}" if query else self.map_url

    def plane_over_home(self, passes, cfg) -> bool:
        """One notification for the plane(s) that just came over."""
        from .emails import fmt_alt, fmt_dist, highlights, label, route  # avoid a circular import
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
        if any(highlights(p) for p in passes):
            title = "✨" + title[1:]
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
