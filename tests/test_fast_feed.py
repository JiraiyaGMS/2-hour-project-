"""Between adsb.lol's rationed checks, the planes come from adsb.fi: seconds old instead of minutes."""

import unittest
import urllib.error
from unittest import mock

from planespotter import adsb
from planespotter.adsb import FastFeed, FeedBusy, LiveFeed, PositionLookup, SharedFeed
from planespotter.cluster import ClusterFeed
from planespotter.track import Sky, where_now

from helpers import make_watcher, plane, ts


def raw(hex_code="abc123", lat=51.5, lon=-0.1, seen_pos=0.0, **more) -> dict:
    return {"hex": hex_code, "flight": "BAW923 ", "lat": lat, "lon": lon, "alt_baro": 3000, "gs": 200, "track": 270,
            "seen_pos": seen_pos, **more}


class Net:
    """What the two services answer, and what was asked of them."""

    def __init__(self, case: unittest.TestCase):
        self.main: object = {"ac": []}
        self.fast: object = {"ac": []}
        self.asked: list[str] = []
        self.now = [1000.0]
        self.slept: list[float] = []
        FastFeed.forget()
        from planespotter import track
        for module in (adsb, track):  # (track has its own name for the same function)
            patch = mock.patch.object(module, "get_json", self.get_json)
            patch.start()
            case.addCleanup(patch.stop)
        case.addCleanup(FastFeed.forget)

    def get_json(self, url, timeout=15, headers=None):
        fast = "fast.test" in url
        self.asked.append(("fast" if fast else "main") + url.split(".test", 1)[1])
        answer = self.fast if fast else self.main
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fast_feed(self) -> FastFeed:
        return FastFeed("http://fast.test/api", monotonic=lambda: self.now[0], sleep=self.slept.append)

    def shared(self, **kw) -> SharedFeed:
        return SharedFeed(LiveFeed("http://main.test"), clock=lambda: self.now[0], fast=self.fast_feed(), **kw)


class PaceTests(unittest.TestCase):
    def setUp(self):
        self.net = Net(self)

    def test_everything_that_asks_shares_one_pace(self):
        a, b = self.net.fast_feed(), self.net.fast_feed()
        a.aircraft_near(51.5, -0.1, 20)
        b.by_hex(["abc123"])                      # another part of the program, a moment later: waits its turn
        self.assertEqual(self.net.slept, [FastFeed.MIN_GAP])
        self.assertEqual(self.net.asked, ["fast/api/v3/lat/51.50000/lon/-0.10000/dist/20", "fast/api/v2/hex/abc123"])
        with self.assertRaises(FeedBusy):         # a third straight away would queue too long: skipped
            a.aircraft_near(51.5, -0.1, 20)
        self.assertEqual(len(self.net.asked), 2)
        self.net.now[0] += 10
        a.aircraft_near(51.5, -0.1, 20)
        self.assertEqual(len(self.net.asked), 3)

    def test_left_alone_after_too_many_requests_or_no_answer(self):
        feed = self.net.fast_feed()
        self.net.fast = urllib.error.HTTPError("http://fast.test", 429, "Too Many Requests", {"Retry-After": "120"}, None)
        with self.assertRaises(urllib.error.HTTPError):
            feed.aircraft_near(51.5, -0.1, 20)
        self.assertTrue(feed.resting())
        self.net.fast = {"ac": [raw()]}
        self.net.now[0] += 60
        with self.assertRaises(FeedBusy):         # it asked for two minutes
            feed.aircraft_near(51.5, -0.1, 20)
        self.assertEqual(len(self.net.asked), 1)
        self.net.now[0] += 61
        self.assertEqual([ac.hex for ac in feed.aircraft_near(51.5, -0.1, 20)], ["abc123"])
        self.net.fast = "<html>sign in to this Wi-Fi</html>"
        self.net.now[0] += 5
        with self.assertRaises(ValueError):
            feed.aircraft_near(51.5, -0.1, 20)
        self.assertTrue(feed.resting())


