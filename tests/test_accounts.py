"""Accounts: you and invited friends, each with your own planespotter behind one sign-in page."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from helpers import make_config, plane

HOME = {"HOME_NAME": "Sam's", "HOME_LAT": "45.5231", "HOME_LON": "-122.6765", "RADIUS_MILES": "2"}


class StorageTests(unittest.TestCase):
    def setUp(self):
        from planespotter.accounts import Accounts
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = [1_800_000_000.0]
        self.a = Accounts(Path(self.tmp.name), clock=lambda: self.clock[0])
        self.owner = self.a.add_user("henry", "correct horse", owner=True)

    def tearDown(self):
        self.a.close()
        self.tmp.cleanup()

    def test_passwords_are_never_stored(self):
        row = self.a.db.execute("SELECT password FROM users").fetchone()[0]
        self.assertTrue(row.startswith(("scrypt$", "pbkdf2$")))
        self.assertNotIn("correct horse", row)
        self.assertEqual(self.a.sign_in("HENRY", "correct horse")["username"], "henry")

    def test_invites_work_once_and_each_friend_gets_their_own(self):
        from planespotter.accounts import AccountError
        code = self.a.new_invite()
        sam = self.a.join(code, "Sam", "battery staple", HOME)
        self.assertFalse(self.a.invite_ok(code))
        with self.assertRaises(AccountError):
            self.a.join(code, "bob", "password1", HOME)
        settings = self.a.settings(sam)
        self.assertEqual((settings["HOME_LAT"], settings["EMAILS"]), ("45.52310", "off"))
        self.assertTrue(settings["NTFY_TOPIC"].startswith("planespotter-"))  # their own private topic
        self.assertEqual(self.a.folder(sam), Path(self.tmp.name) / "accounts" / "sam")
        self.assertEqual(oct((self.a.folder(sam) / ".env").stat().st_mode & 0o777), "0o600")
        self.clock[0] += 8 * 86400
        with self.assertRaises(AccountError):
            self.a.join(self.a.new_invite() + "x", "bob", "password1", HOME)  # a made-up code

    def test_what_people_can_type(self):
        from planespotter.accounts import AccountError, check_settings, check_username
        for bad in ({"HOME_LAT": "x", "HOME_LON": "1"}, {"HOME_LAT": "95", "HOME_LON": "1"},
                    {**HOME, "HOME_NAME": "a\nNTFY_TOPIC=evil"}, {**HOME, "QUIET_HOURS": "10pm"},
                    {**HOME, "RADIUS_MILES": "500"}, {**HOME, "TIMEZONE": "Mars/Olympus"}, {**HOME, "HOME_NAME": "<b>x</b>"}):
            with self.assertRaises(AccountError, msg=bad):
                check_settings(bad)
        self.assertEqual(check_settings({**HOME, "HOME_NAME": "Sam's Place"})["HOME_NAME"], "Sam's Place")
        for bad in ("ab", "admin", "has space", "x" * 21, "../etc"):
            with self.assertRaises(AccountError, msg=bad):
                check_username(bad)

    def test_guessing_is_slowed_down(self):
        from planespotter.accounts import AccountError
        for _ in range(5):
            with self.assertRaises(AccountError):
                self.a.sign_in("henry", "wrong", "1.2.3.4")
        with self.assertRaises(AccountError) as caught:
            self.a.sign_in("henry", "correct horse", "1.2.3.4")
        self.assertIn("Too many tries", str(caught.exception))
        self.clock[0] += 16 * 60
        self.assertEqual(self.a.sign_in("henry", "correct horse")["username"], "henry")
        with self.assertRaises(AccountError) as caught:
            self.a.sign_in("nobody", "x")
        self.assertIn("don't match", str(caught.exception))  # the same answer whether or not the name exists

    def test_sessions_tokens_and_passwords(self):
        session = self.a.new_session(self.owner)
        self.assertEqual(self.a.session_user(session)["username"], "henry")
        token = self.a.phone_token(self.owner)
        self.assertEqual(self.a.token_user(token)["username"], "henry")
        newer = self.a.phone_token(self.owner)
        self.assertIsNone(self.a.token_user(token))  # a new script's token stops the old one
        self.assertEqual(self.a.token_user(newer)["username"], "henry")
        self.a.set_password("henry", "a new password")
        self.assertIsNone(self.a.session_user(session))  # signed out everywhere
        fresh = self.a.new_session(self.owner)
        self.clock[0] += 31 * 86400
        self.assertIsNone(self.a.session_user(fresh))  # sessions run out after 30 days

    def test_removing_a_friend_deletes_their_things_but_never_the_owner(self):
        from planespotter.accounts import AccountError
        sam = self.a.join(self.a.new_invite(), "sam", "battery staple", HOME)
        folder = self.a.folder(sam)
        self.a.remove_user("sam")
        self.assertFalse(folder.exists())
        with self.assertRaises(AccountError):
            self.a.remove_user("henry")

    def test_a_friends_planespotter_settings(self):
        from planespotter.accounts import account_config
        sam = self.a.join(self.a.new_invite(), "sam", "battery staple", HOME)
        owner_cfg = make_config(db_path=Path(self.tmp.name) / "planespotter.db", adsb_api_key="KEY", gates_key="")
        cfg = account_config(owner_cfg, self.a, sam, 8931, "https://planebell.com")
        self.assertEqual((cfg.home_name, cfg.home_lat, cfg.radius_miles), ("Sam's", 45.5231, 2.0))
        self.assertEqual((cfg.map_port, cfg.map_lan, cfg.emails, cfg.adsb_api_key), (8931, False, False, "KEY"))
        self.assertEqual(cfg.db_path, self.a.folder(sam) / "planespotter.db")


class GroupingTests(unittest.TestCase):
    def test_homes_near_each_other_share_one_request(self):
        from planespotter.cluster import ClusterFeed

        class Live:
            asked = []

            def aircraft_near(self, lat, lon, r):
                self.asked.append((lat, lon, r))
                return [plane("aaa111", lat=45.52, lon=-122.68), plane("bbb222", lat=45.60, lon=-122.40),
                        plane("ccc333", lat=33.98, lon=-118.39)]

        class Budget:
            def poll_interval(self):
                return 240.0
        clock = [1000.0]
        homes = [(45.5231, -122.6765, 13), (45.61, -122.42, 13), (33.985, -118.39, 13)]  # Portland, Camas, LA
        live = Live()
        feed = ClusterFeed(live, homes, Budget(), clock=lambda: clock[0])
        self.assertEqual(feed.count, 2)
        portland, camas, la = (feed.view(*h) for h in homes)
        self.assertEqual(sorted(p.hex for p in portland.aircraft_near(*homes[0])), ["aaa111", "bbb222"])
        clock[0] += 100
        self.assertEqual(len(camas.aircraft_near(*homes[1])), 2)
        self.assertEqual(len(live.asked), 1)  # Camas used Portland's answer
        self.assertEqual([p.hex for p in la.aircraft_near(*homes[2])], ["ccc333"])
        self.assertEqual(len(live.asked), 2)


class FrontDoorTests(unittest.TestCase):
    """The sign-in page in front of two planespotters (stand-ins that say whose they are)."""

    @classmethod
    def setUpClass(cls):
        from planespotter.accounts import Accounts
        from planespotter.accounts_web import Front, start_front
        cls.tmp = tempfile.TemporaryDirectory()
        cls.a = Accounts(Path(cls.tmp.name))
        cls.a.add_user("henry", "correct horse", owner=True)
        (Path(cls.tmp.name) / ".env").write_text("HOME_LAT=33.985\nHOME_LON=-118.39\nHOME_NAME=Henry's\n")
        cls.a.join(cls.a.new_invite(), "sam", "battery staple", HOME)
        cls.inner, ports = [], {}
        for name in ("henry", "sam"):
            class Inner(BaseHTTPRequestHandler):
                who = name

                def log_message(self, *a):
                    pass

                def do_GET(self):
                    body = json.dumps({"home": {"name": self.who}, "path": self.path}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), Inner)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.inner.append(srv)
            ports[name] = srv.server_address[1]
        cls.restarts = []
        front = Front(cls.a, Path(cls.tmp.name) / ".env", ports, restart=lambda: cls.restarts.append(1),
                      phone_script=lambda user, token, base: f'{{"token": "{token}", "base": "{base}"}}')
        cls.front = start_front(front, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.front.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for s in [cls.front, *cls.inner]:
            s.shutdown()
            s.server_close()
        cls.a.close()
        cls.tmp.cleanup()

    def req(self, path, data=None, cookie="", headers=None, accept="application/json"):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        h = {"Accept": accept, **({"Cookie": cookie} if cookie else {}), **(headers or {})}
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers=h)
        try:
            resp = urllib.request.build_opener(NoRedirect).open(r, timeout=10)
            return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def sign_in(self, name, password):
        s, h, _ = self.req("/login", {"username": name, "password": password, "next": "/"}, accept="text/html")
        return (h.get("Set-Cookie") or "").split(";")[0] if s == 303 else ""

    def test_nothing_without_signing_in(self):
        self.assertEqual(self.req("/api/live")[0], 401)
        s, h, _ = self.req("/", accept="text/html")
        self.assertEqual((s, h.get("Location")), (303, "/login"))

    def test_everyone_gets_their_own(self):
        henry, sam = self.sign_in("henry", "correct horse"), self.sign_in("sam", "battery staple")
        self.assertEqual(json.loads(self.req("/api/config", cookie=henry)[2])["home"]["name"], "henry")
        self.assertEqual(json.loads(self.req("/api/config?x=1", cookie=sam)[2]), {"home": {"name": "sam"}, "path": "/api/config?x=1"})
        self.assertIn("Only the owner", self.req("/admin", cookie=sam, accept="text/html")[2])

    def test_the_phones_pass(self):
        sam = self.sign_in("sam", "battery staple")
        _, _, body = self.req("/account/phone", {}, cookie=sam, accept="text/html")
        token = body.split("&quot;token&quot;: &quot;")[1].split("&quot;")[0]
        got = self.req("/api/next", headers={"Authorization": "Bearer " + token})
        self.assertEqual(json.loads(got[2])["home"]["name"], "sam")
        self.assertEqual(self.req("/api/next", headers={"Authorization": "Bearer made-up-token-made-up"})[0], 401)
        s, h, _ = self.req("/auth?k=" + token, accept="text/html")
        self.assertTrue(s == 303 and h.get("Set-Cookie"))

    def test_the_installed_app_pieces_load_before_signing_in(self):
        # Android's "Install app" (and iPhone's Add to Home Screen) fetch these without the sign-in.
        s, h, body = self.req("/manifest.webmanifest")
        manifest = json.loads(body)
        self.assertEqual((s, h.get("Content-Type")), (200, "application/manifest+json"))
        self.assertEqual([x["url"] for x in manifest["shortcuts"]], ["/?say=next", "/?say=mine", "/?track=1", "/#point"])
        self.assertTrue(any(i.get("purpose") == "maskable" for i in manifest["icons"]))
        for path, kind in (("/sw.js", "text/javascript"), ("/icon-512.png", "image/png"), ("/apple-touch-icon.png", "image/png")):
            s, h, _ = self.req(path)
            self.assertEqual(s, 200, path)
            self.assertTrue(h.get("Content-Type", "").startswith(kind), path)
        self.assertEqual(self.req("/api/live")[0], 401)  # but nothing of yours

    def test_the_android_app_downloads(self):
        s, h, _ = self.req("/planespotter.apk")
        self.assertEqual((s, h.get("Content-Type")), (200, "application/vnd.android.package-archive"))
        self.assertIn('filename="planespotter.apk"', h.get("Content-Disposition", ""))
        apk = (Path(__file__).resolve().parent.parent / "planespotter" / "web" / "planespotter.apk").read_bytes()
        self.assertEqual(apk[:2], b"PK")  # an Android app is a zip
        self.assertIn(b"AndroidManifest.xml", apk)

    def test_android_phones_see_android_steps_first(self):
        henry = self.sign_in("henry", "correct horse")
        android = self.req("/account", cookie=henry, accept="text/html",
                           headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) Chrome/130"})[2]
        iphone = self.req("/account", cookie=henry, accept="text/html",
                          headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"})[2]
        self.assertLess(android.index("🤖 Android"), android.index("📱 iPhone"))
        self.assertIn('href="/planespotter.apk"', android)
        self.assertLess(iphone.index("📱 iPhone"), iphone.index("🤖 Android"))

    def test_forms_from_other_sites_and_unsafe_next(self):
        henry = self.sign_in("henry", "correct horse")
        self.assertEqual(self.req("/admin/invite", {}, cookie=henry, headers={"Origin": "https://evil.example"})[0], 403)
        from planespotter.accounts_web import safe_next
        self.assertEqual([safe_next(x) for x in ("/collection", "//evil.example", "https://evil.example", "/\\evil")],
                         ["/collection", "/", "/", "/"])

    def test_changing_your_home_restarts(self):
        sam = self.sign_in("sam", "battery staple")
        before = len(self.restarts)
        s, _, body = self.req("/account", {**HOME, "HOME_NAME": "Sam's new place"}, cookie=sam, accept="text/html")
        self.assertEqual((s, len(self.restarts)), (200, before + 1))
        self.assertEqual(self.a.settings(self.a.user("sam"))["HOME_NAME"], "Sam's new place")
        s, _, body = self.req("/account", {**HOME, "HOME_LAT": "999"}, cookie=sam, accept="text/html")
        self.assertEqual(s, 400)


class InternetGuardTests(unittest.TestCase):
    def test_the_map_refuses_the_internet_while_accounts_are_off(self):
        from helpers import FakeFeed
        from planespotter.webmap import start_map_server
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(db_path=Path(tmp) / "map.db", map_lan=False, adsbdb_api="http://127.0.0.1:9")
            server, urls = start_map_server(cfg, feed=FakeFeed(), port=0)
            try:
                via_web = urllib.request.Request(urls[0] + "/api/config", headers={"X-Forwarded-For": "203.0.113.9"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(via_web, timeout=10)
                self.assertEqual(caught.exception.code, 403)
                self.assertEqual(urllib.request.urlopen(urls[0] + "/api/config", timeout=10).status, 200)  # this computer
            finally:
                server.shutdown()
                server.server_close()

    def test_server_web_needs_accounts_and_a_real_domain(self):
        from planespotter.server import ServerError, check_domain, web

        class Remote:
            ran = []

            def ssh(self, command, timeout=0):
                self.ran.append(command)
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        with self.assertRaises(ServerError) as caught:
            web(Remote(), "planebell.com", 8765, say=lambda *a: None)
        self.assertIn("accounts on", str(caught.exception))
        for bad in ("x;rm -rf /", "bad domain", "localhost"):
            with self.assertRaises(ServerError):
                check_domain(bad)


if __name__ == "__main__":
    unittest.main()
