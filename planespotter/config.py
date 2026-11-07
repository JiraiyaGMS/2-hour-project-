"""Settings, read from a .env file and/or environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The folder that holds README.md, .env and the planespotter/ package.
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV = PROJECT_DIR / ".env"


class ConfigError(Exception):
    """Raised when a setting is missing or invalid."""


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE file. Blank lines and # comments are ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


def parse_clock(text: str) -> time:
    """'07:00', '7', '7:30', '10pm' -> time. Raises ValueError."""
    m = _CLOCK.match(text.strip().lower())
    if not m:
        raise ValueError(text)
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError(text)
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    return time(hour, minute)  # raises ValueError if out of range


def parse_quiet_hours(text: str) -> tuple[time, time] | None:
    """'22:00-07:00' or '10pm to 7am' -> (start, end). Blank/'none' -> None."""
    text = text.strip().lower()
    if text in {"", "none", "off", "no"}:
        return None
    parts = re.split(r"\s*(?:-|–|—|\bto\b)\s*", text)
    if len(parts) != 2:
        raise ValueError(text)
    start, end = parse_clock(parts[0]), parse_clock(parts[1])
    if start == end:
        raise ValueError(text)
    return start, end


def fmt_clock_range(hours: tuple[time, time]) -> str:
    return f"{hours[0]:%H:%M}–{hours[1]:%H:%M}"


@dataclass(frozen=True)
class Config:
    home_lat: float
    home_lon: float
    home_name: str = "Home"
    radius_miles: float = 3.0
    max_altitude_ft: int | None = None
    poll_seconds: int = 20
    pass_gap_minutes: int = 30
    max_alerts_per_hour: int = 12
    alerts_enabled: bool = True
    quiet_hours: tuple[time, time] | None = None  # (start, end); may wrap past midnight
    digest_enabled: bool = True
    digest_time: time = time(7, 0)
    timezone: tzinfo | None = None  # None = the computer's local time zone
    email_to: str = ""
    email_from: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    db_path: Path = PROJECT_DIR / "planespotter.db"
    outbox_dir: Path = PROJECT_DIR / "outbox"
    adsb_api: str = "https://api.adsb.lol"
    adsbdb_api: str = "https://api.adsbdb.com"
    map_enabled: bool = True  # `watch` also serves the live map
    map_port: int = 8765
    map_lan: bool = True  # let phones on the same Wi-Fi open the map

    @property
    def view_radius_miles(self) -> float:
        """How far out the live map shows planes."""
        return min(115.0, max(self.radius_miles * 4, 15.0))

    @property
    def dry_run(self) -> bool:
        """With no email login configured, emails are saved to outbox/ instead."""
        return not (self.email_to and self.smtp_user and self.smtp_password)

    # --- time helpers -------------------------------------------------------

    def local(self, ts: float) -> datetime:
        """A Unix timestamp as an aware datetime in the configured zone."""
        if self.timezone is not None:
            return datetime.fromtimestamp(ts, self.timezone)
        return datetime.fromtimestamp(ts).astimezone()

    def at(self, day: date, clock: time) -> float:
        """Unix timestamp of a local wall-clock time on ``day``."""
        if self.timezone is not None:
            return datetime.combine(day, clock, self.timezone).timestamp()
        return datetime.combine(day, clock).timestamp()

    def day_start(self, day: date) -> float:
        """Unix timestamp of local midnight at the start of ``day``."""
        return self.at(day, time(0))

    def in_quiet_hours(self, ts: float) -> bool:
        if not self.quiet_hours:
            return False
        start, end = self.quiet_hours
        now = self.local(ts).time()
        if start < end:
            return start <= now < end
        return now >= start or now < end  # wraps past midnight, e.g. 22:00-07:00

    # --- loading ------------------------------------------------------------

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Config":
        path = Path(env_file) if env_file else DEFAULT_ENV
        values = load_dotenv(path)
        values.update({k: v for k, v in os.environ.items() if k in KNOWN_KEYS})
        return cls.from_mapping(values, base_dir=path.resolve().parent, env_path=path)

    @classmethod
    def from_mapping(
        cls, v: dict[str, str], base_dir: Path = PROJECT_DIR, env_path: Path | None = None
    ) -> "Config":
        def get(key: str, default: str = "") -> str:
            return (v.get(key) or default).strip()

        def number(key: str, default: str, kind=float):
            raw = get(key, default)
            try:
                return kind(raw)
            except ValueError:
                raise ConfigError(f"{key} must be a number, got {raw!r}") from None

        def flag(key: str, default: bool) -> bool:
            raw = get(key)
            if not raw:
                return default
            return raw.lower() in {"1", "true", "yes", "on"}

        def path(key: str, default: str) -> Path:
            p = Path(get(key, default)).expanduser()
            return p if p.is_absolute() else base_dir / p

        if not get("HOME_LAT") or not get("HOME_LON"):
            where = f" (looked in {env_path})" if env_path else ""
            raise ConfigError(
                f"No home location set{where}.\n"
                "Run this to set it up:  python3 -m planespotter setup"
            )
        lat = number("HOME_LAT", "")
        lon = number("HOME_LON", "")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ConfigError("HOME_LAT must be -90..90 and HOME_LON -180..180")

        radius = number("RADIUS_MILES", "3")
        if not 0 < radius <= 100:
            raise ConfigError("RADIUS_MILES must be between 0 and 100")

        max_alt = get("MAX_ALTITUDE_FT")
        poll = number("POLL_SECONDS", "20", int)
        if poll < 10:
            raise ConfigError("POLL_SECONDS must be at least 10 (be kind to the free API)")

        digest_raw = get("DIGEST_TIME", "07:00")
        try:
            digest_time = parse_clock(digest_raw)
        except ValueError:
            raise ConfigError(f"DIGEST_TIME must look like 07:00, got {digest_raw!r}") from None

        quiet_raw = get("QUIET_HOURS")
        try:
            quiet_hours = parse_quiet_hours(quiet_raw)
        except ValueError:
            raise ConfigError(
                f"QUIET_HOURS must look like 22:00-07:00 (or be blank), got {quiet_raw!r}"
            ) from None

        tz: tzinfo | None = None
        if get("TIMEZONE"):
            try:
                tz = ZoneInfo(get("TIMEZONE"))
            except (ZoneInfoNotFoundError, ValueError):
                raise ConfigError(
                    f"Unknown TIMEZONE {get('TIMEZONE')!r}. Use a name like "
                    "Europe/London or America/New_York (on Windows: pip install tzdata)."
                ) from None

        smtp_user = get("SMTP_USER")
        return cls(
            home_lat=lat,
            home_lon=lon,
            home_name=get("HOME_NAME", "Home"),
            radius_miles=radius,
            max_altitude_ft=int(number("MAX_ALTITUDE_FT", "0")) if max_alt else None,
            poll_seconds=poll,
            pass_gap_minutes=number("PASS_GAP_MINUTES", "30", int),
            max_alerts_per_hour=number("MAX_ALERTS_PER_HOUR", "12", int),
            alerts_enabled=flag("ALERTS_ENABLED", True),
            quiet_hours=quiet_hours,
            digest_enabled=flag("DIGEST_ENABLED", True),
            digest_time=digest_time,
            timezone=tz,
            email_to=get("EMAIL_TO"),
            email_from=get("EMAIL_FROM", smtp_user),
            smtp_host=get("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=number("SMTP_PORT", "587", int),
            smtp_user=smtp_user,
            smtp_password=get("SMTP_PASSWORD").replace(" ", ""),
            db_path=path("DB_PATH", "planespotter.db"),
            outbox_dir=path("OUTBOX_DIR", "outbox"),
            adsb_api=get("ADSB_API", "https://api.adsb.lol").rstrip("/"),
            adsbdb_api=get("ADSBDB_API", "https://api.adsbdb.com").rstrip("/"),
            map_enabled=flag("MAP_ENABLED", True),
            map_port=number("MAP_PORT", "8765", int),
            map_lan=flag("MAP_LAN", True),
        )


KNOWN_KEYS = {
    "HOME_LAT", "HOME_LON", "HOME_NAME", "RADIUS_MILES", "MAX_ALTITUDE_FT",
    "POLL_SECONDS", "PASS_GAP_MINUTES", "MAX_ALERTS_PER_HOUR", "ALERTS_ENABLED",
    "QUIET_HOURS", "DIGEST_ENABLED", "DIGEST_TIME", "TIMEZONE", "EMAIL_TO",
    "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "DB_PATH",
    "OUTBOX_DIR", "ADSB_API", "ADSBDB_API", "MAP_ENABLED", "MAP_PORT", "MAP_LAN",
}
