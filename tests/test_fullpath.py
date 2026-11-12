import json
import sqlite3
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.budget import ApiBudget
from planespotter.fullpath import (DONE, UNAVAILABLE, WAITING, FlightLookup, FullPathFetcher, TraceNotFound,
                                   parse_trace, simplify, split_legs, trace_url)
from planespotter.heat import heat_json
from planespotter.store import Store

from helpers import make_config, plane

PDX = (45.589, -122.597)
LAX = (33.942, -118.408)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def leg(start_t, a, b, minutes, step_s=60, land=True, take_off=True, alt=30000):
    """Positions for a straight flight from a to b, readsb style: [t, lat, lon, alt, gs, track, flags]."""
    n = int(minutes * 60 / step_s)
    pts = []
    for i in range(n + 1):
        f = i / n
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        altitude = alt
        if take_off and i == 0 or land and i == n:
            altitude = "ground"
        flags = 2 if i == 0 and take_off else 0
        pts.append([start_t + i * step_s, lat, lon, altitude, 400, 160, flags])
    return pts


def trace_file(points, base=None):
    base = base if base is not None else points[0][0]
    return {"icao": "abc123", "timestamp": base, "trace": [[p[0] - base, *p[1:]] for p in points]}


class FakeAdsbLol:
    """Serves trace files from a dict of url -> data; anything else is 'not found'."""

    def __init__(self, files=None):
        self.files = files or {}
        self.requests = []
        self.error = None

    def __call__(self, url):
        self.requests.append(url)
        if self.error:
            raise self.error
        if url not in self.files:
            raise TraceNotFound(url)
        return self.files[url]


class ParsingTests(unittest.TestCase):
    def test_parse_and_split_into_flights(self):
        t = utc(2026, 11, 7, 12)
        first = leg(t, PDX, LAX, 120)
        second = leg(t + 3 * 3600, LAX, PDX, 120)
        points = parse_trace(trace_file(first + second))
        self.assertTrue(points[0].ground and points[0].new_leg)
        legs = split_legs(points)
        self.assertEqual(len(legs), 2)
        self.assertAlmostEqual(legs[0][0].lat, PDX[0], places=3)
        self.assertAlmostEqual(legs[1][-1].lat, PDX[0], places=3)

    def test_long_gaps_also_split_flights(self):
        t = utc(2026, 11, 7, 12)
        a = leg(t, PDX, LAX, 60, land=False)
        b = leg(t + 2 * 3600, LAX, PDX, 60, take_off=False)  # no "new leg" flag, but a 1 hour gap
        self.assertEqual(len(split_legs(parse_trace(trace_file(a + b)))), 2)

    def test_simplify_keeps_the_shape_and_the_ends(self):
        t = utc(2026, 11, 7, 12)
        points = parse_trace(trace_file(leg(t, PDX, LAX, 120, step_s=5)))  # 1441 points on a straight line
        short = simplify(points)
        self.assertLessEqual(len(short), 5)
        self.assertEqual((short[0].lat, short[-1].lat), (points[0].lat, points[-1].lat))
        zigzag = [p._replace(lat=p.lat + (0.05 if i % 50 < 25 else 0)) for i, p in enumerate(points)]
        self.assertLessEqual(len(simplify(zigzag, max_points=40)), 40)


