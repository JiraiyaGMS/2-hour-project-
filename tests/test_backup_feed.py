"""Live planes from adsb.fi when adsb.lol can't be reached."""

import unittest
import urllib.error
from unittest import mock

from planespotter import adsb
from planespotter.adsb import FastFeed, LiveFeed, SharedFeed

PLANE = {"hex": "abc123", "flight": "BAW923 ", "lat": 51.5, "lon": -0.1, "alt_baro": 3000, "gs": 200, "track": 270}


class BackupFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.asked: list[str] = []
        self.main: object = {"ac": [PLANE]}       # what adsb.lol answers (or the error it raises)
        self.backup: object = {"ac": [{**PLANE, "hex": "def456"}], "msg": "No error"}

        def get_json(url, timeout=15, headers=None):
            self.asked.append("backup" if "backup.test" in url else "main")
            answer = self.backup if "backup.test" in url else self.main
            if isinstance(answer, Exception):
                raise answer
            return answer

        FastFeed.forget()  # (no waiting between requests here, and nothing left over from another test)
        for patch in (mock.patch.object(adsb, "get_json", get_json), mock.patch.object(FastFeed, "MIN_GAP", 0.0)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(FastFeed.forget)

    def feed(self, backup="http://backup.test/api") -> LiveFeed:
        return LiveFeed("http://main.test", backup_url=backup, clock=lambda: self.now[0])

    def hexes(self, feed) -> list[str]:
        return [ac.hex for ac in feed.aircraft_near(51.5, -0.1, 20)]

    def test_adsb_lol_first_and_the_backup_only_when_it_fails(self):
        feed = self.feed()
        self.assertEqual((self.hexes(feed), feed.using_backup, self.asked), (["abc123"], False, ["main"]))
        self.main = urllib.error.URLError("connection reset")
        self.assertEqual((self.hexes(feed), feed.using_backup), (["def456"], True))
        # adsb.lol is left alone for a couple of minutes, then tried again.
        self.asked.clear()
        self.now[0] += 60
        self.hexes(feed)
        self.assertEqual(self.asked, ["backup"])
        self.main = {"ac": [PLANE]}
        self.now[0] += 120
        self.assertEqual((self.hexes(feed), feed.using_backup, self.asked[-1]), (["abc123"], False, "main"))

    def test_asked_too_often_or_trouble_at_its_end(self):
        feed = self.feed()
        self.main = urllib.error.HTTPError("http://main.test", 429, "Too Many Requests", {"Retry-After": "600"}, None)
        self.assertEqual(self.hexes(feed), ["def456"])
        self.assertEqual(feed.main_down_until, 1600.0)  # as long as it asked
        feed = self.feed()
        self.main = urllib.error.HTTPError("http://main.test", 503, "Unavailable", {}, None)
        self.assertEqual(self.hexes(feed), ["def456"])
        # Something wrong with what we asked (not the service's trouble) isn't papered over.
        self.main = urllib.error.HTTPError("http://main.test", 400, "Bad Request", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self.hexes(self.feed())

    def test_neither_answers_and_no_backup(self):
        self.main = urllib.error.HTTPError("http://main.test", 429, "Too Many Requests", {}, None)
        self.backup = urllib.error.URLError("offline")
        shared = SharedFeed(self.feed(), clock=lambda: self.now[0])
        with self.assertRaises(urllib.error.HTTPError):  # adsb.lol's own answer is what's reported...
            shared.aircraft_near(51.5, -0.1, 20)
        self.assertGreater(shared.paused_for(), 0)       # ...so the usual wait still applies
        self.backup = "<html>sign in to this Wi-Fi</html>"
        self.main = urllib.error.URLError("offline")
        with self.assertRaises(urllib.error.URLError):
            self.hexes(self.feed())
        with self.assertRaises(urllib.error.URLError):
            self.hexes(self.feed(backup=""))
        self.assertNotIn("backup", self.asked[-1:])


if __name__ == "__main__":
    unittest.main()
