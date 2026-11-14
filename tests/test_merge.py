"""One app on the phone and the Mac: the phone's ✈︎ Track a flight, 🚪 Gate and 🔭 Point on the Mac's map."""

import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from helpers import make_config, ts

PDX = (45.5887, -122.5975)
HOME = (33.985, -118.39)
TZ = "America/Los_Angeles"


def raw(hex_, flight, lat, lon, alt, gs=300, track=0, rate=0):
    return {"hex": hex_, "flight": flight.ljust(8), "lat": lat, "lon": lon, "alt_baro": alt, "gs": gs,
            "track": track, "baro_rate": rate, "seen_pos": 1, "t": "B738", "r": "N" + hex_}


class FakeSky:
    def __init__(self, answers, clock=time.time):
        self.answers, self.calls, self.clock, self.stopped = answers, [], clock, False

    def get_at(self, path, ttl=None, kind="search"):
        self.calls.append(path)
        if self.stopped:
            return None, 0.0
        for prefix, value in self.answers.items():
            if path == prefix or (prefix.endswith("*") and path.startswith(prefix[:-1])):
                return value, self.clock()
        return [], self.clock()

    def get(self, path, ttl=None, kind="search"):
        return self.get_at(path, ttl, kind)[0]

    def paused(self, kind):
        return self.stopped


def trip(icao, status, frm, to, out, inn, gate="", dep=None, arr=None):
    return {"flight_date": out[:10], "flight_status": status, "flight": {"icao": icao, "codeshared": None},
            "departure": {"iata": frm, "timezone": TZ, "gate": gate or None, "scheduled": out, **(dep or {})},
            "arrival": {"iata": to, "timezone": TZ, "scheduled": inn, **(arr or {})}}


class ReadingSearchesTests(unittest.TestCase):
    def test_what_a_search_asks_for(self):
        from planespotter.track import read_query
        cases = {"UA 1234": (["UAL1234"], "", None, None), "Alaska 1349": (["ASA1349"], "", None, None),
                 "southwest flight 1672": (["SWA1672"], "", None, None), "N12345": ([], "N12345", None, None),
                 "Alaska": ([], "", "ASA", None), "SWA": ([], "", "SWA", None), "PDX": ([], "", None, "PDX"),
                 "Portland": ([], "", None, "PDX"), "Chicago": ([], "", None, "ORD"), "New York": ([], "", None, "JFK"),
                 "Alaska Portland": ([], "", "ASA", "PDX"), "Delta Los Angeles": ([], "", "DAL", "LAX"),
                 "John Wayne": ([], "", None, "SNA")}
        for q, want in cases.items():
            r = read_query(q, HOME)
            got = (r["callsigns"], r["reg"], r["airline"] and r["airline"][0], r["airport"] and r["airport"].code)
            self.assertEqual(got, want, q)

    def test_horizon_flies_alaskas_2000s(self):
        from planespotter.gates import listed_as
        from planespotter.track import flown_as
        self.assertEqual((flown_as("ASA2034"), flown_as("QXE2034"), flown_as("ASA1349")), (["QXE2034"], ["ASA2034"], []))
        self.assertEqual(listed_as("QXE2034"), "ASA2034")


