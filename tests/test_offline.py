import unittest
from datetime import date

from planespotter.budget import ApiBudget
from planespotter.catchup import CatchUp, candidates, crossings, gap_to_check, offline_summary, raw_points, summary_message
from planespotter.fullpath import FlightLookup
from planespotter.store import Store
from planespotter.weekly import build_week, weekly_email

from helpers import HOME, FakeLookup, make_config, make_watcher, plane, ts

NOW = ts(2026, 11, 12, 8)
OFF_AT = ts(2026, 11, 11, 22)  # planespotter stopped at 10 pm


def trace_over_home(start: float, hex_code="a1b2c3", callsign="DAL573 ", landed=True) -> dict:
    """A flight from the north-west that crosses right over home at 03:00 and lands to the south."""
    pts = []
    base = start
    for i in range(41):  # a point every 30 s, 20 minutes, from 0.5° NW to 0.5° SE of home
        f = i / 40
        lat = HOME[0] + 0.5 - f
        lon = HOME[1] - 0.5 + f
        alt = int(12000 - f * 11000)
        details = {"flight": callsign} if i % 10 == 0 else None
        pts.append([i * 30, lat, lon, alt, 250.0, 135.0, 0, -800, details])
    if landed:
        pts.append([41 * 30, HOME[0] - 0.52, HOME[1] + 0.52, "ground", 20.0, 135.0, 0, 0, None])
    return {"icao": hex_code, "r": "N123DL", "t": "A321", "desc": "AIRBUS A-321", "timestamp": base, "trace": pts}


