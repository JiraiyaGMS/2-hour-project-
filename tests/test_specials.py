import unittest
from dataclasses import replace

from planespotter.adsb import parse_aircraft
from planespotter.notify import PushNotifier, derived_topic
from planespotter.specials import special_reasons

from helpers import HOME, make_config, make_watcher, plane, ts


class FakePost:
    def __init__(self):
        self.sent = []

    def __call__(self, url, payload):
        self.sent.append(payload)


class ReasonTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def test_parses_the_special_fields(self):
        ac = parse_aircraft({"hex": "ae1234", "lat": 1, "lon": 1, "alt_baro": 9000, "squawk": "7700",
                             "category": "A7", "dbFlags": 1, "emergency": "general", "t": "H60"})
        self.assertEqual((ac.category, ac.db_flags, ac.emergency, ac.squawk), ("A7", 1, "general", "7700"))

    def test_each_kind(self):
        keys = lambda ac, d=1.0: [r[0] for r in special_reasons(ac, d, self.cfg)]  # noqa: E731
        self.assertEqual(keys(plane(type_code="A320")), [])
        self.assertEqual(keys(replace(plane(), squawk="7700")), ["emergency"])
        self.assertEqual(keys(replace(plane(), db_flags=1)), ["military"])
        self.assertEqual(keys(plane(type_code="A388")), ["type"])
        self.assertEqual(keys(replace(plane(), category="A7")), ["helicopter"])
        self.assertEqual(keys(replace(plane(), category="A7"), d=10.0), [])  # helicopters only over the zone
        self.assertTrue(special_reasons(replace(plane(), squawk="7700"), 1.0, self.cfg)[0][2])  # an emergency

    def test_can_be_turned_off(self):
        self.assertEqual(special_reasons(plane(type_code="A388"), 1.0, make_config(special_alerts=False)), [])


class WatcherSpecialTests(unittest.TestCase):
    def test_announced_once_with_email_and_push(self):
        watcher, feed, mailer = make_watcher(ntfy_topic="planespotter-test")
        post = FakePost()
        watcher.notifier = PushNotifier(watcher.cfg, post=post)
        # An A380 ten miles away: not over the zone, but worth looking up for.
        feed.planes = [plane("7c4920", callsign="QFA11", type_code="A388", lat=HOME[0] + 0.145)]
        t = ts(2026, 11, 1, 12)
        for i in range(3):
            watcher.poll(t + i * 30)
            specials, watcher.new_specials = watcher.new_specials, []
            watcher.send_specials(specials, t + i * 30)
        self.assertEqual(len(mailer.sent), 1)
        self.assertIn("Airbus A380", mailer.sent[0].subject)
        self.assertIn("✨ Airbus A380", mailer.sent[0].html)
        self.assertEqual([m["topic"] for m in post.sent], ["planespotter-test", derived_topic("planespotter-test", "family")])
        self.assertEqual(post.sent[0]["priority"], 4)  # and once to your family's feed

    def test_emergencies_are_urgent(self):
        watcher, feed, mailer = make_watcher(ntfy_topic="planespotter-test")
        post = FakePost()
        watcher.notifier = PushNotifier(watcher.cfg, post=post)
        feed.planes = [replace(plane(callsign="UAL123", lat=HOME[0] + 0.1), squawk="7700")]
        t = ts(2026, 11, 1, 12)
        watcher.poll(t)
        watcher.send_specials(watcher.new_specials, t)
        self.assertTrue(mailer.sent[0].subject.startswith("🚨 Emergency (7700): UAL123"))
        self.assertEqual(post.sent[0]["priority"], 5)

    def test_quiet_hours_are_respected(self):
        from datetime import time
        watcher, feed, mailer = make_watcher(quiet_hours=(time(22), time(7)))
        feed.planes = [plane(type_code="A388", lat=HOME[0] + 0.1)]
        t = ts(2026, 11, 1, 23)
        watcher.poll(t)
        self.assertFalse(watcher.send_specials(watcher.new_specials, t))
        self.assertEqual(mailer.sent, [])


class FirstsTests(unittest.TestCase):
    def test_first_airline_and_type_after_a_few_days(self):
        watcher, feed, mailer = make_watcher()
        t = ts(2026, 11, 10, 12)
        watcher.store.set_meta("watch_started", str(t - 5 * 86400))
        feed.planes = [plane("aaa001", callsign="DAL1", type_code="A320")]
        watcher.poll(t - 3600)  # an earlier Delta A320
        feed.planes = [plane("bbb002", callsign="QFA11", type_code="A388")]
        new = watcher.poll(t)
        watcher.send_alerts(new, t)
        html = mailer.sent[-1].html
        self.assertIn("✨ First Test Air flight you&#x27;ve spotted", html)
        self.assertIn("✨ First A388 you&#x27;ve spotted", html)
        feed.planes = [plane("ccc003", callsign="DAL2", type_code="A320")]
        new = watcher.poll(t + 7200)
        self.assertEqual(watcher.store.get_pass(new[0])["firsts"], "[]")  # seen both before

    def test_no_firsts_in_the_first_days(self):
        watcher, feed, mailer = make_watcher()
        t = ts(2026, 11, 10, 12)
        watcher.store.set_meta("watch_started", str(t - 3600))
        feed.planes = [plane(callsign="QFA11", type_code="A388")]
        new = watcher.poll(t)
        self.assertEqual(watcher.store.get_pass(new[0])["firsts"], "[]")


if __name__ == "__main__":
    unittest.main()
