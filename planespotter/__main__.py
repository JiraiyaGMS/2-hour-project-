"""Command line: python3 -m planespotter {setup,check,watch,map,digest,test-email}"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from datetime import date
from pathlib import Path

from .adsb import Lookup
from .config import DEFAULT_ENV, Config, ConfigError, fmt_clock_range
from .emails import fmt_alt, test_email
from .fullpath import FlightLookup, start_full_path_worker
from .geo import bearing_deg, compass, distance_miles, miles_to_nm
from .mailer import Mailer
from .notify import PushNotifier
from .store import Store
from .watcher import FETCH_ERRORS, SEND_ERRORS, Watcher
from .webmap import MapServerError, make_budget, shared_feed, start_feed_poller, start_map_server
from .wizard import Cancelled, Wizard


def build(cfg: Config) -> Watcher:
    store = Store(cfg.db_path)
    budget = make_budget(cfg)
    flights = (FlightLookup(api_base=cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget)
               if cfg.whole_flights else None)
    return Watcher(cfg, store, shared_feed(cfg, budget), Lookup(store, cfg.adsbdb_api), Mailer(cfg),
                   budget=budget, flights=flights, notifier=PushNotifier(cfg))


def cmd_setup(env_path: Path, args) -> int:
    wizard = Wizard(env_path)
    try:
        values = wizard.run()
    except (Cancelled, KeyboardInterrupt):
        print("\n\nSetup cancelled. Nothing was changed.")
        return 1
    wizard.save(values)
    print(f"\n✔ Saved your settings to {env_path}\n")

    try:
        cfg = Config.load(env_path)
    except ConfigError as exc:  # shouldn't happen, the wizard validates everything
        print(f"Something's off with the settings: {exc}", file=sys.stderr)
        return 2
    try:
        if wizard.yes("Look for planes near you right now?"):
            print()
            cmd_check(cfg, args)
            print()
        if not cfg.dry_run and wizard.yes(f"Send a test email to {cfg.email_to}?"):
            cmd_test_email(cfg, args)
        if cfg.ntfy_topic and wizard.yes("Send a test notification to your phone?"):
            cmd_test_push(cfg, args)
    except (Cancelled, KeyboardInterrupt):
        pass
    except FETCH_ERRORS as exc:
        explain_fetch_error(exc)
    print("\nAll set. To start watching, run:\n\n    python3 -m planespotter watch\n")
    return 0


def cmd_check(cfg: Config, args) -> int:
    """Show what's flying near home right now. Sends nothing, saves nothing."""
    look = max(cfg.radius_miles * 3, 10)
    feed = shared_feed(cfg, make_budget(cfg))
    planes = feed.aircraft_near(cfg.home_lat, cfg.home_lon, miles_to_nm(look))
    if getattr(feed, "uses_receiver", False):
        print("(from your own receiver)")
    rows = []
    for ac in planes:
        d = distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon)
        if d <= look:
            rows.append((d, ac))
    rows.sort(key=lambda r: r[0])
    ceiling = f", below {cfg.max_altitude_ft:,} ft" if cfg.max_altitude_ft else ""
    print(f"{len(rows)} planes in the air within {look:g} mi of {cfg.home_name} "
          f"(your zone is {cfg.radius_miles:g} mi{ceiling}):\n")
    for d, ac in rows:
        too_high = cfg.max_altitude_ft is not None and ac.alt_ft is not None and ac.alt_ft > cfg.max_altitude_ft
        mark = "✈ IN ZONE" if d <= cfg.radius_miles and not too_high else ""
        direction = compass(bearing_deg(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon))
        print(f"  {ac.label:<9} {ac.type_code:<5} {d:5.1f} mi {direction:<2}  {fmt_alt(ac.alt_ft):>10}  {mark}")
    if not rows:
        print("  (nothing right now. Try again in a few minutes, or check your coordinates.)")
    if cfg.quiet_hours and cfg.in_quiet_hours(time.time()):
        print(f"\n  🌙 It's quiet hours ({fmt_clock_range(cfg.quiet_hours)}), so `watch` won't email alerts right now.")
    return 0


def print_map_urls(urls: list[str]) -> None:
    print(f"  Map on this computer:  {urls[0]}")
    if len(urls) > 1:
        print(f"  Map on your phone:     {urls[1]}   (same Wi-Fi)")


def cmd_watch(cfg: Config, args) -> int:
    watcher = build(cfg)
    print("Watching the sky. Leave this window open; press Ctrl+C to stop.\n")
    if cfg.map_enabled:
        try:
            _, urls = start_map_server(cfg, feed=watcher.feed, budget=watcher.budget)
            print_map_urls(urls)
            watcher.notifier.map_url = urls[-1]  # tapping a phone notification opens the map (home Wi-Fi)
            print()
            if args.open:
                webbrowser.open(urls[0])
        except MapServerError as exc:
            print(f"  (No map: {exc})\n")
    try:
        watcher.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_map(cfg: Config, args) -> int:
    """Just the live map, without email alerts."""
    try:
        budget = make_budget(cfg)
        feed = shared_feed(cfg, budget)
        _, urls = start_map_server(cfg, feed=feed, budget=budget)
    except MapServerError as exc:
        print(exc, file=sys.stderr)
        return 1
    start_feed_poller(cfg, feed, budget)  # no watcher here, so the map keeps its own planes fresh
    if cfg.whole_flights:
        start_full_path_worker(cfg, budget)
    print("The live map is running. Press Ctrl+C to stop.\n")
    print_map_urls(urls)
    print("\nThe Spotted list fills up while `watch` is running.")
    if not args.no_browser:
        webbrowser.open(urls[0])
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_digest(cfg: Config, args) -> int:
    """Send the daily report right now (last 24 hours + today's forecast)."""
    watcher = build(cfg)
    today = date.fromisoformat(args.date) if args.date else cfg.local(time.time()).date()
    return 0 if watcher.send_digest(today) else 1


