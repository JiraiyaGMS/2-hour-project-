"""Emails off, following the phone, the phone working on its own, and starting by itself on the Mac."""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.__main__ import main
from planespotter.config import Config
from planespotter.mailer import Mailer
from planespotter.notify import PushNotifier
from planespotter.wizard import set_env_value

from helpers import HOME, make_config, make_watcher, plane, ts


class FakePost:
    def __init__(self):
        self.sent = []

    def __call__(self, url, payload):
        self.sent.append(payload)


class EmailsOffTests(unittest.TestCase):
    def test_nothing_is_sent_or_saved(self):
        with TemporaryDirectory() as tmp:
            cfg = make_config(emails=False, outbox_dir=Path(tmp) / "outbox")
            from planespotter.emails import Email
            self.assertIsNone(Mailer(cfg).send(Email(subject="x", text="y", html="<p>y</p>")))
            self.assertFalse((Path(tmp) / "outbox").exists())

    def test_reports_come_as_phone_notifications(self):
        watcher, _, mailer = make_watcher(emails=False, ntfy_topic="planespotter-test")
        post = FakePost()
        watcher.notifier = PushNotifier(watcher.cfg, post=post)
        for d in range(1, 9):
            for i, hour in enumerate((8, 12, 17, 17)):
                watcher.store.record_sighting(plane(f"a{d}{i}{hour:03d}", callsign=f"BAW{d}{i}"), 1.0, 0,
                                              ts(2026, 11, d, hour, 5 * d), gap=60)
        watcher.store.set_meta("watch_started", str(ts(2026, 10, 1)))
        watcher.clock = lambda: ts(2026, 11, 9, 8)
        self.assertTrue(watcher.send_digest(date(2026, 11, 9)))
        title = post.sent[-1]["title"]
        self.assertTrue(title.startswith("☀ The last day over Test House: 4 planes"), title)
        self.assertIn("Busiest hour: 5 pm (2 planes)", post.sent[-1]["message"])
        self.assertTrue(watcher.send_weekly(date(2026, 11, 9)))
        self.assertTrue(post.sent[-1]["title"].startswith("📅 Your week over Test House:"), post.sent[-1]["title"])
        self.assertEqual(mailer.sent, [])
        self.assertEqual(watcher.store.get_meta("last_weekly"), "2026-11-09")

    def test_the_emails_command_and_settings_file(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"# my settings\nHOME_LAT={HOME[0]}\nHOME_LON={HOME[1]}\nEMAILS=on\n")
            self.assertEqual(main(["--env", str(env), "emails", "off"]), 0)
            text = env.read_text()
            self.assertIn("# my settings", text)
            self.assertEqual(text.count("EMAILS="), 1)
            self.assertFalse(Config.load(env).emails)
            set_env_value(env, "FOLLOW_PHONE", "off")
            self.assertFalse(Config.load(env).follow_phone)
            self.assertTrue(Config.load(env).emails is False and "HOME_LAT" in env.read_text())



class FakeWatcher:
    def __init__(self):
        import threading
        self.share, self.stopped = 1, threading.Event()


class FollowPhoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.clock = [ts(2026, 11, 13, 10)]
        self.built = []

    def tearDown(self):
        self.tmp.cleanup()

    def travel(self, **overrides):
        from planespotter.travel import TravelMode
        cfg = make_config(home_lat=33.985, home_lon=-118.39, db_path=Path(self.tmp.name) / "home.db", **overrides)

        def start_watcher(c):
            self.built.append(c)
            return FakeWatcher(), None
        self.home = FakeWatcher()
        return TravelMode(cfg, self.home, None, start_watcher, clock=lambda: self.clock[0])

    def test_alerts_follow_the_phone_and_come_home(self):
        t = self.travel()
        self.assertEqual(t.follow(33.99, -118.38), "")             # at home: nothing to do
        self.assertEqual(self.built, [])
        news = t.follow(33.8090, -117.9265)                         # Disneyland
        self.assertIn("You're near Fullerton", news)
        self.assertEqual((self.built[-1].home_lat, self.built[-1].home_name), (33.809, "Fullerton"))
        self.assertTrue(t.status()["auto"])
        self.assertEqual(self.home.share, 2)
        self.clock[0] += 3 * 3600
        self.assertEqual(t.follow(33.8150, -117.9200), "")          # wandering around the park: same zone
        self.assertEqual(len(self.built), 1)
        self.assertGreater(t.trip["until"], self.clock[0] + 11 * 3600)  # kept going while the phone is there
        self.assertEqual(t.follow(32.7338, -117.1933), "")          # drove on to San Diego: moves, quietly
        self.assertEqual(len(self.built), 2)
        self.assertEqual(self.home.share, 2)                       # still one extra place, not two
        self.assertIn("Welcome back", t.follow(33.986, -118.391))  # home again
        self.assertFalse(t.status()["on"])
        self.assertEqual(self.home.share, 1)

    def test_a_followed_trip_ends_if_the_phone_goes_quiet(self):
        t = self.travel()
        t.follow(33.8090, -117.9265)
        self.clock[0] += 13 * 3600
        t.check()
        self.assertFalse(t.status()["on"])

    def test_following_can_be_turned_off(self):
        t = self.travel(follow_phone=False)
        self.assertEqual(t.follow(33.8090, -117.9265), "")
        self.assertFalse(t.status()["on"])

    def test_location_command(self):
        from planespotter.commands import handle
        from planespotter.store import Store
        t = self.travel()
        store = Store(":memory:")
        self.assertIn("Fullerton", handle("location 33.8090,-117.9265", t.cfg, store, t, self.clock[0]))
        self.assertIn("Following your phone: watching Fullerton", handle("status", t.cfg, store, t, self.clock[0]))
        self.assertEqual(handle("location nonsense", t.cfg, store, t, self.clock[0]), "")



