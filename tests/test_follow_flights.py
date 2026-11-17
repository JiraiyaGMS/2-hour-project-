"""A plane that went over is followed until it lands, and "in the air" is only said on evidence."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.adsb import Grounded
from planespotter.airports import airports
from planespotter.fullpath import DONE, LOST_AFTER, WAITING, FlightFollower, same_flight
from planespotter.store import Store
from planespotter.webmap import MapApp

from helpers import FakeFeed, make_config, plane, ts

PDX, LAS = airports().get("PDX"), airports().get("LAS")


class Seen:
    """Where planes are, as the fast feed would say: {hex: Aircraft} in the air, {hex: Grounded} down."""

    def __init__(self):
        self.flying, self.down, self.asked = {}, {}, []

    def get(self, hexes):
        self.asked.append(list(hexes))
        return {h: ac for h, ac in self.flying.items() if h in hexes}

    def ground_at(self, hexes):
        return {h: g for h, g in self.down.items() if h in hexes}

    def on_ground(self, hexes):
        return set(self.ground_at(hexes))


class FollowerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")
        self.seen = Seen()
        self.now = [ts(2026, 11, 16, 13, 0)]
        self.follower = FlightFollower(self.cfg, self.store, self.seen, clock=lambda: self.now[0])

    def went_over(self, hex_code="a7d6a8", callsign="FFT1106", minutes_ago=5, track=True):
        """A pass, with its track looked up when it came over (from Portland) unless ``track`` is False."""
        t = self.now[0] - minutes_ago * 60
        pid, _ = self.store.record_sighting(plane(hex_code, callsign=callsign), 1.0, 0, t, gap=1800)
        self.store.append_path(pid, [[44.06, -121.31, 33000, t]])
        if track:
            self.store.update_full_path(pid, path=[[PDX.lat, PDX.lon, 0, t - 1500], [44.06, -121.31, 33000, t]], tries=1,
                                        next_try=self.now[0] + 1200)
        return pid

    def path(self, pid):
        return json.loads(self.store.get_pass(pid)["full_path"])

    def test_its_line_is_kept_up_to_date_a_look_a_minute(self):
        pid = self.went_over()
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=43.0, lon=-120.5, alt=34000, callsign="FFT1106")
        self.assertEqual(self.follower.step(), 1)
        self.assertEqual(self.path(pid)[-1], [43.0, -120.5, 34000, self.now[0]])
        self.assertEqual(self.follower.step(), 0)                 # nothing newer: nothing added
        self.now[0] += 60
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=42.2, lon=-120.0, alt=34000, callsign="FFT1106")
        self.follower.step()
        self.assertEqual([p[:2] for p in self.path(pid)[-2:]], [[43.0, -120.5], [42.2, -120.0]])
        self.assertEqual(self.store.get_pass(pid)["full_state"], WAITING)

    def test_seen_on_the_ground_its_flight_is_finished_there(self):
        pid = self.went_over()
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=36.3, lon=-115.3, alt=6000, callsign="FFT1106")
        self.follower.step()
        self.now[0] += 600
        self.seen.flying, self.seen.down = {}, {"a7d6a8": Grounded(36.080, -115.128)}   # at the gate in Las Vegas
        self.assertEqual(self.follower.step(), 1)
        row = self.store.get_pass(pid)
        self.assertEqual(self.path(pid)[-1], [36.08, -115.128, 0, self.now[0]])
        self.assertEqual((row["destination"], row["destination_city"], row["route_checked"]), ("LAS", "Las Vegas", 1))
        self.assertEqual(row["full_state"], DONE)                 # takeoff to landing: nothing left to look up
        self.assertTrue(self.store.heat_cells(table="route_heat"))  # ...and counted among the routes
        self.assertEqual(self.follower.step(), 0)                 # finished: not looked at again
        self.assertEqual(self.store.unfinished_flights(0), [])

    def test_without_its_track_the_line_starts_where_we_saw_it(self):
        pid = self.went_over(track=False)
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=43.0, lon=-120.5, alt=34000, callsign="FFT1106")
        self.follower.step()
        self.assertEqual([p[:2] for p in self.path(pid)], [[44.06, -121.31], [43.0, -120.5]])
        self.now[0] += 300
        self.seen.flying, self.seen.down = {}, {"a7d6a8": Grounded(36.080, -115.128)}
        self.follower.step()
        row = self.store.get_pass(pid)
        self.assertEqual((row["destination"], row["full_state"]), ("LAS", WAITING))  # the part before is still to come
        self.assertLessEqual(row["full_next"], self.now[0])       # ...so its track is asked for now

    def test_only_the_same_flight_and_only_an_aircrafts_newest_pass(self):
        self.assertTrue(same_flight("FFT1106", plane(callsign="FFT1106")))
        self.assertFalse(same_flight("FFT1106", plane(callsign="FFT2069")))
        self.assertTrue(same_flight("", plane(callsign="FFT2069")))   # nothing to tell by
        old = self.went_over(minutes_ago=300)                     # this morning's flight, never finished
        new = self.went_over(callsign="FFT2069", minutes_ago=4)   # the same aircraft again, another flight
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=43.0, lon=-120.5, alt=34000, callsign="FFT2069")
        self.follower.step()
        self.assertEqual(len(self.path(old)), 2)                  # the earlier flight is left alone
        self.assertEqual(len(self.path(new)), 3)
        # It turns up as yet another flight: this one ended where we couldn't see, so nothing is added.
        self.now[0] += 120
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=40.0, lon=-118.0, alt=30000, callsign="FFT999")
        self.assertEqual(self.follower.step(), 0)

    def test_on_the_ground_after_a_long_silence_is_not_where_it_landed(self):
        pid = self.went_over(minutes_ago=90)                      # (it may have flown on somewhere since)
        self.seen.down = {"a7d6a8": Grounded(36.080, -115.128)}
        self.assertEqual(self.follower.step(), 0)
        row = self.store.get_pass(pid)
        self.assertEqual((len(self.path(pid)), row["destination"], row["full_state"]), (2, "", WAITING))  # no guess
        self.assertLessEqual(row["full_next"], self.now[0])       # its recorded track will say


class InTheAirTests(unittest.TestCase):
    """What the map says about a plane that went over."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = make_config(db_path=Path(self.tmp.name) / "map.db", map_lan=False, adsbdb_api="http://127.0.0.1:9",
                          weather=False, space_alerts=False)
        self.store = Store(cfg.db_path)
        self.seen = Seen()
        self.app = MapApp(cfg, feed=FakeFeed(), position_lookup=self.seen)
        self.now = time.time()

    def went_over(self, heard_minutes_ago: float):
        t = self.now - heard_minutes_ago * 60
        pid, _ = self.store.record_sighting(plane("a7d6a8", callsign="FFT1106"), 1.0, 0, t - 600, gap=1800)
        self.store.update_full_path(pid, path=[[PDX.lat, PDX.lon, 0, t - 2000], [42.0, -121.5, 33200, t]], tries=1)
        return pid

    def flight(self, pid):
        return next(f for f in self.app.whole_json(1)["flights"] if f["id"] == pid)

    def test_seen_now_or_heard_from_lately(self):
        pid = self.went_over(heard_minutes_ago=3)
        f = self.flight(pid)
        self.assertEqual((f["flying"], "now" in f), (True, False))   # heard from 3 minutes ago: taken to be flying
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=41.0, lon=-120.9, alt=33000, callsign="FFT1106")
        self.app._whole_cache.clear()
        f = self.flight(pid)
        self.assertEqual((f["flying"], f["now"]["lat"]), (True, 41.0))

    def test_not_heard_from_for_a_while_is_not_in_the_air(self):
        # Its line stopped near Klamath Falls hours ago and nothing can see it now: no claim that it's flying.
        pid = self.went_over(heard_minutes_ago=LOST_AFTER / 60 + 1)
        f = self.flight(pid)
        self.assertEqual((f["flying"], f.get("lost"), "now" in f), (False, True, False))

    def test_on_the_ground_or_flying_as_another_flight(self):
        pid = self.went_over(heard_minutes_ago=3)
        self.seen.down = {"a7d6a8": Grounded(36.080, -115.128)}
        f = self.flight(pid)
        self.assertEqual((f["flying"], f.get("landed")), (False, True))
        self.seen.down = {}
        self.seen.flying["a7d6a8"] = plane("a7d6a8", lat=37.0, lon=-114.0, alt=20000, callsign="FFT2069")
        self.app._whole_cache.clear()
        f = self.flight(pid)
        self.assertEqual((f["flying"], f.get("flown_on"), "now" in f), (False, True, False))


if __name__ == "__main__":
    unittest.main()
