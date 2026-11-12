import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from planespotter.adsb import FeedWithReceiver, ReceiverFeed
from planespotter.webmap import shared_feed
from planespotter.wizard import check_receiver

from helpers import HOME, FakeFeed, make_config, make_watcher, plane


class FakeReceiver:
    """A tiny web server answering like readsb's aircraft.json."""

    def __init__(self, aircraft):
        self.aircraft = aircraft
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"now": 1, "aircraft": outer.aircraft}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/data/aircraft.json"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


NEAR = {"hex": "a1b2c3", "flight": "SWA1  ", "lat": HOME[0] + 0.01, "lon": HOME[1], "alt_baro": 3000, "seen_pos": 1}
FAR = {"hex": "d4e5f6", "flight": "UAL2  ", "lat": HOME[0] + 2.0, "lon": HOME[1], "alt_baro": 30000, "seen_pos": 1}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.receiver = FakeReceiver([NEAR, FAR, {"hex": "ground", "lat": 1, "lon": 1, "alt_baro": "ground"}])
        self.addCleanup(self.receiver.close)

    def test_reads_aircraft_json_and_keeps_the_ones_nearby(self):
        planes = ReceiverFeed(self.receiver.url).aircraft_near(HOME[0], HOME[1], 20)
        self.assertEqual([p.callsign for p in planes], ["SWA1"])

    def test_falls_back_to_adsb_lol_when_the_receiver_is_down(self):
        backup = FakeFeed()
        backup.planes = [plane(callsign="BACKUP1")]
        backup.paused_for = lambda: 0
        feed = FeedWithReceiver(ReceiverFeed(self.receiver.url), backup)
        self.assertEqual(feed.aircraft_near(*HOME, 20)[0].callsign, "SWA1")
        self.assertTrue(feed.uses_receiver)
        self.receiver.close()
        self.assertEqual(feed.aircraft_near(*HOME, 20)[0].callsign, "BACKUP1")
        self.assertFalse(feed.uses_receiver)
        self.assertEqual(feed.latest()[0][0].callsign, "BACKUP1")

    def test_settings_and_setup(self):
        cfg = make_config(receiver_url=self.receiver.url)
        self.assertIsInstance(shared_feed(cfg), FeedWithReceiver)
        self.assertNotIsInstance(shared_feed(make_config()), FeedWithReceiver)
        self.assertIsNone(check_receiver(self.receiver.url))
        self.assertIsNone(check_receiver(""))
        self.assertIn("http://", check_receiver("raspberrypi.local"))
        self.assertIn("Couldn't read", check_receiver("http://127.0.0.1:9/aircraft.json"))

    def test_watcher_checks_at_full_speed_with_a_receiver(self):
        watcher, _, _ = make_watcher(receiver_url=self.receiver.url, poll_seconds=5)
        watcher.feed = FeedWithReceiver(ReceiverFeed(self.receiver.url), FakeFeed())
        new = watcher.poll(1000.0)
        self.assertEqual(len(new), 1)  # SWA1 is right over the zone
        self.assertTrue(watcher.feed.uses_receiver)


if __name__ == "__main__":
    unittest.main()