class AutostartTests(unittest.TestCase):
    def test_the_mac_launch_agent(self):
        import plistlib
        from planespotter.autostart import LABEL, build_plist
        data = plistlib.loads(build_plist("/usr/bin/python3", Path("/Users/me/planespotter/.env"),
                                          Path("/Users/me/planespotter"), Path("/Users/me/planespotter/planespotter.log")))
        self.assertEqual(data["Label"], LABEL)
        self.assertTrue(data["RunAtLoad"] and data["KeepAlive"])        # from login, and again if it stops
        self.assertEqual(data["ProgramArguments"][:3], ["/usr/bin/caffeinate", "-i", "-s"])  # awake on power
        self.assertEqual(data["ProgramArguments"][-1], "watch")
        self.assertIn("--log-file", data["ProgramArguments"])
        self.assertEqual(data["WorkingDirectory"], "/Users/me/planespotter")

    def test_only_one_copy_runs(self):
        from planespotter.autostart import single_instance
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "planespotter.db"
            first = single_instance(db)
            self.assertIsNotNone(first)
            self.assertIsNone(single_instance(db))      # a second copy can tell
            env = Path(tmp) / ".env"
            env.write_text(f"HOME_LAT={HOME[0]}\nHOME_LON={HOME[1]}\nDB_PATH={db}\nMAP_ENABLED=off\n")
            self.assertEqual(main(["--env", str(env), "watch"]), 0)  # steps aside straight away
            first.close()
            again = single_instance(db)
            self.assertIsNotNone(again)                  # free once the first one stops
            again.close()

    def test_log_file_trims_itself(self):
        import logging
        import sys
        from planespotter import autostart
        saved = (sys.stdout, sys.stderr, list(logging.getLogger().handlers), logging.getLogger().level)
        old_limit = autostart.LOG_BYTES
        try:
            with TemporaryDirectory() as tmp:
                log = Path(tmp) / "planespotter.log"
                autostart.LOG_BYTES = 2000
                autostart.log_to_file(log, logging.INFO)
                for i in range(200):
                    print(f"line {i} " + "x" * 40)
                for h in logging.getLogger().handlers:
                    h.flush()
                self.assertIn("line 199", log.read_text())
                self.assertLess(log.stat().st_size, 2500)
                self.assertTrue((Path(tmp) / "planespotter.log.1").exists())
                for h in logging.getLogger().handlers:
                    h.close()
        finally:
            autostart.LOG_BYTES = old_limit
            sys.stdout, sys.stderr = saved[0], saved[1]
            root = logging.getLogger()
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in saved[2]:
                root.addHandler(h)
            root.setLevel(saved[3])

    def test_unsupported_computer(self):
        from unittest import mock
        from planespotter import autostart
        with mock.patch.object(autostart, "supported", return_value=False):
            self.assertEqual(main(["autostart"]), 1)

    def test_linux_service(self):
        from unittest import mock
        from planespotter import autostart
        unit = autostart.build_unit("/usr/bin/python3", Path("/home/ubuntu/planespotter/.env"),
                                    Path("/home/ubuntu/planespotter"), Path("/home/ubuntu/planespotter/planespotter.log"))
        self.assertIn("Restart=always", unit)
        self.assertIn("ExecStart=/usr/bin/python3 -m planespotter --env /home/ubuntu/planespotter/.env", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn('"/home/my files/.env"', autostart.build_unit("p", Path("/home/my files/.env"), Path("/x"), Path("/x/l")))
        calls = []

        def run(cmd, **kw):
            import subprocess
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "ActiveState=active\nMainPID=4242\n", "")
        with TemporaryDirectory() as tmp:
            with mock.patch.object(autostart, "unit_path", return_value=Path(tmp) / "planespotter.service"):
                ok, _ = autostart._linux_install(Path(tmp) / ".env", Path(tmp), run=run)
                self.assertTrue(ok)
                self.assertIn(["systemctl", "--user", "enable", "planespotter"], calls)
                self.assertTrue(any(c[:2] == ["loginctl", "enable-linger"] for c in calls))  # runs from boot
                self.assertEqual(autostart._linux_status(run=run), {"installed": True, "running": True, "pid": 4242})
                self.assertTrue(autostart._linux_uninstall(run=run))

        def needs_sudo(cmd, **kw):
            import subprocess
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "loginctl" else 0, "", "")
        with TemporaryDirectory() as tmp:
            with mock.patch.object(autostart, "unit_path", return_value=Path(tmp) / "planespotter.service"):
                ok, detail = autostart._linux_install(Path(tmp) / ".env", Path(tmp), run=needs_sudo)
                self.assertTrue(ok)
                self.assertEqual(calls[-1][:3], ["sudo", "-n", "loginctl"])  # kept running after logging out
                self.assertNotIn("⚠", detail)