class LookupTests(unittest.TestCase):
    def test_finds_the_flight_that_went_over_home(self):
        t = utc(2026, 11, 7, 12)
        earlier = leg(t - 5 * 3600, LAX, PDX, 120)
        pdx_lax = leg(t, PDX, LAX, 120)
        fake = FakeAdsbLol({trace_url("abc123"): trace_file(earlier + pdx_lax)})
        lookup = FlightLookup(fetch=fake, clock=lambda: t + 3 * 3600)
        points, finished = lookup.flight("abc123", t + 100 * 60, t + 102 * 60)  # near LAX at the end
        self.assertTrue(finished)
        self.assertAlmostEqual(points[0].lat, PDX[0], places=3)
        self.assertAlmostEqual(points[-1].lat, LAX[0], places=3)

    def test_a_flight_still_in_the_air_is_not_finished(self):
        t = utc(2026, 11, 7, 12)
        so_far = leg(t, PDX, LAX, 60, land=False)
        fake = FakeAdsbLol({trace_url("abc123"): trace_file(so_far)})
        lookup = FlightLookup(fetch=fake, clock=lambda: t + 61 * 60)
        points, finished = lookup.flight("abc123", t + 30 * 60, t + 31 * 60)
        self.assertFalse(finished)
        self.assertEqual(len(points), 61)
        self.assertEqual(len(lookup.current_flight("abc123")), 61)

    def test_uses_adsb_lol_api_with_the_key(self):
        self.assertEqual(trace_url("abc123"), "https://api.adsb.lol/1/trace/full/abc123")
        seen = {}

        def fake_get(url, timeout=25, headers=None):
            seen.update(url=url, headers=headers)
            raise TraceNotFound(url)

        import planespotter.fullpath as fp
        original = fp.get_trace_json
        fp.get_trace_json = fake_get
        try:
            FlightLookup(api_key="secret-key").flight("abc123", 0, 1)
        finally:
            fp.get_trace_json = original
        self.assertEqual(seen["url"], "https://api.adsb.lol/1/trace/full/abc123")
        self.assertEqual(seen["headers"], {"x-api-key": "secret-key"})

    def test_unknown_aircraft(self):
        lookup = FlightLookup(fetch=FakeAdsbLol(), clock=lambda: utc(2026, 11, 8))
        self.assertEqual(lookup.flight("abc123", utc(2026, 11, 7, 12), utc(2026, 11, 7, 12, 2)), (None, False))


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config(home_lat=LAX[0] + 0.05, home_lon=LAX[1])  # home just north of LAX
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.t = utc(2026, 11, 7, 12)
        self.now = [self.t + 3 * 3600]
        ac = plane("abc123", lat=self.cfg.home_lat, lon=self.cfg.home_lon)
        self.pid, _ = self.store.record_sighting(ac, 0.5, 0, self.t + 110 * 60, gap=1800)

    def fetcher(self, files):
        fake = FakeAdsbLol(files)
        clock = lambda: self.now[0]  # noqa: E731
        return FullPathFetcher(self.cfg, self.store, FlightLookup(fetch=fake, clock=clock), clock=clock), fake

    def row(self):
        return self.store.get_pass(self.pid)

    def test_a_finished_flight_is_saved_and_added_to_the_route_heat(self):
        fetcher, _ = self.fetcher({trace_url("abc123"): trace_file(leg(self.t, PDX, LAX, 120))})
        self.assertTrue(fetcher.step())
        row = self.row()
        self.assertEqual(row["full_state"], DONE)
        path = json.loads(row["full_path"])
        self.assertAlmostEqual(path[0][0], PDX[0], places=3)
        cells = self.store.heat_cells(table="route_heat")
        self.assertGreater(len(cells), 50)  # Portland to LA, minus the part near home
        self.assertFalse(fetcher.step())  # nothing left to do
        data = heat_json(self.store, self.cfg, self.now[0], days=7)
        self.assertEqual(data["whole_flights"], 1)
        self.assertEqual(len(data["route_cells"]), len(cells))

    def test_a_flight_still_flying_is_kept_and_checked_again(self):
        so_far = leg(self.t, PDX, LAX, 115, land=False)
        fetcher, fake = self.fetcher({trace_url("abc123"): trace_file(so_far)})
        self.now[0] = self.t + 116 * 60 + 600  # just after it settled
        self.assertTrue(fetcher.step())
        row = self.row()
        self.assertEqual(row["full_state"], WAITING)
        self.assertTrue(json.loads(row["full_path"]))  # the part so far is already there
        self.assertEqual(self.store.heat_cells(table="route_heat"), [])  # counted only when finished
        self.assertFalse(fetcher.step())  # not due again yet
        fake.files[trace_url("abc123")] = trace_file(leg(self.t, PDX, LAX, 120))
        self.now[0] += 31 * 60
        fetcher.lookup.cache.clear()
        self.assertTrue(fetcher.step())
        self.assertEqual(self.row()["full_state"], DONE)

    def test_gives_up_when_there_is_no_record(self):
        fetcher, fake = self.fetcher({})
        for _ in range(2):
            self.assertTrue(fetcher.step())
            self.now[0] += 31 * 60
            fetcher.lookup.cache.clear()
        self.assertEqual(self.row()["full_state"], UNAVAILABLE)
        self.assertEqual(len(fake.requests), 2)

    def test_flights_older_than_a_day_are_not_looked_up(self):
        fetcher, fake = self.fetcher({})
        self.now[0] = self.t + 30 * 3600
        self.assertTrue(fetcher.step())
        self.assertEqual(self.row()["full_state"], UNAVAILABLE)
        self.assertEqual(fake.requests, [])  # adsb.lol's API only keeps about a day

    def test_stays_within_its_share_of_the_daily_budget(self):
        with TemporaryDirectory() as tmp:
            budget = ApiBudget(Path(tmp) / "b.db", daily_limit=100, clock=lambda: self.now[0])
            fake = FakeAdsbLol({trace_url("abc123"): trace_file(leg(self.t, PDX, LAX, 120))})
            clock = lambda: self.now[0]  # noqa: E731
            fetcher = FullPathFetcher(self.cfg, self.store, FlightLookup(fetch=fake, clock=clock, budget=budget), clock=clock)
            for _ in range(25):
                budget.record("trace")  # whole flights get a quarter of the day: used up
            self.assertFalse(fetcher.step())
            self.assertEqual(fake.requests, [])
            self.assertEqual(self.row()["full_state"], WAITING)
            self.now[0] += 25 * 3600  # a day later the share is free again (the pass is too old now, though)
            self.assertTrue(fetcher.step())
            self.assertEqual(self.row()["full_state"], UNAVAILABLE)

    def test_backs_off_when_adsb_lol_is_busy(self):
        fetcher, fake = self.fetcher({})
        fake.error = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        self.assertTrue(fetcher.step())
        self.assertEqual(self.row()["full_state"], WAITING)
        self.assertEqual(self.row()["full_tries"], 0)  # being busy isn't the flight's fault
        self.assertFalse(fetcher.step())  # paused
        self.assertEqual(len(fake.requests), 1)

    def test_aircraft_without_an_address_are_skipped(self):
        self.store.db.execute("UPDATE passes SET hex = '~abc12' WHERE id = ?", (self.pid,))
        fetcher, fake = self.fetcher({})
        self.assertFalse(fetcher.step())
        self.assertEqual(self.row()["full_state"], UNAVAILABLE)
        self.assertEqual(fake.requests, [])


class MigrationTests(unittest.TestCase):
    def test_older_databases_get_the_new_columns(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE passes (id INTEGER PRIMARY KEY, hex TEXT NOT NULL, first_seen REAL, "
                        "last_seen REAL, closest_miles REAL, track REAL, path TEXT NOT NULL DEFAULT '[]')")
            con.execute("INSERT INTO passes (hex, first_seen, last_seen, closest_miles) VALUES ('abc123', 1, 2, 1.0)")
            con.commit()
            con.close()
            store = Store(db)
            row = store.get_pass(1)
            self.assertEqual((row["full_path"], row["full_state"], row["full_tries"]), ("[]", WAITING, 0))
            store.close()


if __name__ == "__main__":
    unittest.main()
