import json
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.adsb import PositionLookup
from planespotter.fullpath import DONE, WAITING
from planespotter.heat import ROUTE_DLAT, cell_of, heat_json
from planespotter.logos import PNG, LogoCache, airline_code
from planespotter.store import Store
from planespotter.webmap import start_map_server

from helpers import FakeFeed, make_config, plane

FAKE_PNG = PNG + b"fake image data"


class LogoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requests = []

    def cache(self, available):
        def fetch(url):
            self.requests.append(url)
            return available.get(url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1])
        return LogoCache(Path(self.tmp.name), fetch=fetch)

    def test_downloads_once_then_serves_from_disk(self):
        cache = self.cache({"flightaware_logos/DAL.png": FAKE_PNG})
        self.assertEqual(cache.get("DAL"), FAKE_PNG)
        self.assertEqual(cache.get("DAL"), FAKE_PNG)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue((Path(self.tmp.name) / "DAL.png").is_file())

    def test_falls_back_to_the_second_source(self):
        cache = self.cache({"radarbox_logos/SKW.png": FAKE_PNG})
        self.assertEqual(cache.get("SKW"), FAKE_PNG)
        self.assertEqual(len(self.requests), 2)

    def test_remembers_airlines_without_a_logo(self):
        cache = self.cache({})
        self.assertIsNone(cache.get("ZZZ"))
        self.assertIsNone(cache.get("ZZZ"))
        self.assertEqual(len(self.requests), 2)  # both sources, once

    def test_rejects_anything_but_a_three_letter_code(self):
        cache = self.cache({})
        for bad in ("../x", "DL", "dal", "DAL1", ""):
            self.assertIsNone(cache.get(bad))
        self.assertEqual(self.requests, [])

    def test_not_a_png_is_not_saved(self):
        cache = self.cache({"flightaware_logos/DAL.png": b"<html>oops</html>"})
        self.assertIsNone(cache.get("DAL"))
        self.assertFalse((Path(self.tmp.name) / "DAL.png").exists())

    def test_airline_code(self):
        self.assertEqual(airline_code("DAL1140"), "DAL")
        self.assertEqual(airline_code("SKW6346"), "SKW")
        self.assertIsNone(airline_code("N802SC"))
        self.assertIsNone(airline_code("SQUID27"))
        self.assertIsNone(airline_code(""))


class FakePositions(PositionLookup):
    def __init__(self, planes):
        super().__init__(clock=lambda: self.now)
        self.now = 1000.0
        self.planes = planes
        self.calls = []
        self.error = None
        self.ground_hexes = set()

    def fetch(self, hexes):
        self.calls.append(list(hexes))
        if self.error:
            raise self.error
        return [p for p in self.planes if p.hex in hexes], {h for h in self.ground_hexes if h in hexes}


class PositionTests(unittest.TestCase):
    def test_one_request_for_many_planes_then_cached(self):
        lookup = FakePositions([plane("aaa111", lat=40.0), plane("bbb222", lat=41.0)])
        found = lookup.get(["aaa111", "bbb222", "ccc333"])
        self.assertEqual(sorted(found), ["aaa111", "bbb222"])  # ccc333 has landed
        self.assertEqual(lookup.calls, [["aaa111", "bbb222", "ccc333"]])
        lookup.now += 10
        lookup.get(["aaa111", "bbb222", "ccc333"])
        self.assertEqual(len(lookup.calls), 1)
        lookup.now += 60  # answers are reused for a minute
        lookup.get(["aaa111"])
        self.assertEqual(lookup.calls[-1], ["aaa111"])

    def test_knows_which_planes_have_landed(self):
        lookup = FakePositions([plane("aaa111")])
        lookup.ground_hexes = {"bbb222"}
        self.assertEqual(sorted(lookup.get(["aaa111", "bbb222", "ccc333"])), ["aaa111"])
        self.assertEqual(lookup.on_ground(["aaa111", "bbb222", "ccc333"]), {"bbb222"})

    def test_ignores_bad_addresses_and_backs_off(self):
        lookup = FakePositions([])
        self.assertEqual(lookup.get(["~abc12", "zzz", ""]), {})
        self.assertEqual(lookup.calls, [])
        lookup.error = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        lookup.get(["aaa111"])
        lookup.now += 60
        lookup.get(["aaa111"])
        self.assertEqual(len(lookup.calls), 1)  # paused after being told to slow down


class WholeFlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False, adsbdb_api="http://127.0.0.1:9")
        cls.cfg = cfg
        now = time.time()
        store = Store(cfg.db_path)
        # One that landed (Portland -> home), and one still flying (home -> Seattle, last heard 5 min ago).
        landed, _ = store.record_sighting(plane("aaa111", callsign="ASA1"), 1.0, 0, now - 7200, gap=1800)
        store.update_full_path(landed, state=DONE, path=[[45.59, -122.6, 0, now - 9000], [48.0, -110.0, 35000, now - 8400],
                                                        [51.5, -0.1, 0, now - 7200]])
        flying, _ = store.record_sighting(plane("bbb222", callsign="DAL2"), 1.0, 0, now - 1800, gap=1800)
        store.update_full_path(flying, state=WAITING, path=[[51.5, -0.1, 0, now - 1900], [53.0, -2.0, 30000, now - 300]])
        store.close()
        cls.ids = {"landed": landed, "flying": flying}
        cls.positions = FakePositions([plane("bbb222", lat=54.0, lon=-3.0, alt=33000)])
        cls.positions.clock = time.time
        tmp_logos = Path(cls.tmp.name) / "logos"
        logos = LogoCache(tmp_logos, fetch=lambda url: FAKE_PNG if "/ASA.png" in url else None)
        cls.server, urls = start_map_server(cfg, feed=FakeFeed(), port=0, position_lookup=cls.positions,
                                            logo_cache=logos)
        cls.base = urls[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()

    def test_whole_flights_with_live_positions(self):
        flights = {f["id"]: f for f in json.loads(self.get("/api/whole?days=1")[2])["flights"]}
        landed, flying = flights[self.ids["landed"]], flights[self.ids["flying"]]
        self.assertFalse(landed["flying"])
        self.assertEqual(landed["path"][0][:2], [-122.6, 45.59])  # [lon, lat, alt], from takeoff
        self.assertNotIn("now", landed)
        self.assertTrue(flying["flying"])
        self.assertEqual((flying["now"]["lat"], flying["now"]["alt_ft"]), (54.0, 33000))
        self.assertIn(["bbb222"], self.positions.calls)  # only the plane still in the air was looked up

    def test_replay_gives_timed_paths_for_the_day(self):
        from datetime import datetime, timezone
        now = time.time()
        day = datetime.fromtimestamp(now - 1800, timezone.utc).date().isoformat()  # the tests use UTC
        data = json.loads(self.get(f"/api/replay?day={day}")[2])
        flying = next(f for f in data["flights"] if f["id"] == self.ids["flying"])
        self.assertEqual(flying["callsign"], "DAL2")
        lon, lat, alt, secs = flying["path"][0]
        self.assertEqual((lon, lat, alt), (-0.1, 51.5, 0))
        self.assertAlmostEqual(secs, now - 1900 - data["start"], delta=2)  # seconds after midnight
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/replay?day=not-a-date")

    def test_heat_includes_flights_still_in_the_air(self):
        store = Store(self.cfg.db_path)
        data = heat_json(store, self.cfg, time.time(), days=2)  # today and yesterday, so it passes just after midnight too
        store.close()
        cells = {cell_of(lat, lon, ROUTE_DLAT) for lon, lat, _ in data["route_cells"]}
        self.assertIn(cell_of(53.0, -2.0, ROUTE_DLAT), cells)  # where the flying plane has been

    def test_logo_route(self):
        status, headers, body = self.get("/logo/ASA.png")
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", FAKE_PNG))
        self.assertIn("max-age", headers["Cache-Control"])
        for path in ("/logo/ZZZ.png", "/logo/..%2F.env.png", "/logo/asa.png"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
