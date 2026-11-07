"""Command line: python -m planespotter {check,watch,digest,test-email}"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date

from .adsb import LiveFeed, Lookup
from .config import Config, ConfigError
from .emails import fmt_alt, test_email
from .geo import bearing_deg, compass, distance_miles, miles_to_nm
from .mailer import Mailer
from .store import Store
from .watcher import FETCH_ERRORS, SEND_ERRORS, Watcher


def build(cfg: Config) -> Watcher:
    store = Store(cfg.db_path)
    return Watcher(
        cfg, store, LiveFeed(cfg.adsb_api), Lookup(store, cfg.adsbdb_api), Mailer(cfg)
    )


def cmd_check(cfg: Config, args) -> int:
    """Show what's flying near home right now. Sends nothing, saves nothing."""
    look = max(cfg.radius_miles * 3, 10)
    planes = LiveFeed(cfg.adsb_api).aircraft_near(cfg.home_lat, cfg.home_lon, miles_to_nm(look))
    rows = []
    for ac in planes:
        d = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        if d <= look:
            rows.append((d, ac))
    rows.sort(key=lambda r: r[0])
    print(f"{len(rows)} airborne within {look:g} mi of {cfg.home_name} "
          f"(your zone is {cfg.radius_miles:g} mi{f', below {cfg.max_altitude_ft:,} ft' if cfg.max_altitude_ft else ''}):\n")
    for d, ac in rows:
        too_high = cfg.max_altitude_ft is not None and ac.alt_ft is not None and ac.alt_ft > cfg.max_altitude_ft
        mark = "✈ IN ZONE" if d <= cfg.radius_miles and not too_high else ""
        direction = compass(bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon))
        print(f"  {ac.label:<9} {ac.type_code:<5} {d:5.1f} mi {direction:<2}  {fmt_alt(ac.alt_ft):>10}  {mark}")
    if not rows:
        print("  (nothing right now — try a bigger RADIUS_MILES to sanity-check your location)")
    return 0


def cmd_watch(cfg: Config, args) -> int:
    watcher = build(cfg)
    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def cmd_digest(cfg: Config, args) -> int:
    """Send the daily digest right now (yesterday's recap + today's forecast)."""
    watcher = build(cfg)
    today = date.fromisoformat(args.date) if args.date else cfg.local(time.time()).date()
    return 0 if watcher.send_digest(today) else 1


def cmd_test_email(cfg: Config, args) -> int:
    if cfg.dry_run:
        print("No email login set (EMAIL_TO, SMTP_USER, SMTP_PASSWORD), so this goes to outbox/.")
    try:
        Mailer(cfg).send(test_email(cfg))
    except SEND_ERRORS as exc:
        print(f"Sending failed: {exc}", file=sys.stderr)
        return 1
    if not cfg.dry_run:
        print(f"Sent a test email to {cfg.email_to}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planespotter", description="Email alerts for planes over your house.")
    parser.add_argument("--env", default=".env", help="settings file (default: .env)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="list planes near home right now")
    sub.add_parser("watch", help="run forever: alerts + daily digest")
    p_digest = sub.add_parser("digest", help="send the daily digest now")
    p_digest.add_argument("--date", help="pretend today is YYYY-MM-DD")
    sub.add_parser("test-email", help="send a test email")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        cfg = Config.load(args.env)
    except ConfigError as exc:
        print(f"Config problem: {exc}", file=sys.stderr)
        return 2

    commands = {"check": cmd_check, "watch": cmd_watch, "digest": cmd_digest, "test-email": cmd_test_email}
    try:
        return commands[args.command](cfg, args)
    except FETCH_ERRORS as exc:
        print(f"Couldn't reach the flight data API: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
