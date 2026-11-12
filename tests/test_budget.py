import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.budget import ApiBudget

from helpers import HOME, make_watcher, plane, ts


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [ts(2026, 11, 1, 12)]
        self.db = Path(self.tmp.name) / "budget.db"

    def budget(self, limit):
        return ApiBudget(self.db, limit, min_poll=20, clock=lambda: self.now[0])

    def use(self, budget, n, kind="poll"):
        for _ in range(n):
            budget.record(kind)

    def test_spreads_checks_over_the_day(self):
        self.assertAlmostEqual(self.budget(500).poll_interval(), 246.9, places=1)   # ~4 min without a key
        self.assertAlmostEqual(self.budget(1000).poll_interval(), 123.4, places=1)  # ~2 min with a free key
        self.assertEqual(self.budget(7200).poll_interval(), 20)                     # feeders: every 20 s

    def test_checks_faster_while_the_map_is_open_until_the_budget_runs_low(self):
        b = self.budget(500)
        b.mark_viewing()
        self.assertEqual(b.poll_interval(), 30)
        self.use(b, 300)  # 60% used
        self.assertAlmostEqual(b.poll_interval(), 246.9, places=1)
        self.now[0] += 120  # map closed
        self.assertFalse(b.viewing())

    def test_nearly_out_slows_right_down(self):
        b = self.budget(500)
        self.use(b, 490)
        self.assertEqual(b.poll_interval(), 900)
        self.assertFalse(b.allow("trace"))
        self.assertFalse(b.allow("click"))

    def test_whole_flights_get_a_quarter(self):
        b = self.budget(500)
        self.use(b, 124, "trace")
        self.assertTrue(b.allow("trace"))
        b.record("trace")
        self.assertFalse(b.allow("trace"))
        self.assertTrue(b.allow("click"))  # what you click on the map still works
        self.assertTrue(b.allow("positions"))

    def test_counts_the_last_24_hours_and_survive_restarts(self):
        self.use(self.budget(500), 100)
        self.assertEqual(self.budget(500).used(), 100)  # a second process / a restart sees the same count
        self.now[0] += 23 * 3600
        self.assertEqual(self.budget(500).used(), 100)
        self.now[0] += 2 * 3600
        self.assertEqual(self.budget(500).used(), 0)


class WatcherSearchAreaTests(unittest.TestCase):
    def test_search_area_grows_when_checks_are_minutes_apart(self):
        watcher, feed, mailer = make_watcher(radius_miles=3.0)
        watcher.interval = 20
        near = watcher.query_radius_nm()
        watcher.interval = 240
        self.assertGreater(watcher.query_radius_nm(), near + 25)

    def test_fast_plane_seen_four_minutes_apart_still_caught(self):
        watcher, feed, mailer = make_watcher(radius_miles=3.0)
        watcher.interval = 240
        t0 = ts(2026, 11, 1, 12)
        feed.planes = [plane(lat=HOME[0] + 0.25)]  # ~17 miles north
        self.assertEqual(watcher.poll(t0), [])
        feed.planes = [plane(lat=HOME[0] - 0.25)]  # 4 minutes later: ~17 miles south, it flew right over
        self.assertEqual(len(watcher.poll(t0 + 240)), 1)


if __name__ == "__main__":
    unittest.main()
