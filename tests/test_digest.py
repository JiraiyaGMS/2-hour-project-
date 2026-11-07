import unittest
from datetime import date, time

from planespotter.adsb import FlightInfo
from planespotter.digest import build_forecast, build_recap
from planespotter.emails import alert_email, digest_email
from planespotter.store import Store

from helpers import make_config, plane, ts


def seed(store, hex_code, callsign, when, miles=1.0, alt=3000):
    pid, _ = store.record_sighting(plane(hex_code, callsign=callsign, alt=alt), miles, 45, when, gap=1800)
    store.set_info(pid, FlightInfo(airline="Test Air", origin="LHR", destination="JFK"))
    return pid


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")

    def test_forecast_learns_daily_flights(self):
        for day, minute in [(3, 2), (4, 10), (5, 6)]:
            seed(self.store, f"a{day}", "BAW117", ts(2026, 11, day, 8, minute))
        seed(self.store, "zz", "ONE1", ts(2026, 11, 5, 15))  # only seen once
        forecast = build_forecast(self.store, self.cfg, date(2026, 11, 6))
        self.assertFalse(forecast.learning)
        self.assertEqual([f.callsign for f in forecast.flights], ["BAW117"])
        self.assertEqual(forecast.flights[0].typical_time, "08:06")
        self.assertEqual(forecast.flights[0].days_seen, 3)
        self.assertEqual(forecast.days_of_history, 3)

    def test_forecast_ignores_flights_older_than_a_week(self):
        seed(self.store, "a1", "OLD1", ts(2026, 10, 20, 9))
        seed(self.store, "a2", "OLD1", ts(2026, 10, 21, 9))
        forecast = build_forecast(self.store, self.cfg, date(2026, 11, 6))
        self.assertEqual(forecast.flights, [])
        self.assertEqual(forecast.days_of_history, 7)

    def test_forecast_is_learning_without_history(self):
        self.assertTrue(build_forecast(self.store, self.cfg, date(2026, 11, 6)).learning)

    def test_recap_covers_the_24_hours_before_the_report(self):
        # The report goes out at 07:00 on the 6th, so it covers 07:00 on the 5th to 07:00 on the 6th.
        seed(self.store, "a0", "TOOEARLY", ts(2026, 11, 5, 6, 59))
        seed(self.store, "a1", "LOW1", ts(2026, 11, 5, 9), miles=2.5, alt=900)
        seed(self.store, "a2", "NEAR1", ts(2026, 11, 5, 17, 5), miles=0.3, alt=4000)
        seed(self.store, "a3", "BUSY1", ts(2026, 11, 5, 17, 40), miles=1.5, alt=5000)
        seed(self.store, "a4", "NIGHT1", ts(2026, 11, 6, 1))
        seed(self.store, "a5", "TOOLATE", ts(2026, 11, 6, 7, 0))
        recap = build_recap(self.store, self.cfg, date(2026, 11, 6))
        self.assertEqual([p["callsign"] for p in recap.passes], ["LOW1", "NEAR1", "BUSY1", "NIGHT1"])
        self.assertEqual(recap.busiest_hour, (17, 2))
        self.assertEqual(recap.lowest["callsign"], "LOW1")
        self.assertEqual(recap.closest["callsign"], "NEAR1")
        self.assertEqual(recap.top_airlines, [("Test Air", 4)])
        self.assertEqual(recap.quiet_count, 0)  # quiet hours are off in this config

    def test_quiet_hours_planes_are_marked_in_the_report(self):
        cfg = make_config(quiet_hours=(time(22), time(7)))
        seed(self.store, "a1", "DAY1", ts(2026, 11, 5, 12))
        seed(self.store, "a2", "NIGHT1", ts(2026, 11, 5, 23, 30))
        seed(self.store, "a3", "NIGHT2", ts(2026, 11, 6, 2))
        recap = build_recap(self.store, cfg, date(2026, 11, 6))
        self.assertEqual(recap.quiet_count, 2)
        email = digest_email(recap, build_forecast(self.store, cfg, date(2026, 11, 6)), cfg)
        self.assertIn("2 planes came over during quiet hours (22:00–07:00)", email.text)
        self.assertIn("23:30 🌙", email.text)
        self.assertIn("Fri 6 Nov", email.html)  # divider row at midnight

    def test_emails_render(self):
        for day in (3, 4, 5):
            seed(self.store, f"a{day}", "BAW117", ts(2026, 11, day, 8))
        recap = build_recap(self.store, self.cfg, date(2026, 11, 6))
        forecast = build_forecast(self.store, self.cfg, date(2026, 11, 6))
        email = digest_email(recap, forecast, self.cfg)
        self.assertIn("1 plane in the last day, 1 expected today", email.subject)
        self.assertIn("BAW117", email.html)
        self.assertIn("~08:00", email.text)

        alert = alert_email(self.store.get_passes([1]), self.cfg)
        self.assertIn("BAW117 over Test House", alert.subject)
        self.assertIn("globe.adsb.lol/?icao=a3", alert.html)

    def test_html_is_escaped(self):
        pid = seed(self.store, "a1", "<b>X</b>", ts(2026, 11, 5, 8))
        alert = alert_email(self.store.get_passes([pid]), self.cfg)
        self.assertNotIn("<b>X</b>", alert.html)


if __name__ == "__main__":
    unittest.main()