class ServerTests(unittest.TestCase):
    def test_settings_for_the_server(self):
        from planespotter.server import server_env
        text = server_env("# mine\nHOME_LAT=1\nDB_PATH=/Users/me/p.db\nSERVER=ubuntu@1.2.3.4\nMAP_LAN=true\n",
                          "America/Los_Angeles")
        self.assertIn("HOME_LAT=1", text)
        self.assertIn("TIMEZONE=America/Los_Angeles", text)   # servers run on UTC
        self.assertNotIn("DB_PATH", text)
        self.assertNotIn("SERVER=", text)
        self.assertIn("MAP_LAN=false", text)                  # the map isn't opened to the internet
        self.assertNotIn("TIMEZONE=America/Los_Angeles", server_env("TIMEZONE=Europe/London\n", "America/Los_Angeles"))

    def test_address_check(self):
        from planespotter.server import ServerError, check_target
        self.assertEqual(check_target(" ubuntu@203.0.113.5 "), "ubuntu@203.0.113.5")
        for bad in ("203.0.113.5", "ubuntu@", "ubuntu@1.2.3.4; rm -rf ~", "status"):
            with self.assertRaises(ServerError):
                check_target(bad)

    def test_moving_to_the_server(self):
        import subprocess
        from planespotter.server import Remote, move
        from planespotter.store import Store
        sent, commands = {}, []

        def run(cmd, **kw):
            commands.append(cmd)
            if cmd[0] == "scp":
                sent[cmd[-1].split(":", 1)[1]] = Path(cmd[-2]).read_bytes()
                return subprocess.CompletedProcess(cmd, 0, "", "")
            remote_cmd = cmd[-1]
            if "sys.version_info" in remote_cmd:
                return subprocess.CompletedProcess(cmd, 0, "(3, 12)\n", "")
            if remote_cmd.startswith("test -e"):
                return subprocess.CompletedProcess(cmd, 1, "", "")       # no history there yet
            return subprocess.CompletedProcess(cmd, 0, "✔ planespotter now runs by itself\n", "")
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"HOME_LAT={HOME[0]}\nHOME_LON={HOME[1]}\nNTFY_TOPIC=planespotter-x\n")
            db = Path(tmp) / "planespotter.db"
            store = Store(db)
            store.record_sighting(plane("abc123"), 1.0, 0, ts(2026, 11, 13, 9), gap=60)
            store.close()
            said = []
            move(Remote("ubuntu@203.0.113.5", run=run), env, db, say=said.append)
            remote_cmds = [c[-1] for c in commands if c[0] == "ssh"]
            self.assertIn("curl -fsSL", remote_cmds[1])                          # installs planespotter there
            self.assertIn("NTFY_TOPIC=planespotter-x", sent["~/planespotter/.env"].decode())
            self.assertIn("python3 -m planespotter autostart", remote_cmds[-1])   # and starts it by itself
            copied = Path(tmp) / "copied.db"
            copied.write_bytes(sent["~/planespotter/planespotter.db"])
            self.assertEqual(Store(copied).db.execute("SELECT COUNT(*) FROM passes").fetchone()[0], 1)  # history

    def test_the_mac_steps_aside_while_the_server_runs_it(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"HOME_LAT={HOME[0]}\nHOME_LON={HOME[1]}\nDB_PATH={Path(tmp) / 'p.db'}\n"
                           "SERVER=ubuntu@203.0.113.5\n")
            self.assertEqual(main(["--env", str(env), "watch"]), 0)
            self.assertFalse((Path(tmp) / "p.db.lock").exists())  # it didn't even start



