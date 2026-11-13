"""`python3 -m planespotter phone`: everything to set up your phone, emailed to you (works from anywhere)."""

from __future__ import annotations

from html import escape

from .emails import Email, _page
from .notify import derived_topic


def kit(cfg, map_url: str) -> dict:
    from .webmap import relay_url, widget_script

    server = cfg.ntfy_server.rstrip("/")
    topics = {k: derived_topic(cfg.ntfy_topic, k) for k in ("relay", "cmd", "family", "share")}
    return {
        "widget": widget_script(cfg, map_url),
        "siri_url": relay_url(cfg),
        "cmd_url": f"{server}/{topics['cmd']}",
        "family_topic": topics["family"],
        "family_url": f"{server}/{topics['family']}",
        "friend_code": topics["share"],
        "calendar_url": map_url.replace("http://", "webcal://") + "/calendar.ics" if map_url else "",
    }


def kit_email(cfg, k: dict) -> Email:
    sections = [
        ("1. The home-screen widget (works anywhere)", [
            "Install the free Scriptable app.",
            "Open the attached file planespotter-widget.js, or copy the script at the bottom of this email "
            "(press and hold, Select All, Copy).",
            "In Scriptable tap +, paste, rename it planespotter, tap ▶ to try it, then Done.",
            "Home screen: press and hold an empty spot → Edit → Add Widget → Scriptable → Add Widget. "
            "Press and hold the widget → Edit Widget → Script: planespotter.",
        ]),
        ("2. Siri: \"What's that plane?\" (works anywhere)", [
            "Open Shortcuts and tap +.",
            f"Add Get Contents of URL and set the URL to: {k['siri_url']}",
            "Add Get Dictionary from Input.",
            "Add Get Dictionary Value, and set Key to: siri",
            "Add Speak Text. Rename the shortcut What's that plane, then say \"Hey Siri, what's that plane?\"",
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