class BlendTests(unittest.TestCase):
    def setUp(self):
        self.net = Net(self)

    def at(self, planes) -> dict:
        return {ac.hex: (round(ac.lat, 2), ac.seen_pos) for ac in planes}

    def test_each_plane_where_it_was_last_seen_by_either(self):
        net, shared = self.net, self.net.shared()
        net.main = {"ac": [raw("aaa111", lat=51.0), raw("bbb222", lat=52.0, seen_pos=3.0)]}
        self.assertEqual(self.at(shared.aircraft_near(51.5, -0.1, 20)), {"aaa111": (51.0, 0.0), "bbb222": (52.0, 3.0)})
        # Ten seconds on, the fast feed: it has the first one further along, and one adsb.lol didn't have.
        net.now[0] += 10
        net.fast = {"ac": [raw("aaa111", lat=51.1, seen_pos=1.0), raw("ccc333", lat=53.0)]}
        blended = shared.aircraft_fast(51.5, -0.1, 20, every=9)
        self.assertEqual(self.at(blended), {"aaa111": (51.1, 1.0),    # the newer position
                                            "bbb222": (52.0, 13.0),   # only adsb.lol sees it: as it was, and older
                                            "ccc333": (53.0, 0.0)})
        self.assertEqual(self.at(shared.latest()[0]), self.at(blended))
        self.assertEqual(shared.latest()[1], 1010.0)
        # Asked again within the gap (another account in the same group): the same answer, no new request.
        net.now[0] += 4
        asked = len(net.asked)
        self.assertEqual(self.at(shared.aircraft_fast(51.5, -0.1, 20, every=9))["aaa111"], (51.1, 5.0))
        self.assertEqual(len(net.asked), asked)

    def test_a_plane_the_fast_feed_was_following_that_has_gone_is_gone(self):
        net, shared = self.net, self.net.shared()
        net.main = {"ac": [raw("aaa111"), raw("bbb222")]}
        shared.aircraft_near(51.5, -0.1, 20)
        net.now[0] += 10
        net.fast = {"ac": [raw("aaa111"), raw("bbb222")]}
        shared.aircraft_fast(51.5, -0.1, 20, every=9)
        net.now[0] += 10
        net.fast = {"ac": [raw("aaa111")]}        # bbb222 has flown out of range
        self.assertEqual(sorted(self.at(shared.aircraft_fast(51.5, -0.1, 20, every=9))), ["aaa111"])

    def test_without_a_fast_feed_or_when_it_cannot_be_asked(self):
        net = self.net
        plain = SharedFeed(LiveFeed("http://main.test"), clock=lambda: net.now[0])
        self.assertIsNone(plain.aircraft_fast(51.5, -0.1, 20))
        shared = net.shared()
        net.main = {"ac": [raw("aaa111")]}
        shared.aircraft_near(51.5, -0.1, 20)
        net.fast = urllib.error.URLError("offline")
        self.assertIsNone(shared.aircraft_fast(51.5, -0.1, 20))
        self.assertEqual(sorted(self.at(shared.latest()[0])), ["aaa111"])   # adsb.lol's answer still stands
        # The fast feed's answer only counts while it's recent.
        net.fast = {"ac": [raw("ccc333")]}
        net.now[0] += 120
        shared.aircraft_fast(51.5, -0.1, 20)
        self.assertEqual(sorted(self.at(shared.latest()[0])), ["aaa111", "ccc333"])
        net.now[0] += SharedFeed.FAST_KEEP + 1
        self.assertEqual(sorted(self.at(shared.latest()[0])), ["aaa111"])

    def test_accounts_in_a_group_share_the_fast_answer(self):
        net = self.net
        cluster = ClusterFeed(LiveFeed("http://main.test"), [(51.5, -0.1, 30.0), (51.6, -0.2, 30.0)],
                              clock=lambda: net.now[0], fast=net.fast_feed())
        mine, theirs = cluster.view(51.5, -0.1, 30.0), cluster.view(51.6, -0.2, 30.0)
        net.main = {"ac": [raw("aaa111")]}
        mine.aircraft_near(51.5, -0.1, 30.0)
        net.fast = {"ac": [raw("aaa111", lat=51.52), raw("far999", lat=60.0)]}
        net.now[0] += 10
        self.assertEqual([ac.hex for ac in mine.aircraft_fast(51.5, -0.1, 30.0, every=9)], ["aaa111"])  # near me only
        asked = len(net.asked)
        self.assertEqual([round(ac.lat, 2) for ac in theirs.aircraft_fast(51.6, -0.2, 30.0, every=9)], [51.52])
        self.assertEqual(len(net.asked), asked)   # one request for both


