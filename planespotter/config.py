"""Settings, read from a .env file and/or environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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


@dataclass(frozen=True, kw_only=True)
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
    digest_enabled: bool = True
    digest_time: time = time(7, 0)
    timezone: tzinfo | None = None  # None = the computer's local time zone
    email_to: str = ""
    email_from: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    db_path: Path = Path("planespotter.db")
    outbox_dir: Path = Path("outbox")
    adsb_api: str = "https://api.adsb.lol"
    adsbdb_api: str = "https://api.adsbdb.com"

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

    def day_start(self, day: date) -> float:
        """Unix timestamp of local midnight at the start of ``day``."""
        if self.timezone is not None:
            return datetime.combine(day, time(0), self.timezone).timestamp()
        return datetime.combine(day, time(0)).timestamp()

    # --- loading ------------------------------------------------------------

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "Config":
        values = load_dotenv(Path(env_file))
        values.update({k: v for k, v in os.environ.items() if k in _KNOWN_KEYS})
        return cls.from_mapping(values)

    @classmethod
    def from_mapping(cls, v: dict[str, str]) -> "Config":
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

        if not get("HOME_LAT") or not get("HOME_LON"):
            raise ConfigError(
                "HOME_LAT and HOME_LON are required. Copy .env.example to .env "
                "and fill in your location."
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
            digest_time = time.fromisoformat(digest_raw)
        except ValueError:
            raise ConfigError(f"DIGEST_TIME must look like 07:00, got {digest_raw!r}") from None

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
            digest_enabled=flag("DIGEST_ENABLED", True),
            digest_time=digest_time,
            timezone=tz,
            email_to=get("EMAIL_TO"),
            email_from=get("EMAIL_FROM", smtp_user),
            smtp_host=get("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=number("SMTP_PORT", "587", int),
            smtp_user=smtp_user,
            smtp_password=get("SMTP_PASSWORD").replace(" ", ""),
            db_path=Path(get("DB_PATH", "planespotter.db")),
            outbox_dir=Path(get("OUTBOX_DIR", "outbox")),
            adsb_api=get("ADSB_API", "https://api.adsb.lol").rstrip("/"),
            adsbdb_api=get("ADSBDB_API", "https://api.adsbdb.com").rstrip("/"),
        )


_KNOWN_KEYS = {
    "HOME_LAT", "HOME_LON", "HOME_NAME", "RADIUS_MILES", "MAX_ALTITUDE_FT",
    "POLL_SECONDS", "PASS_GAP_MINUTES", "MAX_ALERTS_PER_HOUR", "ALERTS_ENABLED",
    "DIGEST_ENABLED", "DIGEST_TIME", "TIMEZONE", "EMAIL_TO", "EMAIL_FROM",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "DB_PATH",
    "OUTBOX_DIR", "ADSB_API", "ADSBDB_API",
}