class SearchTests(unittest.TestCase):
    def test_flight_numbers_horizon_and_not_flying(self):
        from planespotter.track import search
        sky = FakeSky({"/v2/callsign/QXE2034": [raw("a02034", "QXE2034", 46.2, -122.5, 21000)],
                       "/v2/callsign/ASA2034": [], "/v2/callsign/ASA1349": []})
        out = search("AS 2034", sky, HOME)
        self.assertEqual([r["callsign"] for r in out["results"]], ["QXE2034"])
        self.assertEqual(out["results"][0]["why"], "Flight ASA2034 (flown as QXE2034)")
        out = search("Alaska 1349", sky, HOME)
        self.assertEqual((out["results"], out["not_flying"]), ([], ["ASA1349"]))

    def test_an_airport_flights_going_there_first(self):
        from planespotter.track import search
        planes = [raw("a00001", "ASA1", 45.7, -122.7, 3000), raw("a00002", "ASA2", 45.9, -122.9, 9000),
                  raw("a00003", "DAL3", 45.6, -122.6, 2000)]
        sky = FakeSky({"/v2/point/*": planes})
        routes = {"ASA2": {"airline": "Alaska Airlines", "origin": "SEA", "destination": "PDX"}}
        out = search("Alaska PDX", sky, HOME, lambda rs: {r["hex"]: routes.get(r["callsign"], {}) for r in rs})
        self.assertTrue(sky.calls[0].startswith("/v2/point/45.5"))
        self.assertEqual([r["callsign"] for r in out["results"]], ["ASA2", "ASA1"])  # Delta's left out; to PDX first
        self.assertEqual((out["results"][0]["why"], out["results"][0]["route"]), ("Flying to PDX", "SEA → PDX"))

    def test_where_a_tracked_flight_is(self):
        from planespotter.track import where_now
        sky = FakeSky({"/v2/callsign/ASA99": [raw("a00099", "ASA99", PDX[0] + 0.004, PDX[1] + 0.01, "ground", gs=12)],
                       "/v2/callsign/QXE2034": [raw("a02034", "QXE2034", 46.2, -122.5, 21000)]})
        ground = where_now("ASA99", "", sky)["ground"]
        self.assertEqual((ground["airport"]["code"], ground["gs"]), ("PDX", 12))
        self.assertEqual(where_now("ASA2034", "", sky)["flying"].callsign, "QXE2034")
        self.assertEqual(where_now("ASA5", "", sky), {})
        sky.stopped = True  # the day's share is used up (or adsb.lol asked us to wait)
        self.assertEqual(where_now("ASA99", "", sky), {"paused": True})


class AllowanceTests(unittest.TestCase):
    """Tracking and searching on the Mac's map can't use up the allowance the plane alerts need."""

    def setUp(self):
        from planespotter.budget import ApiBudget
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = [ts(2026, 11, 14, 20)]
        self.budget = ApiBudget(Path(self.tmp.name) / "b.db", 500, clock=lambda: self.clock[0])
        self.asked = []

    def tearDown(self):
        self.budget.db.close()
        self.tmp.cleanup()

    def sky(self, answer):
        import planespotter.track as track
        sky = track.Sky("https://adsb.test", budget=self.budget, clock=lambda: self.clock[0])
        self.fetch = lambda url, timeout=0, headers=None: (self.asked.append(url), answer())[1]
        self._old, track.get_json = track.get_json, self.fetch
        self.addCleanup(lambda: setattr(track, "get_json", self._old))
        return sky

    def test_tracking_stops_at_a_fifth_of_the_day(self):
        sky = self.sky(lambda: {"ac": [raw("a00001", "ASA1", 45.7, -122.7, 30000)]})
        for _ in range(400):  # a tab left open all day, asking every minute
            self.clock[0] += 61
            sky.get_at("/v2/hex/a00001", 60, "track")
        self.assertEqual(len(self.asked), 100)                       # 20% of 500, then it stops asking
        self.assertTrue(sky.paused("track"))
        self.assertTrue(self.budget.allow("poll"))                   # the alerts still have theirs

    def test_answers_are_kept_and_failures_cost_nothing(self):
        import urllib.error
        sky = self.sky(lambda: {"ac": []})
        sky.get("/v2/callsign/ASA1", 600, "track")
        self.clock[0] += 300
        sky.get("/v2/callsign/ASA1", 600, "track")                  # 5 minutes later: kept (asked once)
        self.assertEqual((len(self.asked), self.budget.used("track")), (1, 1))

        def offline():
            raise urllib.error.URLError("no route to host")
        sky = self.sky(offline)
        self.assertIsNone(sky.get("/v2/callsign/ASA2", 60, "track"))
        self.assertEqual(self.budget.used("track"), 1)               # never reached adsb.lol: not counted

        def too_many():
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "600"}, None)
        sky = self.sky(too_many)
        sky.get("/v2/callsign/ASA3", 60, "track")
        before = len(self.asked)
        self.clock[0] += 120
        sky.get("/v2/callsign/ASA4", 60, "track")                    # told to wait 10 minutes: doesn't ask
        self.assertEqual(len(self.asked), before)

    def test_a_sign_in_page_isnt_counted_for_the_alerts_either(self):
        import planespotter.adsb as adsb

        def sign_in_page(url, timeout=0, headers=None):
            raise ValueError("Expecting value: line 1 column 1")     # a web page, not JSON
        old, adsb.get_json = adsb.get_json, sign_in_page
        self.addCleanup(lambda: setattr(adsb, "get_json", old))
        feed = adsb.LiveFeed("https://adsb.test", budget=self.budget)
        with self.assertRaises(ValueError):
            feed.aircraft_near(45.0, -122.0, 20)
        self.assertEqual(self.budget.used(), 0)


class GateBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = [ts(2026, 11, 14, 20)]  # noon in Portland
        self.asked = []
        self.body = {"data": [trip("ASA1349", "scheduled", "PDX", "LAX", "2026-11-14T13:00:00+00:00",
                                   "2026-11-14T15:20:00+00:00", "")]}

    def tearDown(self):
        self.tmp.cleanup()

    def book(self, **kw):
        from planespotter.gates import GateBook
        cfg = make_config(db_path=Path(self.tmp.name) / "g.db", gates_api="aviationstack", gates_key="K", **kw)
        return GateBook(cfg, clock=lambda: self.clock[0], fetch=lambda u, h: (self.asked.append(u), (200, self.body))[1])

    def test_the_mac_and_phone_share_the_allowance(self):
        from planespotter.gates import shares
        from planespotter.phonekit import kit
        cfg = make_config(ntfy_topic="planespotter-test", gates_api="aviationstack", gates_key="K")
        self.assertEqual(shares(cfg), (54, 36))  # 90 between them: the phone's 54, the Mac's 36
        self.assertIn('"monthly": 54', kit(cfg, "")["widget"])
        self.assertEqual(shares(make_config(gates_api="aeroapi", gates_key="K")), (540, 360))

    def test_kept_and_asked_again_when_gates_are_given_out(self):
        book = self.book()
        first = book.lookup("ASA1349")
        self.assertEqual((first["dep"]["code"], first["dep"]["gate"], first["source"]), ("PDX", "", "Aviationstack"))
        self.assertEqual(len(self.asked), 1)
        self.clock[0] += 10 * 60
        book.lookup("ASA1349")
        self.assertEqual(len(self.asked), 1)  # 10 minutes later: kept
        self.body["data"][0]["departure"]["gate"] = "C5"
        self.clock[0] += 15 * 60
        self.assertEqual(book.lookup("ASA1349")["dep"]["gate"], "C5")  # no gate, take-off within 3 h: asked again
        self.assertEqual(len(self.asked), 2)
        self.assertEqual(book.lookup("ASA1349", force=True)["dep"]["gate"], "C5")
        self.assertEqual((len(self.asked), book.used()), (3, 3))

    def test_stops_at_the_macs_share(self):
        book = self.book(gates_monthly=5)  # the Mac's share: 2
        for _ in range(2):
            book.lookup("ASA1349", force=True)
        self.assertEqual(book.lookup("ASA1349", force=True), {"error": "limit"})
        self.assertEqual(len(self.asked), 2)

    def test_where_the_plane_is_picks_the_trip_and_spots_a_diversion(self):
        book = self.book()
        self.body = {"data": [trip("ASA99", "active", "SEA", "ANC", "2026-11-14T09:00:00+00:00",
                                   "2026-11-14T11:30:00+00:00", "D4", dep={"actual": "2026-11-14T09:05:00+00:00"})]}
        info = book.lookup("ASA99", {"at": ["PDX", "KPDX"]})
        self.assertIn("diverted to Portland", info["note"])
        self.body = {"data": [trip("ASA2034", "scheduled", "SEA", "GEG", "2026-11-14T16:00:00+00:00",
                                   "2026-11-14T17:10:00+00:00", "A2")]}
        info = book.lookup("ASA2034", {"air": True, "lat": 46.2, "lon": -122.5, "alt": 21000, "vr": 0})
        self.assertTrue(info["other"])  # flying, but the service only lists a later leg