class NeverStuckTests(unittest.TestCase):
    """Siri must never get stuck saying something is 'coming over now'."""

    def test_things_that_stay_in_the_zone_are_not_coming_over(self):
        from planespotter.predict import crossings
        cfg = make_config(radius_miles=3)
        hovering = plane("aaa001", lat=HOME[0] + 0.01, speed_kt=3.0, callsign="N44HV")
        no_speed = plane("aaa002", lat=HOME[0] - 0.01, speed_kt=None, callsign="N55PK")
        passing = plane("aaa003", lat=HOME[0] + 0.02, speed_kt=160.0, callsign="DAL1")
        self.assertEqual([c.ac.label for c in crossings(cfg, [hovering, no_speed, passing])], ["DAL1"])

    def test_airport_vehicles_are_not_aircraft(self):
        from planespotter.adsb import parse_aircraft
        raw = {"hex": "a33333", "lat": HOME[0], "lon": HOME[1], "alt_baro": 50, "gs": 12, "track": 0}
        self.assertIsNone(parse_aircraft({**raw, "category": "C2"}))
        self.assertIsNotNone(parse_aircraft({**raw, "category": "A7"}))

    def test_what_siri_says(self):
        from planespotter.predict import say_name
        from planespotter.relay import spoken_now
        self.assertEqual(say_name("", "~2ABC01"), "An unidentified plane")
        now = ts(2026, 11, 13, 22)
        status = {"home": "Anaheim", "updated": now, "nearby": 4,
                  "next": [{"label": "N944AM", "say": "N944AM", "at": now, "direction": "SE", "alt_ft": 1050}]}
        self.assertTrue(spoken_now(status, now, str).startswith("N944AM is coming over now at 1,050 feet, look up"))
        quiet = {"home": "Anaheim", "updated": now, "next": [], "nearby": 4,
                 "hovering": [{"label": "N44HV", "say": "N44HV", "miles": 0.8}]}
        self.assertIn("N44HV is hovering nearby, about 1 mile away.", spoken_now(quiet, now, str))



class NightTests(unittest.TestCase):
    """Quiet hours: nothing on the phone at night; it starts again with the next plane after."""

    def notifier(self, clock, **overrides):
        from datetime import time as t
        cfg = make_config(ntfy_topic="planespotter-test", quiet_hours=(t(22), t(7)), **overrides)
        post = FakePost()
        n = PushNotifier(cfg, post=post)
        n.clock = lambda: clock[0]
        return n, post

    def test_nothing_reaches_the_phone_at_night(self):
        clock = [ts(2026, 11, 14, 3)]
        n, post = self.notifier(clock)
        self.assertFalse(n.send("🛬 BA123 has landed", "in London"))              # my flight: waits
        self.assertFalse(n.send("📴 found 3 planes", "while you were off"))       # offline log: waits
        self.assertFalse(n.send("planespotter", "📍 You're near Fullerton", kind="follow"))
        self.assertTrue(n.send("planespotter", "🏠 Watching home", kind="reply"))  # you asked: answered
        self.assertTrue(n.send("Test", "it works", kind="test"))
        self.assertEqual([p["message"] for p in post.sent], ["🏠 Watching home", "it works"])
        clock[0] = ts(2026, 11, 14, 7)
        self.assertTrue(n.send("✈ DAL1 over Test House", "…"))                    # quiet hours are over

    def test_alerts_start_again_with_the_next_plane(self):
        from datetime import time as t
        watcher, feed, _ = make_watcher(quiet_hours=(t(22), t(7)), ntfy_topic="planespotter-test", emails=False)
        clock = [ts(2026, 11, 14, 3)]
        watcher.notifier, post = self.notifier(clock)
        for hour, hex_code in [(3, "aaa001"), (6, "aaa002")]:
            clock[0] = ts(2026, 11, 14, hour, 30)
            feed.planes = [plane(hex_code, callsign=f"NIGHT{hour}")]
            watcher.send_alerts(watcher.poll(clock[0]), clock[0])
        self.assertEqual(post.sent, [])
        clock[0] = ts(2026, 11, 14, 7, 5)
        feed.planes = [plane("bbb001", callsign="MORNING1")]
        watcher.send_alerts(watcher.poll(clock[0]), clock[0])
        self.assertEqual([p["title"] for p in post.sent], ["✈ MORNING1 over Test House"])  # no pile of night ones

    def test_the_morning_report_waits_for_quiet_hours_to_end(self):
        from datetime import time as t
        watcher, feed, _ = make_watcher(quiet_hours=(t(22), t(7)), digest_time=t(6, 30), ntfy_topic="planespotter-test",
                                        emails=False)
        clock = [ts(2026, 11, 14, 6, 30)]
        watcher.notifier, post = self.notifier(clock)
        watcher.clock = lambda: clock[0]
        watcher.store.set_meta("watch_started", str(ts(2026, 11, 1)))
        watcher.store.record_sighting(plane("aaa001"), 1.0, 0, ts(2026, 11, 13, 12), gap=60)
        self.assertFalse(watcher.send_digest(date(2026, 11, 14)))   # 6:30: still quiet hours
        self.assertIsNone(watcher.digest_due(ts(2026, 11, 14, 6, 35)))  # tries again in 10 minutes
        clock[0] = ts(2026, 11, 14, 7, 1)
        self.assertEqual(watcher.digest_due(clock[0]), date(2026, 11, 14))
        self.assertTrue(watcher.send_digest(date(2026, 11, 14)))
        self.assertTrue(post.sent[-1]["title"].startswith("☀"))

    def test_location_news_waits_but_your_commands_are_answered(self):
        from datetime import time as t
        from planespotter.commands import Commands
        from planespotter.store import Store
        clock = [ts(2026, 11, 14, 2)]
        n, post = self.notifier(clock)
        msgs = [{"event": "message", "id": "m1", "message": "location 33.8090,-117.9265"},
                {"event": "message", "id": "m2", "message": "status"}]
        n.poll = lambda kind, since="latest": list(msgs)

        class Travel:
            def follow(self, lat, lon):
                return "📍 You're near Fullerton"

            def status(self):
                return {"on": True, "auto": True, "name": "Fullerton", "until_text": "Sat 2 PM"}
        Commands(n.cfg, Store(":memory:"), n, Travel(), clock=lambda: clock[0]).step()
        self.assertEqual(len(post.sent), 1)
        self.assertIn("Following your phone", post.sent[0]["message"])

    def test_the_widget_keeps_updating_all_night(self):
        from planespotter.relay import Relay
        clock = [ts(2026, 11, 14, 3)]
        n, post = self.notifier(clock)
        relay = Relay(n, lambda: clock[0])
        self.assertTrue(relay.maybe_send({"v": 1, "home": "Home", "updated": clock[0], "next": [], "last": None}))
        self.assertEqual(len(post.sent), 1)  # its status isn't a notification, so quiet hours don't stop it