class GapTests(unittest.TestCase):
    def test_gaps(self):
        store = Store(":memory:")
        self.assertIsNone(gap_to_check(store, NOW))            # never watched: nothing missed
        store.set_meta("last_poll", str(NOW - 300))
        self.assertIsNone(gap_to_check(store, NOW))            # 5 minutes is just a slow check
        store.set_meta("last_poll", str(OFF_AT))
        self.assertEqual(gap_to_check(store, NOW), (OFF_AT, NOW))

    def test_databases_from_before_the_offline_log(self):
        store = Store(":memory:")
        store.record_sighting(plane(), 1.0, 0, OFF_AT - 3600, gap=60)
        self.assertEqual(gap_to_check(store, NOW), (OFF_AT - 3600, NOW))  # from the last plane it saw
        store.db.execute("CREATE TABLE api_usage (minute INTEGER, kind TEXT, n INTEGER)")
        store.db.execute("INSERT INTO api_usage VALUES (?, 'poll', 1)", (int(OFF_AT // 60),))
        self.assertEqual(gap_to_check(store, NOW), (int(OFF_AT // 60) * 60 + 59, NOW))  # its last check

    def test_watcher_remembers_its_last_check(self):
        watcher, feed, _ = make_watcher()
        watcher.poll(NOW)
        self.assertEqual(float(watcher.store.get_meta("last_poll")), NOW)


class CandidateTests(unittest.TestCase):
    def test_picks_planes_that_could_have_come_over(self):
        cfg = make_config()
        raw = [
            # 100 mi south-east now, flying away south-east: its course leads back over home.
            {"hex": "aaa001", "lat": HOME[0] - 1.0, "lon": HOME[1] + 1.4, "gs": 450, "track": 135},
            # Same place, but flying north-east: never near home.
            {"hex": "aaa002", "lat": HOME[0] - 1.0, "lon": HOME[1] + 1.4, "gs": 450, "track": 45},
            # Parked at an airport 10 mi away.
            {"hex": "aaa003", "lat": HOME[0] - 0.14, "lon": HOME[1], "alt_baro": "ground"},
            # Parked far away.
            {"hex": "aaa004", "lat": HOME[0] - 3, "lon": HOME[1], "alt_baro": "ground"},
            # No real address (from a ground station): can't be looked up.
            {"hex": "~abc123", "lat": HOME[0], "lon": HOME[1], "gs": 200, "track": 10},
            # A regular of yours, far away and going nowhere near.
            {"hex": "aaa005", "lat": HOME[0] + 3, "lon": HOME[1] + 3, "gs": 400, "track": 45},
        ]
        got = candidates(cfg, raw, OFF_AT, NOW, regulars={"aaa005"})
        self.assertEqual(set(got), {"aaa001", "aaa003", "aaa005"})
        self.assertEqual(got[-1], "aaa005")  # the regular is the long shot


class CrossingTests(unittest.TestCase):
    def test_finds_the_crossing_in_the_gap(self):
        cfg = make_config()
        points = raw_points(trace_over_home(ts(2026, 11, 12, 2, 50)))
        found = crossings(cfg, points, OFF_AT, NOW)
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertLess(c["closest_miles"], 0.3)
        self.assertAlmostEqual(c["point"]["t"], ts(2026, 11, 12, 3), delta=60)
        self.assertEqual(c["point"]["callsign"], "DAL573")
        self.assertEqual(crossings(cfg, points, NOW - 600, NOW), [])  # outside the gap
        high = make_config(max_altitude_ft=3000)
        self.assertEqual(crossings(high, points, OFF_AT, NOW), [])     # too high for your zone


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")
        self.traces = {"a1b2c3": trace_over_home(ts(2026, 11, 12, 2, 50))}
        self.asked = []

        def fetch(url):
            self.asked.append(url)
            hex_code = url.rsplit("/", 1)[1]
            if hex_code in self.traces:
                return self.traces[hex_code]
            from planespotter.fullpath import TraceNotFound
            raise TraceNotFound(url)

        self.flights = FlightLookup(fetch=fetch, clock=lambda: NOW)
        wide = [{"hex": "a1b2c3", "lat": HOME[0] - 0.3, "lon": HOME[1] + 0.3, "alt_baro": "ground"},
                {"hex": "bbb222", "lat": HOME[0] - 0.1, "lon": HOME[1], "alt_baro": "ground"}]
        self.catchup = CatchUp(self.cfg, self.store, FakeLookup(), flights=self.flights, fetch_wide=lambda: wide,
                               clock=lambda: NOW, pause=0)

    def test_finds_and_saves_planes_that_came_over(self):
        summary = self.catchup.run(OFF_AT, NOW)
        self.assertEqual(summary["looked_up"], 2)
        self.assertEqual(len(summary["found"]), 1)
        p = self.store.get_pass(summary["found"][0])
        self.assertEqual((p["offline"], p["hex"], p["callsign"], p["registration"], p["type_code"]),
                         (1, "a1b2c3", "DAL573", "N123DL", "A321"))
        self.assertEqual(p["airline"], "Test Air")     # details looked up like any other plane
        self.assertEqual(p["full_state"], 1)          # it landed, so its whole flight is complete
        self.assertGreater(len(p["path"]), 10)
        self.assertEqual(self.store.db.execute("SELECT found, looked_up FROM offline_gaps").fetchone()[:], (1, 2))
        self.assertTrue(self.store.heat_cells())       # on the heat map too

        again = self.catchup.run(OFF_AT, NOW)           # the same gap twice: no duplicates
        self.assertEqual(again["found"], [])

        title, message = summary_message(self.store, summary["found"], NOW - OFF_AT)
        self.assertIn("1 plane came over while planespotter was off", title)
        self.assertIn("DAL573", message)
        self.assertIn("off for 10 hours", message)

    def test_skips_planes_it_saw_itself(self):
        self.store.record_sighting(plane("a1b2c3"), 0.5, 0, ts(2026, 11, 12, 3, 5), gap=1800)
        self.assertEqual(self.catchup.run(OFF_AT, NOW)["found"], [])

    def test_too_long_ago_and_budget(self):
        old = self.catchup.run(NOW - 5 * 86400, NOW - 4 * 86400)
        self.assertEqual((old["looked_up"], old["found"]), (0, []))  # adsb.lol has forgotten by now
        budget = ApiBudget(":memory:", 500, clock=lambda: NOW)
        for _ in range(100):
            budget.record("catchup")
        self.flights.budget = budget
        self.assertEqual(self.catchup.run(OFF_AT, NOW)["looked_up"], 0)  # its share is used up

    def test_backs_off_when_adsb_lol_is_busy(self):
        import urllib.error
        calls = []

        def busy(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "1"}, None)

        self.flights.fetch = busy
        summary = self.catchup.run(OFF_AT, NOW)
        self.assertEqual(len(calls), 2)  # two candidates, both refused (it stops after three)
        self.assertEqual(summary["found"], [])

    def test_weekly_email_lists_them(self):
        summary = self.catchup.run(OFF_AT, NOW)
        self.store.db.execute("UPDATE passes SET origin = 'SEA', origin_city = 'Seattle'")
        self.store.db.commit()
        week = build_week(self.store, self.cfg, date(2026, 11, 13))
        off = offline_summary(self.store, week.start, week.end)
        self.assertEqual(len(off["found"]), 1)
        self.assertAlmostEqual(off["off_s"], NOW - OFF_AT)
        email = weekly_email(week, self.cfg)
        self.assertIn("Found while planespotter was off", email.html)
        self.assertIn("found 1 plane that came over", email.text)
        self.assertIn("Where they came from: Seattle (1)", email.text)
        self.assertEqual(len(summary["found"]), 1)

    def test_daily_report_marks_them(self):
        watcher, feed, mailer = make_watcher()
        watcher.store = self.store
        self.catchup.run(OFF_AT, NOW)
        watcher.send_digest(date(2026, 11, 12))
        self.assertIn("📴", mailer.sent[-1].text)
        self.assertIn("1 plane was found afterwards", mailer.sent[-1].text)


if __name__ == "__main__":
    unittest.main()
