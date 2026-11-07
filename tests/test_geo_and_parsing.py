import unittest
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.adsb import parse_aircraft, route_is_plausible
from planespotter.config import Config, ConfigError, load_dotenv, parse_clock, parse_quiet_hours
from planespotter.geo import bearing_deg, compass, distance_miles, segment_min_distance_miles

from helpers import plane


class GeoTests(unittest.TestCase):
    def test_distance_lax_to_jfk(self):
        self.assertAlmostEqual(distance_miles(33.9425, -118.408, 40.6398, -73.7789), 2475, delta=15)

    def test_bearing_and_compass(self):
        self.assertAlmostEqual(bearing_deg(0, 0, 1, 0), 0, delta=0.01)
        self.assertAlmostEqual(bearing_deg(0, 0, 0, 1), 90, delta=0.01)
        self.assertEqual(compass(0), "N")
        self.assertEqual(compass(359), "N")
        self.assertEqual(compass(135), "SE")
        self.assertEqual(compass(None), "?")

    def test_segment_passing_north_of_home(self):
        # A line from west to east, 0.05 degrees north of home.
        d = segment_min_distance_miles(0, 0, 0.05, -0.1, 0.05, 0.1)
        self.assertAlmostEqual(d, distance_miles(0, 0, 0.05, 0), delta=0.05)

    def test_segment_that_stops_short(self):
        # The plane ends its segment 0.1 degrees west of home, so that's the closest point.
        d = segment_min_distance_miles(0, 0, 0, -0.3, 0, -0.1)
        self.assertAlmostEqual(d, distance_miles(0, 0, 0, -0.1), delta=0.05)


class ParseTests(unittest.TestCase):
    def test_parse_airborne(self):
        ac = parse_aircraft({"hex": "4AC8A8", "flight": "SAS932  ", "r": "SE-REH", "t": "A333",
                             "alt_baro": 4025, "gs": 222.0, "track": 264.5,
                             "lat": 33.92, "lon": -118.57, "seen_pos": 0.7})
        self.assertEqual(ac.hex, "4ac8a8")
        self.assertEqual(ac.callsign, "SAS932")
        self.assertEqual(ac.alt_ft, 4025)
        self.assertEqual(ac.label, "SAS932")

    def test_skips_ground_stale_and_positionless(self):
        self.assertIsNone(parse_aircraft({"hex": "a", "alt_baro": "ground", "lat": 1, "lon": 1}))
        self.assertIsNone(parse_aircraft({"hex": "a", "alt_baro": 100, "lat": 1, "lon": 1, "seen_pos": 120}))
        self.assertIsNone(parse_aircraft({"hex": "a", "alt_baro": 100}))

    def test_falls_back_to_geometric_altitude(self):
        ac = parse_aircraft({"hex": "a", "alt_geom": 5100, "lat": 1, "lon": 1})
        self.assertEqual(ac.alt_ft, 5100)

    def test_route_plausibility(self):
        lax = {"latitude": 33.9425, "longitude": -118.408}
        cph = {"latitude": 55.6179, "longitude": 12.656}
        lga = {"latitude": 40.7769, "longitude": -73.874}
        den = {"latitude": 39.8617, "longitude": -104.673}
        just_left_lax = plane(lat=33.92, lon=-118.57)
        self.assertTrue(route_is_plausible(just_left_lax, lax, cph))
        self.assertFalse(route_is_plausible(just_left_lax, lga, den))
        self.assertFalse(route_is_plausible(just_left_lax, {}, den))


class ConfigTests(unittest.TestCase):
    def test_dotenv_parsing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\nHOME_LAT=51.5\nexport HOME_LON = -0.1  # inline\n"
                'HOME_NAME="My #1 House"\n\nBROKEN LINE\n',
                encoding="utf-8",
            )
            values = load_dotenv(path)
        self.assertEqual(values, {"HOME_LAT": "51.5", "HOME_LON": "-0.1", "HOME_NAME": "My #1 House"})

    def test_requires_location(self):
        with self.assertRaises(ConfigError):
            Config.from_mapping({})

    def test_rejects_bad_values(self):
        base = {"HOME_LAT": "51.5", "HOME_LON": "-0.1"}
        for key, value in [("HOME_LAT", "123"), ("RADIUS_MILES", "0"), ("POLL_SECONDS", "2"),
                           ("DIGEST_TIME", "breakfast"), ("TIMEZONE", "Mars/Olympus")]:
            with self.subTest(key=key), self.assertRaises(ConfigError):
                Config.from_mapping({**base, key: value})

    def test_clock_and_quiet_hours_parsing(self):
        self.assertEqual(parse_clock("07:00"), time(7))
        self.assertEqual(parse_clock("7"), time(7))
        self.assertEqual(parse_clock("7:30"), time(7, 30))
        self.assertEqual(parse_clock("10pm"), time(22))
        self.assertEqual(parse_clock("12am"), time(0))
        self.assertEqual(parse_clock("12pm"), time(12))
        for bad in ("25:00", "7:75", "13pm", "noon", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_clock(bad)

        self.assertEqual(parse_quiet_hours("22:00-07:00"), (time(22), time(7)))
        self.assertEqual(parse_quiet_hours("10pm to 7am"), (time(22), time(7)))
        self.assertEqual(parse_quiet_hours("23 – 6"), (time(23), time(6)))
        self.assertIsNone(parse_quiet_hours(""))
        self.assertIsNone(parse_quiet_hours("none"))
        for bad in ("22:00", "22-22", "late-early"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_quiet_hours(bad)

        cfg = Config.from_mapping({"HOME_LAT": "51.5", "HOME_LON": "-0.1", "QUIET_HOURS": "22:00-07:00"})
        self.assertEqual(cfg.quiet_hours, (time(22), time(7)))
        with self.assertRaises(ConfigError):
            Config.from_mapping({"HOME_LAT": "51.5", "HOME_LON": "-0.1", "QUIET_HOURS": "nights"})

    def test_relative_paths_follow_the_settings_file(self):
        cfg = Config.from_mapping({"HOME_LAT": "51.5", "HOME_LON": "-0.1"}, base_dir=Path("/x/y"))
        self.assertEqual(cfg.db_path, Path("/x/y/planespotter.db"))
        self.assertEqual(cfg.outbox_dir, Path("/x/y/outbox"))

    def test_defaults_and_dry_run(self):
        cfg = Config.from_mapping({"HOME_LAT": "51.5", "HOME_LON": "-0.1"})
        self.assertEqual(cfg.radius_miles, 3.0)
        self.assertIsNone(cfg.max_altitude_ft)
        self.assertTrue(cfg.dry_run)

        cfg = Config.from_mapping({
            "HOME_LAT": "51.5", "HOME_LON": "-0.1", "EMAIL_TO": "me@example.com",
            "SMTP_USER": "me@example.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop",
            "MAX_ALTITUDE_FT": "10000",
        })
        self.assertFalse(cfg.dry_run)
        self.assertEqual(cfg.smtp_password, "abcdefghijklmnop")
        self.assertEqual(cfg.email_from, "me@example.com")
        self.assertEqual(cfg.max_altitude_ft, 10000)


if __name__ == "__main__":
    unittest.main()
