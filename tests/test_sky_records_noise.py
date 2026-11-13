import base64
import csv
import io
import json
import shutil
import ssl
import time
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.noise import build_noise, closest_time, is_night, measure, noise_csv, settle
from planespotter.records import build_records, flight_miles, ordinal, plane_of_the_day, streak
from planespotter.sky import SkyWatch, daylight, nearest_report, sky_note, sun_position
from planespotter.store import Store
from planespotter.webmap import jpeg_from_data_url, start_map_server

from helpers import HOME, FakeFeed, make_config, make_watcher, plane, ts

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
JPEG_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def metar(icao, lat, lon, clouds, visib="10+", elev=30, obs=None, vert=None):
    return {"icaoId": icao, "name": f"{icao} Airport, XX", "lat": lat, "lon": lon, "elev": elev,
            "obsTime": obs, "visib": visib, "cover": clouds[0]["cover"] if clouds else "CLR",
            "clouds": clouds, "vertVis": vert, "rawOb": f"METAR {icao} ..."}


class SunTests(unittest.TestCase):
    def test_midsummer_noon_at_greenwich(self):
        alt, az = sun_position(51.4779, 0.0, ts(2024, 6, 21, 12))
        self.assertAlmostEqual(alt, 61.96, delta=0.5)  # 90 - latitude + the sun's tilt
        self.assertAlmostEqual(az, 180, delta=2)        # due south

    def test_day_twilight_night(self):
        self.assertEqual(daylight(10), "day")
        self.assertEqual(daylight(-3), "twilight")
        self.assertEqual(daylight(-20), "night")
        alt, _ = sun_position(HOME[0], HOME[1], ts(2026, 12, 21, 0))  # London, midnight
        self.assertEqual(daylight(alt), "night")


