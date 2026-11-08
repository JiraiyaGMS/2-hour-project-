import json
import sqlite3
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.adsb import SharedFeed
from planespotter.fullpath import FlightLookup, TraceNotFound
from planespotter.store import Store
from planespotter.webmap import pass_json, start_map_server

from helpers import HOME, FakeFeed, make_config, make_watcher, plane, ts


class PathTests(unittest.TestCase):
    def test_old_databases_get_the_path_column(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, hex TEXT NOT NULL, callsign TEXT NOT NULL "
                        "DEFAULT '', first_seen REAL, last_seen REAL, closest_miles REAL, track REAL)")
            con.execute("INSERT INTO passes (hex, first_seen, last_seen, closest_miles, track) "
                        "VALUES ('abc', 1, 1, 2.0, 90)")
            con.commit()
            con.close()
            store = Store(db)
            store.append_path(1, [[51.5, -0.1, 3000, 10]])
            row = store.get_passes([1])[0]
            self.assertEqual(json.loads(row["path"]), [[51.5, -0.1, 3000, 10]])
            self.assertEqual(row["track"], 90)  # the heading column is untouched
            store.close()

    def test_watcher_records_the_path_through_the_zone(self):
        watcher, feed, _ = make_watcher(radius_miles=1.0)
        t0 = ts(2026, 11, 1, 12)
        for i, dlat in enumerate([0.04, 0.0, -0.04, -0.08]):  # north of home, over it, then south
            feed.planes = [plane(lat=HOME[0] + dlat, alt=2000 + i)]
            watcher.poll(t0 + i * 20)
        row = watcher.store.recent_passes(0)[0]
        path = json.loads(row["path"])
        self.assertEqual([p[2] for p in path], [2000, 2001, 2002, 2003])  # before, during, after
        info = pass_json(watcher.cfg, row)
        self.assertAlmostEqual(info["lat"], HOME[0], places=3)  # closest point is right overhead

    def test_records_without_a_path_use_distance_and_direction(self):
        watcher, feed, _ = make_watcher()
        pid, _ = watcher.store.record_sighting(plane(), 2.0, 90, ts(2026, 11, 1, 12), gap=1800)
        info = pass_json(watcher.cfg, watcher.store.get_passes([pid])[0])
        self.assertAlmostEqual(info["lat"], HOME[0], places=2)
        self.assertGreater(info["lon"], HOME[1])  # 2 miles east


class FlakyFeed:
    def __init__(self):
        self.calls = 0
        self.fail_with = None

    def aircraft_near(self, lat, lon, radius_nm):
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        return [plane(callsign=f"CALL{self.calls}")]


class SharedFeedTests(unittest.TestCase):
    def test_reuses_recent_answers_and_backs_off_when_asked(self):
        now = [1000.0]
        upstream = FlakyFeed()
        feed = SharedFeed(upstream, min_radius_nm=10, ttl=8, clock=lambda: now[0])
        self.assertEqual(feed.aircraft_near(0, 0, 5)[0].callsign, "CALL1")
        now[0] += 5
        feed.aircraft_near(0, 0, 5)
        self.assertEqual(upstream.calls, 1)  # still fresh
        now[0] += 5
        upstream.fail_with = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            feed.aircraft_near(0, 0, 5)
        now[0] += 10
        self.assertEqual(feed.aircraft_near(0, 0, 5)[0].callsign, "CALL1")  # last answer, no new request
        self.assertEqual(upstream.calls, 2)
        now[0] += 200  # the 45 s pause is over: it asks again, and is told to wait again
        with self.assertRaises(urllib.error.HTTPError):
            feed.aircraft_near(0, 0, 5)
        self.assertEqual(upstream.calls, 3)
        self.assertEqual(feed.blocked_until - now[0], 90)  # twice as long this time
        now[0] += 10  # paused, and the last good answer is now too old to show
        with self.assertRaises(urllib.error.URLError):
            feed.aircraft_near(0, 0, 5)
        self.assertEqual(upstream.calls, 3)
        now[0] += 85
        upstream.fail_with = None
        self.assertEqual(feed.aircraft_near(0, 0, 5)[0].callsign, "CALL4")
        self.assertEqual(feed.backoff, 0)  # back to normal

    def test_follows_retry_after(self):
        now = [1000.0]
        upstream = FlakyFeed()
        upstream.fail_with = urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "120"}, None)
        feed = SharedFeed(upstream, ttl=8, clock=lambda: now[0])
        with self.assertRaises(urllib.error.HTTPError):
            feed.aircraft_near(0, 0, 5)
        self.assertEqual(feed.blocked_until, 1120)


class MapServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False,
                          adsbdb_api="http://127.0.0.1:9")  # lookups fail fast, offline
        store = Store(cfg.db_path)
        pid, _ = store.record_sighting(plane(), 1.5, 45, ts(2026, 11, 1, 12), gap=1800)
        store.update_full_path(pid, path=[[45.5, -122.6, 30000, 1], [51.5, -0.1, 3000, 2]], state=1)
        cls.pass_id = pid
        store.close()
        feed = FakeFeed()
        feed.planes = [plane("near01", lat=HOME[0] + 0.01, callsign="NEAR1"),
                       plane("far001", lat=HOME[0] + 1.0, callsign="FAR1")]  # ~69 mi: off the map
        # A live plane (hex abc456) that took off a minute ago; the lookup never touches the network.
        now = time.time()
        trace = {"timestamp": now - 60, "trace": [[0, 45.5, -122.6, "ground", 0, 0, 2], [30, 45.6, -122.6, 1000, 150, 0, 0],
                                                  [60, 45.7, -122.6, 2000, 160, 0, 0]]}
        fake = lambda url: trace if "abc456" in url else (_ for _ in ()).throw(TraceNotFound(url))  # noqa: E731
        cls.server, cls.urls = start_map_server(cfg, feed=feed, port=0, flight_lookup=FlightLookup(fetch=fake))
        cls.base = cls.urls[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()

    def test_page_and_assets(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"planespotter", body)
        self.assertEqual(self.get("/app.js")[0], 200)
        self.assertEqual(self.get("/style.css")[0], 200)

    def test_private_files_are_never_served(self):
        for path in ("/.env", "/../.env", "/%2e%2e/.env", "/map.db", "/planespotter/config.py", "/web/app.js"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)

    def test_api(self):
        config = json.loads(self.get("/api/config")[2])
        self.assertEqual(config["home"]["name"], "Test House")
        self.assertEqual(config["radius_miles"], 3.0)

        live = json.loads(self.get("/api/live")[2])
        self.assertEqual([a["callsign"] for a in live["aircraft"]], ["NEAR1"])
        self.assertTrue(live["aircraft"][0]["in_zone"])

        flights = json.loads(self.get("/api/flights?days=0")[2])["flights"]
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0]["callsign"], "BAW123")
        self.assertEqual(json.loads(self.get("/api/flights?days=1")[2])["flights"], [])  # too old

        heat = json.loads(self.get("/api/heat?days=0")[2])
        self.assertEqual(heat["max"], 1)  # built from the one flight already in the history
        self.assertEqual(len(heat["cells"]), 1)

        flight = json.loads(self.get(f"/api/flight?id={self.pass_id}")[2])
        self.assertEqual(flight["callsign"], "BAW123")
        self.assertEqual(flight["whole_state"], "done")
        self.assertEqual(flight["whole_path"][0], [45.5, -122.6, 30000, 1])
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/flight?id=999")

        so_far = json.loads(self.get("/api/trace?hex=abc456")[2])["path"]
        self.assertEqual([p[0] for p in so_far], [45.5, 45.7])  # takeoff to now, simplified
        self.assertEqual(json.loads(self.get("/api/trace?hex=abc999")[2])["path"], [])
        self.assertEqual(json.loads(self.get("/api/trace?hex=../../etc")[2])["path"], [])

        info = json.loads(self.get("/api/info?hex=abc123&callsign=BAW123&lat=51.5&lon=-0.1")[2])
        self.assertEqual(info["origin"], "")  # lookup service unreachable: blank, not an error


if __name__ == "__main__":
    unittest.main()
