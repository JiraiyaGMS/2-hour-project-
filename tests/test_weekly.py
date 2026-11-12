import unittest
from datetime import date

from planespotter.adsb import FlightInfo
from planespotter.weekly import build_week, weekly_email

from helpers import make_config, make_watcher, plane, ts


def seed(store, hex_code, callsign, when, type_code="A320", airline="Test Air", route=("LHR", "JFK"), alt=3000):
    pid, _ = store.record_sighting(plane(hex_code, callsign=callsign, type_code=type_code, alt=alt), 1.0, 0, when,
                                   gap=1800)
    store.set_info(pid, FlightInfo(airline=airline, origin=route[0], destination=route[1]))
    return pid


class WeeklyTests(unittest.TestCase):
    def test_the_week_in_numbers(self):
        watcher, _, _ = make_watcher()
        store = watcher.store
        # Last week: 2 planes. This week (Sun 1 Nov 07:00 -> Sun 8 Nov 07:00): 4.
        seed(store, "o1", "OLD1", ts(2026, 10, 27, 12))
        seed(store, "o2", "OLD2", ts(2026, 10, 28, 12))
        seed(store, "a1", "DAL1", ts(2026, 11, 2, 9), airline="Delta")
        seed(store, "a2", "DAL2", ts(2026, 11, 2, 9, 30), airline="Delta", alt=800)
        seed(store, "a3", "QFA11", ts(2026, 11, 4, 18), type_code="A388", airline="Qantas", route=("SYD", "LAX"))
        pid = seed(store, "a4", "DAL3", ts(2026, 11, 6, 9), airline="Delta")
        store.set_firsts(pid, ["First Delta flight you've spotted"])
        store.log_special("a3", "type", ts(2026, 11, 4, 18))

        week = build_week(store, watcher.cfg, date(2026, 11, 8))
        self.assertEqual(len(week.passes), 4)
        self.assertEqual(week.previous_count, 2)
        self.assertEqual(week.busiest_day, (date(2026, 11, 2), 2))
        self.assertEqual(week.top_airlines[0], ("Delta", 3))
        self.assertEqual(week.lowest["callsign"], "DAL2")
        self.assertEqual(week.rarest[0]["callsign"], "QFA11")
        self.assertEqual(week.rarest[1], 1)

        email = weekly_email(week, watcher.cfg)
        self.assertIn("4 planes (+100%)", email.subject)
        self.assertIn("first one you&#x27;ve ever seen", email.html)
        self.assertIn("✨ First Delta flight", email.html)
        self.assertIn("Rare big planes: <strong>1</strong>", email.html)
        self.assertIn("SYD → LAX", email.html)

    def test_scheduling(self):
        watcher, _, mailer = make_watcher()  # Sunday is the default, at the 07:00 report time
        watcher.store.set_meta("watch_started", str(ts(2026, 10, 1)))
        self.assertIsNone(watcher.weekly_due(ts(2026, 11, 7, 12)))   # a Saturday
        self.assertIsNone(watcher.weekly_due(ts(2026, 11, 8, 6)))    # Sunday, too early
        self.assertEqual(watcher.weekly_due(ts(2026, 11, 8, 7, 5)), date(2026, 11, 8))
        self.assertTrue(watcher.send_weekly(date(2026, 11, 8)))
        self.assertIsNone(watcher.weekly_due(ts(2026, 11, 8, 9)))    # once a week
        self.assertIn("No planes came through", mailer.sent[0].html)

    def test_weekly_day_setting(self):
        from planespotter.config import Config, ConfigError
        base = {"HOME_LAT": "1", "HOME_LON": "1"}
        self.assertEqual(Config.from_mapping({**base, "WEEKLY_DAY": "Friday"}).weekly_day, 4)
        with self.assertRaises(ConfigError):
            Config.from_mapping({**base, "WEEKLY_DAY": "someday"})
        self.assertIsNone(make_watcher(weekly_report=False)[0].weekly_due(ts(2026, 11, 8, 8)))
        self.assertTrue(make_config().weekly_report)


if __name__ == "__main__":
    unittest.main()