class WeatherTests(unittest.TestCase):
    def test_nearest_station_that_reports_the_sky(self):
        reports = [
            {"icaoId": "WIND", "lat": HOME[0], "lon": HOME[1], "clouds": None, "cover": None},  # no sky data
            metar("NEAR", HOME[0] + 0.05, HOME[1], [{"cover": "SCT", "base": 4000}, {"cover": "OVC", "base": 1800}]),
            metar("FAR", HOME[0] + 0.4, HOME[1], []),
        ]
        r = nearest_report(reports, *HOME)
        self.assertEqual(r["station"], "NEAR")
        self.assertEqual(r["name"], "NEAR Airport")
        self.assertEqual(r["ceiling_ft"], 1800)  # the lowest solid layer
        self.assertEqual([x["base_ft"] for x in r["layers"]], [1800, 4000])
        self.assertEqual(r["elevation_ft"], 98)  # 30 m

    def test_fog_and_odd_visibility(self):
        r = nearest_report([metar("FOG", HOME[0], HOME[1], [], visib="1/2", vert=200)], *HOME)
        self.assertEqual(r["ceiling_ft"], 200)
        self.assertIsNone(r["visibility_miles"])  # couldn't read it: say nothing rather than guess

    def test_cached_and_retried(self):
        calls, now = [], [ts(2026, 11, 12, 12)]

        def fetch(lat, lon, span):
            calls.append(span)
            if len(calls) == 1:
                raise OSError("offline")
            return [metar("NEAR", lat, lon, [{"cover": "OVC", "base": 2000}], obs=now[0])]

        sky = SkyWatch(make_config(), fetch=fetch, clock=lambda: now[0], background=False)
        self.assertIsNone(sky.report()["weather"])     # offline: no weather, still the sun
        now[0] += 60
        sky.report()
        self.assertEqual(len(calls), 1)                 # waits before trying again
        now[0] += 11 * 60
        self.assertEqual(sky.report()["weather"]["ceiling_ft"], 2000)
        now[0] += 5 * 60
        sky.report()
        self.assertEqual(len(calls), 2)                 # cached for 15 minutes
        now[0] += 4 * 3600
        calls.append("stop")                            # pretend the station went quiet...
        sky._ok, sky._checked = True, now[0]            # ...and nothing new arrived
        self.assertIsNone(sky.report()["weather"])      # a 4-hour-old report says nothing about now

    def test_sky_notes(self):
        day = {"daylight": "day"}
        overcast = {"weather": {"ceiling_ft": 1800, "visibility_miles": 10.0,
                                "layers": [{"cover": "OVC", "base_ft": 1800}]}, "sun": day}
        self.assertIn("Above the overcast at 1,800 ft", sky_note(overcast, 5000, 1.0, ground_ft=100))
        self.assertEqual(sky_note(overcast, 1500, 1.0, ground_ft=100), "")  # below the clouds: visible
        broken = {"weather": {"ceiling_ft": 3000, "visibility_miles": 10.0,
                              "layers": [{"cover": "BKN", "base_ft": 3000}]}, "sun": day}
        self.assertIn("through a gap", sky_note(broken, 9000, 1.0))
        hazy = {"weather": {"ceiling_ft": None, "visibility_miles": 2.0, "layers": []}, "sun": day}
        self.assertIn("Visibility is only 2 mi", sky_note(hazy, 3000, 2.5))
        self.assertIn("flashing lights", sky_note({"weather": None, "sun": {"daylight": "night"}}, 3000, 1.0))
        self.assertEqual(sky_note(None, 3000, 1.0), "")

    def test_alerts_say_when_clouds_hide_the_plane(self):
        class Sky:
            def report(self):
                return {"weather": {"ceiling_ft": 1200, "visibility_miles": 10.0,
                                    "layers": [{"cover": "OVC", "base_ft": 1200}]}, "sun": {"daylight": "day"}}

        watcher, feed, mailer = make_watcher()
        watcher.sky = Sky()
        feed.planes = [plane(alt=6000)]
        watcher.send_alerts(watcher.poll(ts(2026, 11, 1, 12)), ts(2026, 11, 1, 12))
        self.assertTrue(mailer.sent)
        self.assertIn("Above the overcast at 1,200 ft", mailer.sent[-1].text)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.store = Store(":memory:")
        self.t = ts(2026, 11, 10, 12)

    def add(self, hex_code, callsign, type_code, alt, miles, speed=250.0, origin="", destination="",
            at=None, firsts=None):
        ac = plane(hex_code, callsign=callsign, alt=alt, type_code=type_code, speed_kt=speed)
        pid, _ = self.store.record_sighting(ac, miles, 0, at or self.t, gap=60)
        self.store.db.execute("UPDATE passes SET origin = ?, destination = ?, aircraft_desc = ? WHERE id = ?",
                              (origin, destination, {"A388": "Airbus A380-800"}.get(type_code, ""), pid))
        if firsts:
            self.store.set_firsts(pid, firsts)
        self.store.db.commit()
        self.t += 600
        return pid

    def test_records(self):
        self.add("aaa001", "BAW1", "A320", 4000, 2.0, speed=300, origin="LHR", destination="JFK")
        self.add("aaa002", "QFA1", "A388", 900, 0.4, speed=520, origin="SYD", destination="LHR")
        self.add("aaa003", "EZY9", "A320", 12000, 2.9, speed=410)
        for _ in range(3):
            self.add("aaa003", "EZY9", "A320", 12000, 2.9)
        recs = {r["key"]: r for r in build_records(self.store.db.execute("SELECT * FROM passes").fetchall())}
        self.assertEqual(recs["lowest"]["label"], "QFA1")
        self.assertEqual(recs["closest"]["value"], "0.4 mi")
        self.assertIn("520 kt", recs["fastest"]["value"])
        self.assertEqual(recs["highest"]["value"], "12,000 ft")
        self.assertEqual(recs["longest"]["label"], "QFA1")      # Sydney to London, ~10,500 mi
        self.assertGreater(flight_miles("SYD", "LHR"), 10000)
        self.assertIn("4 times", recs["regular"]["value"])

    def test_plane_of_the_day(self):
        for i in range(5):
            self.add(f"bbb00{i}", f"BAW{i}", "A320", 3000, 1.0, at=ts(2026, 11, 9, 8 + i))
        today = [self.add("ccc001", "BAW9", "A320", 2000, 1.0), self.add("ccc002", "QFA1", "A388", 5000, 1.0)]
        rows = self.store.get_passes(today)
        star = plane_of_the_day(rows, self.store, self.cfg)
        self.assertEqual(star["label"], "QFA1")  # a special type beats a lower, common one
        self.assertEqual(star["why"], "Only the 1st Airbus A380-800 you've spotted")
        first = self.add("ddd001", "VIR1", "B789", 9000, 1.0, firsts=["First Virgin Atlantic flight you've spotted"])
        star = plane_of_the_day(self.store.get_passes([first, today[0]]), self.store, self.cfg)
        self.assertEqual(star["why"], "First Virgin Atlantic flight you've spotted")
        self.assertIsNone(plane_of_the_day([], self.store, self.cfg))
        self.assertEqual([ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101)],
                         ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st"])

    def test_plane_of_the_day_is_in_the_daily_report(self):
        watcher, feed, mailer = make_watcher()
        watcher.store.set_meta("watch_started", str(ts(2026, 11, 1)))
        feed.planes = [plane(alt=2000, type_code="A388")]
        watcher.poll(ts(2026, 11, 11, 12))
        watcher.send_digest(date(2026, 11, 12))
        self.assertIn("Plane of the day: BAW123", mailer.sent[-1].text)

    def test_streaks(self):
        cfg = self.cfg
        day = lambda d, h=12: ts(2026, 11, d, h)  # noqa: E731
        s = streak([day(1), day(2), day(2, 18), day(3), day(6), day(7)], cfg, date(2026, 11, 7))
        self.assertEqual((s["current"], s["best"], s["total"], s["today"]), (2, 3, 6, 1))
        self.assertTrue(s["alive_today"])
        s = streak([day(6)], cfg, date(2026, 11, 7))
        self.assertEqual((s["current"], s["alive_today"]), (1, False))  # still alive until tonight
        self.assertEqual(streak([day(4)], cfg, date(2026, 11, 7))["current"], 0)  # broken
        self.assertEqual(streak([], cfg, date(2026, 11, 7))["best"], 0)


class NoiseTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config(quiet_hours=None)
        self.store = Store(":memory:")

    def test_measure(self):
        t0 = 10_000
        levels = [(t, -60.0) for t in range(t0 - 600, t0 + 600)]
        levels = [(t, -35.0 if abs(t - t0) < 20 else db) for t, db in levels]
        self.assertEqual(measure(levels, t0), (-35.0, -60.0))
        self.assertIsNone(measure([(t0, -40.0)], t0))  # the microphone wasn't on

    def test_settle_matches_levels_to_planes(self):
        start = ts(2026, 11, 10, 22, 30)
        pid, _ = self.store.record_sighting(plane(), 0.5, 0, start, gap=1800)
        self.store.append_path(pid, [[HOME[0] + 0.02, HOME[1], 3000, start], [HOME[0], HOME[1], 2800, start + 40],
                                     [HOME[0] - 0.02, HOME[1], 2600, start + 80]])
        self.store.db.execute("UPDATE passes SET last_seen = ? WHERE id = ?", (start + 80, pid))
        before, _ = self.store.record_sighting(plane("def456"), 2.5, 0, start - 7200, gap=1800)
        gap, _ = self.store.record_sighting(plane("fed654"), 2.5, 0, start + 2000, gap=1800)
        self.assertEqual(closest_time(self.cfg, self.store.get_pass(pid)), start + 40)
        levels = [(t, -30.0 if abs(t - (start + 40)) < 15 else -55.0) for t in range(int(start) - 600, int(start) + 700)]
        self.store.add_noise_levels(levels)
        self.assertEqual(settle(self.store, self.cfg, start + 120), 0)  # too soon: it might still be going
        self.assertEqual(settle(self.store, self.cfg, start + 400), 1)
        row = self.store.get_pass(pid)
        self.assertEqual((row["noise_state"], row["noise_peak"], row["noise_bg"]), (1, -30.0, -55.0))
        self.assertEqual(self.store.get_pass(before)["noise_state"], 0)  # before the meter was ever on: ignored
        settle(self.store, self.cfg, start + 3000)
        self.assertEqual(self.store.get_pass(gap)["noise_state"], 2)  # the meter was off by then

        noise = build_noise(self.store, self.cfg, start + 3600, 7, ground_ft=0)
        self.assertEqual(noise["loudest"][0]["louder_db"], 25.0)
        self.assertEqual(noise["night_count"], 1)  # 23:03 is night (by default 23:00-07:00); 22:30 is not

    def test_night_and_low(self):
        cfg = make_config(quiet_hours=None)
        self.assertTrue(is_night(cfg, ts(2026, 11, 10, 23, 30)))
        self.assertTrue(is_night(cfg, ts(2026, 11, 11, 6, 59)))
        self.assertFalse(is_night(cfg, ts(2026, 11, 11, 7, 0)))
        from datetime import time as t
        cfg = make_config(quiet_hours=(t(22, 0), t(6, 0)))
        self.assertTrue(is_night(cfg, ts(2026, 11, 10, 22, 30)))   # your quiet hours are your night
        self.assertFalse(is_night(cfg, ts(2026, 11, 11, 6, 30)))

    def test_summary_and_spreadsheet(self):
        now = ts(2026, 11, 12, 12)
        low, _ = self.store.record_sighting(plane("aaa001", alt=1500, callsign="LOW1"), 0.3, 90, now - 86400, gap=60)
        night, _ = self.store.record_sighting(plane("aaa002", alt=8000, callsign="LATE1"), 2.0, 180,
                                              ts(2026, 11, 12, 1, 15), gap=60)
        self.store.record_sighting(plane("aaa003", alt=9000, callsign="OLD1"), 2.0, 180, now - 40 * 86400, gap=60)
        n = build_noise(self.store, self.cfg, now, 30, ground_ft=100)
        self.assertEqual((n["total"], n["low_count"], n["night_count"]), (2, 1, 1))
        self.assertEqual(n["lowest"][0]["above_home_ft"], 1400)
        self.assertEqual(n["latest_night"][0]["label"], "LATE1")
        self.assertEqual(len(n["nights"]), 30)
        self.assertEqual(n["nights"][-2], {"date": "2026-11-11", "count": 1})  # 1:15 am belongs to the night before

        text = noise_csv(self.store, self.cfg, now, 30, ground_ft=100)
        self.assertTrue(text.startswith("﻿"))
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        self.assertEqual([r["Flight"] for r in rows], ["LOW1", "LATE1"])
        self.assertEqual((rows[0]["Low"], rows[0]["Height above home (ft)"], rows[0]["Direction"]), ("yes", "1400", "E"))
        self.assertEqual((rows[1]["Night"], rows[1]["Time"]), ("yes", "01:15"))
        self.assertEqual(len(list(csv.reader(io.StringIO(noise_csv(self.store, self.cfg, now, 0))))), 4)  # all


class PhotoAndEyeServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False, weather=False,
                          adsbdb_api="http://127.0.0.1:9")
        store = Store(cfg.db_path)
        cls.pass_id, _ = store.record_sighting(plane(), 1.5, 45, time.time() - 600, gap=1800)
        store.close()
        cls.server, urls = start_map_server(cfg, feed=FakeFeed(), port=0)
        cls.base = urls[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()

    def post(self, path, data, headers=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def test_photos(self):
        added = self.post("/api/photo", {"pass_id": self.pass_id, "full": JPEG_URL, "thumb": JPEG_URL,
                                         "width": 1600, "height": 1200})
        photo = next(p for p in added["photos"] if p["id"] == added["id"])
        self.assertEqual((photo["pass_id"], photo["hex"], photo["callsign"]), (self.pass_id, "abc123", "BAW123"))
        status, headers, body = self.get(f"/photo/{added['id']}.jpg")
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/jpeg", JPEG))
        self.assertEqual(self.get(f"/photo/{added['id']}-thumb.jpg")[2], JPEG)

        live = self.post("/api/photo", {"hex": "ABCDEF", "callsign": "dal1", "full": JPEG_URL, "thumb": JPEG_URL})
        photo = next(p for p in live["photos"] if p["id"] == live["id"])
        self.assertEqual((photo["pass_id"], photo["hex"], photo["callsign"]), (None, "abcdef", "DAL1"))

        left = self.post("/api/photo/delete", {"photo": added["id"]})
        self.assertNotIn(added["id"], [p["id"] for p in left["photos"]])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get(f"/photo/{added['id']}.jpg")
        self.assertEqual(caught.exception.code, 404)

    def test_bad_uploads_are_refused(self):
        not_jpeg = "data:image/jpeg;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
        cases = [
            ({"hex": "abcdef", "full": not_jpeg, "thumb": JPEG_URL}, {}, 400),
            ({"hex": "abcdef", "full": "data:image/png;base64,AAAA", "thumb": JPEG_URL}, {}, 400),
            ({"hex": "nope", "full": JPEG_URL, "thumb": JPEG_URL}, {}, 400),
            ({"hex": "abcdef", "full": JPEG_URL, "thumb": JPEG_URL}, {"Origin": "https://evil.example"}, 403),
            ({"hex": "abcdef", "full": JPEG_URL, "thumb": JPEG_URL}, {"Content-Type": "text/plain"}, 415),
        ]
        for data, headers, code in cases:
            with self.subTest(code=code, headers=headers), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post("/api/photo", data, headers)
            self.assertEqual(caught.exception.code, code)
        for path in ("/photo/..%2Fmap.db.jpg", "/photo/abc.jpg", "/photo/1-thumb-thumb.jpg"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError):
                self.get(path)
        with self.assertRaises(ValueError):
            jpeg_from_data_url(JPEG_URL, 10)  # too big

    def test_i_saw_it(self):
        yours = self.post("/api/saw", {"hex": "abc123", "callsign": "BAW123"})
        self.assertEqual(yours["streak"]["current"], 1)
        self.assertEqual(yours["eyes"][0][0], "abc123")
        self.post("/api/saw", {"hex": "abc123"})  # twice for the same plane counts once
        self.assertEqual(len(self.post("/api/saw", {"hex": "abc123"})["eyes"]), 1)
        collection = json.loads(self.get("/api/collection")[2])
        self.assertIn("eyes1", [b["key"] for b in collection["badges"] if b["earned_at"]])
        self.assertEqual(collection["streak"]["total"], 1)
        self.assertEqual(collection["star"]["label"], "BAW123")
        self.assertEqual(self.post("/api/saw", {"hex": "abc123", "undo": True})["eyes"], [])

    def test_noise_endpoints(self):
        now = int(time.time())
        got = self.post("/api/noise/levels", {"samples": [[now - i, -50.0] for i in range(20)] + [[now, 99]]})
        self.assertEqual(got["stored"], 20)  # the impossible level is dropped
        noise = json.loads(self.get("/api/noise?days=7")[2])
        self.assertEqual(noise["total"], 1)
        self.assertEqual(noise["listening"], now)
        status, headers, body = self.get("/api/noise.csv?days=7")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("BAW123", body.decode("utf-8"))
        sky = json.loads(self.get("/api/sky")[2])
        self.assertIsNone(sky["weather"])  # weather checks are off here
        self.assertIn(sky["sun"]["daylight"], ("day", "twilight", "night"))


@unittest.skipUnless(shutil.which("openssl"), "needs openssl")
class HttpsTests(unittest.TestCase):
    def test_secure_address_for_phones(self):
        with TemporaryDirectory() as tmp:
            cfg = make_config(db_path=Path(tmp) / "map.db", map_lan=True, weather=False)
            from planespotter import webmap
            real = webmap.lan_address
            webmap.lan_address = lambda: "127.0.0.1"  # pretend this is the home network
            try:
                server, urls = start_map_server(cfg, feed=FakeFeed(), port=0, https_port=0)
            finally:
                webmap.lan_address = real
            try:
                secure = urls[-1]
                self.assertTrue(secure.startswith("https://127.0.0.1:"))
                ctx = ssl.create_default_context(cafile=str(Path(tmp) / "https-cert" / "cert.pem"))
                with urllib.request.urlopen(secure + "/api/config", context=ctx, timeout=10) as resp:
                    self.assertEqual(json.loads(resp.read())["secure_url"], secure)
                with urllib.request.urlopen(secure + "/sky", context=ctx, timeout=10) as resp:
                    self.assertIn(b"<html", resp.read())
                with self.assertRaises(urllib.error.URLError):  # an untrusting visitor doesn't break anything
                    urllib.request.urlopen(secure + "/api/config", timeout=5)
                with urllib.request.urlopen(secure + "/api/config", context=ctx, timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