class FarAwayTests(unittest.TestCase):
    def setUp(self):
        self.net = Net(self)

    def test_positions_from_the_fast_feed_and_adsb_lol_only_for_what_it_cannot_see(self):
        net = self.net
        lookup = PositionLookup("http://main.test", clock=lambda: net.now[0], fast=net.fast_feed())
        net.fast = {"ac": [raw("aaa111", lat=40.0, seen_pos=2.0), raw("bbb222", alt_baro="ground")]}
        net.main = {"ac": [raw("ccc333", lat=45.0)]}
        got = lookup.get(["aaa111", "bbb222", "ccc333"])
        self.assertEqual(sorted(got), ["aaa111", "ccc333"])
        self.assertEqual(lookup.on_ground(["aaa111", "bbb222"]), {"bbb222"})
        self.assertEqual(net.asked, ["fast/api/v2/hex/aaa111,bbb222,ccc333", "main/v2/hex/ccc333"])  # only the one it missed
        # A few seconds later: nothing asked, and the answers say they're that much older.
        net.now[0] += 5
        self.assertEqual(lookup.get(["aaa111"])["aaa111"].seen_pos, 7.0)
        self.assertEqual(len(net.asked), 2)
        # After the fast gap: the fast feed again (adsb.lol not until its own, longer, minute is up).
        net.now[0] += PositionLookup.FAST_TTL
        net.fast = {"ac": [raw("aaa111", lat=40.2)]}
        self.assertEqual(round(lookup.get(["aaa111", "ccc333"])["aaa111"].lat, 1), 40.2)
        self.assertEqual(net.asked[2:], ["fast/api/v2/hex/aaa111,ccc333"])

    def test_a_flight_being_followed(self):
        net = self.net
        sky = Sky("http://main.test", clock=lambda: net.now[0], fast=net.fast_feed())
        net.fast = {"ac": [raw("abc123", lat=40.0)]}
        where = where_now("BAW923", "abc123", sky, ttl=60)
        self.assertEqual((round(where["flying"].lat, 1), where["at"]), (40.0, 1000.0))
        self.assertEqual(net.asked, ["fast/api/v2/hex/abc123"])   # none of adsb.lol's allowance used
        net.now[0] += Sky.FAST_TTL + 1
        net.fast = {"ac": [raw("abc123", lat=40.3)]}
        self.assertEqual(round(where_now("BAW923", "abc123", sky, ttl=60)["flying"].lat, 1), 40.3)
        # The fast feed doesn't see it: adsb.lol is asked, as before.
        net.now[0] += Sky.FAST_TTL + 1
        net.fast, net.main = {"ac": []}, {"ac": [raw("abc123", lat=41.0)]}
        self.assertEqual(round(where_now("BAW923", "abc123", sky, ttl=60)["flying"].lat, 1), 41.0)
        self.assertEqual(net.asked[-1], "main/v2/hex/abc123")


class WatcherTests(unittest.TestCase):
    def test_a_plane_caught_between_adsb_lols_turns(self):
        watcher, feed, mailer = make_watcher()
        t = ts(2026, 11, 1, 12)
        feed.planes = []
        feed.fast = None                          # what the fast feed has, when asked
        feed.aircraft_fast = lambda lat, lon, radius, every=10.0: feed.fast
        self.assertEqual(watcher.poll(t), [])
        self.assertEqual(watcher.poll(t + 10, fast=True), [])         # it can't be asked just now: nothing happens
        feed.fast = [plane(callsign="DAL573")]                       # over the house, 20 s after adsb.lol's turn
        new = watcher.poll(t + 20, fast=True)
        self.assertEqual(len(new), 1)
        watcher.send_alerts(new, t + 20)
        self.assertEqual(len(mailer.sent), 1)
        # The same answer again (nothing newer about it): not a second sighting in the same place.
        from dataclasses import replace
        feed.fast = [replace(feed.fast[0], seen_pos=10.0)]
        self.assertEqual(watcher.poll(t + 30, fast=True), [])
        import json
        row = watcher.store.get_pass(new[0])
        self.assertEqual(len(json.loads(row["path"])), 1)
        self.assertEqual(row["last_seen"], t + 20)
        # It moves on: one more point on its path, stamped with when it was really there.
        feed.fast = [replace(feed.fast[0], lat=feed.fast[0].lat + 0.01, seen_pos=4.0)]
        watcher.poll(t + 40, fast=True)
        path = json.loads(watcher.store.get_pass(new[0])["path"])
        self.assertEqual([p[3] for p in path], [t + 20, t + 36])


if __name__ == "__main__":
    unittest.main()
