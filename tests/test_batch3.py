import json
import math
import time
import unittest
import urllib.request
from datetime import date, time as clock
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.config import Config, ConfigError, parse_places
from planespotter.flow import dominant, flow_report, observe
from planespotter.follow import check as check_follows
from planespotter.follow import follow, following
from planespotter.games import (bingo, choose_squares, family, old_friends, period_bounds, profile, quiz_answer,
                                quiz_question, quiz_score, wrapped)
from planespotter.predict import crossings, describe, glide
from planespotter.schedule import delays_for, punctuality, today_board
from planespotter.space import Satellite, look_angles, passes, sunlit
from planespotter.store import Store
from planespotter.webmap import start_map_server

from helpers import HOME, FakeFeed, make_config, make_watcher, plane, ts

ISS = ("1 25544U 98067A   26316.17460339  .00008107  00000+0  15334-3 0  9995",
       "2 25544  51.6324 287.7668 0007312  16.4277 343.6947 15.49327857594697")


def add(store, hex_code, callsign, at, type_code="A320", alt=3000, miles=1.0, origin="", destination=""):
    pid, _ = store.record_sighting(plane(hex_code, callsign=callsign, alt=alt, type_code=type_code), miles, 0, at, gap=60)
    if origin or destination:
        store.db.execute("UPDATE passes SET origin = ?, destination = ? WHERE id = ?", (origin, destination, pid))
        store.db.commit()
    return pid


class PredictTests(unittest.TestCase):
    def test_crossings_and_sentence(self):
        cfg = make_config(radius_miles=2)
        coming = plane("aaa001", lat=HOME[0] - 0.1, alt=4000, track=0.0, speed_kt=240.0, callsign="DAL573")
        away = plane("aaa002", lat=HOME[0] - 0.1, track=180.0)
        over = plane("aaa003", alt=2000)
        got = crossings(cfg, [coming, away, over])
        self.assertEqual([c.ac.hex for c in got], ["aaa003", "aaa001"])
        self.assertAlmostEqual(got[1].eta, 6.9 * 3600 / (240 * 1.15078), delta=8)  # ~6.9 mi at 240 kt
        self.assertEqual(round(got[1].az), 180)

        class Info:
            airline, origin_city = "Delta Air Lines", "Atlanta"
        text = describe(got[1], Info(), "🌙 After dark: look for its flashing lights")
        self.assertTrue(text.startswith("Delta flight 573 from Atlanta is coming over in about 2 minutes."), text)
        self.assertIn("Look south,", text)
        self.assertIn("After dark, look for its flashing lights.", text)
        moved = glide(coming, 60)
        self.assertGreater(moved.lat, coming.lat)  # flew north for a minute
        self.assertEqual(moved.callsign, "DAL573")


class FlowTests(unittest.TestCase):
    def test_learns_the_flow_and_what_it_means_for_you(self):
        cfg = make_config(home_lat=33.985, home_lon=-118.39)  # near LAX
        store = Store(":memory:")
        base = ts(2026, 11, 1, 0)
        for day in range(6):
            for hour in range(8, 20):
                t = base + day * 86400 + hour * 3600
                west = hour < 16  # west flow most of the day, east flow late afternoon
                heading = 250 if west else 70
                lax = plane("bbb001", lat=33.94, lon=-118.36 if west else -118.45, alt=900, track=heading,
                            speed_kt=150.0)
                observe(store, _lax(), [lax, lax], t + 600)
                for i in range(6 if west else 1):  # lots over you in west flow
                    add(store, f"c{day}{hour:02d}{i}", f"UAL{i}", t + 900 + i * 60, origin="SFO", destination="LAX")
        now = base + 6 * 86400 + 10 * 3600
        observe(store, _lax(), [plane("bbb002", lat=33.94, lon=-118.36, alt=800, track=251.0, speed_kt=140.0)], now - 60)
        report = flow_report(store, cfg, now, {"wind_dir": 250, "wind_kt": 12})
        self.assertEqual(report["airport"]["code"], "LAX")
        self.assertEqual(report["now"]["towards"], "W")
        self.assertEqual(report["now"]["per_hour"], 6.0)
        self.assertEqual(report["now"]["other"]["towards"], "E")
        self.assertIn("LAX is landing towards the west (into a 12 kt west wind)", report["text"])
        self.assertIn("about 6 planes an hour over you (1 an hour when it lands towards the east)", report["text"])
        self.assertAlmostEqual(dominant([350, 10, 5, 180]), 1.7, delta=2)


