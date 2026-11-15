"""Accounts: you and the friends you invite each sign in to your own planespotter.

Every account has its own home, history, collection, tracked flights, notifications and phone script, kept
in its own folder; they run side by side in one planespotter (see `watch`). There is no public sign-up: the
owner makes an invite link, and whoever opens it picks a username and password and sets their home.

What's stored (in accounts.db, next to your settings): usernames, password *hashes* (scrypt: the password
itself is never stored and can't be read back), sign-in sessions and phone tokens (only their hashes), invite
codes (hashes), and failed sign-in counts (to slow down guessing).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

from .config import Config, ConfigError, load_dotenv

SESSION_DAYS = 30
INVITE_DAYS = 7
MAX_TRIES = 5            # wrong passwords for one username...
LOCK_MINUTES = 15        # ...before it's locked for this long
MAX_TRIES_IP = 20        # and from one address, whatever the usernames
MAX_ACCOUNTS = 12        # they share one adsb.lol allowance: this is for friends, not the public
USERNAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,19}")
RESERVED = {"admin", "login", "logout", "join", "account", "api", "auth", "root", "owner", "planespotter"}


class AccountError(Exception):
    """Something the person can fix (said to them as it is)."""


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt (made to be slow to guess): "scrypt$salt$hash"."""
    salt = salt or secrets.token_bytes(16)
    try:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${digest.hex()}"
    except (AttributeError, ValueError):  # a Python built without scrypt
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        return f"pbkdf2${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        kind, salt, _ = stored.split("$")
        again = hash_password(password, bytes.fromhex(salt)) if kind == "scrypt" else \
            f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()}"
    except ValueError:
        return False
    return hmac.compare_digest(again, stored)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_username(name: str) -> str:
    name = (name or "").strip().lower()
    if not USERNAME.fullmatch(name) or name in RESERVED:
        raise AccountError("A username is 3 to 20 letters, digits, - or _ (like sam or sam_2).")
    return name


def check_password(password: str) -> str:
    if len(password or "") < 8:
        raise AccountError("A password needs at least 8 characters.")
    if len(password) > 200:
        raise AccountError("That password is too long.")
    return password


