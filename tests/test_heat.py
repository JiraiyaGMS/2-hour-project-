import json
import unittest

from planespotter.adsb import FlightInfo
from planespotter.geo import distance_miles, offset
from planespotter.heat import HeatRecorder, backfill, cell_center, cell_of, cells_along, heat_json
from planespotter.store import Store

from helpers import HOME, make_config, make_watcher, plane, ts


def heat_by_cell(store, since=""):
    return {(cx, cy): n for cx, cy, n in store.heat_cells(since)}


class GridTests(unittest.TestCase):
    def test_cells_are_about_280_m_and_round_trip(self):
        cx, cy = cell_of(*HOME)
        lat, lon = cell_center(cx, cy)
        self.assertEqual(cell_of(lat, lon), (cx, cy))
        self.assertLess(distance_miles(HOME[0], HOME[1], lat, lon) * 1609, 220)  # within half a diagonal
        east = cell_center(cx + 1, cy)
        north = cell_center(cx, cy + 1)
        self.assertAlmostEqual(distance_miles(lat, lon, *east) * 1609, 278, delta=15)
        self.assertAlmostEqual(distance_miles(lat, lon, *north) * 1609, 278, delta=15)

    def test_lines_leave_a_continuous_streak(self):
        cells = cells_along(HOME[0], HOME[1] - 0.03, HOME[0], HOME[1] + 0.03)  # ~4 km west to east
        xs = sorted(cx for cx, _ in cells)
        self.assertGreaterEqual(len(xs), 14)
        self.assertEqual(xs, list(range(xs[0], xs[-1] + 1)))  # no holes


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")
        self.heat = HeatRecorder(self.store, self.cfg)
        self.t0 = ts(2026, 11, 1, 12)

    def fly(self, hex_code, lons, start, step=20, lat=HOME[0]):
        """One plane flying east along ``lat``, one poll per longitude."""
        prev = {}
        for i, lon in enumerate(lons):
            now = start + i * step
            ac = plane(hex_code, lat=lat, lon=lon)
            self.heat.record([ac], prev, now)
            prev = {hex_code: (ac.lat, ac.lon, now, ac.alt_ft)}

    def test_each_plane_counts_once_per_cell(self):
        lons = [HOME[1] - 0.02, HOME[1], HOME[1] + 0.02]
        self.fly("aaa001", lons, self.t0)
        once = heat_by_cell(self.store)
        self.assertTrue(once)
        self.assertEqual(set(once.values()), {1})
        # Hovering in the same spot (a helicopter, a slow update) doesn't add more.
        self.fly("aaa001", [HOME[1] + 0.02] * 5, self.t0 + 60)
        self.assertEqual(heat_by_cell(self.store), once)
        # A second plane on the same path doubles it.
        self.fly("bbb002", lons, self.t0 + 300)
        self.assertEqual(set(heat_by_cell(self.store).values()), {2})

    def test_the_same_plane_counts_again_on_a_later_trip(self):
        lons = [HOME[1] - 0.01, HOME[1] + 0.01]
        self.fly("aaa001", lons, self.t0)
        self.fly("aaa001", lons, self.t0 + 3 * 3600)
        self.assertEqual(set(heat_by_cell(self.store).values()), {2})

    def test_planes_beyond_the_map_are_ignored(self):
        self.fly("far001", [HOME[1], HOME[1] + 0.01], self.t0, lat=HOME[0] + 1.0)  # ~69 miles north
        self.assertEqual(heat_by_cell(self.store), {})

    def test_days_are_kept_apart_for_the_time_filter(self):
        self.fly("aaa001", [HOME[1]], ts(2026, 11, 1, 12))
        self.fly("bbb002", [HOME[1]], ts(2026, 11, 3, 12))
        self.assertEqual(sum(heat_by_cell(self.store).values()), 2)
        self.assertEqual(sum(heat_by_cell(self.store, "2026-11-02").values()), 1)

    def test_watcher_feeds_the_heat_map(self):
        watcher, feed, _ = make_watcher()
        feed.planes = [plane(lat=HOME[0] + 0.1)]  # ~7 miles away: outside the zone, still on the map
        watcher.poll(self.t0)
        self.assertEqual(watcher.store.recent_passes(0), [])
        self.assertEqual(sum(heat_by_cell(watcher.store).values()), 1)


class BackfillAndApiTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")

    def add_pass(self, hex_code, when, path=None, route=("SFO", "LAX")):
        pid, _ = self.store.record_sighting(plane(hex_code), 1.0, 90, when, gap=1800)
        self.store.set_info(pid, FlightInfo(origin=route[0], origin_city="San Francisco",
                                            destination=route[1], destination_city="Los Angeles"))
        if path:
            self.store.append_path(pid, path)
        return pid

    def test_backfill_uses_existing_history_once(self):
        t = ts(2026, 11, 1, 12)
        self.add_pass("aaa001", t, path=[[HOME[0], HOME[1] - 0.01, 3000, t], [HOME[0], HOME[1] + 0.01, 3000, t + 20]])
        self.add_pass("bbb002", t)  # older record without a path: one spot, where it came closest
        backfill(self.store, self.cfg)
        cells = heat_by_cell(self.store)
        self.assertGreaterEqual(len(cells), 6)
        self.assertEqual(set(cells.values()), {1})
        self.assertIn(cell_of(*offset(HOME[0], HOME[1], 90, 1.0)), cells)  # 1 mile east, where bbb002 was
        backfill(self.store, self.cfg)  # second run does nothing
        self.assertEqual(heat_by_cell(self.store), cells)

    def test_heat_json(self):
        now = ts(2026, 11, 8, 12)
        for i in range(3):
            self.add_pass(f"a{i}", now - i * 3600)
        self.add_pass("b1", now, route=("LAX", "JFK"))
        self.add_pass("old", now - 20 * 86400, route=("SEA", "LAX"))
        self.store.add_heat("2026-11-08", [cell_of(*HOME)] * 3)
        self.store.add_heat("2026-10-01", [cell_of(*HOME)])
        data = heat_json(self.store, self.cfg, now, days=7)
        self.assertEqual(len(data["cells"]), 1)
        self.assertEqual(data["max"], 3)
        self.assertEqual([(r["origin"], r["destination"], r["flights"]) for r in data["routes"]],
                         [("SFO", "LAX", 3), ("LAX", "JFK", 1)])
        self.assertEqual(data["first_day"], "2026-10-01")
        self.assertEqual(heat_json(self.store, self.cfg, now, days=0)["max"], 4)  # all time
        json.dumps(data)  # it must be sendable to the browser


if __name__ == "__main__":
    unittest.main()
