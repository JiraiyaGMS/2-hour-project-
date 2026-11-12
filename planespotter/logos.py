"""Airline logos, fetched once per airline and kept on this computer.

Logos come from the community airline-logos collection on GitHub
(github.com/Jxck-S/airline-logos), keyed by the airline's three-letter ICAO
code, which is also how airline callsigns start (DAL123 = Delta). Logos are
trademarks of their airlines; they're only used here to label flights.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .adsb import USER_AGENT

SOURCES = [
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{code}.png",
    "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/radarbox_logos/{code}.png",
]
CODE = re.compile(r"^[A-Z]{3}$")
PNG = b"\x89PNG\r\n\x1a\n"
MISSING_FOR = 7 * 86400  # don't ask again for a week about an airline with no logo


class LogoCache:
    def __init__(self, folder: Path, fetch=None, clock=time.time):
        self.folder = Path(folder)
        self.fetch = fetch or self._download
        self.clock = clock
        self.lock = threading.Lock()

    @staticmethod
    def _download(url: str) -> bytes | None:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def get(self, code: str) -> bytes | None:
        """The PNG for an airline code like "DAL", or None if there isn't one."""
        if not CODE.match(code):
            return None
        png = self.folder / f"{code}.png"
        missing = self.folder / f"{code}.missing"
        with self.lock:  # one download per airline, even with many requests at once
            if png.is_file():
                return png.read_bytes()
            if missing.is_file() and self.clock() - missing.stat().st_mtime < MISSING_FOR:
                return None
            try:
                for source in SOURCES:
                    data = self.fetch(source.format(code=code))
                    if data and data.startswith(PNG):
                        self.folder.mkdir(parents=True, exist_ok=True)
                        png.write_bytes(data)
                        return data
            except (urllib.error.URLError, TimeoutError, OSError):
                return None  # offline or GitHub hiccup: try again next time
            self.folder.mkdir(parents=True, exist_ok=True)
            missing.write_text("no logo found\n")
            return None


def airline_code(callsign: str) -> str | None:
    """'DAL1140' -> 'DAL'. Private planes (N123AB) and odd callsigns have none."""
    m = re.match(r"^([A-Z]{3})\d", callsign or "")
    return m.group(1) if m else None