class SearchAndPointTests(unittest.TestCase):
    def test_airline_table(self):
        from planespotter.airlines import AIRLINES, IATA_TO_ICAO
        from planespotter.myflight import callsign_for
        self.assertEqual(len({a[0] for a in AIRLINES}), len(AIRLINES))  # each airline once
        self.assertEqual(IATA_TO_ICAO["WN"], "SWA")
        self.assertEqual((callsign_for("WN 12"), callsign_for("NK 123")), ("SWA12", "NKS123"))

    def test_the_phone_gets_airlines_and_airports(self):
        from planespotter.webmap import AIRPORTS_URL, big_airports, widget_script
        script = widget_script(make_config(ntfy_topic="planespotter-test"), "")
        for left in ("__SETTINGS__", "__AIRLINES__", "__AIRPORTS__"):
            self.assertNotIn(left, script)
        self.assertIn('["SWA","WN","Southwest Airlines"]', script)
        lax = [a for a in big_airports() if a[0] == "LAX"][0]
        self.assertEqual((lax[1], lax[3]), ("KLAX", "Los Angeles"))
        self.assertIn(AIRPORTS_URL, script)  # every other airport, when a search needs it
        self.assertLess(len(script.encode()), 200_000)

    def test_siri_says_where_to_look(self):
        from planespotter.relay import spoken_now
        now = ts(2026, 11, 13, 22)
        status = {"home": "Anaheim", "updated": now, "next": [], "nearby": 6,
                  "nearest": {"label": "SWA1672", "say": "SWA1672", "miles": 6.2, "direction": "NE", "up": 9}}
        said = spoken_now(status, now, str)
        self.assertIn("The nearest plane is SWA1672, about 6 miles away: look north-east, about 9° up.", said)
        self.assertNotIn("planes within 10 miles", said)
        both = {**status, "hovering": [{"label": "SWA1672", "say": "SWA1672", "miles": 0.8}]}
        self.assertNotIn("The nearest plane", spoken_now(both, now, str))  # not the same one twice

    def test_point_opens_in_safari(self):
        # Scriptable's web views can't use the compass, so 🔭 Point hands the planes to a page in Safari.
        from pathlib import Path
        from planespotter.webmap import POINT_URL, widget_script
        repo = Path(__file__).resolve().parent.parent
        script = widget_script(make_config(ntfy_topic="planespotter-test"), "")
        self.assertIn(json.dumps(POINT_URL), script)
        self.assertTrue(POINT_URL.startswith("https://"))  # Safari only gives the compass to https pages
        page = repo / POINT_URL.split("/claude/exciting-cray-6oq525/", 1)[1]
        self.assertTrue(page.is_file(), page)
        html = page.read_text()
        for needed in ("location.hash", "requestPermission", "webkitCompassHeading", "speechSynthesis", "data.back"):
            self.assertIn(needed, html)
        self.assertFalse("https://api.adsb.lol" in html or "/v2/point" in html)  # pages can't ask it: planes come in the link
        self.assertNotIn("DeviceOrientationEvent", script)  # no compass in Scriptable itself any more
        self.assertIn("Safari.open(pointLink(", script)



