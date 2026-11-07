import unittest
from datetime import date, time

from planespotter.adsb import FlightInfo
from planespotter.store import Store

from helpers import HOME, make_watcher, plane, ts


class StoreTests(unittest.TestCase):
    def test_passes_are_grouped_by_gap(self):
        store = Store(":memory:")
        t0 = ts(2026, 11, 1, 12)
        pid, new = store.record_sighting(plane(), 2.0, 90, t0, gap=1800)
        self.assertTrue(new)
        same, new = store.record_sighting(plane(), 1.0, 80, t0 + 20, gap=1800)
        self.assertEqual((same, new), (pid, False))
        self.assertEqual(store.get_passes([pid])[0]["closest_miles"], 1.0)
        later, new = store.record_sighting(plane(), 1.0, 80, t0 + 20 + 1801, gap=1800)
        self.assertTrue(new)
        self.assertNotEqual(later, pid)

    def test_callsign_filled_in_later(self):
        store = Store(":memory:")
        pid, _ = store.record_sighting(plane(callsign=""), 2.0, 90, 100, gap=1800)
        store.record_sighting(plane(callsign="EZY42"), 2.5, 90, 120, gap=1800)
        self.assertEqual(store.get_passes([pid])[0]["callsign"], "EZY42")

    def test_info_and_cache(self):
        store = Store(":memory:")
        pid, _ = store.record_sighting(plane(), 2.0, 90, 100, gap=1800)
        store.set_info(pid, FlightInfo(airline="Test Air", origin="LHR"))
        self.assertEqual(store.get_passes([pid])[0]["origin"], "LHR")

        store.cache_put("route:X", {"a": 1}, now=0, ttl=10)
        self.assertEqual(store.cache_get("route:X", now=5), {"a": 1})
        self.assertIsNone(store.cache_get("route:X", now=11))


class AlertTests(unittest.TestCase):
    def test_one_email_per_pass(self):
        watcher, feed, mailer = make_watcher()
        feed.planes = [plane(lat=HOME[0] + 0.01)]
        t0 = ts(2026, 11, 1, 12)
        for i in range(3):
            watcher.send_alerts(watcher.poll(t0 + i * 20), t0 + i * 20)
        self.assertEqual(len(mailer.sent), 1)
        self.assertIn("BAW123", mailer.sent[0].subject)
        self.assertIn("London (LHR) → New York (JFK)", mailer.sent[0].html)

    def test_several_new_planes_share_one_email(self):
        watcher, feed, mailer = make_watcher()
        feed.planes = [plane("aaa111", callsign="AAA1"), plane("bbb222", callsign="BBB2")]
        t0 = ts(2026, 11, 1, 12)
        watcher.send_alerts(watcher.poll(t0), t0)
        self.assertEqual(len(mailer.sent), 1)
        self.assertIn("2 planes", mailer.sent[0].subject)

    def test_outside_radius_and_too_high_are_ignored(self):
        watcher, feed, mailer = make_watcher(max_altitude_ft=10000)
        feed.planes = [
            plane("far001", lat=HOME[0] + 0.2),           # ~14 miles away
            plane("high01", alt=35000),                     # right overhead, but cruising
        ]
        t0 = ts(2026, 11, 1, 12)
        self.assertEqual(watcher.poll(t0), [])

    def test_fast_plane_between_polls_is_caught(self):
        watcher, feed, mailer = make_watcher(radius_miles=1.0)
        t0 = ts(2026, 11, 1, 12)
        feed.planes = [plane(lat=HOME[0] + 0.03)]  # ~2 mi north
        self.assertEqual(watcher.poll(t0), [])
        feed.planes = [plane(lat=HOME[0] - 0.03)]  # ~2 mi south: it flew straight over
        self.assertEqual(len(watcher.poll(t0 + 20)), 1)

    def test_hourly_cap(self):
        watcher, feed, mailer = make_watcher(max_alerts_per_hour=1)
        t0 = ts(2026, 11, 1, 12)
        feed.planes = [plane("aaa111", callsign="AAA1")]
        watcher.send_alerts(watcher.poll(t0), t0)
        feed.planes = [plane("bbb222", callsign="BBB2")]
        watcher.send_alerts(watcher.poll(t0 + 60), t0 + 60)
        self.assertEqual(len(mailer.sent), 1)
        # An hour later alerts flow again.
        feed.planes = [plane("ccc333", callsign="CCC3")]
        watcher.send_alerts(watcher.poll(t0 + 3700), t0 + 3700)
        self.assertEqual(len(mailer.sent), 2)

    def test_quiet_hours_overnight(self):
        watcher, feed, mailer = make_watcher(quiet_hours=(time(22), time(7)))
        for hour, hex_code in [(23, "aaa001"), (3, "aaa002"), (6, "aaa003")]:
            t = ts(2026, 11, 2 if hour < 12 else 1, hour, 30)
            feed.planes = [plane(hex_code, callsign=hex_code.upper())]
            self.assertFalse(watcher.send_alerts(watcher.poll(t), t))
        self.assertEqual(mailer.sent, [])
        # The planes were still recorded, so they appear in the morning report.
        night = watcher.store.passes_between(ts(2026, 11, 1, 22), ts(2026, 11, 2, 7))
        self.assertEqual(len(night), 3)
        self.assertTrue(all(p["alerted"] == 0 for p in night))
        # 07:00 sharp: quiet hours are over.
        t = ts(2026, 11, 2, 7, 0)
        feed.planes = [plane("bbb001", callsign="MORNING1")]
        self.assertTrue(watcher.send_alerts(watcher.poll(t), t))
        self.assertEqual(len(mailer.sent), 1)

    def test_quiet_hours_within_one_day(self):
        watcher, feed, mailer = make_watcher(quiet_hours=(time(13), time(15)))
        self.assertTrue(watcher.cfg.in_quiet_hours(ts(2026, 11, 1, 14)))
        self.assertFalse(watcher.cfg.in_quiet_hours(ts(2026, 11, 1, 15)))
        self.assertFalse(watcher.cfg.in_quiet_hours(ts(2026, 11, 1, 2)))

    def test_alerts_can_be_turned_off(self):
        watcher, feed, mailer = make_watcher(alerts_enabled=False)
        feed.planes = [plane()]
        t0 = ts(2026, 11, 1, 12)
        watcher.send_alerts(watcher.poll(t0), t0)
        self.assertEqual(mailer.sent, [])


class DigestScheduleTests(unittest.TestCase):
    def test_digest_once_a_day_after_digest_time(self):
        watcher, feed, mailer = make_watcher()
        watcher.store.set_meta("watch_started", str(ts(2026, 10, 30)))
        self.assertIsNone(watcher.digest_due(ts(2026, 11, 2, 6, 59)))
        self.assertEqual(watcher.digest_due(ts(2026, 11, 2, 7, 0)), date(2026, 11, 2))
        self.assertTrue(watcher.send_digest(date(2026, 11, 2)))
        self.assertEqual(len(mailer.sent), 1)
        self.assertIsNone(watcher.digest_due(ts(2026, 11, 2, 7, 1)))
        self.assertEqual(watcher.digest_due(ts(2026, 11, 3, 7, 0)), date(2026, 11, 3))

    def test_no_digest_on_the_first_morning(self):
        watcher, feed, mailer = make_watcher()
        watcher.store.set_meta("watch_started", str(ts(2026, 11, 2, 6)))
        self.assertIsNone(watcher.digest_due(ts(2026, 11, 2, 7)))
        self.assertEqual(watcher.digest_due(ts(2026, 11, 3, 7)), date(2026, 11, 3))


if __name__ == "__main__":
    unittest.main()
