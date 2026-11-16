"""`python3 -m planespotter phone`: everything to set up your phone, emailed to you (works from anywhere)."""

from __future__ import annotations

from html import escape

from .emails import Email, _page
from .notify import derived_topic


def kit(cfg, map_url: str, token: str = "") -> dict:
    from .webmap import command_url, relay_url, widget_script

    server = cfg.ntfy_server.rstrip("/")
    topics = {k: derived_topic(cfg.ntfy_topic, k) for k in ("relay", "cmd", "family", "share")}
    return {
        "widget": widget_script(cfg, map_url, with_keys=True, token=token),
        "siri_url": relay_url(cfg),
        "cmd_url": command_url(cfg),
        "family_topic": topics["family"],
        "family_url": f"{server}/{topics['family']}",
        "friend_code": topics["share"],
        "calendar_url": map_url.replace("http://", "webcal://") + "/calendar.ics" if map_url else "",
    }


def kit_email(cfg, k: dict) -> Email:
    sections = [
        ("1. The home-screen widget (works anywhere, even with your Mac off)", [
            "Install the free Scriptable app.",
            "Open the attached file planespotter-widget.js, or copy the script at the bottom of this email "
            "(press and hold, Select All, Copy).",
            "In Scriptable tap +, paste, rename it planespotter, tap the play button to try it, then Done. "
            "When it asks for your location, tap Allow While Using App.",
            "In the iPhone's Settings → Scriptable → Location, choose While Using the App or Widgets, and turn "
            "Precise Location on. (It finds the planes over wherever you are, even with your Mac off, and tells "
            "your Mac where you are so your alerts follow you.)",
            "Home screen: press and hold an empty spot → Edit → Add Widget → Scriptable → Add Widget. "
            "Press and hold the widget → Edit Widget → Script: planespotter.",
            "Tap the widget any time for a live map of the planes around you. On the map: Track a flight "
            "(search by flight number, airline or airport) and Point (opens Safari: tap Start, allow motion & "
            "orientation, and point your phone at a plane; the one in the blue beam is the one it tells you about).",
        ]),
        ("2. Siri (works anywhere; set up the widget script first)", [
            "Open Shortcuts and tap +.",
            "Tap Add Action (or the search bar), type Run Script, and choose the one from Scriptable. "
            "Tap Script and choose planespotter. Tap the arrow › on that step and turn Show When Run off "
            "(so Siri answers straight away, without a Done button).",
            "Search for Speak Text and add it underneath. It should say Speak Script Result (if not, tap the "
            "blank and choose Script Result).",
            "Tap the name at the top, rename the shortcut Planespotter, and tap Done. "
            "Then say \"Hey Siri, Planespotter\".",
        ]),
        ("2b. Siri: \"Where's my plane?\"", [
            "Make a second shortcut the same way (Run Script → planespotter, Show When Run off, then Speak Text), and in the "
            "Run Script step's Parameter (or Texts) type: my plane",
            "Name it Where's my plane. It tells you where the flight you picked in Track a flight is, or when and where it landed.",
        ]),
        ("2c. Siri: \"Find a flight\" (any flight)", [
            "Shortcuts → + → Ask for Input (Text, prompt: Which flight?), then Run Script → planespotter (Show When Run off, "
            "Parameter: Provided Input), then Speak Text. Name it Find a flight.",
            "Say \"Hey Siri, find a flight\", then the flight when it asks, like \"Alaska 1349\".",
        ]),
        ("3. Commands from your phone (travel mode, your flight)", [
            f"Open this page in Safari and bookmark it: {k['cmd_url']}",
            "Type a message and tap send. For example: travel 33.8090,-117.9265 Disneyland  ·  home  ·  "
            "flight UA1234  ·  flight UA1234 tomorrow  ·  flights off  ·  status",
            "planespotter answers in your ntfy app within a minute.",
            "Optional \"Spot from here\" shortcut: in Shortcuts tap +, add Get Current Location, then Text with "
            "the words travel , then (tap Current Location → Latitude) , (Current Location → Longitude). "
            f"Then Get Contents of URL: {k['cmd_url']}, tap Show More, Method POST, Request Body File, and choose the Text.",
        ]),
        ("4. Your family's feed (highlights only)", [
            "Ask them to install the free ntfy app, tap +, and subscribe to this topic name:",
            k["family_topic"],
            "They'll get the A380s, 747s, military planes, emergencies, the space station, and your flight "
            "taking off and landing (at most 20 a day). Not every plane.",
        ]),
        ("5. Spotting with friends", [
            f"Your friend code: {k['friend_code']}",
            "Send it to friends who run planespotter. Add theirs to your .env file like:",
            "FRIENDS=Sam=planespotter-share-abc123def456; Alex=planespotter-share-0123456789ab",
        ]),
        ("6. Calendar (space station passes, best time to look up)", [
            "The weekly email has a calendar file attached: tap it to add the events.",
            f"Or, on your home Wi-Fi, subscribe in Settings → Calendar → Accounts → Add Account → Other → "
            f"Add Subscribed Calendar: {k['calendar_url']}" if k["calendar_url"] else "",
        ]),
    ]
    text, html = [], ['<h1 style="font-size:24px;margin:0 0 6px">Your planespotter phone setup</h1>'
                      '<p style="font-size:14px;color:#5f6b7a">Everything below works from anywhere: a hotel, '
                      'mobile data... as long as planespotter is running on your Mac.</p>']
    for title, steps in sections:
        steps = [x for x in steps if x]
        text += ["", title.upper(), *[f"  - {x}" for x in steps]]
        html.append(f'<h2 style="font-size:17px;margin:22px 0 6px">{escape(title)}</h2><ol style="font-size:14px;'
                    'padding-left:20px;margin:0">' + "".join(f'<li style="margin:4px 0">{escape(x)}</li>' for x in steps)
                    + "</ol>")
    text += ["", "THE WIDGET SCRIPT (copy everything below this line)", "", k["widget"]]
    html.append('<h2 style="font-size:17px;margin:22px 0 6px">The widget script</h2>'
                '<pre style="font-size:11px;white-space:pre-wrap;background:#f4f7fb;padding:10px;border-radius:8px">'
                f'{escape(k["widget"])}</pre>')
    subject = "Your planespotter phone setup"
    return Email(subject=subject, text="\n".join(text), html=_page(subject, "".join(html)),
                 attachments=[("planespotter-widget.js", k["widget"].encode("utf-8"), "text/plain")])


