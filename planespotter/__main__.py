"""Command line: python3 -m planespotter {setup,check,watch,map,digest,test-email}"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path

from .adsb import Lookup, PositionLookup
from .follow import start_follow_worker
from .space import SpaceWatch
from .config import DEFAULT_ENV, Config, ConfigError, fmt_clock_range
from .emails import fmt_alt, test_email
from .fullpath import FlightLookup, start_full_path_worker
from .geo import bearing_deg, compass, distance_miles, miles_to_nm
from .mailer import Mailer
from .commands import start_command_worker
from .friends import Friends, start_friends_worker
from .jobs import relay_job, transit_job
from .myflight import start_flight_worker
from .notify import NtfyQuota, PushNotifier
from .outlook import Outlook
from .relay import Relay
from .travel import TravelMode
from .sky import SkyWatch
from .store import Store
from .watcher import FETCH_ERRORS, SEND_ERRORS, Watcher
from .webmap import (MapServerError, home_elevation_ft, make_budget, shared_feed, start_feed_poller,
                     start_map_server)
from .wizard import Cancelled, Wizard


def build(cfg: Config, budget=None, share: int = 1, quota=None) -> Watcher:
    store = Store(cfg.db_path)
    budget = budget or make_budget(cfg)
    flights = (FlightLookup(api_base=cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget)
               if cfg.whole_flights else None)
    watcher = Watcher(cfg, store, shared_feed(cfg, budget), Lookup(store, cfg.adsbdb_api), Mailer(cfg),
                      budget=budget, flights=flights, notifier=PushNotifier(cfg, quota=quota),
                      sky=SkyWatch(cfg) if cfg.weather else None,
                      ground_ft=home_elevation_ft(cfg),
                      space=SpaceWatch(cfg, lambda: store) if cfg.space_alerts else None)
    watcher.share = share
    return watcher


def start_place(cfg: Config, budget, share, maps: list, quota=None, ready=None) -> threading.Thread:
    """Watch one of your other places (or where you're travelling) in the background, with its own map."""
    holder: dict = {}

    def run():
        watcher = build(cfg, budget=budget, quota=quota)  # built in its own thread (SQLite)
        if isinstance(share, dict):
            watcher.shared = share  # one count for all places, so travel mode slows them all
        else:
            watcher.share = share
        server = None
        if cfg.map_enabled:
            try:
                server, urls = start_map_server(cfg, feed=watcher.feed, budget=budget, sky=watcher.sky)
                server.app.places = maps
                watcher.notifier.map_url = phone_url(urls)
            except MapServerError as exc:
                print(f"  (No map for {cfg.home_name}: {exc})")
        holder.update(watcher=watcher, server=server)
        if ready is not None:
            ready.set()
        watcher.run()

    thread = threading.Thread(target=run, name=f"place-{cfg.home_name}", daemon=True)
    thread.holder = holder
    thread.start()
    return thread


def launch_place(cfg: Config, budget, share, maps: list, quota=None):
    """Start a place and wait until it's running: (watcher, map server)."""
    ready = threading.Event()
    thread = start_place(cfg, budget, share, maps, quota, ready)
    if not ready.wait(30) or thread.holder.get("watcher") is None:
        raise RuntimeError(f"couldn't start watching {cfg.home_name}")
    return thread.holder["watcher"], thread.holder["server"]


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


def phone_url(urls: list[str]) -> str:
    """The plain address for phones on the Wi-Fi (or this computer's, if phones can't reach it)."""
    return next((u for u in urls[1:] if u.startswith("http://")), urls[0])


def print_map_urls(urls: list[str]) -> None:
    print(f"  Map on this computer:  {urls[0]}")
    if phone_url(urls) != urls[0]:
        print(f"  Map on your phone:     {phone_url(urls)}   (same Wi-Fi)")
    secure = next((u for u in urls if u.startswith("https://")), "")
    if secure:
        print(f"  AR sky view (phone):   {secure}/sky   (secure address: the README explains the warning)")


def cmd_watch(cfg: Config, args) -> int:
    places = cfg.place_configs()
    quota = NtfyQuota(cfg.db_path, cfg.ntfy_server) if cfg.ntfy_topic else None  # ntfy's 250 a day
    shared = {"n": 1 + len(places)}  # how many places share adsb.lol's daily limit (travel mode adds one)
    watcher = build(cfg, quota=quota)
    watcher.shared = shared
    print("Watching the sky. Leave this window open; press Ctrl+C to stop.\n")
    maps = [{"name": c.home_name, "port": c.map_port} for c in [cfg, *places]] if places else []
    travel = TravelMode(cfg, watcher, watcher.budget,
                        lambda c: launch_place(c, watcher.budget, shared, maps, quota))
    outlook = Outlook(cfg)
    friends = Friends(cfg, None, watcher.notifier)
    if cfg.map_enabled:
        try:
            server, urls = start_map_server(cfg, feed=watcher.feed, budget=watcher.budget, sky=watcher.sky)
            server.app.places = maps
            server.app.travel, server.app.outlook, server.app.friends = travel, outlook, friends
            print_map_urls(urls)
            watcher.notifier.map_url = phone_url(urls)  # tapping a phone notification opens the map (home Wi-Fi)
            for c in places:
                print(f"  Map of {c.home_name}:{' ' * max(1, 14 - len(c.home_name))}http://localhost:{c.map_port}")
            print()
            if args.open:
                webbrowser.open(urls[0])
        except MapServerError as exc:
            print(f"  (No map: {exc})\n")
    positions = PositionLookup(cfg.adsb_api, api_key=cfg.adsb_api_key, budget=watcher.budget)
    start_follow_worker(cfg, positions, watcher.notifier)
    start_flight_worker(cfg, watcher.budget, watcher.notifier)
    watcher.after_poll += [relay_job(watcher, Relay(watcher.notifier, time.time), outlook), transit_job(watcher)]
    if cfg.ntfy_topic:
        start_command_worker(cfg, watcher.notifier, travel)  # "travel ...", "flight UA1234" from your phone
        start_friends_worker(friends)
    for c in places:
        print(f"Also watching {c.home_name} ({c.radius_miles:g} mi zone).")
        start_place(c, watcher.budget, shared, maps, quota)
    try:
        travel.restore()  # carry on with a trip that was running before a restart
    except Exception as exc:
        print(f"  (Couldn't restart travel mode: {exc})")
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
    start_follow_worker(cfg, PositionLookup(cfg.adsb_api, api_key=cfg.adsb_api_key, budget=budget), PushNotifier(cfg))
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


def copy_to_clipboard(text: str) -> bool:
    """Put text on the Mac's clipboard (Universal Clipboard then lets you paste it on your iPhone)."""
    import shutil
    import subprocess

    if not shutil.which("pbcopy"):
        return False
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    return True


def phone_test(cfg: Config) -> int:
    """Read the status the widget and Siri read (from ntfy), and say what Siri would say."""
    import json
    import urllib.error

    try:
        body = PushNotifier(cfg).poll("relay", since="latest", raw=True).strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Couldn't reach ntfy: {exc}")
        return 1
    if not body:
        print("Nothing for Siri or the widget yet: planespotter hasn't posted a status in the last 12 hours.")
        print("Start it (python3 -m planespotter watch), keep the Mac awake, wait 5 minutes, then try again.")
        print("If it's running, look in its window for lines mentioning ntfy.")
        return 1
    try:
        status = json.loads(body.splitlines()[-1])
    except ValueError:
        print(f"The status isn't readable: {body[:200]}")
        return 1
    from .relay import clock as clock_text
    from .relay import spoken_now

    now = time.time()
    age = (now - status.get("updated", 0)) / 60
    print(f"✔ Siri would say right now: {spoken_now(status, now, lambda at: clock_text(cfg, at))}")
    print(f"  (from the status posted {age:.0f} min ago; planespotter posts a new one whenever the next plane changes)")
    print("  If Siri says something else, make sure your shortcut uses Scriptable's Run Script action")
    print("  (see the Siri steps in the README), and that the planespotter script in Scriptable is up to date:")
    print("        python3 -m planespotter phone --copy widget")
    return 0


def cmd_phone(cfg: Config, args) -> int:
    """Email yourself everything for the phone: widget, Siri, commands, family, friends, calendar."""
    from .phonekit import kit, kit_email
    from .webmap import lan_address

    if not cfg.ntfy_topic:
        print("Set up phone notifications first: run  python3 -m planespotter setup  and answer y at question 5.")
        return 1
    ip = lan_address() if cfg.map_lan else None
    k = kit(cfg, f"http://{ip}:{cfg.map_port}" if ip else "")
    folder = Path(cfg.db_path).parent / "phone-setup"
    folder.mkdir(exist_ok=True)
    (folder / "planespotter-widget.js").write_text(k["widget"], encoding="utf-8")
    email = kit_email(cfg, k)
    (folder / "phone-setup.txt").write_text(email.text, encoding="utf-8")

    if args.test:
        return phone_test(cfg)
    if args.copy:
        what = {"widget": k["widget"], "siri": k["siri_url"], "commands": k["cmd_url"], "family": k["family_topic"],
                "friend": k["friend_code"]}[args.copy]
        if copy_to_clipboard(what):
            print(f"✔ Copied the {args.copy} {'script' if args.copy == 'widget' else 'link'} to this Mac's clipboard.")
            print("  On your iPhone (same Apple ID, Bluetooth on), paste it within a couple of minutes:")
            print("  in Scriptable for the widget script (Siri uses the same script), or wherever the link goes.")
        else:
            print(what)
        return 0

    print(f"Your phone setup is saved in {folder}\n")
    print(f"  Status link (advanced): {k['siri_url']}")
    print(f"  Commands page:         {k['cmd_url']}")
    print(f"  Family topic:          {k['family_topic']}")
    print(f"  Your friend code:      {k['friend_code']}\n")
    try:
        saved = Mailer(cfg).send(email)
    except SEND_ERRORS as exc:
        print(f"Couldn't email it: {exc}")
        print("Hotel, school and work Wi-Fi often block sending email. Other ways to get it onto your phone:")
        print("  • Copy the widget script (Siri uses it too), then paste it in Scriptable on your iPhone")
        print("    (Universal Clipboard):  python3 -m planespotter phone --copy widget")
        print(f"  • Or AirDrop the files in {folder} to your iPhone.")
        print("  • Or try the other email port:  SMTP_PORT=465 python3 -m planespotter phone")
        return 1
    print(f"✔ Emailed your phone setup to {cfg.email_to}." if not saved else f"Saved: {saved}")
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
    p_phone = sub.add_parser("phone", help="email yourself the phone setup (widget, Siri, commands, family, friends)")
    p_phone.add_argument("--copy", choices=["widget", "siri", "commands", "family", "friend"],
                         help="copy one part to this Mac's clipboard instead (paste it on your iPhone)")
    p_phone.add_argument("--test", action="store_true", help="show what Siri and the widget would say right now")
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
                "test-special": cmd_test_special, "phone": cmd_phone}
    try:
        return commands[args.command](cfg, args)
    except BrokenPipeError:  # output piped into e.g. `head`; not a network problem
        return 0
    except FETCH_ERRORS as exc:
        explain_fetch_error(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
