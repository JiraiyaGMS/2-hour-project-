"""`python3 -m planespotter setup`: asks a few questions and writes .env for you."""

from __future__ import annotations

import getpass
import os
import re
from pathlib import Path
from typing import Callable

from .adsb import get_json
from .notify import new_topic
from .config import KNOWN_KEYS, load_dotenv, parse_clock, parse_quiet_hours

# Email providers whose outgoing server we can fill in automatically.
SMTP_SERVERS = {
    "gmail.com": "smtp.gmail.com",
    "googlemail.com": "smtp.gmail.com",
    "icloud.com": "smtp.mail.me.com",
    "me.com": "smtp.mail.me.com",
    "mac.com": "smtp.mail.me.com",
    "yahoo.com": "smtp.mail.yahoo.com",
}
APP_PASSWORD_HELP = {
    "smtp.gmail.com": "https://myaccount.google.com/apppasswords (needs 2-Step Verification on)",
    "smtp.mail.me.com": "https://account.apple.com → Sign-In and Security → App-Specific Passwords",
    "smtp.mail.yahoo.com": "https://login.yahoo.com/account/security → Generate app password",
}
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


class Cancelled(Exception):
    pass


class Wizard:
    def __init__(self, env_path: Path, ask: Callable[[str], str] = input,
                 ask_secret: Callable[[str], str] = getpass.getpass):
        self.env_path = env_path
        self._ask = ask
        self._ask_secret = ask_secret
        self.current = load_dotenv(env_path)

    # --- prompting helpers --------------------------------------------------

    def ask(self, question: str, default: str = "", check: Callable[[str], str | None] | None = None) -> str:
        """Ask until ``check`` returns None (no error). Enter keeps the default."""
        suffix = f" [{default}]" if default else ""
        while True:
            try:
                answer = self._ask(f"{question}{suffix}: ").strip()
            except EOFError:
                raise Cancelled from None
            answer = answer or default
            problem = check(answer) if check else None
            if problem is None:
                return answer
            print(f"  ⚠ {problem}")

    def yes(self, question: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        try:
            answer = self._ask(f"{question} [{hint}]: ").strip().lower()
        except EOFError:
            raise Cancelled from None
        return default if not answer else answer.startswith("y")

    # --- questions ----------------------------------------------------------

    def run(self) -> dict[str, str]:
        cur = self.current
        values: dict[str, str] = {}

        print("\n✈  planespotter setup\n")
        if cur:
            print(f"Editing your existing settings in {self.env_path}")
            print("Press Enter to keep what's in [brackets].\n")

        print("1. WHERE IS HOME?")
        print("   In Google Maps, right-click your house. The first line of the menu is two")
        print("   numbers like 51.50071, -0.12462. Click it to copy, then paste it here.")
        default_loc = f"{cur['HOME_LAT']}, {cur['HOME_LON']}" if cur.get("HOME_LAT") else ""
        location = self.ask("   Coordinates", default_loc, check_location)
        lat, lon = NUMBER.findall(location)[:2]
        values["HOME_LAT"], values["HOME_LON"] = lat, lon
        values["HOME_NAME"] = self.ask("   A name for it, used in emails", cur.get("HOME_NAME", "Home"))

        print("\n2. HOW CLOSE DOES A PLANE HAVE TO BE?")
        values["RADIUS_MILES"] = self.ask("   Radius in miles", cur.get("RADIUS_MILES", "3"), check_radius)
        print("   Airliners cruise at 30,000-40,000 ft. To only hear about low planes")
        print("   (taking off, landing, helicopters), enter a ceiling like 10000.")
        values["MAX_ALTITUDE_FT"] = self.ask(
            "   Maximum altitude in feet (Enter for no limit)", cur.get("MAX_ALTITUDE_FT", ""), check_altitude)

        print("\n3. QUIET HOURS")
        print("   No alert emails during these hours. Those planes show up in the morning report.")
        values["QUIET_HOURS"] = self.ask(
            "   Quiet hours (or 'none')", cur.get("QUIET_HOURS", "22:00-07:00"), check_quiet)
        values["DIGEST_TIME"] = self.ask(
            "   Time for the daily sky report", cur.get("DIGEST_TIME", "07:00"), check_clock)

        print("\n4. EMAIL")
        print("   Leave this blank to try things out first. Emails are then saved as files")
        print("   in the outbox folder instead of being sent.")
        values["EMAIL_TO"] = self.ask("   Send alerts to this address", cur.get("EMAIL_TO", ""), check_email)
        if values["EMAIL_TO"]:
            values["SMTP_USER"] = self.ask(
                "   Send from this account", cur.get("SMTP_USER", values["EMAIL_TO"]), check_email_required)
            domain = values["SMTP_USER"].rsplit("@", 1)[-1].lower()
            known = SMTP_SERVERS.get(domain)
            if known:
                values["SMTP_HOST"] = known
            else:
                values["SMTP_HOST"] = self.ask(
                    "   Outgoing (SMTP) server for that account", cur.get("SMTP_HOST", ""), check_required)
            values["SMTP_PORT"] = cur.get("SMTP_PORT", "587") if not known else "587"
            print("   You need an app password: a special password just for this program,")
            print("   not your normal one. Create it here:")
            print(f"     {APP_PASSWORD_HELP.get(values['SMTP_HOST'], 'your email provider’s security settings')}")
            values["SMTP_PASSWORD"] = self.ask_password(cur.get("SMTP_PASSWORD", ""))
        else:
            values.update(SMTP_USER="", SMTP_PASSWORD="")

        print("\n5. PHONE NOTIFICATIONS (optional)")
        print("   Alerts on your phone with the free ntfy app (no account needed), as well as email.")
        has_topic = bool(cur.get("NTFY_TOPIC"))
        if self.yes("   Send alerts to your phone?", default=has_topic):
            topic = cur.get("NTFY_TOPIC") or new_topic()
            values["NTFY_TOPIC"] = topic
            print("   1. Install \"ntfy\" from the App Store or Google Play.")
            print("   2. Open it, tap + (Subscribe to topic), and type this topic name exactly:")
            print(f"        {topic}")
            print("      Keep it to yourself: anyone with this name could read your alerts.")
        else:
            values["NTFY_TOPIC"] = ""

        print("\n6. ADSB.LOL KEY (optional)")
        print("   The flight data is free from adsb.lol: 500 requests a day without a key, which")
        print("   means checking for planes about every 4 minutes. A free key doubles that (every")
        print("   2 minutes). Ask for one at https://www.adsb.lol/request, or press Enter to skip.")
        values["ADSB_API_KEY"] = self.ask("   adsb.lol key", cur.get("ADSB_API_KEY", ""))

        print("\n7. YOUR OWN ADS-B RECEIVER (optional)")
        print("   If you have a receiver (e.g. a Raspberry Pi with a radio stick running adsb.im,")
        print("   readsb or dump1090), paste the address of its aircraft.json for unlimited, live data.")
        print("   For example: http://adsb-feeder.local:8080/data/aircraft.json  (Enter to skip)")
        values["RECEIVER_URL"] = self.ask("   Receiver address", cur.get("RECEIVER_URL", ""), check_receiver)

        # Keep any advanced settings the person added by hand.
        for key, value in cur.items():
            if key in KNOWN_KEYS and key not in values:
                values[key] = value
        return values

    def ask_password(self, existing: str) -> str:
        prompt = "   App password (typing is hidden; Enter keeps the saved one): " if existing \
            else "   App password (typing is hidden, that's normal): "
        while True:
            try:
                answer = self._ask_secret(prompt).strip().replace(" ", "")
            except EOFError:
                raise Cancelled from None
            if answer or existing:
                return answer or existing
            print("  ⚠ Paste the app password, or press Ctrl+C to stop and come back later.")

    def save(self, values: dict[str, str]) -> None:
        write_env(self.env_path, values)


# --- validation (return an error message, or None if fine) -------------------

def check_location(text: str) -> str | None:
    nums = NUMBER.findall(text)
    if len(nums) < 2:
        return "Paste two numbers, like: 51.50071, -0.12462"
    lat, lon = float(nums[0]), float(nums[1])
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return "Those don't look like map coordinates (latitude first, then longitude)."
    return None


def check_radius(text: str) -> str | None:
    try:
        return None if 0 < float(text) <= 100 else "Pick a number between 0.5 and 100."
    except ValueError:
        return "Enter a number of miles, like 3."


def check_altitude(text: str) -> str | None:
    if not text:
        return None
    return None if text.replace(",", "").isdigit() else "Enter feet as a number (like 10000), or leave blank."


def check_quiet(text: str) -> str | None:
    try:
        parse_quiet_hours(text)
        return None
    except ValueError:
        return "Use a range like 22:00-07:00 or 10pm-7am, or type none."


def check_clock(text: str) -> str | None:
    try:
        parse_clock(text)
        return None
    except ValueError:
        return "Use a time like 07:00 or 7am."


def check_email(text: str) -> str | None:
    if not text:
        return None
    return None if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text) else "That doesn't look like an email address."