class Accounts:
    def __init__(self, data_dir: Path, clock=time.time):
        self.dir = Path(data_dir)
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.dir / "accounts.db"), timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, owner INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                phone_token TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL,
                used_by TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS attempts (who TEXT PRIMARY KEY, fails INTEGER NOT NULL, since REAL NOT NULL);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- people ---

    def users(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, username, owner, created FROM users ORDER BY owner DESC, id").fetchall()
        return [dict(r) for r in rows]

    def enabled(self) -> bool:
        return bool(self.users())

    def user(self, username: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT id, username, owner, created FROM users WHERE username = ?",
                                  ((username or "").strip().lower(),)).fetchone()
        return dict(row) if row else None

    def add_user(self, username: str, password: str, owner: bool = False) -> dict:
        name, password = check_username(username), check_password(password)
        if len(self.users()) >= MAX_ACCOUNTS:
            raise AccountError(f"There's room for {MAX_ACCOUNTS} accounts (they share one adsb.lol allowance).")
        try:
            with self.lock:
                self.db.execute("INSERT INTO users (username, password, owner, created) VALUES (?, ?, ?, ?)",
                                (name, hash_password(password), int(owner), self.clock()))
                self.db.commit()
        except sqlite3.IntegrityError:
            raise AccountError("That username is taken.") from None
        return self.user(name)

    def set_password(self, username: str, password: str) -> None:
        check_password(password)
        with self.lock:
            done = self.db.execute("UPDATE users SET password = ? WHERE username = ?",
                                   (hash_password(password), username)).rowcount
            # A new password signs every device out (whoever knew the old one is out too).
            self.db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))
            self.db.commit()
        if not done:
            raise AccountError("There's no account with that username.")

    def remove_user(self, username: str, delete_files: bool = True) -> None:
        user = self.user(username)
        if not user:
            raise AccountError("There's no account with that username.")
        if user["owner"]:
            raise AccountError("The owner's account can't be removed (turn accounts off instead).")
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            self.db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
            self.db.commit()
        if delete_files:
            shutil.rmtree(self.folder(user), ignore_errors=True)  # their history, photos and settings

    # --- signing in ---

    def _locked(self, who: str, limit: int) -> bool:
        row = self.db.execute("SELECT fails, since FROM attempts WHERE who = ?", (who,)).fetchone()
        return bool(row and row["fails"] >= limit and self.clock() - row["since"] < LOCK_MINUTES * 60)

    def _failed(self, who: str) -> None:
        now = self.clock()
        row = self.db.execute("SELECT fails, since FROM attempts WHERE who = ?", (who,)).fetchone()
        if row and now - row["since"] < LOCK_MINUTES * 60:
            self.db.execute("UPDATE attempts SET fails = fails + 1 WHERE who = ?", (who,))
        else:
            self.db.execute("INSERT OR REPLACE INTO attempts (who, fails, since) VALUES (?, 1, ?)", (who, now))

    def sign_in(self, username: str, password: str, address: str = "") -> dict:
        """The user, or AccountError (wrong, or too many tries: wait). Wrong usernames and wrong passwords get
        the same answer, and guessing is slowed for a username and for an address."""
        name = (username or "").strip().lower()
        with self.lock:
            if self._locked("u:" + name, MAX_TRIES) or (address and self._locked("a:" + address, MAX_TRIES_IP)):
                raise AccountError(f"Too many tries. Wait {LOCK_MINUTES} minutes, then try again.")
            row = self.db.execute("SELECT id, username, owner, created, password FROM users WHERE username = ?", (name,)).fetchone()
        # (checked even when there's no such user, so both take as long)
        ok = password_matches(password or "", row["password"] if row else hash_password("x" * 12, b"0" * 16))
        with self.lock:
            if not row or not ok:
                self._failed("u:" + name)
                if address:
                    self._failed("a:" + address)
                self.db.commit()
                raise AccountError("That username and password don't match.")
            self.db.execute("DELETE FROM attempts WHERE who = ?", ("u:" + name,))
            self.db.commit()
        return {k: row[k] for k in ("id", "username", "owner", "created")}

    def new_session(self, user: dict) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self.db.execute("INSERT INTO sessions (token, user_id, expires) VALUES (?, ?, ?)",
                            (_digest(token), user["id"], now + SESSION_DAYS * 86400))
            self.db.commit()
        return token

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT u.id, u.username, u.owner, u.created FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = ? AND s.expires > ?", (_digest(token), self.clock())).fetchone()
        return dict(row) if row else None

    def end_session(self, token: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE token = ?", (_digest(token or ""),))
            self.db.commit()

    # --- the phone's script signs in with a token of its own ---

    def phone_token(self, user: dict, renew: bool = False) -> str:
        """A new token for this account's phone script (only its hash is kept, so it's shown once: making a
        new one stops the old one)."""
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.db.execute("UPDATE users SET phone_token = ? WHERE id = ?", (_digest(token), user["id"]))
            self.db.commit()
        return token

    def token_user(self, token: str) -> dict | None:
        if not token or len(token) < 20:
            return None
        with self.lock:
            row = self.db.execute("SELECT id, username, owner, created FROM users WHERE phone_token = ?",
                                  (_digest(token),)).fetchone()
        return dict(row) if row else None

    # --- invites ---

    def new_invite(self) -> str:
        code = secrets.token_urlsafe(12)
        now = self.clock()
        with self.lock:
            self.db.execute("DELETE FROM invites WHERE expires < ?", (now,))
            self.db.execute("INSERT INTO invites (code, created, expires) VALUES (?, ?, ?)",
                            (_digest(code), now, now + INVITE_DAYS * 86400))
            self.db.commit()
        return code

    def invite_ok(self, code: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT 1 FROM invites WHERE code = ? AND expires > ? AND used_by = ''",
                                  (_digest(code or ""), self.clock())).fetchone()
        return bool(row)

    def join(self, code: str, username: str, password: str, settings: dict) -> dict:
        """Use an invite: make the account and its settings. The invite works once."""
        if not self.invite_ok(code):
            raise AccountError("That invite has been used or has run out. Ask for a new one.")
        clean = check_settings(settings)
        user = self.add_user(username, password)
        with self.lock:
            self.db.execute("UPDATE invites SET used_by = ? WHERE code = ?", (user["username"], _digest(code)))
            self.db.commit()
        clean["NTFY_TOPIC"] = "planespotter-" + secrets.token_hex(8)  # their own private notification topic
        self.save_settings(user, clean)
        return user

    # --- each account's own folder and settings ---

    def folder(self, user: dict) -> Path:
        """Where an account keeps its settings, history and photos (the owner's is planespotter's own)."""
        return self.dir if user["owner"] else self.dir / "accounts" / user["username"]

    def env_path(self, user: dict, owner_env: Path | None = None) -> Path:
        return (owner_env or self.dir / ".env") if user["owner"] else self.folder(user) / ".env"

    def settings(self, user: dict, owner_env: Path | None = None) -> dict:
        return load_dotenv(self.env_path(user, owner_env))

    def save_settings(self, user: dict, values: dict, owner_env: Path | None = None) -> None:
        """Write these settings (already checked) into the account's settings file, keeping its others."""
        from .wizard import set_env_value

        path = self.env_path(user, owner_env)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# planespotter settings for this account\nEMAILS=off\n", encoding="utf-8")
            path.chmod(0o600)
        for key, value in values.items():
            set_env_value(path, key, value)


EDITABLE = ("HOME_NAME", "HOME_LAT", "HOME_LON", "RADIUS_MILES", "MAX_ALTITUDE_FT", "QUIET_HOURS", "TIMEZONE")


def check_settings(form: dict) -> dict:
    """The settings a person may change, checked: {KEY: text}. AccountError says what's wrong."""
    out = {}
    name = " ".join(str(form.get("HOME_NAME", "")).split())[:40].strip("'") or "Home"  # (Sam's is fine)
    if re.search(r'[\r\n=#"<>]', name):
        raise AccountError("The place's name can't have \", =, #, < or > in it.")
    out["HOME_NAME"] = name
    try:
        lat, lon = float(form.get("HOME_LAT", "")), float(form.get("HOME_LON", ""))
    except (TypeError, ValueError):
        raise AccountError("Home needs a latitude and a longitude, like 45.5231 and -122.6765.") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        raise AccountError("Those coordinates aren't on the map: latitude is -90 to 90, longitude -180 to 180.")
    out["HOME_LAT"], out["HOME_LON"] = f"{lat:.5f}", f"{lon:.5f}"
    try:
        radius = float(form.get("RADIUS_MILES") or 3)
    except (TypeError, ValueError):
        raise AccountError("The zone's size is a number of miles, like 3.") from None
    if not 0.5 <= radius <= 25:
        raise AccountError("The zone can be 0.5 to 25 miles.")
    out["RADIUS_MILES"] = f"{radius:g}"
    ceiling = str(form.get("MAX_ALTITUDE_FT") or "").replace(",", "").strip()
    if ceiling:
        if not ceiling.isdigit() or not 500 <= int(ceiling) <= 60000:
            raise AccountError("The height limit is in feet, 500 to 60,000 (or leave it empty for none).")
    out["MAX_ALTITUDE_FT"] = ceiling
    quiet = str(form.get("QUIET_HOURS") or "").strip()
    if quiet and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d", quiet):
        raise AccountError("Quiet hours look like 22:00-07:00 (or leave them empty for none).")
    out["QUIET_HOURS"] = quiet
    tz = str(form.get("TIMEZONE") or "").strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
        except Exception:
            raise AccountError("That time zone isn't one I know (it looks like America/Los_Angeles).") from None
    out["TIMEZONE"] = tz
    return out


def account_config(owner: Config, accounts: Accounts, user: dict, port: int, public_url: str = "",
                   owner_env: Path | None = None) -> Config:
    """An account's settings as planespotter runs them: its own home, zone, history and notifications; the
    server's shared things (adsb.lol's address and key) from the owner's; its map only on this computer
    (people reach it through the sign-in page)."""
    if user["owner"]:
        cfg = owner
    else:
        folder = accounts.folder(user)
        try:
            cfg = Config.from_mapping(load_dotenv(folder / ".env"), base_dir=folder, env_path=folder / ".env")
        except ConfigError as exc:
            raise AccountError(f"{user['username']}'s settings can't be read: {exc}") from None
        cfg = replace(cfg, adsb_api=owner.adsb_api, adsb_api_key=owner.adsb_api_key, adsbdb_api=owner.adsbdb_api,
                      adsb_daily_limit=owner.adsb_daily_limit, ntfy_server=owner.ntfy_server, receiver_url="",
                      emails=False, server="", places="", db_path=folder / "planespotter.db",
                      outbox_dir=folder / "outbox")
    # Reached through the sign-in page (and the phone asks it directly), so no relay posts are needed there.
    return replace(cfg, map_enabled=True, map_lan=False, map_https=False, map_port=port,
                   relay=cfg.relay and not public_url)