def _lax():
    from planespotter.airports import airports
    return airports().get("LAX")


class ScheduleTests(unittest.TestCase):
    def test_late_and_early(self):
        cfg = make_config()
        store = Store(":memory:")
        for d in range(1, 6):
            add(store, "aaa001", "BAW1", ts(2026, 11, d, 9, 0))
            add(store, "aaa002", "BAW2", ts(2026, 11, d, 15, 0))
            add(store, "aaa003", "BAW3", ts(2026, 11, d, 6, 0))
        today = ts(2026, 11, 6, 0)
        late = add(store, "aaa001", "BAW1", today + 9 * 3600 + 25 * 60)  # 25 min late
        board = today_board(store, cfg, today + 11 * 3600)
        by = {f["callsign"]: f for f in board["flights"]}
        self.assertEqual((by["BAW1"]["state"], by["BAW1"]["text"]), ("seen", "25 min late"))
        self.assertEqual(by["BAW2"]["state"], "due")
        self.assertEqual(by["BAW3"]["text"], "not seen today")
        self.assertEqual(delays_for(store, cfg, store.get_passes([late]))[late], 25)
        p = punctuality(store, cfg, ts(2026, 11, 2), ts(2026, 11, 7))
        self.assertEqual(p["worst"]["callsign"], "BAW1")


class GamesTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")

    def test_bingo(self):
        monday = ts(2026, 11, 9)
        add(self.store, "aaa001", "BAW1", monday + 3600 * 5, type_code="B789")   # early, a 787, widebody
        add(self.store, "aaa002", "FDX1", monday + 3600 * 23, type_code="B763")  # late, cargo
        keys = choose_squares(self.store, self.cfg, date(2026, 11, 9))
        self.assertEqual(len(keys), 8)
        self.assertEqual(choose_squares(self.store, self.cfg, date(2026, 11, 9)), keys)  # kept for the week
        card = bingo(self.store, self.cfg, monday + 86400)
        self.assertEqual(len(card["squares"]), 9)
        self.assertEqual(card["squares"][4]["key"], "free")
        self.assertTrue(card["squares"][4]["by"])
        hit = {s["key"] for s in card["squares"] if s["by"]}
        self.assertTrue({"early", "late", "wide", "cargo"} & set(keys) <= hit | {"free"})

    def test_quiz(self):
        self.assertEqual(family("B38M"), "Boeing 737")
        self.assertIsNone(family("ZZZZ"))
        q = quiz_question(self.store, "abc123", "A21N", ts(2026, 11, 9))
        self.assertIn("Airbus A320 family", q["options"])
        self.assertEqual(len(set(q["options"])), 4)
        r = quiz_answer(self.store, q["id"], "Airbus A320 family")
        self.assertTrue(r["correct"])
        q2 = quiz_question(self.store, "abc124", "B789", ts(2026, 11, 9))
        self.assertFalse(quiz_answer(self.store, q2["id"], "Boeing 737")["correct"])
        self.assertEqual(quiz_answer(self.store, q2["id"], "Boeing 787")["chosen"], "Boeing 737")  # no second go
        self.assertEqual(quiz_score(self.store), {"answered": 2, "right": 1, "run": 0, "best_run": 1, "percent": 50})
        self.assertIsNone(quiz_question(self.store, "abc125", "ZZZZ", 0))

    def test_wrapped_and_profiles(self):
        for i, m in enumerate((1, 3, 3, 11)):
            add(self.store, f"aa{i:04d}", "QFA11", ts(2026, m, 5, 12), type_code="A388", origin="SYD", destination="LHR")
        for i in range(3):
            add(self.store, "bbb001", f"BAW{i}", ts(2026, 11, 2 + i, 8), origin="LHR", destination="JFK")
        w = wrapped(self.store, self.cfg, "2026")
        self.assertEqual((w["planes"], w["chart"][2], w["chart"][10]), (7, 2, 4))
        self.assertEqual(w["farthest"]["code"], "SYD")
        self.assertEqual(w["regular"]["times"], 3)
        self.assertEqual(wrapped(self.store, self.cfg, "2026-11")["planes"], 4)
        self.assertEqual(period_bounds(self.cfg, "2026-12")[2], "December 2026")
        prof = profile(self.store, self.cfg, "bbb001")
        self.assertEqual((prof["times"], prof["routes"][0]), (3, ["LHR → JFK", 3]))
        self.assertEqual(old_friends(self.store)[0]["hex"], "bbb001")
        with self.assertRaises(ValueError):
            profile(self.store, self.cfg, "ffffff")