def check_email_required(text: str) -> str | None:
    return "Enter an email address." if not text else check_email(text)


def check_receiver(text: str) -> str | None:
    """Blank is fine; otherwise it must answer with aircraft."""
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        return "Start the address with http://"
    try:
        data = get_json(text, timeout=5)
    except (OSError, ValueError) as exc:
        return f"Couldn't read it ({exc}). Check the address, or press Enter with it blank to skip."
    planes = data.get("aircraft", data.get("ac")) if isinstance(data, dict) else None
    if planes is None:
        return "That address answered, but not with aircraft.json data."
    print(f"  ✔ Your receiver can see {len(planes)} aircraft right now.")
    return None


def check_required(text: str) -> str | None:
    return None if text else "This one is needed."


# --- writing .env --------------------------------------------------------------

TEMPLATE = """\
# planespotter settings, written by `python3 -m planespotter setup`.
# Run setup again to change them, or edit this file by hand.
# This file is private: it is never uploaded to GitHub.

# Where to watch
HOME_LAT={HOME_LAT}
HOME_LON={HOME_LON}
HOME_NAME={HOME_NAME}
RADIUS_MILES={RADIUS_MILES}
MAX_ALTITUDE_FT={MAX_ALTITUDE_FT}

# No alert emails during these hours (planes still go in the morning report)
QUIET_HOURS={QUIET_HOURS}
DIGEST_TIME={DIGEST_TIME}

# Email (blank = save emails to the outbox folder instead of sending)
EMAIL_TO={EMAIL_TO}
SMTP_USER={SMTP_USER}
SMTP_PASSWORD={SMTP_PASSWORD}
SMTP_HOST={SMTP_HOST}
SMTP_PORT={SMTP_PORT}

# Phone notifications: the ntfy topic your phone subscribes to (blank = off)
NTFY_TOPIC={NTFY_TOPIC}

# Your own ADS-B receiver's aircraft.json (blank = use adsb.lol only)
RECEIVER_URL={RECEIVER_URL}

# adsb.lol key (blank = 500 requests a day; a free key = 1,000; feeders get 7,200
# and can set ADSB_DAILY_LIMIT=7200)
ADSB_API_KEY={ADSB_API_KEY}
"""


TEMPLATE_KEYS = set(re.findall(r"\{(\w+)\}", TEMPLATE))


def write_env(path: Path, values: dict[str, str]) -> None:
    filled = {key: "" for key in TEMPLATE_KEYS}
    filled.update(SMTP_HOST="smtp.gmail.com", SMTP_PORT="587")
    filled.update({k: v.replace("\n", " ") for k, v in values.items() if k in TEMPLATE_KEYS})
    filled["MAX_ALTITUDE_FT"] = filled["MAX_ALTITUDE_FT"].replace(",", "")
    if "#" in filled["HOME_NAME"]:
        filled["HOME_NAME"] = f'"{filled["HOME_NAME"]}"'
    text = TEMPLATE.format(**filled)

    extras = sorted(k for k in values if k not in TEMPLATE_KEYS)
    if extras:
        text += "\n# Advanced settings\n" + "".join(f"{k}={values[k]}\n" for k in extras)

    # Owner-only permissions: the file holds an email password.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
