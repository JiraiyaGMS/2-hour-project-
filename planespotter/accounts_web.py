"""The front door when accounts are on: sign in, then you're passed through to your own planespotter.

Each account's planespotter (its map and data) listens only on this computer, on its own port. This is the
one address people open: it shows the sign-in page, the invite page, your account page and (for the owner)
the page for inviting people, and passes everything else to the signed-in person's planespotter. Nothing gets
through without a sign-in (or, for a phone's script, that account's token).
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
from functools import lru_cache
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .accounts import EDITABLE, AccountError, Accounts, check_settings

log = logging.getLogger(__name__)
COOKIE = "planespotter_session"
MAX_FORM = 20_000
PASS_ON = ("Content-Type", "Origin", "Sec-Fetch-Site", "Accept", "User-Agent", "Range", "If-None-Match")
PASS_BACK = ("Content-Type", "Cache-Control", "Content-Disposition", "X-Content-Type-Options", "Location", "ETag")

@lru_cache(maxsize=1)
def _icons() -> dict:
    """The same icons as the map page (web/icons.json, built by scripts/build_icons.py)."""
    try:
        return json.loads((Path(__file__).resolve().parent / "web" / "icons.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def icon(name: str) -> str:
    return f'<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">{_icons().get(name, "")}</svg>'


MARK = ('<svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="15" fill="#f08c1a"/>'
        '<path fill="#fff" transform="rotate(45 32 32) translate(9.6 9.6) scale(.7)" d="M32 3c2.1 0 3.5 2.4 3.5 5.6v14.7l21.7 '
        '12.9v5.9L35.5 35.3v12.6l7.3 5.6v4.9L32 55.6l-10.8 2.8v-4.9l7.3-5.6V35.3L6.8 42.1v-5.9l21.7-12.9V8.6C28.5 5.4 29.9 3 32 3z"/></svg>')

CSS = """
:root { color-scheme: light dark; --bg: #f3f5f8; --card: #fff; --ink: #131b25; --muted: #5b6878; --line: #e1e6ed; --accent: #e07b00; }
@media (prefers-color-scheme: dark) { :root { --bg: #090d12; --card: #0f151c; --ink: #e9eef4; --muted: #8d9aab; --line: #212b38; --accent: #ffad1f; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; -webkit-font-smoothing: antialiased; }
main { max-width: 520px; margin: 0 auto; padding: 28px 16px 60px; }
.logo { display: flex; align-items: center; gap: 10px; font-size: 24px; font-weight: 750; letter-spacing: -.02em; margin-bottom: 20px; } .logo span { color: var(--accent); }
.mark { width: 36px; height: 36px; border-radius: 9px; }
.ic { width: 1.1em; height: 1.1em; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; vertical-align: -.18em; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; margin-bottom: 16px; }
h1 { font-size: 21px; margin: 0 0 6px; } h2 { font-size: 16px; margin: 0 0 8px; }
h1, h2 { display: flex; align-items: center; gap: 8px; letter-spacing: -.01em; } h1 .ic, h2 .ic { color: var(--accent); }
p { color: var(--muted); margin: 6px 0 12px; font-size: 14.5px; line-height: 1.45; } p b, li b { color: var(--ink); }
label { display: block; font-size: 13px; font-weight: 600; margin: 12px 0 4px; }
input, select, textarea { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg); color: var(--ink); font: inherit; }
textarea { font: 11px ui-monospace, Menlo, monospace; height: 120px; }
.row { display: flex; gap: 10px; } .row > * { flex: 1; }
button, .btn { display: inline-flex; align-items: center; gap: 7px; margin-top: 14px; padding: 10px 16px; border: 0; border-radius: 99px; background: var(--ink); color: var(--card); font: inherit; font-weight: 700; cursor: pointer; text-decoration: none; }
button.ghost, .btn.ghost { background: none; color: var(--ink); border: 1px solid var(--line); font-weight: 600; }
button.danger { background: none; color: #d6453d; border: 1px solid var(--line); margin-top: 0; padding: 6px 10px; font-weight: 600; }
.error { background: #d6453d22; border: 1px solid #d6453d66; color: var(--ink); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 14.5px; }
.ok { background: #2fbf7122; border: 1px solid #2fbf7166; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 14.5px; }
.who { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); font-size: 14.5px; }
.who:first-of-type { border-top: 0; } .muted { color: var(--muted); font-size: 13px; }
code { background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 1px 5px; font-size: 13px; word-break: break-all; }
a { color: var(--accent); } nav a { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; font-weight: 600; } nav { display: flex; gap: 16px; margin-bottom: 14px; font-size: 14.5px; flex-wrap: wrap; } ol { padding-left: 20px; color: var(--muted); font-size: 14.5px; line-height: 1.5; }
"""


def page(title: str, body: str, refresh: str = "") -> bytes:
    meta = f'<meta http-equiv="refresh" content="{escape(refresh)}">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
            '<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/apple-touch-icon.png">'
            '<link rel="icon" href="/icon-192.png"><meta name="theme-color" content="#0b0f14">'
            f'<title>{escape(title)} · planespotter</title><style>{CSS}</style></head><body><main>'
            f'<div class="logo">{MARK}<div>plane<span>spotter</span></div></div>{body}</main></body></html>').encode()


def notice(text: str, kind: str = "error") -> str:
    return f'<div class="{kind}">{escape(text)}</div>' if text else ""


def home_fields(v: dict, first_time: bool) -> str:
    """The form fields for a home (the same on the invite page and the account page)."""
    g = lambda k, d="": escape(str(v.get(k, d) or d))  # noqa: E731
    return f"""
      <label for="hn">What to call it</label><input id="hn" name="HOME_NAME" value="{g('HOME_NAME', 'Home')}" maxlength="40">
      <div class="row"><div><label for="lat">Latitude</label><input id="lat" name="HOME_LAT" value="{g('HOME_LAT')}" inputmode="decimal" placeholder="45.5231" required></div>
      <div><label for="lon">Longitude</label><input id="lon" name="HOME_LON" value="{g('HOME_LON')}" inputmode="decimal" placeholder="-122.6765" required></div></div>
      <button type="button" class="ghost" id="here" style="margin-top:8px">{icon("locate")}Use where I am now</button>
      <p class="muted" id="here-note">Or find your home in Apple or Google Maps, press and hold on it, and copy the two numbers.</p>
      <div class="row"><div><label for="r">Zone size (miles)</label><input id="r" name="RADIUS_MILES" value="{g('RADIUS_MILES', '3')}" inputmode="decimal"></div>
      <div><label for="alt">Only below (feet)</label><input id="alt" name="MAX_ALTITUDE_FT" value="{g('MAX_ALTITUDE_FT')}" inputmode="numeric" placeholder="any height"></div></div>
      <label for="q">Quiet hours (no notifications)</label><input id="q" name="QUIET_HOURS" value="{g('QUIET_HOURS', '22:00-07:00' if first_time else '')}" placeholder="22:00-07:00, or empty for none">
      <input type="hidden" id="tz" name="TIMEZONE" value="{g('TIMEZONE')}">
      <script>
        var tz = document.getElementById("tz");
        try {{ if (!tz.value) tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }} catch (e) {{}}
        document.getElementById("here").onclick = function () {{
          var note = document.getElementById("here-note");
          if (!navigator.geolocation || !window.isSecureContext) {{ note.textContent = "Your browser only shares your location on a secure (https) page. Type the two numbers instead: in Apple or Google Maps, press and hold on your home and copy them."; return; }}
          note.textContent = "Finding you…";
          navigator.geolocation.getCurrentPosition(function (p) {{
            document.getElementById("lat").value = p.coords.latitude.toFixed(5);
            document.getElementById("lon").value = p.coords.longitude.toFixed(5);
            note.textContent = "Got it. Check it's your home, not where you are right now.";
          }}, function () {{ note.textContent = "Couldn't get your location. Type the two numbers instead."; }}, {{ enableHighAccuracy: true, timeout: 15000 }});
        }};
      </script>"""


class Front:
    """What the front door knows. ``ports``: username -> the port that account's planespotter listens on.
    ``restart``: called after an account is added, changed or removed (planespotter starts again to pick it up)."""

    def __init__(self, accounts: Accounts, owner_env, ports: dict, public_url: str = "", restart=None,
                 phone_script=None):
        self.accounts, self.owner_env, self.ports = accounts, owner_env, ports
        self.public_url = public_url.rstrip("/")
        self.restart = restart or restart_planespotter
        self.phone_script = phone_script  # (user, token, base url) -> the Scriptable script for that account


def restart_planespotter(delay: float = 1.5) -> None:
    """Start planespotter again in place, a moment from now (so the page that asked gets its answer first)."""
    def go() -> None:
        log.info("restarting to pick up the account changes")
        os.execv(sys.executable, [sys.executable, "-m", "planespotter", *sys.argv[1:]])
    threading.Timer(delay, go).start()


def make_handler(front: Front):
    accounts = front.accounts

    class Handler(BaseHTTPRequestHandler):
        server_version = "planespotter"

        def log_message(self, fmt, *args):
            log.debug("front: " + fmt, *args)

        # --- who's asking ---

        def local_peer(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def secure(self) -> bool:
            """Reached over https? (Said by the web server in front of us, which is on this computer.)"""
            return (self.local_peer() and self.headers.get("X-Forwarded-Proto", "") == "https") or \
                front.public_url.startswith("https://") and self.local_peer()

        def address(self) -> str:
            fwd = self.headers.get("X-Forwarded-For", "") if self.local_peer() else ""
            return (fwd.split(",")[0].strip() or self.client_address[0])[:60]

        def base_url(self) -> str:
            if front.public_url:
                return front.public_url
            return f"{'https' if self.secure() else 'http'}://{self.headers.get('Host', 'localhost')}"

        def cookie(self) -> str:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return ""

        def user(self):
            found = accounts.session_user(self.cookie())
            if found:
                return found
            auth = self.headers.get("Authorization", "")
            return accounts.token_user(auth[7:].strip()) if auth.startswith("Bearer ") else None

        # --- answering ---

        def send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (headers or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def go(self, where: str, headers: dict | None = None) -> None:
            self.send(303, b"", headers={"Location": where, **(headers or {})})

        def session_cookie(self, token: str, days: int = 30) -> str:
            return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={days * 86400 if token else 0}"
                    + ("; Secure" if self.secure() else ""))

        def form(self) -> dict:
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 <= size <= MAX_FORM:
                raise AccountError("That's too much to send.")
            raw = self.rfile.read(size).decode("utf-8", "replace")
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        def same_site(self) -> bool:
            """Forms only from this site's own pages."""
            origin = self.headers.get("Origin")
            return not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

        # --- pages ---

        def login_page(self, error: str = "", next_path: str = "/") -> bytes:
            return page("Sign in", f"""<div class="card"><h1>Sign in</h1>{notice(error)}
              <form method="post" action="/login"><input type="hidden" name="next" value="{escape(next_path)}">
              <label for="u">Username</label><input id="u" name="username" autocomplete="username" autocapitalize="none" required autofocus>
              <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
              <button>Sign in</button></form></div>
              <p>planespotter is private: accounts are by invitation. If a friend invited you, open the link they sent.</p>""")

        def join_page(self, code: str, v: dict | None = None, error: str = "") -> bytes:
            v = v or {}
            if not accounts.invite_ok(code):
                return page("Invite", '<div class="card"><h1>That invite isn\'t good any more</h1>'
                            "<p>Invites work once and run out after a week. Ask your friend for a new one.</p></div>")
            return page("Join", f"""<div class="card"><h1>You're invited</h1>
              <p>Make your own planespotter: your home, your history, your notifications. Nobody else sees them.</p>{notice(error)}
              <form method="post" action="/join"><input type="hidden" name="code" value="{escape(code)}">
              <label for="u">Pick a username</label><input id="u" name="username" value="{escape(v.get('username', ''))}" autocomplete="username" autocapitalize="none" required>
              <label for="p">Pick a password (8 characters or more)</label><input id="p" name="password" type="password" autocomplete="new-password" minlength="8" required>
              <h2 style="margin-top:20px">Your home</h2><p>Planes that fly over this spot are yours. It's only used to find them.</p>
              {home_fields(v, True)}
              <button>Make my planespotter</button></form></div>""")

        def account_page(self, user: dict, error: str = "", ok: str = "", script: str = "") -> bytes:
            v = accounts.settings(user, front.owner_env)
            topic = v.get("NTFY_TOPIC", "")
            nav = f'<nav><a href="/">{icon("arrow-left")}The map</a>' + ('<a href="/admin">Invite people</a>' if user["owner"] else "") + "</nav>"
            phone = ""
            if script:
                phone = f"""<div class="ok">Here's your script. It works for you only, and making a new one stops the old one.</div>
                  <textarea id="script" readonly>{escape(script)}</textarea>
                  <button type="button" onclick="navigator.clipboard.writeText(document.getElementById('script').value).then(function () {{ this.textContent = 'Copied'; }}.bind(this))">Copy it</button>
                  <ol><li>On your iPhone, install <b>Scriptable</b> (free) from the App Store.</li>
                  <li>Open it, tap <b>+</b>, paste, and name the script <b>planespotter</b>.</li>
                  <li>Add a Scriptable widget to your Home Screen and choose the script.</li></ol>"""
            return page("Your account", f"""{nav}<div class="card"><h1>{icon("user")}{escape(user['username'])}</h1>{notice(error)}{notice(ok, 'ok')}
              <form method="post" action="/account"><h2>Your home</h2>{home_fields(v, False)}<button>Save</button>
              <p class="muted">Saving restarts planespotter for a few seconds.</p></form></div>
              <div class="card"><h2>{icon("bell")}Notifications on your phone</h2>
              {"<ol><li>Install the free <b>ntfy</b> app (App Store on iPhone, Google Play on Android).</li><li>Tap <b>+</b> and subscribe to this topic (it's private to you):<br><code>" + escape(topic) + "</code></li><li>Leave the server as it is (ntfy.sh).</li></ol>" if topic else "<p>Not set up for this account.</p>"}</div>
              {self.phone_cards(phone, script)}
              <div class="card"><h2>Change your password</h2><form method="post" action="/account/password">
              <label for="o">Your password now</label><input id="o" name="old" type="password" autocomplete="current-password" required>
              <label for="n">New password</label><input id="n" name="new" type="password" autocomplete="new-password" minlength="8" required>
              <button class="ghost">Change it</button><p class="muted">Changing it signs you out everywhere.</p></form></div>
              <form method="post" action="/logout"><button class="ghost">Sign out</button></form>""")

        def phone_cards(self, phone: str, script: str) -> str:
            """How to put planespotter on a phone: iPhone (Scriptable: widget and Siri) and Android (an installed app
            with spoken shortcuts). The phone that's looking gets its own first."""
            iphone = f"""<div class="card"><h2>{icon("phone")}iPhone: widget and Siri</h2>
              <p>Your own script for the free Scriptable app: a home-screen widget, Siri ("Hey Siri, what's that
              plane?"), and the planes over wherever you are.</p>{phone}
              <form method="post" action="/account/phone"><button class="ghost">{'Make a new script' if script else 'Get my script'}</button></form>
              <p class="muted">Also: in Safari, <b>Share → Add to Home Screen</b> gives the app its own icon.</p></div>"""
            site = escape(self.base_url().replace("https://", "").replace("http://", ""))
            android = f"""<div class="card"><h2>{icon("phone")}Android: the planespotter app</h2>
              <p>An app for any Android phone (any browser): planespotter with a <b>home-screen widget</b> (the next plane
              over you, with a countdown), and shortcuts that <b>answer out loud</b>.</p>
              <a class="btn" href="/planespotter.apk" download>Download the app</a>
              <ol style="margin-top:14px"><li>Tap <b>Download the app</b>, then open the download (it's called
              <b>planespotter.apk</b>).</li>
              <li>Android asks whether your browser may install apps: tap <b>Settings</b>, turn on <b>Allow from this
              source</b>, go back, and tap <b>Install</b>. (It comes from this site, not the Play Store, so Android
              asks; if Play Protect asks to scan it, let it.)</li>
              <li>Open planespotter, type <b>{site}</b>, and sign in.</li>
              <li>Widget: press and hold an empty spot on your home screen → <b>Widgets</b> → <b>planespotter</b>.</li>
              <li><b>Press and hold the app's icon</b> for <b>What's that plane?</b> and <b>Where's my plane?</b> (out loud),
              <b>Track a flight</b> and <b>Point</b>.</li>
              <li>Notifications: the ntfy app (above), from Google Play.</li></ol>
              <p class="muted">Rather not install an app? In Chrome: ⋮ → <b>Add to Home screen</b> gives the same app
              without the widget.</p></div>"""
            on_android = "Android" in self.headers.get("User-Agent", "")
            return android + iphone if on_android else iphone + android

        def admin_page(self, user: dict, error: str = "", link: str = "") -> bytes:
            rows = "".join(
                f'<div class="who"><span><b>{escape(u["username"])}</b>{" (you, the owner)" if u["owner"] else ""}'
                f'<br><span class="muted">since {time.strftime("%b %d, %Y", time.localtime(u["created"]))}</span></span>'
                + ("" if u["owner"] else f'<form method="post" action="/admin/remove" onsubmit="return confirm(\'Remove {escape(u["username"])} '
                   f'and delete their history?\')"><input type="hidden" name="username" value="{escape(u["username"])}">'
                   '<button class="danger">Remove</button></form>') + "</div>" for u in accounts.users())
            made = f"""<div class="ok">Send this link to one friend. It works once, for a week:<br><code id="link">{escape(link)}</code></div>
              <button type="button" class="ghost" style="margin-top:0" onclick="navigator.clipboard.writeText(document.getElementById('link').textContent).then(function () {{ this.textContent = 'Copied'; }}.bind(this))">Copy the link</button>""" if link else ""
            return page("Invite people", f"""<nav><a href="/">{icon("arrow-left")}The map</a><a href="/account">{icon("user")}Your account</a></nav>
              <div class="card"><h1>People</h1>{notice(error)}{rows}</div>
              <div class="card"><h2>Invite a friend</h2><p>They get their own planespotter: their own home, history and
              notifications. Everyone shares this server's adsb.lol allowance; friends who live within about 150 miles
              of each other cost no extra.</p>{made}
              <form method="post" action="/admin/invite"><button>Make an invite link</button></form></div>""")

        # --- requests ---

        def do_GET(self):
            try:
                self.route("GET")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            try:
                self.route("POST")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def route(self, method: str) -> None:
            url = urllib.parse.urlsplit(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            path = url.path
            if method == "POST" and path in ("/login", "/join", "/logout", "/account", "/account/password",
                                               "/account/phone", "/admin/invite", "/admin/remove"):
                if not self.same_site():
                    self.send(403, page("Not allowed", '<div class="card"><h1>Not allowed</h1></div>'))
                    return
                try:
                    self.post(path, self.form())
                except AccountError as exc:
                    self.send(400, page("Sorry", f'<div class="card"><h1>Sorry</h1>{notice(str(exc))}<a class="btn ghost" href="/">Back</a></div>'))
                return
            from .webmap import PUBLIC_FILES, WEB_DIR
            if path in PUBLIC_FILES:  # the installed app's icon and pieces: before signing in, too
                name, ctype = PUBLIC_FILES[path]
                extra = {"Cache-Control": "no-cache"} if path in ("/sw.js", "/manifest.webmanifest", "/planespotter.apk") \
                    else {"Cache-Control": "max-age=86400"}
                if path.endswith(".apk"):
                    extra["Content-Disposition"] = 'attachment; filename="planespotter.apk"'
                self.send(200, (WEB_DIR / name).read_bytes(), ctype, headers=extra)
                return
            if path == "/login":
                self.send(200, self.login_page(next_path=safe_next(query.get("next", "/"))))
            elif path == "/join":
                self.send(200, self.join_page(query.get("code", "")))
            elif path == "/auth":  # the phone's script signing its owner in to the app
                found = accounts.token_user(query.get("k", ""))
                if found:
                    self.go("/", {"Set-Cookie": self.session_cookie(accounts.new_session(found)), "Referrer-Policy": "no-referrer"})
                else:
                    self.go("/login")
            else:
                user = self.user()
                if not user:
                    wants_page = "text/html" in self.headers.get("Accept", "") and not path.startswith("/api/")
                    if wants_page:
                        self.go("/login" + ("" if path == "/" else "?next=" + urllib.parse.quote(self.path, safe="")))
                    else:
                        self.send(401, b'{"error":"sign in first"}', "application/json")
                elif path == "/account":
                    self.send(200, self.account_page(user, ok="Saved. planespotter is restarting: give it ten seconds." if "saved" in query else ""))
                elif path == "/admin":
                    self.send(200, self.admin_page(user) if user["owner"] else page("Not yours", '<div class="card"><h1>Only the owner can invite people</h1></div>'))
                else:
                    self.pass_on(user, method)

        def post(self, path: str, form: dict) -> None:
            if path == "/login":
                try:
                    user = accounts.sign_in(form.get("username", ""), form.get("password", ""), self.address())
                except AccountError as exc:
                    self.send(401, self.login_page(str(exc), safe_next(form.get("next", "/"))))
                    return
                self.go(safe_next(form.get("next", "/")), {"Set-Cookie": self.session_cookie(accounts.new_session(user))})
                return
            if path == "/join":
                try:
                    user = accounts.join(form.get("code", ""), form.get("username", ""), form.get("password", ""), form)
                except AccountError as exc:
                    self.send(400, self.join_page(form.get("code", ""), form, str(exc)))
                    return
                front.restart()
                self.send(200, page("Welcome", """<div class="card"><h1>Welcome aboard</h1>
                  <p>Your planespotter is starting. This page moves on by itself in a few seconds.</p>
                  <p>Next: on your account page, set up <b>notifications</b> and the <b>iPhone widget</b>.</p></div>""",
                                 refresh="12; url=/account"), headers={"Set-Cookie": self.session_cookie(accounts.new_session(user))})
                return
            user = accounts.session_user(self.cookie())  # (forms need the sign-in itself, not a phone's token)
            if not user:
                self.go("/login")
                return
            if path == "/logout":
                accounts.end_session(self.cookie())
                self.go("/login", {"Set-Cookie": self.session_cookie("")})
            elif path == "/account":
                try:
                    accounts.save_settings(user, check_settings({k: form.get(k, "") for k in EDITABLE}), front.owner_env)
                except AccountError as exc:
                    self.send(400, self.account_page(user, str(exc)))
                    return
                front.restart()
                self.send(200, page("Saved", '<div class="card"><h1>Saved</h1><p>planespotter is restarting to use your new '
                                    "settings. This page moves on by itself in a few seconds.</p></div>", refresh="12; url=/account"))
            elif path == "/account/password":
                try:
                    accounts.sign_in(user["username"], form.get("old", ""), self.address())
                    accounts.set_password(user["username"], form.get("new", ""))
                except AccountError as exc:
                    self.send(400, self.account_page(user, str(exc)))
                    return
                self.go("/login", {"Set-Cookie": self.session_cookie("")})
            elif path == "/account/phone":
                if not front.phone_script:
                    raise AccountError("The phone script isn't available here.")
                token = accounts.phone_token(user)
                self.send(200, self.account_page(user, script=front.phone_script(user, token, self.base_url())))
            elif path == "/admin/invite" and user["owner"]:
                self.send(200, self.admin_page(user, link=f"{self.base_url()}/join?code={accounts.new_invite()}"))
            elif path == "/admin/remove" and user["owner"]:
                try:
                    accounts.remove_user(form.get("username", ""))
                except AccountError as exc:
                    self.send(400, self.admin_page(user, str(exc)))
                    return
                front.restart()
                self.go("/admin")
            else:
                self.send(403, page("Not allowed", '<div class="card"><h1>Not allowed</h1></div>'))

        def pass_on(self, user: dict, method: str) -> None:
            """Everything else: to this person's own planespotter, and its answer back."""
            port = front.ports.get(user["username"])
            starting = page("Starting", '<div class="card"><h1>Your planespotter is starting…</h1><p>This page tries again '
                            "by itself in a few seconds.</p></div>", refresh="5")
            if not port:
                self.send(503, starting)
                return
            from .webmap import MAX_UPLOAD
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_UPLOAD:
                self.send(413, b'{"error":"too big"}', "application/json")
                return
            body = self.rfile.read(size) if method == "POST" and size else None
            headers = {k: self.headers[k] for k in PASS_ON if self.headers.get(k)}
            headers["Host"] = self.headers.get("Host", "localhost")  # (so its own-pages-only check still works)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=90)
            try:
                conn.request(method, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError:
                self.send(503, starting if "text/html" in self.headers.get("Accept", "") else b'{"error":"starting"}',
                          "text/html; charset=utf-8" if "text/html" in self.headers.get("Accept", "") else "application/json")
                return
            finally:
                conn.close()
            self.send_response(resp.status)
            for k in PASS_BACK:
                if resp.getheader(k):
                    self.send_header(k, resp.getheader(k))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def safe_next(path: str) -> str:
    """Where to go after signing in: only a page on this site."""
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path and "\n" not in path else "/"


def start_front(front: Front, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(front))
    server.daemon_threads = True
    server.front = front
    threading.Thread(target=server.serve_forever, name="front-door", daemon=True).start()
    return server
