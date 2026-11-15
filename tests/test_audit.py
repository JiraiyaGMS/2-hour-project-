"""Safety checks: kinds of network (`doctor`), the one-step `update`, and what planes broadcast."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import make_config


def fake_probe(**special):
    """Every site answers normally, except the ones given: key -> answer (matched by a word in the address)."""
    normal = {"captive.apple": {"status": 200, "text": "<HTML><BODY>Success</BODY></HTML>", "type": "text/html"},
              "/v2/point/": {"status": 200, "text": '{"ac":[],"msg":"No error"}', "type": "application/json"},
              "/v0/callsign/": {"status": 200, "text": '{"response":{}}', "type": "application/json"},
              "/v1/health": {"status": 200, "text": '{"healthy":true}', "type": "application/json"},
              "githack": {"status": 200, "text": "<!doctype html>", "type": "text/html"},
              "unpkg": {"status": 200, "text": ".maplibregl-map{}", "type": "text/css"},
              "openfreemap": {"status": 200, "text": '{"version":8}', "type": "application/json"},
              "github.com": {"status": 200, "text": "<!DOCTYPE html>", "type": "text/html"},
              "localhost": {"status": 200, "text": '{"home":{"lat":1}}', "type": "application/json"},
              "192.168.": {"status": 200, "text": '{"home":{"lat":1}}', "type": "application/json"},
              "100.101.": {"status": 200, "text": '{"home":{"lat":1}}', "type": "application/json"}}

    def probe(url, timeout=8, headers=None):
        for word, answer in {**normal, **special}.items():
            if word in url:
                return answer
        return {"error": "unexpected " + url, "kind": "offline"}
    return probe


HOME_WIFI = {"addresses": ["192.168.1.23"], "mac": True, "firewall": False, "icloud": True,
             "autostart": {"installed": True, "running": True}, "used": 120, "limit": 500}
PAGE = {"status": 200, "text": "<!DOCTYPE html><html><body>Welcome to Airport Free WiFi</body></html>", "type": "text/html"}
OFFLINE = {"error": "nodename nor servname provided", "kind": "offline"}


class DoctorTests(unittest.TestCase):
    def run_it(self, facts=None, **special):
        from planespotter.doctor import diagnose
        cfg = make_config(ntfy_topic="planespotter-test")
        return diagnose(cfg, fake_probe(**special), facts or HOME_WIFI)

    def marks(self, results, word):
        return [m for m, what, _ in results if word in what]

    def test_home_wifi_all_good(self):
        results = self.run_it()
        self.assertEqual({m for m, _, _ in results}, {"✔"}, [r for r in results if r[0] != "✔"])
        self.assertTrue(any("http://192.168.1.23:8765" in what for _, what, _ in results))

    def test_a_wifi_that_wants_a_sign_in(self):
        results = self.run_it(**{"captive.apple": PAGE, "/v2/point/": PAGE, "/v1/health": PAGE, "/v0/callsign/": PAGE})
        self.assertEqual(self.marks(results, "Internet"), ["✘"])
        self.assertIn("sign in", next(what for _, what, _ in results if "Internet" in what))
        self.assertEqual(self.marks(results, "Plane data"), ["✘"])
        self.assertEqual(self.marks(results, "Phone notifications"), ["✘"])

    def test_a_shared_wifi_thats_over_the_limit(self):
        results = self.run_it(**{"/v2/point/": {"status": 429, "text": "Too Many Requests", "type": "text/plain"}})
        line = next(r for r in results if "Plane data" in r[1])
        self.assertEqual(line[0], "✘")
        self.assertIn("asked too often", line[1])
        self.assertIn("shared Wi-Fi", line[2])
        self.assertEqual(self.marks(results, "Internet"), ["✔"])

    def test_offline_blocked_and_inspected_networks(self):
        offline = self.run_it(**{k: OFFLINE for k in ("captive.apple", "/v2/point/", "/v1/health", "/v0/callsign/",
                                                      "githack", "unpkg", "openfreemap", "github.com")})
        self.assertIn("isn't online", next(what for _, what, _ in offline if "Internet" in what))
        blocked = self.run_it(**{"githack": OFFLINE, "unpkg": {"status": 403, "text": "", "type": ""}})
        self.assertEqual((self.marks(blocked, "Point page"), self.marks(blocked, "drawing parts")), (["⚠"], ["⚠"]))
        self.assertEqual(self.marks(blocked, "Plane data"), ["✔"])  # the alerts themselves still work there
        cert = self.run_it(**{"/v2/point/": {"error": "CERTIFICATE_VERIFY_FAILED", "kind": "cert"}})
        self.assertIn("Install Certificates", next(d for _, what, d in cert if "Plane data" in what))

    def test_the_phone_reaching_the_mac(self):
        vpn = self.run_it({**HOME_WIFI, "addresses": ["100.101.2.3", "192.168.1.23"]})
        self.assertEqual(self.marks(vpn, "VPN"), ["⚠"])
        deaf = self.run_it(**{"192.168.": OFFLINE})  # answers on this computer, not on its network address
        self.assertEqual(self.marks(deaf, "Phones can't open the full app"), ["✘"])
        firewall = self.run_it({**HOME_WIFI, "firewall": True})
        self.assertEqual(self.marks(firewall, "firewall"), ["⚠"])
        stopped = self.run_it({**HOME_WIFI, "autostart": {"installed": False, "running": False}}, localhost=OFFLINE)
        self.assertEqual((self.marks(stopped, "isn't running"), self.marks(stopped, "start by itself")), (["⚠"], ["⚠"]))

    def test_which_address_a_phone_should_use(self):
        from planespotter.net import kind_of
        kinds = [kind_of(ip) for ip in ("192.168.1.5", "10.0.0.7", "172.20.1.1", "100.101.2.3", "169.254.9.9", "8.8.8.8", "x")]
        self.assertEqual(kinds, ["lan", "lan", "lan", "vpn", "none", "public", "none"])


class SignInPageTests(unittest.TestCase):
    """A Wi-Fi's sign-in page answers every request with a web page: that mustn't count as the real thing."""

    def test_a_notification_the_sign_in_page_swallowed_isnt_sent(self):
        import io as _io
        import urllib.request
        from planespotter.notify import PushNotifier

        class Answer(_io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        answers = [b"<html><body>Sign in to Hotel WiFi</body></html>", b'{"id":"abc","event":"message"}']
        old = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=0: Answer(answers.pop(0))
        self.addCleanup(lambda: setattr(urllib.request, "urlopen", old))
        n = PushNotifier(make_config(ntfy_topic="planespotter-test", quiet_hours=None))
        self.assertFalse(n.send("✈ Test", "one plane", kind="test"))   # the page answered: not sent
        self.assertTrue(n.send("✈ Test", "one plane", kind="test"))    # ntfy answered: sent

    def test_a_gate_lookup_that_never_reached_the_service_isnt_counted(self):
        import urllib.error
        from planespotter.gates import GateBook
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(db_path=Path(tmp) / "g.db", gates_api="aviationstack", gates_key="K")

            def offline(url, headers):
                raise urllib.error.URLError("no route to host")

            def page(url, headers):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")  # a web page, not the service's JSON
            for fetch in (offline, page):
                book = GateBook(cfg, fetch=fetch)
                self.assertEqual(book.lookup("ASA1349")["error"], "other")
                self.assertEqual(book.used(), 0)
                book.db.close()
            book = GateBook(cfg, fetch=lambda u, h: (200, {"data": []}))
            self.assertTrue(book.lookup("ASA1349").get("none"))
            self.assertEqual(book.used(), 1)
            book.db.close()

    def test_the_mac_posts_where_its_map_is(self):
        import planespotter.jobs as jobs
        from helpers import make_watcher, ts
        from test_always_on import FakePost
        from planespotter.jobs import relay_job
        from planespotter.notify import PushNotifier
        from planespotter.relay import Relay
        import json as _json
        old = jobs.lan_address
        jobs.lan_address = lambda: "192.168.1.50"
        self.addCleanup(lambda: setattr(jobs, "lan_address", old))
        watcher, _, _ = make_watcher(ntfy_topic="planespotter-test")
        now = ts(2026, 11, 13, 18)
        post = FakePost()
        relay_job(watcher, Relay(PushNotifier(watcher.cfg, post=post), lambda: now), None)([], now)
        self.assertEqual(_json.loads(post.sent[-1]["message"])["mac"], "http://192.168.1.50:8765")


def archive(files: dict, links: dict = None) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, text in files.items():
            info = tarfile.TarInfo("2-hour-project--branch/" + name)
            data = text.encode()
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
        for name, to in (links or {}).items():
            info = tarfile.TarInfo("2-hour-project--branch/" + name)
            info.type, info.linkname = tarfile.SYMTYPE, to
            tar.addfile(info)
    return out.getvalue()


GOOD = {"planespotter/__main__.py": "print('new')\n", "planespotter/web/scriptable.js": "// new\n", "README.md": "new\n"}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "planespotter-folder"
        (self.home / "planespotter").mkdir(parents=True)
        (self.home / ".env").write_text("HOME_LAT=1\nHOME_LON=2\nGATES_KEY=secret\n")
        (self.home / "planespotter.db").write_text("history")
        (self.home / "planespotter" / "__main__.py").write_text("print('old')\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_replaces_planespotters_files_only(self):
        from planespotter.updater import unpack
        data = archive({**GOOD, ".env": "HOME_LAT=666\n", "../outside.txt": "x", "planespotter/../../up.txt": "x"},
                       links={"planespotter/link.py": "/etc/passwd"})
        self.assertEqual(unpack(data, self.home), (3, 3))
        self.assertEqual((self.home / "planespotter" / "__main__.py").read_text(), "print('new')\n")
        self.assertEqual((self.home / ".env").read_text(), "HOME_LAT=1\nHOME_LON=2\nGATES_KEY=secret\n")  # yours: never replaced
        self.assertEqual((self.home / "planespotter.db").read_text(), "history")
        self.assertFalse((self.home.parent / "outside.txt").exists() or (self.home.parent / "up.txt").exists())
        self.assertFalse((self.home / "planespotter" / "link.py").exists())
        self.assertEqual(unpack(data, self.home), (0, 3))  # again: nothing to change
        self.assertEqual([p.name for p in self.home.rglob("*.updating")], [])

    def test_a_sign_in_page_or_something_else_changes_nothing(self):
        from planespotter.updater import UpdateError, download, unpack

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with self.assertRaises(UpdateError) as caught:
            download(opener=lambda req, timeout=0: Answer(b"<html>Please sign in to Hotel WiFi</html>"))
        self.assertIn("sign in", str(caught.exception))
        with self.assertRaises(UpdateError):
            unpack(archive({"README.md": "some other project\n"}), self.home)
        with self.assertRaises(UpdateError):
            unpack(archive(GOOD)[:200], self.home)  # cut off part-way
        self.assertEqual((self.home / "planespotter" / "__main__.py").read_text(), "print('old')\n")

    def test_the_command(self):
        import contextlib
        import sys
        from unittest import mock
        from planespotter.updater import UpdateError, cmd_update
        ran, out = [], io.StringIO()
        patch = mock.patch.object(sys, "platform", "darwin")  # on a Mac, the phone's script is updated too
        patch.start()
        self.addCleanup(patch.stop)

        def offline():
            raise UpdateError("Couldn't reach GitHub: this computer isn't online")
        with contextlib.redirect_stdout(out):
            self.assertEqual(cmd_update(self.home / ".env", None, self.home, offline, lambda *a, **k: ran.append(a[0])), 1)
            self.assertEqual(cmd_update(self.home / ".env", None, self.home, lambda: archive(GOOD),
                                        lambda *a, **k: ran.append(a[0])), 0)
        self.assertIn("isn't online", out.getvalue())
        self.assertIn("Updated (3 of 3 files changed)", out.getvalue())
        self.assertEqual(ran[-1][-2:], ["phone", "--install"])  # then the phone's script
        (self.home / "accounts.db").write_text("")  # with accounts on, not: it would replace your phone's pass
        ran.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_update(self.home / ".env", None, self.home, lambda: archive({**GOOD, "README.md": "newer\n"}),
                       lambda *a, **k: ran.append(a[0]))
        self.assertFalse(any("phone" in r for r in ran))


class NightlyTests(unittest.TestCase):
    """By itself, at night: only a version that works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "planespotter").mkdir()
        (self.home / ".env").write_text("HOME_LAT=1\nHOME_LON=2\n")
        (self.home / "planespotter" / "__main__.py").write_text("print('old')\n")
        (self.home / "README.md").write_text("old\n")
        self.ran, self.lines = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, fetch, loads=True, starts=True):
        from planespotter.updater import nightly

        class Done:
            def __init__(self, code):
                self.returncode, self.stdout, self.stderr = code, "", "" if code == 0 else "SyntaxError: oops"

        def run(cmd, **kw):
            self.ran.append(cmd)
            return Done(0 if loads or "-c" not in cmd else 1)
        return nightly(self.home / ".env", self.home, fetch, run, check=lambda port: starts, log=self.lines.append)

    def files(self):
        return ((self.home / "planespotter" / "__main__.py").read_text(), (self.home / "README.md").read_text(),
                (self.home / "planespotter" / "web" / "scriptable.js").exists())

    def test_a_working_version_goes_in(self):
        self.assertEqual(self.run_it(lambda: archive(GOOD)), 0)
        self.assertEqual(self.files(), ("print('new')\n", "new\n", True))
        self.assertEqual(sum(1 for r in self.ran if r[-1:] == ["autostart"]), 1)  # restarted once
        self.assertIn("updated", self.lines[-1])
        self.assertEqual(self.run_it(lambda: archive(GOOD)), 0)
        self.assertIn("already up to date", self.lines[-1])

    def test_a_version_that_doesnt_load_never_runs(self):
        self.assertEqual(self.run_it(lambda: archive(GOOD), loads=False), 1)
        self.assertEqual(self.files(), ("print('old')\n", "old\n", False))  # yesterday's, and nothing new left behind
        self.assertFalse(any(r[-1:] == ["autostart"] for r in self.ran))   # never restarted
        self.assertIn("didn't load", self.lines[-1])

    def test_a_version_that_doesnt_start_is_swapped_back(self):
        self.assertEqual(self.run_it(lambda: archive(GOOD), starts=False), 1)
        self.assertEqual(self.files(), ("print('old')\n", "old\n", False))
        self.assertEqual(sum(1 for r in self.ran if r[-1:] == ["autostart"]), 2)  # tried the new, then yesterday's
        self.assertIn("went back", self.lines[-1])

    def test_no_internet_or_a_bad_download_changes_nothing(self):
        from planespotter.updater import UpdateError

        def offline():
            raise UpdateError("Couldn't reach GitHub")
        self.assertEqual(self.run_it(offline), 1)
        self.assertEqual(self.run_it(lambda: archive(GOOD)[: len(archive(GOOD)) // 2]), 1)  # cut off part-way
        self.assertEqual(self.files(), ("print('old')\n", "old\n", False))
        self.assertEqual(self.ran, [])


class WhatPlanesBroadcastTests(unittest.TestCase):
    def test_only_letters_digits_and_dashes_are_kept(self):
        from planespotter.adsb import parse_aircraft
        ac = parse_aircraft({"hex": "A1B2C3", "flight": "UAL1'); alert(1)//", "r": 'N1<b onload="x">', "t": "B738",
                             "lat": 1.0, "lon": 2.0, "alt_baro": 3000, "squawk": "7700<"})
        self.assertEqual((ac.hex, ac.callsign, ac.registration, ac.type_code, ac.squawk), ("a1b2c3", "UAL1ALER", "N1BONLOADX", "B738", "7700"))
        ok = parse_aircraft({"hex": "a1b2c3", "flight": "G-ABCD  ", "r": "G-ABCD", "t": "A20N", "lat": 1.0, "lon": 2.0, "alt_baro": 3000})
        self.assertEqual((ok.callsign, ok.registration), ("G-ABCD", "G-ABCD"))
        radar = parse_aircraft({"hex": "~2ABC01", "lat": 1.0, "lon": 2.0, "alt_baro": 3000})
        self.assertEqual(radar.label, "~2ABC01")  # a radar-only target keeps its "~": it's read out as unidentified

    def test_the_phone_does_the_same_and_point_only_goes_back_somewhere_safe(self):
        repo = Path(__file__).resolve().parent.parent
        script = (repo / "planespotter" / "web" / "scriptable.js").read_text()
        self.assertIn('replace(/[^A-Za-z0-9-]/g, "")', script)
        self.assertIn('"🚪 Gate " + esc(i.gate)', script)
        point = (repo / "planespotter" / "web" / "point.html").read_text()
        self.assertIn("/^(scriptable:\\/\\/\\/|https?:\\/\\/)/i.test(data.back)", point)


if __name__ == "__main__":
    unittest.main()