class FollowTests(unittest.TestCase):
    def test_landing_notification(self):
        store = Store(":memory:")
        follow(store, "abc123", "DAL573", ts(2026, 11, 9, 10))

        class Positions:
            budget = None
            answer = ([plane("abc123", lat=33.66, lon=-84.43, alt=1500)], set())  # near ATL, coming in

            def fetch(self, hexes):
                return self.answer

        class Notifier:
            enabled, sent = True, []

            def send(self, title, message, **kw):
                self.sent.append(title)
                return True

            def link(self, **kw):
                return ""

        positions, notifier = Positions(), Notifier()
        self.assertEqual(check_follows(store, positions, notifier, ts(2026, 11, 9, 11)), [])
        positions.answer = ([], {"abc123"})
        sent = check_follows(store, positions, notifier, ts(2026, 11, 9, 11, 5))
        self.assertEqual(sent, ["🛬 DAL573 has landed in Atlanta (ATL)"])
        self.assertEqual(following(store), [])


class SpaceTests(unittest.TestCase):
    def test_orbit_matches_the_reference_test_case(self):
        # The standard SGP4 test satellite (Vallado, "Revisiting Spacetrack Report #3"), at its epoch.
        sat = Satellite("1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
                        "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667")
        for got, want in zip(sat.position(sat.epoch), (7022.46529266, -1400.08296755, 0.03995155)):
            self.assertAlmostEqual(got, want, places=4)
        x, y, z = sat.position(sat.epoch + 360 * 60)
        self.assertAlmostEqual(x, -7154.03120202, places=3)

    def test_passes(self):
        sat = Satellite(*ISS, name="ISS")
        found = passes(sat, 33.985, -118.39, ts(2026, 11, 13, 0), hours=48)
        self.assertGreater(len(found), 4)
        for p in found:
            self.assertGreaterEqual(p["max_el"], 10)
            self.assertLess(p["minutes"], 12)  # the ISS crosses the sky in a few minutes
        visible = [p for p in found if p["visible"]]
        self.assertTrue(visible)
        self.assertTrue(all(p["visible_from"] for p in visible))
        az, el, rng = look_angles(sat.position(found[0]["max_t"]), found[0]["max_t"], 33.985, -118.39)
        self.assertAlmostEqual(el, found[0]["max_el"], delta=1)
        self.assertTrue(400 < rng < 2700)
        self.assertTrue(sunlit((0, 0, 7000), ts(2026, 6, 21, 12)) or sunlit((0, 0, -7000), ts(2026, 6, 21, 12)))


