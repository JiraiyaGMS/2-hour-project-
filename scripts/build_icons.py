#!/usr/bin/env python3
"""Rebuild the icon sprite in planespotter/web/index.html (and icons.js for the other pages).

The icons are Lucide's (https://lucide.dev, ISC licence). Get them once with
    npm pack lucide-static && tar xzf lucide-static-*.tgz
then run
    python3 scripts/build_icons.py package/icons
Only the icons named in ICONS are kept, so the page stays small.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "planespotter" / "web"

# our name -> Lucide's file name
ICONS = {
    "plane": "plane", "takeoff": "plane-takeoff", "landing": "plane-landing", "bell": "bell", "bell-off": "bell-off",
    "bell-ring": "bell-ring", "volume": "volume-2", "mute": "volume-x", "pin": "map-pin", "luggage": "luggage",
    "telescope": "telescope", "external": "arrow-up-right", "share": "share", "gate": "door-open", "moon": "moon",
    "sun": "sun", "cloud": "cloud", "cloud-sun": "cloud-sun", "cloudy": "cloudy", "fog": "cloud-fog",
    "sunset": "sunset", "sunrise": "sunrise", "eye": "eye", "check": "check", "target": "target", "camera": "camera",
    "images": "images", "sparkles": "sparkles", "siren": "siren", "history": "history", "timer": "timer",
    "clock": "clock", "hourglass": "hourglass", "mic": "mic", "satellite": "satellite", "calendar": "calendar",
    "calendar-plus": "calendar-plus", "star": "star", "flame": "flame", "video": "video", "stop": "square",
    "play": "play", "pause": "pause", "download": "download", "refresh": "refresh-cw", "alert": "triangle-alert",
    "party": "party-popper", "home": "house", "user": "user", "x": "x", "search": "search",
    "chevron-right": "chevron-right", "chevron-down": "chevron-down", "radar": "radar", "list": "list",
    "trophy": "trophy", "noise": "audio-lines", "cube": "box", "map": "map", "phone": "smartphone",
    "navigation": "navigation", "compass": "compass", "plus": "plus", "trash": "trash-2", "info": "info",
    "wind": "wind", "award": "award", "ticket": "ticket", "globe": "globe", "earth": "earth", "crown": "crown",
    "medal": "medal", "bird": "bird", "zap": "zap", "gem": "gem", "package": "package", "briefcase": "briefcase",
    "feather": "feather", "fan": "fan", "crosshair": "crosshair", "ruler": "ruler", "users": "users", "copy": "copy",
    "layers": "layers", "binoculars": "binoculars", "route": "route", "gauge": "gauge", "mountain": "mountain",
    "handshake": "handshake", "scan": "scan-eye", "offline": "cloud-off", "ear": "ear", "locate": "locate-fixed",
    "undo": "rotate-ccw", "settings": "settings", "arrow-right": "arrow-right", "arrow-left": "arrow-left",
    "arrow-up": "arrow-up", "arrow-down": "arrow-down", "sheet": "file-spreadsheet", "circle-check": "circle-check",
    "shield": "shield", "tower": "tower-control", "chart": "chart-column", "lock": "lock", "mail": "mail",
    "grid": "grid-3x3", "rocket": "rocket", "orbit": "orbit", "glasses": "glasses", "link": "link",
    "upload": "upload", "tv": "tv", "more": "ellipsis", "sort": "arrow-up-down", "pointer": "pointer",
    "activity": "activity", "passport": "book-open-check", "key": "key", "logout": "log-out", "pencil": "pencil",
    "cloud-moon": "cloud-moon", "hash": "hash", "circle-alert": "circle-alert",
}


def body_of(svg: str) -> str:
    """The shapes inside a Lucide <svg>, on one line."""
    inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
    return re.sub(r"\s+", " ", inner).replace(" />", "/>").replace("> <", "><").strip()


def main(folder: str) -> int:
    source = Path(folder)
    shapes = {}
    for name, lucide in sorted(ICONS.items()):
        path = source / f"{lucide}.svg"
        if not path.is_file():
            print(f"no such Lucide icon: {lucide}", file=sys.stderr)
            return 1
        shapes[name] = body_of(path.read_text(encoding="utf-8"))
    sprite = ('<svg xmlns="http://www.w3.org/2000/svg" hidden aria-hidden="true">'
              + "".join(f'<symbol id="i-{n}" viewBox="0 0 24 24">{s}</symbol>' for n, s in shapes.items()) + "</svg>")
    page = WEB / "index.html"
    html = page.read_text(encoding="utf-8")
    start, end = "<!-- icons: built by scripts/build_icons.py -->", "<!-- /icons -->"
    a, b = html.index(start), html.index(end)
    page.write_text(html[:a] + start + "\n" + sprite + "\n" + html[b:], encoding="utf-8")
    # The other pages (sky view, sign-in...) take what they need from this list.
    (WEB / "icons.json").write_text(
        "{\n" + ",\n".join(f'  "{n}": "{s.replace(chr(34), chr(39))}"' for n, s in shapes.items()) + "\n}\n",
        encoding="utf-8")
    print(f"{len(shapes)} icons, {len(sprite) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "package/icons"))