# --- putting the script straight into Scriptable, through iCloud Drive (no copy and paste) ---

SCRIPTABLE_ICLOUD = "~/Library/Mobile Documents/iCloud~dk~simonbs~Scriptable/Documents"
FIRST_LINE = "// planespotter for the free Scriptable app"
HEADER = "// Variables used by Scriptable."


def _in_the_cloud(path) -> bool:
    """A file iCloud keeps only in the cloud (newer macOS shows it under its real name): reading it makes
    macOS download it first, which can take a long time, or forever with no internet."""
    try:
        return bool(getattr(path.stat(), "st_flags", 0) & 0x40000000)  # SF_DATALESS
    except OSError:
        return False


def _read_quick(path, seconds: float = 10, size: int = -1) -> str | None:
    """The file's text, or None if it can't be read in time (iCloud still downloading it)."""
    import threading

    box: list = []

    def read() -> None:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                box.append(f.read(size))
        except OSError:
            box.append(None)
    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(seconds)
    return box[0] if box else None


def _is_planespotter(path) -> bool:
    """Is this script planespotter's? Scripts with planespotter in their name are read (waiting a little for
    iCloud); other people's scripts are only looked at when they're already on this Mac."""
    named = "planespotter" in path.name.lower()
    if not named and _in_the_cloud(path):
        return False
    text = _read_quick(path, 10 if named else 3, 2000)
    return bool(text and FIRST_LINE in text)


def install_in_scriptable(script: str, folder=None, backup_dir=None, download=None, wait_s: float = 30) -> dict:
    """Write the script into Scriptable's iCloud Drive folder on this Mac; iCloud then puts it on the iPhone.

    Every planespotter script already there is replaced (so a second copy can't keep the old one), keeping
    the icon and colour Scriptable stores at the top; the old ones are kept in ``backup_dir``. With none
    there yet, it's added as "planespotter". Returns {"folder", "updated": [names], "added": name or None},
    or {"error": why} when this Mac has no Scriptable folder in iCloud Drive.
    """
    import shutil
    import subprocess
    import time
    from pathlib import Path

    folder = Path(folder or SCRIPTABLE_ICLOUD).expanduser()
    if not folder.is_dir():
        return {"error": f"no Scriptable folder in iCloud Drive on this Mac ({folder})"}
    # Files iCloud hasn't downloaded to this Mac show up as ".name.js.icloud": fetch the planespotter ones first.
    waiting = [folder / p.name[1:-len(".icloud")] for p in folder.glob(".*.js.icloud") if "planespotter" in p.name.lower()]
    if waiting:
        fetch = download or (lambda p: subprocess.run(["brctl", "download", str(p)], capture_output=True, timeout=60)
                             if shutil.which("brctl") else None)
        for p in waiting:
            fetch(p)
        until = time.time() + wait_s
        while time.time() < until and not all(p.exists() for p in waiting):
            time.sleep(1)
    found = sorted(p for p in folder.glob("*.js") if _is_planespotter(p))
    added = None
    if not found:
        found = [folder / "planespotter.js"]
        added = None if found[0].exists() else "planespotter"
    for path in found:
        header = ""
        if path.exists():
            old = _read_quick(path, 15) or ""  # (not waiting for ever on iCloud: then it's just replaced)
            if backup_dir and old:
                Path(backup_dir).mkdir(parents=True, exist_ok=True)
                (Path(backup_dir) / f"previous-{path.name}").write_text(old, encoding="utf-8")
            if old.startswith(HEADER):  # Scriptable's icon and colour for the script
                lines = old.splitlines(keepends=True)
                while lines and lines[0].startswith("//") and not lines[0].startswith(FIRST_LINE):
                    header += lines.pop(0)
        path.write_text(header + script, encoding="utf-8")
    return {"folder": str(folder), "updated": [p.stem for p in found if p.stem != added], "added": added}
