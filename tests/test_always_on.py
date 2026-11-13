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


if __name__ == "__main__":
    unittest.main()