class FullAppTests(unittest.TestCase):
    """The Mac's map answers the phone's features (and gives the page what it needs for 🔭 Point)."""

    @classmethod
    def setUpClass(cls):
        from planespotter.webmap import start_map_server
        from test_batch4 import FakeFeed
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False, adsbdb_api="http://127.0.0.1:9",
                          gates_api="aviationstack", gates_key="K")
        cls.server, urls = start_map_server(cfg, feed=FakeFeed(), port=0)
        cls.base = urls[0]
        app = cls.server.app
        now = time.time()
        local = lambda t: time.strftime("%Y-%m-%dT%H:%M:00+00:00", time.localtime(t))  # noqa: E731
        cls.body = {"data": [{"flight_date": local(now)[:10], "flight_status": "scheduled",
                              "flight": {"icao": "ASA1349"},
                              "departure": {"iata": "PDX", "timezone": time.tzname[0] and "UTC", "gate": "C7",
                                            "scheduled": time.strftime("%Y-%m-%dT%H:%M:00+00:00", time.gmtime(now + 7200)),
                                            "delay": 20},
                              "arrival": {"iata": "LAX", "timezone": "UTC",
                                          "scheduled": time.strftime("%Y-%m-%dT%H:%M:00+00:00", time.gmtime(now + 16000))}}]}
        app.gatebook.fetch = lambda u, h: (200, cls.body)
        app.search_sky = FakeSky({"/v2/callsign/QXE2034": [raw("a02034", "QXE2034", 46.2, -122.5, 21000)],
                                  "/v2/callsign/ASA1349": []})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return json.loads(resp.read())

    def test_config_says_gates_are_on_and_where_point_is(self):
        from planespotter.webmap import POINT_URL
        cfg = self.get("/api/config")
        self.assertEqual((cfg["gates"], cfg["point_url"]), ({"on": True, "name": "Aviationstack"}, POINT_URL))

    def test_search_and_track(self):
        out = self.get("/api/search?q=AS%202034")
        self.assertEqual(out["results"][0]["callsign"], "QXE2034")
        flying = self.get("/api/track?cs=ASA2034")
        self.assertEqual((flying["plane"]["callsign"], flying["plane"]["seen_pos"], flying["paused"]), ("QXE2034", 0, False))
        self.assertGreater(flying["plane"]["lat"], 46.2)  # moved on along its heading (north) to now
        waiting = self.get("/api/track?cs=ASA1349")
        info = waiting["info"]
        self.assertEqual((info["dep"]["gate"], info["dep"]["delay"], waiting["ground"]), ("C7", 20, None))
        self.assertAlmostEqual(info["takeoff_at"], info["dep"]["scheduled"] + 900, delta=1)

    def test_another_website_cant_spend_the_allowance(self):
        import urllib.error
        req = urllib.request.Request(self.base + "/api/gate?cs=ASA1349&force=1", headers={"Sec-Fetch-Site": "cross-site"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(caught.exception.code, 403)
        ok = urllib.request.Request(self.base + "/api/gate?cs=ASA1349", headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(urllib.request.urlopen(ok, timeout=10).status, 200)

    def test_gate(self):
        g = self.get("/api/gate?cs=ASA1349&force=1")
        self.assertEqual((g["dep"]["code"], g["dep"]["gate"], g["source"]), ("PDX", "C7", "Aviationstack"))


if __name__ == "__main__":
    unittest.main()