def cmd_weekly(cfg: Config, args) -> int:
    """Send the weekly email right now (the seven days up to today's report time)."""
    watcher = build(cfg)
    today = date.fromisoformat(args.date) if args.date else cfg.local(time.time()).date()
    return 0 if watcher.send_weekly(today) else 1


def cmd_test_special(cfg: Config, args) -> int:
    """Send a sample special-plane alert (email + phone) so you can see what they look like."""
    from .adsb import Aircraft, FlightInfo
    from .emails import special_email

    ac = Aircraft(hex="7c4920", callsign="QFA11", registration="VH-OQA", type_code="A388",
                  lat=cfg.home_lat, lon=cfg.home_lon, alt_ft=4200, speed_kt=180.0, track=250.0)
    info = FlightInfo(airline="Qantas (TEST)", origin="SYD", origin_city="Sydney", destination="LAX",
                      destination_city="Los Angeles", aircraft_desc="Airbus A380-842")
    item = {"ac": ac, "info": info, "distance": 6.4, "bearing": 32.0,
            "reasons": [("type", "Airbus A380 (TEST)", False)]}
    email = special_email([item], cfg)
    email.subject = "TEST · " + email.subject
    try:
        path = Mailer(cfg).send(email)
    except SEND_ERRORS as exc:
        print(f"Email failed: {exc}", file=sys.stderr)
        path = None
    print(f"Saved: {path}" if path else f"✔ Sent a test special-plane email to {cfg.email_to}.")
    if cfg.ntfy_topic:
        ok = PushNotifier(cfg).special("✨ TEST · Airbus A380: QFA11", "Qantas · Airbus A380-842\n6.4 mi NE, 4,200 ft",
                                       ac.hex, ac.callsign)
        print("✔ Sent a test phone notification." if ok else "Phone notification failed (see above).")
    return 0


def cmd_test_push(cfg: Config, args) -> int:
    if not cfg.ntfy_topic:
        print("Phone notifications aren't set up yet. Run: python3 -m planespotter setup")
        return 1
    ok = PushNotifier(cfg).send(
        f"planespotter is set up for {cfg.home_name}",
        "Phone notifications are working. You'll get one when a plane flies over.",
        tags=("white_check_mark", "airplane"))
    print(f"✔ Sent a test notification to topic {cfg.ntfy_topic}." if ok else "Sending failed (see above).")
    return 0 if ok else 1


def cmd_test_email(cfg: Config, args) -> int:
    if cfg.dry_run:
        print("No email login is set up yet, so the test email is saved as a file instead.")
    try:
        path = Mailer(cfg).send(test_email(cfg))
    except SEND_ERRORS as exc:
        print(f"Sending failed: {exc}", file=sys.stderr)
        if "Username and Password not accepted" in str(exc) or "535" in str(exc):
            print("That usually means the app password is wrong. Make a new one and run setup again.",
                  file=sys.stderr)
        return 1
    if path:
        print(f"Saved: {path}")
    else:
        print(f"✔ Sent a test email to {cfg.email_to}. Check your inbox (and spam folder).")
    return 0


def explain_fetch_error(exc: Exception) -> None:
    print(f"Couldn't reach the flight data website: {exc}", file=sys.stderr)
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        print(
            "\nOn a Mac this is a one-time fix: open Finder → Applications → the Python 3 folder,\n"
            "double-click “Install Certificates.command”, then try again.",
            file=sys.stderr,
        )
    else:
        print("Check your internet connection and try again.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planespotter", description="Email alerts for planes over your house.")
    parser.add_argument("--env", help=f"settings file (default: {DEFAULT_ENV})")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.add_parser("setup", help="answer a few questions to create your settings")
    sub.add_parser("check", help="list planes near home right now")
    p_watch = sub.add_parser("watch", help="run forever: alerts, daily report and the live map")
    p_watch.add_argument("--open", action="store_true", help="open the map in your browser")
    p_map = sub.add_parser("map", help="just the live 3D/2D map, no emails")
    p_map.add_argument("--no-browser", action="store_true", help="don't open a browser")
    p_digest = sub.add_parser("digest", help="send the daily report now")
    p_digest.add_argument("--date", help="pretend today is YYYY-MM-DD")
    p_weekly = sub.add_parser("weekly", help="send the weekly email now")
    p_weekly.add_argument("--date", help="pretend today is YYYY-MM-DD")
    sub.add_parser("test-email", help="send a test email")
    sub.add_parser("test-push", help="send a test phone notification")
    sub.add_parser("test-special", help="send a sample special-plane alert")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    env_path = Path(args.env).expanduser() if args.env else DEFAULT_ENV
    if args.command == "setup":
        return cmd_setup(env_path, args)

    try:
        cfg = Config.load(env_path)
    except ConfigError as exc:
        print(f"Settings problem: {exc}", file=sys.stderr)
        return 2

    commands = {"check": cmd_check, "watch": cmd_watch, "map": cmd_map, "digest": cmd_digest,
                "test-email": cmd_test_email, "test-push": cmd_test_push, "weekly": cmd_weekly,
                "test-special": cmd_test_special}
    try:
        return commands[args.command](cfg, args)
    except BrokenPipeError:  # output piped into e.g. `head`; not a network problem
        return 0
    except FETCH_ERRORS as exc:
        explain_fetch_error(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