class PlacesTests(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_places("Work @ 47.61,-122.33 r2; Mum's house @ 51.5, -0.12"),
                         [("Work", 47.61, -122.33, 2.0), ("Mum's house", 51.5, -0.12, None)])
        for bad in ("Work 47,1", "X @ 95,0", "X @ 1,2 r500"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_places(bad)
        cfg = Config.from_mapping({"HOME_LAT": "51.5", "HOME_LON": "-0.1", "PLACES": "Work @ 47.61,-122.33 r2",
                                   "DB_PATH": "/tmp/x/planespotter.db"})
        work = cfg.place_configs()[0]
        self.assertEqual((work.home_name, work.radius_miles, work.db_path.name, work.map_port, work.space_alerts),
                         ("Work", 2.0, "planespotter-work.db", 8767, False))


class RushTests(unittest.TestCase):
    def test_rush_notification(self):
        watcher, feed, _ = make_watcher(radius_miles=2, rush_count=3)

        class Notifier:
            enabled, sent = True, []

            def send(self, title, message, **kw):
                self.sent.append(title)
                return True

            def link(self, **kw):
                return ""

            def plane_over_home(self, passes, cfg):
                return True

        watcher.notifier = Notifier()
        self.assertGreaterEqual(watcher.query_radius_nm(), 60)
        now = ts(2026, 11, 9, 12)
        feed.planes = [plane(f"aaa00{i}", lat=HOME[0] - 0.1 * (i + 1), track=0.0, speed_kt=300.0) for i in range(4)]
        watcher.poll(now)
        self.assertEqual(watcher.notifier.sent, ["🛫 Rush: 4 planes coming over in the next 15 minutes"])
        watcher.poll(now + 120)
        self.assertEqual(len(watcher.notifier.sent), 1)  # at most one an hour


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False, weather=False, space_alerts=False,
                          adsbdb_api="http://127.0.0.1:9")
        store = Store(cfg.db_path)
        add(store, "abc123", "BAW123", time.time() - 3600, type_code="A320")
        store.close()
        feed = FakeFeed()
        feed.planes = [plane("def456", lat=HOME[0] - 0.05, track=0.0, speed_kt=200.0, callsign="EZY12", type_code="A20N")]
        feed.latest = lambda: (feed.planes, time.time())  # just checked
        cls.server, urls = start_map_server(cfg, feed=feed, port=0)
        cls.base = urls[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()

    def post(self, path, data):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def test_everything_answers(self):
        for path in ("/api/bingo", "/api/wrapped?period=2026", "/api/plane?hex=abc123", "/api/today", "/api/flow",
                     "/api/space", "/api/next", "/api/collection"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 200)
        nxt = json.loads(self.get("/api/next")[2])
        self.assertEqual(nxt["next"][0]["label"], "EZY12")
        self.assertIn("hour_count", nxt)  # the widget shows these when no plane is due
        self.assertIsInstance(nxt["regulars"], list)
        self.assertTrue(0 < nxt["next"][0]["eta_s"] < 60)  # 3.5 mi south of a 3 mi zone, flying north
        text = self.get("/api/now.txt")[2].decode()
        self.assertIn("EZY12", text)
        status, headers, body = self.get("/scriptable.js")
        self.assertIn(b'const PLANESPOTTER = "http://localhost:', body)  # the address the phone used
        self.assertEqual(self.get("/kiosk")[0], 200)

    def test_quiz_and_follow(self):
        q = json.loads(self.get("/api/quiz?hex=def456")[2])
        self.assertIn("Airbus A320 family", q["options"])
        self.assertTrue(self.post("/api/quiz", {"id": q["id"], "chosen": "Airbus A320 family"})["correct"])
        yours = self.post("/api/follow", {"hex": "def456", "callsign": "EZY12"})
        self.assertEqual(yours["following"][0]["hex"], "def456")
        self.assertEqual(self.post("/api/follow", {"hex": "def456", "undo": True})["following"], [])


if __name__ == "__main__":
    unittest.main()
