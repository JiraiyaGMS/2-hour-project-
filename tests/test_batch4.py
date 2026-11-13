import json
import math
import time
import unittest
import urllib.request
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter import astro
from planespotter.adsb import Aircraft
from planespotter.calendar_ics import build_calendar
from planespotter.commands import Commands, handle
from planespotter.friends import Friends, parse_friends
from planespotter.geo import offset
from planespotter.jobs import regulars_due, relay_job
from planespotter.myflight import FlightWatch, add_flight, callsign_for, my_flights
from planespotter.notify import EXTRA_CAPS, NtfyQuota, PushNotifier, derived_topic
from planespotter.outlook import best_time, best_time_text, contrail_kind, contrail_text, ice_humidity
from planespotter.phonekit import kit, kit_email
from planespotter.relay import BURST, MAX_BYTES, RETRY, Relay, build_status, compact, spoken_now
from planespotter.predict import crossings
from planespotter.store import Store
from planespotter.travel import TravelMode, parse_where
from planespotter.webmap import start_map_server

from helpers import HOME, FakeFeed, make_config, make_watcher, plane, ts

REF_TS = 1794560400  # 13 Nov 2026 08:20 UTC; positions below from PyEphem (with refraction)


class FakePost:
    def __init__(self):
        self.sent = []

    def __call__(self, url, payload):
        self.sent.append(payload)


class AstroTests(unittest.TestCase):
    def test_positions_match_an_astronomy_library(self):
        cases = [((33.985, -118.39), "Jupiter", 84.451, 16.619), ((51.5, -0.1), "Jupiter", 234.288, 40.813),
                 ((51.5, -0.1), "Venus", 165.555, 26.928), ((33.985, -118.39), "Sirius", 143.324, 30.603)]
        for (lat, lon), name, az, el in cases:
            with self.subTest(name=name, lat=lat):
                if name == "Sirius":
                    got = astro.star(6.7525, -16.7161, REF_TS, lat, lon)
                else:
                    got = astro.planet(name, REF_TS, lat, lon)[:2]
                self.assertLess(astro.separation(got[0], got[1], az, el), 0.05)  # within 3 arc-minutes
        maz, mel, _ = astro.moon(REF_TS, 51.5, -0.1)
        self.assertLess(astro.separation(maz, mel, 107.978, -21.446), 0.1)
        self.assertAlmostEqual(astro.refraction(0), 0.48, delta=0.05)  # the horizon is lifted by about half a degree

    def test_not_a_plane(self):
        # London before dawn: Venus is low in the south-east... but it's below 8° then, so check the logic both ways.
        text = astro.not_a_plane(REF_TS, 51.5, -0.1)
        self.assertTrue(text == "" or "not a plane" in text)
        self.assertEqual(astro.not_a_plane(ts(2026, 6, 21, 12), 51.5, -0.1), "")  # broad daylight

    def test_plane_across_the_moon(self):
        cfg = make_config(home_lat=33.985, home_lon=-118.39)
        t = REF_TS
        while True:  # find the Moon well up and bright
            az, el, lit = astro.moon(t, cfg.home_lat, cfg.home_lon)
            if el > 25 and lit > 0.3:
                break
            t += 1800
        az, el, _ = astro.moon(t + 60, cfg.home_lat, cfg.home_lon)  # where it'll be when the plane crosses
        ground = 30000 * 0.3048 / math.tan(math.radians(el))
        lat, lon = offset(cfg.home_lat, cfg.home_lon, az, ground / 1609.344)
        slat, slon = offset(lat, lon, (az + 270) % 360, 450 * 1.15078 * 60 / 3600)
        ac = Aircraft(hex="abc123", callsign="TEST1", registration="", type_code="A320", lat=slat, lon=slon,
                      alt_ft=30000, speed_kt=450, track=(az + 90) % 360)
        found = astro.transits(cfg, [ac], t)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].at - t, 60, delta=2)
        self.assertLess(found[0].miss, astro.MOON_RADIUS_DEG)  # right across it
        away = Aircraft(**{**ac.__dict__, "track": (az + 270) % 360})
        self.assertEqual(astro.transits(cfg, [away], t), [])