class InstallInScriptableTests(unittest.TestCase):
    """`phone --install`: the script goes straight into Scriptable's iCloud Drive folder, no copy and paste."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        self.icloud = self.tmp / "iCloud~dk~simonbs~Scriptable" / "Documents"
        self.icloud.mkdir(parents=True)
        self.backups = self.tmp / "phone-setup"
        from planespotter.webmap import widget_script
        self.new = widget_script(make_config(ntfy_topic="planespotter-test"), "", with_keys=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_no_icloud_folder(self):
        from planespotter.phonekit import install_in_scriptable
        self.assertIn("error", install_in_scriptable(self.new, folder=self.tmp / "nope"))

    def test_replaces_every_copy_and_keeps_the_icon(self):
        from planespotter.phonekit import install_in_scriptable
        header = ("// Variables used by Scriptable.\n// These must be at the very top of the file. Do not edit.\n"
                  "// icon-color: deep-blue; icon-glyph: plane;\n")
        old = "// planespotter for the free Scriptable app (https://scriptable.app): old\nnoCompass();\n"
        (self.icloud / "planespotter.js").write_text(header + old)
        (self.icloud / "planespotter 2.js").write_text(old)        # pasted into a second copy by mistake
        (self.icloud / "Hello World.js").write_text("log('hi')\n")  # someone else's script
        done = install_in_scriptable(self.new, folder=self.icloud, backup_dir=self.backups)
        self.assertEqual((sorted(done["updated"]), done["added"]), (["planespotter", "planespotter 2"], None))
        main = (self.icloud / "planespotter.js").read_text()
        self.assertTrue(main.startswith(header + "// planespotter for the free Scriptable app"))
        self.assertIn("🔭 Point ↗", main)
        self.assertNotIn("noCompass", main)
        self.assertEqual((self.icloud / "planespotter 2.js").read_text(), self.new)
        self.assertEqual((self.icloud / "Hello World.js").read_text(), "log('hi')\n")
        self.assertEqual((self.backups / "previous-planespotter.js").read_text(), header + old)

    def test_adds_it_when_missing(self):
        from planespotter.phonekit import install_in_scriptable
        done = install_in_scriptable(self.new, folder=self.icloud)
        self.assertEqual((done["updated"], done["added"]), ([], "planespotter"))
        self.assertEqual((self.icloud / "planespotter.js").read_text(), self.new)

    def test_fetches_a_copy_icloud_left_in_the_cloud(self):
        from planespotter.phonekit import install_in_scriptable
        (self.icloud / ".planespotter.js.icloud").write_bytes(b"placeholder")
        asked = []

        def download(path):  # what `brctl download` does: the real file appears
            asked.append(path.name)
            path.write_text("// planespotter for the free Scriptable app: old\n")
            (self.icloud / ".planespotter.js.icloud").unlink()
        done = install_in_scriptable(self.new, folder=self.icloud, download=download, wait_s=2)
        self.assertEqual((asked, done["updated"], done["added"]), (["planespotter.js"], ["planespotter"], None))
        self.assertEqual((self.icloud / "planespotter.js").read_text(), self.new)
        self.assertEqual(sorted(p.name for p in self.icloud.iterdir()), ["planespotter.js"])  # no duplicate


class WheresMyPlaneTests(unittest.TestCase):
    def test_the_mac_shares_the_flight_it_follows(self):
        from planespotter.jobs import relay_job
        from planespotter.myflight import add_flight
        from planespotter.relay import Relay
        watcher, _, _ = make_watcher(ntfy_topic="planespotter-test")
        now = ts(2026, 11, 13, 18)
        add_flight(watcher.store, "UA 1234", date(2026, 11, 13), now - 3600)
        watcher.store.db.execute("UPDATE my_flights SET state = 'landed', landed = ?, hex = 'abc123', "
                                 "destination_city = 'Seattle' WHERE callsign = 'UAL1234'", (now - 600,))
        post = FakePost()
        relay_job(watcher, Relay(PushNotifier(watcher.cfg, post=post), lambda: now), None)([], now)
        mine = json.loads(post.sent[-1]["message"])["myflight"]
        self.assertEqual((mine["callsign"], mine["state"], mine["destination_city"]), ("UAL1234", "landed", "Seattle"))



AEROAPI_LANDED = {"flights": [
    {"ident": "ASA312", "ident_icao": "ASA312", "status": "Scheduled", "origin": {"code": "KLAX", "code_iata": "LAX"},
     "destination": {"code": "KSEA", "code_iata": "SEA", "city": "Seattle"}, "gate_origin": "21A", "gate_destination": None,
     "scheduled_out": "2026-11-14T18:00:00Z"},
    {"ident": "ASA312", "ident_icao": "ASA312", "status": "Arrived / Gate Arrival", "origin": {"code": "KLAX", "code_iata": "LAX"},
     "destination": {"code": "KSEA", "code_iata": "SEA", "city": "Seattle"}, "gate_origin": "22B", "terminal_origin": "6",
     "gate_destination": "N12", "terminal_destination": "N", "baggage_claim": "12",
     "scheduled_out": "2026-11-13T15:00:00Z", "actual_out": "2026-11-13T15:05:00Z", "actual_off": "2026-11-13T15:20:00Z",
     "actual_on": "2026-11-13T17:40:00Z", "actual_in": "2026-11-13T17:48:00Z"},
]}
AVIATIONSTACK_LANDED = {"pagination": {"limit": 100, "offset": 0, "count": 1, "total": 1}, "data": [
    {"flight_date": "2026-11-13", "flight_status": "landed",
     "departure": {"airport": "Los Angeles International", "iata": "LAX", "terminal": "6", "gate": "22B"},
     "arrival": {"airport": "Seattle-Tacoma International", "iata": "SEA", "terminal": "N", "gate": "N12", "baggage": "12"},
     "flight": {"iata": "AS312", "icao": "ASA312", "number": "312"}}]}


class GateTests(unittest.TestCase):
    now = ts(2026, 11, 13, 18, 10)

    def test_flightaware(self):
        from planespotter.gates import describe, lookup
        asked = []

        def fetch(url, headers):
            asked.append((url, headers))
            return 200, AEROAPI_LANDED
        info = lookup("aeroapi", "KEY", "ASA312", self.now, fetch)
        self.assertEqual(asked[0], ("https://aeroapi.flightaware.com/aeroapi/flights/ASA312", {"x-apikey": "KEY"}))
        self.assertEqual((info["arr"]["gate"], info["arr"]["terminal"], info["dep"]["gate"]), ("N12", "N", "22B"))  # today's, not tomorrow's
        self.assertIn("arrives at gate N12 (terminal N)", describe(info))
        self.assertEqual(info["phase"], "landed")

    def test_a_flight_that_hasnt_left_yet(self):
        from planespotter.gates import lookup
        body = {"flights": [{"ident_icao": "ASA1349", "status": "Scheduled / Delayed", "origin": {"code_iata": "SEA"},
                             "destination": {"code_iata": "HNL"}, "gate_origin": "C3", "departure_delay": 1500,
                             "scheduled_out": "2026-11-13T20:00:00Z", "estimated_out": "2026-11-13T20:25:00Z",
                             "estimated_off": "2026-11-13T20:40:00Z"}]}
        info = lookup("aeroapi", "KEY", "ASA1349", self.now, lambda u, h: (200, body))
        self.assertEqual((info["phase"], info["dep"]["gate"], info["dep"]["delay"]), ("scheduled", "C3", 25))
        self.assertEqual(info["takeoff"]["estimated"], ts(2026, 11, 13, 20, 40))

    def test_aviationstack(self):
        from planespotter.gates import lookup
        asked = []

        def fetch(url, headers):
            asked.append(url)
            return 200, AVIATIONSTACK_LANDED
        info = lookup("aviationstack", "KEY", "asa312", self.now, fetch)
        self.assertTrue(asked[0].startswith("https://api.aviationstack.com/v1/flights?access_key=KEY&flight_icao=ASA312"))
        self.assertEqual((info["arr"]["gate"], info["arr"]["baggage"]), ("N12", "12"))

    def _stack(self, *trips):
        return {"data": [dict(t) for t in trips]}

    def test_aviationstack_times_are_the_airports_clock(self):
        # Aviationstack labels times "+00:00" but means the airport's own clock: 3:05 pm in Portland, not 3:05 UTC.
        from planespotter.gates import describe, lookup
        now = ts(2026, 11, 14, 20)  # noon in Portland
        body = self._stack({"flight_date": "2026-11-14", "flight_status": "scheduled",
                            "departure": {"iata": "PDX", "timezone": "America/Los_Angeles", "gate": "C7", "delay": 15,
                                          "scheduled": "2026-11-14T15:05:00+00:00", "estimated": "2026-11-14T15:20:00+00:00"},
                            "arrival": {"iata": "LAX", "timezone": "America/Los_Angeles", "scheduled": "2026-11-14T17:25:00+00:00"},
                            "flight": {"icao": "ASA1349", "codeshared": None}})
        info = lookup("aviationstack", "K", "ASA1349", now, lambda u, h: (200, body))
        self.assertEqual((info["dep"]["scheduled"], info["dep"]["estimated"]), (ts(2026, 11, 14, 23, 5), ts(2026, 11, 14, 23, 20)))
        self.assertEqual(info["arr"]["scheduled"], ts(2026, 11, 15, 1, 25))
        self.assertIn("leaves from gate C7", describe(info))

    def test_if_aviationstack_ever_sends_real_utc_it_still_reads_right(self):
        from planespotter.gates import lookup
        now = ts(2026, 11, 14, 20)
        body = self._stack({"flight_date": "2026-11-14", "flight_status": "active",  # took off an hour ago (real UTC)
                            "departure": {"iata": "PDX", "timezone": "America/Los_Angeles", "actual": "2026-11-14T19:00:00+00:00"},
                            "arrival": {"iata": "LAX", "timezone": "America/Los_Angeles", "estimated": "2026-11-14T21:10:00+00:00"},
                            "live": {"updated": "2026-11-14T19:58:00+00:00", "latitude": 42.0, "longitude": -122.0, "is_ground": False},
                            "flight": {"icao": "ASA1349"}})
        info = lookup("aviationstack", "K", "ASA1349", now, lambda u, h: (200, body))
        self.assertEqual((info["phase"], info["dep"]["actual"]), ("air", ts(2026, 11, 14, 19)))

    def test_the_leg_that_fits_now(self):
        from planespotter.gates import lookup
        now = ts(2026, 11, 14, 21, 30)  # 1:30 pm in Seattle
        tz = "America/Los_Angeles"
        def trip(day, status, frm, to, out, inn, gate, **kw):
            return {"flight_date": day, "flight_status": status, "flight": {"icao": "ASA2034", "codeshared": kw.get("codeshared")},
                    "departure": {"iata": frm, "timezone": tz, "gate": gate, "scheduled": out, **kw.get("dep", {})},
                    "arrival": {"iata": to, "timezone": tz, "scheduled": inn, **kw.get("arr", {})}}
        yesterday = trip("2026-11-13", "landed", "SEA", "GEG", "2026-11-13T14:30:00+00:00", "2026-11-13T15:40:00+00:00", "A1",
                         arr={"actual": "2026-11-13T15:35:00+00:00"})
        leg1 = trip("2026-11-14", "landed", "PDX", "SEA", "2026-11-14T12:00:00+00:00", "2026-11-14T13:00:00+00:00", "C9",
                    arr={"actual": "2026-11-14T12:55:00+00:00"})
        leg2 = trip("2026-11-14", "scheduled", "SEA", "GEG", "2026-11-14T14:30:00+00:00", "2026-11-14T15:40:00+00:00", "A2")
        codeshare = trip("2026-11-14", "scheduled", "SEA", "GEG", "2026-11-14T14:30:00+00:00", "2026-11-14T15:40:00+00:00", "Z9",
                         codeshared={"flight_icao": "asa2034"})
        info = lookup("aviationstack", "K", "QXE2034", now, lambda u, h: (200, self._stack(codeshare, yesterday, leg1, leg2)))
        self.assertEqual((info["dep"]["code"], info["dep"]["gate"]), ("SEA", "A2"))  # about to leave, not just landed
        only_yesterday = lookup("aviationstack", "K", "ASA2034", now, lambda u, h: (200, self._stack(yesterday)))
        self.assertTrue(only_yesterday.get("none"))  # another day's trip is no answer
        asked = []
        lookup("aviationstack", "K", "QXE2034", now, lambda u, h: (asked.append(u), (200, {"data": []}))[1])
        self.assertIn("flight_icao=ASA2034", asked[0])  # Horizon's QXE flights are listed under Alaska's number

    def test_diverted(self):
        from planespotter.gates import describe, lookup
        now = ts(2026, 11, 14, 20)
        body = self._stack({"flight_date": "2026-11-14", "flight_status": "diverted", "flight": {"icao": "ASA99"},
                            "departure": {"iata": "ANC", "timezone": "America/Anchorage", "actual": "2026-11-14T07:00:00+00:00"},
                            "arrival": {"iata": "SEA", "timezone": "America/Los_Angeles", "gate": "N3",
                                        "scheduled": "2026-11-14T11:40:00+00:00"}})
        info = lookup("aviationstack", "K", "ASA99", now, lambda u, h: (200, body))
        self.assertEqual(info["phase"], "diverted")
        self.assertIn("DIVERTED", describe(info))

    def test_problems(self):
        from planespotter.gates import lookup
        self.assertEqual(lookup("aeroapi", "BAD", "ASA312", self.now, lambda u, h: (401, {"title": "Unauthorized"}))["error"], "key")
        used_up = {"error": {"code": "usage_limit_reached", "message": "monthly allowed request volume"}}
        self.assertEqual(lookup("aviationstack", "K", "ASA312", self.now, lambda u, h: (429, used_up))["error"], "limit")
        self.assertTrue(lookup("aviationstack", "K", "ASA312", self.now, lambda u, h: (200, {"data": []})).get("none"))

    def test_the_key_only_goes_to_your_phone(self):
        from planespotter.phonekit import kit
        from planespotter.webmap import widget_script
        cfg = make_config(ntfy_topic="planespotter-test", gates_api="aeroapi", gates_key="SECRET123")
        self.assertIn('"api": "aeroapi", "key": "SECRET123"', kit(cfg, "")["widget"])   # the copy for your phone
        self.assertNotIn("SECRET123", widget_script(cfg, "http://192.168.1.5:8765"))      # what the map serves on Wi-Fi

    def test_the_gates_command(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"HOME_LAT={HOME[0]}\nHOME_LON={HOME[1]}\n")
            self.assertEqual(main(["--env", str(env), "gates", "aeroapi", "KEY123"]), 0)
            cfg = Config.load(env)
            self.assertEqual((cfg.gates_api, cfg.gates_key), ("aeroapi", "KEY123"))
            self.assertEqual(main(["--env", str(env), "gates", "off"]), 0)
            self.assertEqual(Config.load(env).gates_key, "")


if __name__ == "__main__":
    unittest.main()
