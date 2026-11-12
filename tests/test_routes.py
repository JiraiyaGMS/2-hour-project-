import json
import unittest

from planespotter.adsb import FlightInfo
from planespotter.airports import airports, route_from_path
from planespotter.fullpath import DONE, WAITING, FlightLookup, Point
from planespotter.store import Store

from helpers import make_watcher, plane, ts

LAX = (33.9425, -118.408)
FLL = (26.0726, -80.1527)


class AirportTests(unittest.TestCase):
    def test_finds_airports_by_position(self):
        db = airports()
        self.assertEqual(db.at(*FLL, 0).code, "FLL")
        self.assertEqual(db.at(33.95, -118.40, 300).code, "LAX")      # low, just off the runway
        self.assertIsNone(db.at(33.95, -118.40, 30000))                # cruising overhead: not "at" LAX
        self.assertEqual(db.at(33.92, -118.335, 0).code, "HHR")       # the small field next door
        self.assertIsNone(db.at(30.0, -40.0, 0))                       # middle of the Atlantic

    def test_route_from_a_path(self):
        path = [[FLL[0], FLL[1], 0, 1], [30, -95, 35000, 2], [LAX[0], LAX[1], 0, 3]]
        origin, destination = route_from_path(path, landed=True)
        self.assertEqual((origin.code, destination.code), ("FLL", "LAX"))
        origin, destination = route_from_path(path, landed=False)
        self.assertEqual((origin.code, destination), ("FLL", None))
        # Still in the air but on short final into LAX (descending, 300 ft): arriving at LAX.
        final = [[FLL[0], FLL[1], 0, 1], [33.96, -118.25, 3000, 1000], [33.95, -118.38, 300, 1200]]
        self.assertEqual(route_from_path(final, landed=False)[1].code, "LAX")
        # Climbing out of LAX at 800 ft: not "arriving" anywhere yet.
        climbing = [[LAX[0], LAX[1], 0, 1], [33.94, -118.45, 300, 100], [33.93, -118.48, 800, 200]]
        self.assertIsNone(route_from_path(climbing, landed=False)[1])
        midair = [[30, -95, 35000, 1], [LAX[0], LAX[1], 0, 3]]
        self.assertEqual(route_from_path(midair, landed=True)[0], None)  # coverage started mid-air


class StoreRouteTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.pid, _ = self.store.record_sighting(plane(), 1.0, 0, 100, gap=1800)
        # The route database says San Diego -> Boston...
        self.store.set_info(self.pid, FlightInfo(airline="Delta", origin="SAN", origin_city="San Diego",
                                                 destination="BOS", destination_city="Boston"))

    def test_real_route_replaces_the_database_guess(self):
        db = airports()
        # ...but it really took off from Fort Lauderdale and is still flying.
        self.store.set_route(self.pid, db.at(*FLL, 0), None)
        row = self.store.get_pass(self.pid)
        self.assertEqual((row["origin"], row["origin_city"]), ("FLL", "Fort Lauderdale"))
        self.assertEqual(row["destination"], "")  # the guess was about another flight
        self.assertEqual(row["route_checked"], 1)
        self.store.set_route(self.pid, None, db.at(*LAX, 0))  # later: it landed at LAX
        self.assertEqual(self.store.get_pass(self.pid)["destination"], "LAX")

    def test_a_matching_origin_keeps_the_destination(self):
        self.store.set_route(self.pid, airports().at(32.7336, -117.1897, 0), None)  # San Diego
        self.assertEqual(self.store.get_pass(self.pid)["destination"], "BOS")


class FakeFlights(FlightLookup):
    def __init__(self, points, finished=False):
        super().__init__(fetch=lambda url: {})
        self._points, self._finished = points, finished
        self.calls = 0

    def flight(self, hex_code, t_from, t_to):
        self.calls += 1
        return self._points, self._finished


class WatcherRouteTests(unittest.TestCase):
    def test_alert_shows_where_the_plane_really_came_from(self):
        watcher, feed, mailer = make_watcher()
        t = ts(2026, 11, 1, 12)
        so_far = [Point(t - 4 * 3600, FLL[0], FLL[1], 0, True, True), Point(t - 3600, 30, -95, 35000, False, False),
                  Point(t, 51.5, -0.1, 3000, False, False)]
        watcher.flights = FakeFlights(so_far)
        feed.planes = [plane(callsign="DAL573")]
        new = watcher.poll(t)
        watcher.send_alerts(new, t)
        self.assertIn("Fort Lauderdale (FLL)", mailer.sent[0].html)
        row = watcher.store.get_pass(new[0])
        self.assertEqual(row["full_state"], WAITING)  # still flying: checked again later
        self.assertEqual(len(json.loads(row["full_path"])), 3)
        self.assertEqual(row["full_tries"], 1)

    def test_finished_flight_is_counted_straight_away(self):
        watcher, feed, mailer = make_watcher()
        t = ts(2026, 11, 1, 12)
        landed = [Point(t - 3600, FLL[0], FLL[1], 0, True, True), Point(t, 51.47, -0.45, 0, True, False)]
        watcher.flights = FakeFlights(landed, finished=True)
        feed.planes = [plane()]
        new = watcher.poll(t)
        self.assertEqual(watcher.store.get_pass(new[0])["full_state"], DONE)


if __name__ == "__main__":
    unittest.main()