class OutlookTests(unittest.TestCase):
    def test_contrails(self):
        self.assertAlmostEqual(ice_humidity(65, -45), 100, delta=3)  # 65% over water is saturated over ice up there
        hour = {"temperature_250hPa": -48, "relative_humidity_250hPa": 70}
        self.assertEqual(contrail_kind(hour), "long")
        self.assertEqual(contrail_kind({"temperature_250hPa": -48, "relative_humidity_250hPa": 15}), "short")
        self.assertEqual(contrail_kind({"temperature_250hPa": -30, "relative_humidity_250hPa": 90}), "none")
        cfg = make_config()
        now = ts(2026, 11, 13, 9)
        hours = [{"t": ts(2026, 11, 13, h), "temperature_250hPa": -48,
                  "relative_humidity_250hPa": 70 if h < 12 else 10} for h in range(24)]
        self.assertEqual(contrail_text(hours, cfg, now),
                         "✈️ Long-lasting contrails likely this morning; only short contrails (gone in seconds) this afternoon")

    def test_best_time(self):
        cfg = make_config()
        store = Store(":memory:")
        for d in range(1, 8):
            for i in range(6):
                store.record_sighting(plane(f"a{d}{i}000"[:6]), 1.0, 0, ts(2026, 11, d, 17, i * 9), gap=60)
            store.record_sighting(plane(f"b{d}0000"[:6]), 1.0, 0, ts(2026, 11, d, 9), gap=60)
        now = ts(2026, 11, 13, 8)
        clear = [{"t": ts(2026, 11, 13, h), "cloud_cover_low": 0} for h in range(24)]
        best = best_time(store, cfg, now, clear)
        self.assertEqual((best["hour"], best["planes"]), (17, 6))
        self.assertIn("5pm–6pm (usually about 6 planes, clear", best_time_text(best, cfg, now))
        cloudy_evening = [{"t": ts(2026, 11, 13, h), "cloud_cover_low": 100 if h == 17 else 0} for h in range(24)]
        self.assertEqual(best_time(store, cfg, now, cloudy_evening)["hour"], 9)  # cloudy and dark by 5pm in London


class NtfyTests(unittest.TestCase):
    def test_quota_keeps_room_for_alerts(self):
        now = [ts(2026, 11, 13)]
        left = [200]
        q = NtfyQuota(":memory:", "https://ntfy.sh", clock=lambda: now[0], fetch_remaining=lambda: left[0])
        self.assertTrue(q.allow("relay"))
        for _ in range(EXTRA_CAPS["relay"]):
            q.record("relay")
        self.assertFalse(q.allow("relay"))   # its daily cap
        self.assertTrue(q.allow("family"))
        left[0], now[0] = 50, now[0] + 1300
        self.assertFalse(q.allow("family"))  # ntfy is nearly out: keep the rest for plane alerts
        self.assertTrue(q.allow("alert"))

    def test_topics_and_publishing(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        post = FakePost()
        n = PushNotifier(cfg, post=post)
        self.assertTrue(n.topic("relay").startswith("planespotter-relay-"))
        self.assertNotEqual(n.topic("relay"), n.topic("family"))
        self.assertEqual(n.topic("family"), derived_topic("planespotter-test", "family"))
        n.family("✨ A380", "Over home")
        self.assertEqual(post.sent[-1]["topic"], n.topic("family"))
        self.assertFalse(PushNotifier(make_config(ntfy_topic="x", family_alerts=False), post=post).family("a", "b"))

    def test_relay_status(self):
        cfg = make_config(ntfy_topic="planespotter-test", radius_miles=2)
        now = ts(2026, 11, 13, 16)
        coming = crossings(cfg, [plane("aaa001", lat=HOME[0] - 0.06, track=0.0, speed_kt=240.0, callsign="DAL573")])

        class Info:
            airline, origin, destination, origin_city = "Delta Air Lines", "ATL", "LAX", "Atlanta"
        status = build_status(cfg, coming, {"aaa001": Info()}, None, now, {"space": "The space station flies over 6:02 pm."})
        self.assertEqual(status["next"][0]["label"], "DAL573")
        self.assertEqual(status["next"][0]["route"], "ATL → LAX")
        self.assertEqual(status["next"][0]["say"], "Delta flight 573 from Atlanta")
        self.assertNotIn("spoken", status["next"][0])
        self.assertGreater(status["next"][0]["at"], now)
        self.assertTrue(status["siri"].startswith("Delta flight 573 from Atlanta is due over about 4:0"), status["siri"])
        self.assertIn("space station", status["siri"])
        post, clock = FakePost(), [now]
        relay = Relay(PushNotifier(cfg, post=post), lambda: clock[0])
        self.assertTrue(relay.maybe_send(status))
        self.assertFalse(relay.maybe_send(status))  # nothing new
        clock[0] += 31 * 60
        self.assertTrue(relay.maybe_send(status))   # a heartbeat every half hour
        self.assertEqual(json.loads(post.sent[0]["message"])["home"], "Test House")

    def test_widget_has_something_to_show_between_planes(self):
        """No plane due right now: the status still carries the next regular, the last plane and the hour's count."""
        watcher, _, _ = make_watcher(ntfy_topic="planespotter-test")
        store = watcher.store
        for d in range(1, 6):
            for hex_code, callsign, hour in (("aaa001", "BAW1", 9), ("aaa002", "BAW2", 15), ("aaa003", "BAW3", 6),
                                             ("aaa004", "BAW4", 10)):
                store.record_sighting(plane(hex_code, callsign=callsign), 1.0, 0, ts(2026, 11, d, hour), gap=60)
        now = ts(2026, 11, 6, 10, 15)
        store.record_sighting(plane("aaa005", callsign="EZY9"), 1.0, 0, now - 1800, gap=60)
        due = regulars_due(store, watcher.cfg, now)
        self.assertEqual([r["label"] for r in due], ["BAW4", "BAW2"])  # BAW1 is long overdue, BAW3 was missed
        self.assertEqual(due[1]["at"], ts(2026, 11, 6, 15))
        post = FakePost()
        relay_job(watcher, Relay(PushNotifier(watcher.cfg, post=post), lambda: now), None)([], now)
        status = json.loads(post.sent[-1]["message"])
        self.assertEqual(status["next"], [])
        self.assertEqual(status["last"]["label"], "EZY9")
        self.assertEqual(status["hour_count"], 1)
        self.assertEqual([r["label"] for r in status["regulars"]], ["BAW4", "BAW2"])
        quiet = build_status(watcher.cfg, [], {}, None, now, {"hour_count": 0, "regulars": []})
        self.assertEqual(quiet["hour_count"], 0)  # "0 planes in the last hour" is still worth showing
        self.assertNotIn("regulars", quiet)

    def status_at(self, posted, *planes):
        return {"v": 1, "home": "Home", "updated": posted, "last": None,
                "next": [{"label": label, "say": say, "at": posted + eta, "direction": "E"} for label, say, eta in planes]}

    def test_siri_skips_a_plane_that_has_gone_by(self):
        posted = ts(2026, 11, 13, 21)
        status = self.status_at(posted, ("ACA791", "Air Canada flight 791 from Montreal", 240),
                                ("DAL573", "Delta flight 573 from Atlanta", 540))
        status["regulars"] = [{"label": "UAL1", "say": "United flight 1 from Denver", "at": posted + 3600}]
        clock = lambda at: "10:00 pm"
        self.assertTrue(spoken_now(status, posted, clock).startswith(
            "Air Canada flight 791 from Montreal is due in about 4 minutes, look to the east. Then Delta"))
        later = spoken_now(status, posted + 360, clock)   # 6 minutes on: Montreal has been and gone
        self.assertNotIn("Montreal", later)
        self.assertTrue(later.startswith("Delta flight 573 from Atlanta is due in about 3 minutes"), later)
        quiet = spoken_now(status, posted + 20 * 60, clock)
        self.assertIn("No planes are due over Home", quiet)
        self.assertIn("Your next regular, United flight 1 from Denver, usually comes over at about 10:00 pm.", quiet)
        self.assertIn("The last one over was Delta flight 573 from Atlanta, 11 minutes ago.", quiet)
        self.assertIn("planespotter last updated 20 minutes ago.", quiet)
        self.assertIn("Is it still running on your Mac", spoken_now(status, posted + 4 * 3600, clock))
        self.assertIn("can't reach planespotter", spoken_now(None, posted, clock))

    def test_relay_posts_when_the_phone_would_be_wrong(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        post, clock = FakePost(), [ts(2026, 11, 13, 21)]
        relay = Relay(PushNotifier(cfg, post=post), lambda: clock[0], per_day=2000)  # plenty: test the timing only
        t0 = clock[0]
        self.assertTrue(relay.maybe_send(self.status_at(t0, ("ACA791", "", 240), ("DAL573", "", 1500))))
        # Six minutes on, Montreal has gone by and Delta is next: the phone works that out by itself.
        clock[0] = t0 + 360
        self.assertFalse(relay.maybe_send(self.status_at(t0, ("DAL573", "", 1500))))
        # A new plane turns up ahead of Delta: the phone can't know, so post.
        clock[0] = t0 + 420
        self.assertTrue(relay.maybe_send(self.status_at(t0, ("UAL9", "", 800), ("DAL573", "", 1500))))
        # A minute's drift doesn't matter; a plane due 5 minutes later does (but not within 4 minutes of the last post).
        clock[0] = t0 + 480
        self.assertFalse(relay.stale(self.status_at(t0, ("UAL9", "", 860), ("DAL573", "", 1500)), clock[0]))
        moved = self.status_at(t0, ("UAL9", "", 1100), ("DAL573", "", 1500))
        self.assertTrue(relay.stale(moved, clock[0]))
        self.assertFalse(relay.maybe_send(moved))
        clock[0] = t0 + 700
        self.assertTrue(relay.maybe_send(moved))
        self.assertEqual(len(post.sent), 3)

    def test_relay_spreads_its_allowance_over_the_day(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        post, clock = FakePost(), [ts(2026, 11, 13)]
        relay = Relay(PushNotifier(cfg, post=post), lambda: clock[0], per_day=96)
        start, first_hour = clock[0], None
        for minute in range(0, 24 * 60, 1):   # a new plane is next every minute, all day
            clock[0] = start + minute * 60
            relay.maybe_send(self.status_at(clock[0], (f"AAA{minute}", "", 300)))
            if minute == 59:
                first_hour = len(post.sent)
        self.assertLessEqual(len(post.sent), 96 + BURST)
        self.assertGreaterEqual(len(post.sent), 90)       # it keeps posting all day, not just until lunch
        self.assertLessEqual(first_hour, 4 + BURST)        # about every 15 minutes, plus a couple saved up

    def test_relay_waits_after_a_failed_post(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        clock, calls = [ts(2026, 11, 13, 21)], []

        def refuse(url, payload):
            calls.append(payload)
            raise urllib.error.URLError("no network")
        relay = Relay(PushNotifier(cfg, post=refuse), lambda: clock[0])
        status = self.status_at(clock[0], ("ACA791", "", 240))
        self.assertFalse(relay.maybe_send(status))
        clock[0] += 60
        self.assertFalse(relay.maybe_send(status))
        self.assertEqual(len(calls), 1)                    # it doesn't hammer ntfy (or the log)
        clock[0] += RETRY
        relay.maybe_send(status)
        self.assertEqual(len(calls), 2)

    def test_status_fits_in_one_ntfy_message(self):
        posted = ts(2026, 11, 13, 21)
        big = self.status_at(posted, *[(f"ABC{i}", "Some Airline flight 1234 from Somewhere Far Away " * 3, 60 * i)
                                       for i in range(6)])
        big.update(best="x" * 900, sky="y" * 900, space="z" * 600, regulars=[{"label": "A", "at": posted}] * 3,
                   siri="s" * 400)
        body = compact(big)
        self.assertLessEqual(len(body.encode()), MAX_BYTES)
        self.assertEqual(len(json.loads(body)["next"]), 6)  # the planes always stay; the extras go first


class FlightTests(unittest.TestCase):
    def test_flight_numbers(self):
        self.assertEqual(callsign_for("UA 1234"), "UAL1234")
        self.assertEqual(callsign_for("dl0573"), "DAL573")
        self.assertEqual(callsign_for("SWA2527"), "SWA2527")
        self.assertEqual(callsign_for("WN 12"), "SWA12")
        with self.assertRaises(ValueError):
            callsign_for("not a flight!")

    def test_takeoff_over_home_and_landing(self):
        cfg = make_config()
        store = Store(":memory:")
        add_flight(store, "BA 123", date(2026, 11, 13), ts(2026, 11, 13, 8))
        told = []

        class Notifier:
            enabled = True

            def send(self, title, message, **kw):
                told.append(title)

            def family(self, title, message, **kw):
                told.append("family: " + title)

            def link(self, **kw):
                return ""

        class Lookup:
            def info(self, ac, now):
                from planespotter.adsb import FlightInfo
                return FlightInfo(origin="LHR", origin_city="London", destination="JFK", destination_city="New York")

        clock = [ts(2026, 11, 13, 9)]
        answers = [{"ac": [{"hex": "406abc", "flight": "BAW123", "lat": 51.47, "lon": -0.45, "alt_baro": 2000, "gs": 180,
                            "track": 270}]},
                   {"ac": [{"hex": "406abc", "flight": "BAW123", "lat": HOME[0] + 0.05, "lon": HOME[1], "alt_baro": 9000,
                            "gs": 300, "track": 0}]},
                   {"ac": [{"hex": "406abc", "flight": "BAW123", "lat": 40.64, "lon": -73.78, "alt_baro": 900, "gs": 140,
                            "track": 40}]},
                   {"ac": [{"hex": "406abc", "lat": 40.64, "lon": -73.78, "alt_baro": "ground"}]}]
        watch = FlightWatch(cfg, store, Notifier(), fetch=lambda cs: answers.pop(0), lookup=Lookup(),
                            clock=lambda: clock[0])
        events = []
        for _ in range(4):
            events += watch.check()
            clock[0] += 600
        self.assertEqual(events, ["🛫 BA 123 has taken off from London (LHR)",
                                  "✈ BA 123 is flying over Test House right now",
                                  "🛬 BA 123 has landed in New York (JFK)"])
        self.assertIn("family: 🛬 BA 123 has landed in New York (JFK)", told)
        self.assertEqual(my_flights(store)[0]["state"], "landed")


class CommandTests(unittest.TestCase):
    def test_commands(self):
        cfg = make_config()
        store = Store(":memory:")
        started = []

        class Travel:
            def start(self, lat, lon, name=""):
                started.append((lat, lon, name))
                return {"name": name or "Anaheim", "until_text": "Sat 4:00 PM"}

            def stop(self):
                started.append("stop")

            def status(self):
                return {"on": False}

        now = ts(2026, 11, 13, 10)
        self.assertIn("watching Disneyland", handle("travel 33.8090,-117.9265 Disneyland", cfg, store, Travel(), now))
        self.assertEqual(started[-1], (33.809, -117.9265, "Disneyland"))
        self.assertIn("Travel mode is off", handle("home", cfg, store, Travel(), now))
        self.assertIn("Following UA 1234 (UAL1234) on 2026-11-14", handle("flight UA 1234 tomorrow", cfg, store, None, now))
        self.assertIn("UA 1234: waiting", handle("status", cfg, store, Travel(), now))
        self.assertIn("Not following", handle("flights off", cfg, store, None, now))
        self.assertIn("Commands:", handle("hello", cfg, store, None, now))
        self.assertIsNone(parse_where("travel nowhere"))

    def test_reads_new_messages_once(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        store = Store(":memory:")
        post = FakePost()
        n = PushNotifier(cfg, post=post)
        inbox = [[{"event": "message", "id": "a1", "message": "flight DL573"}], []]
        n.poll = lambda kind, since="latest", raw=False: inbox.pop(0)
        c = Commands(cfg, store, n, travel=None, clock=lambda: ts(2026, 11, 13, 10))
        self.assertEqual(len(c.step()), 1)
        self.assertEqual(store.get_meta("cmd_since"), "a1")
        self.assertEqual(post.sent[-1]["topic"], "planespotter-test")  # answered on your main topic
        self.assertEqual(c.step(), [])


class TravelTests(unittest.TestCase):
    def test_start_and_stop(self):
        with TemporaryDirectory() as tmp:
            cfg = make_config(db_path=Path(tmp) / "home.db")

            class Watcher:
                def __init__(self):
                    import threading
                    self.share, self.stopped = 1, threading.Event()

            home = Watcher()
            built = []

            def start_watcher(c):
                built.append(c)
                return Watcher(), None

            t = TravelMode(cfg, home, None, start_watcher, clock=lambda: ts(2026, 11, 13, 10))
            status = t.start(33.809, -117.9265)
            self.assertEqual(status["name"], "Fullerton")  # the nearest airport's town
            self.assertEqual((built[0].home_name, built[0].map_port, home.share), ("Fullerton", cfg.map_port + 9, 2))
            self.assertIn("travel-fullerton", str(built[0].db_path))
            again = TravelMode(cfg, Watcher(), None, start_watcher, clock=lambda: ts(2026, 11, 13, 12))
            again.restore()  # after a restart, the trip carries on
            self.assertTrue(again.status()["on"])
            t.stop()
            self.assertEqual(home.share, 1)
            self.assertFalse(t.status()["on"])


class FriendsTests(unittest.TestCase):
    def test_cards_and_rare_spots(self):
        self.assertEqual(parse_friends("Sam=planespotter-share-abc123def456; bad; Alex=x"),
                         [("Sam", "planespotter-share-abc123def456")])
        cfg = make_config(ntfy_topic="planespotter-test", friends="Sam=planespotter-share-abc123def456", share_name="Henry")
        store = Store(":memory:")
        post = FakePost()
        n = PushNotifier(cfg, post=post)
        now = [ts(2026, 11, 13, 10)]
        f = Friends(cfg, store, n, clock=lambda: now[0])
        self.assertTrue(f.post())
        card = json.loads(post.sent[-1]["message"])
        self.assertEqual((card["name"], post.sent[-1]["topic"]), ("Henry", n.topic("share")))
        cards = [{"name": "Sam", "week": 12, "planes": 400, "rare": [{"at": now[0] - 100, "what": "an A380", "label": "QFA11"}]}]
        n.poll = lambda code, since="latest", raw=False: json.dumps(cards[-1])
        self.assertEqual(f.read(), [])  # the first read just catches up
        cards.append({**cards[0], "rare": [{"at": now[0] + 50, "what": "a military aircraft", "label": "RCH123"}]})
        self.assertEqual(f.read(), ["🎉 Sam just spotted a military aircraft (RCH123)"])
        board = f.board()
        self.assertEqual([r["friend"] for r in board["rows"]], ["Sam", "You"])  # most planes this week first


class CalendarTests(unittest.TestCase):
    def test_ics(self):
        cfg = make_config()
        passes = [{"visible": True, "max_el": 60, "number": 25544, "start": ts(2026, 11, 13, 18), "end": ts(2026, 11, 13, 18, 6),
                   "visible_from": ts(2026, 11, 13, 18, 1), "visible_to": ts(2026, 11, 13, 18, 5), "name": "Space station (ISS)",
                   "start_dir": "W", "max_dir": "NW", "end_dir": "NE"}]
        best = [{"start": ts(2026, 11, 13, 17), "end": ts(2026, 11, 13, 18), "planes": 6, "sky": "clear"}]
        flights = [{"callsign": "UAL1234", "label": "UA 1234", "day": "2026-11-14", "state": "waiting"}]
        ics = build_calendar(cfg, ts(2026, 11, 13), passes, best, flights).decode()
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 3)
        self.assertIn("DTSTART:20261113T180100Z", ics)
        self.assertIn("TRIGGER:-PT10M", ics)
        self.assertIn("DTSTART;VALUE=DATE:20261114", ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))


class PhoneKitTests(unittest.TestCase):
    def test_kit(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        k = kit(cfg, "http://192.168.1.5:8765")
        self.assertIn(f"https://ntfy.sh/{derived_topic('planespotter-test', 'relay')}/raw?poll=1&since=latest", k["widget"])
        self.assertIn('const PLANESPOTTER = "http://192.168.1.5:8765"', k["widget"])
        email = kit_email(cfg, k)
        self.assertIn(k["cmd_url"], email.text)
        self.assertIn(k["family_topic"], email.text)
        self.assertEqual(email.attachments[0][0], "planespotter-widget.js")


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cfg = make_config(db_path=Path(cls.tmp.name) / "map.db", map_lan=False, weather=False, space_alerts=False,
                          adsbdb_api="http://127.0.0.1:9")
        store = Store(cfg.db_path)
        cls.t0 = time.time() - 3600
        pid, _ = store.record_sighting(plane("abc123", callsign="BAW123"), 1.0, 0, cls.t0, gap=60)
        store.append_path(pid, [[HOME[0], HOME[1], 3000, cls.t0 + 30]])
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
            return resp.read()

    def post(self, path, data):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def test_photo_matched_by_time(self):
        import base64
        jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 32).decode()
        r = self.post("/api/photo", {"taken_at": self.t0 + 60, "full": jpeg, "thumb": jpeg})
        self.assertEqual(r["matched"], "BAW123")
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/photo", {"taken_at": self.t0 - 5 * 3600, "full": jpeg, "thumb": jpeg})

    def test_flights_outlook_calendar(self):
        r = self.post("/api/myflights", {"flight": "DL 573", "day": "today"})
        self.assertEqual(r["flights"][0]["callsign"], "DAL573")
        self.assertEqual(self.post("/api/myflights", {"remove": "DAL573"})["flights"], [])
        self.assertIn("best", json.loads(self.get("/api/outlook")))
        self.assertTrue(self.get("/calendar.ics").startswith(b"BEGIN:VCALENDAR"))
        self.assertIn("objects", json.loads(self.get("/api/space")))
        self.assertEqual(json.loads(self.get("/api/friends"))["rows"], [])
        live = json.loads(self.get("/api/live"))
        self.assertEqual(live["transits"], [])
        with self.assertRaises(urllib.error.HTTPError):
            self.post("/api/travel", {"lat": 1, "lon": 2})  # travel mode needs `watch`


import urllib.error  # noqa: E402

if __name__ == "__main__":
    unittest.main()
