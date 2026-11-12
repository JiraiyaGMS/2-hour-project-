import unittest

from planespotter.adsb import FlightInfo
from planespotter.notify import PushNotifier, new_topic

from helpers import FakeMailer, make_config, make_watcher, plane, ts


class FakePost:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def __call__(self, url, payload):
        if self.fail:
            raise OSError("no network")
        self.sent.append((url, payload))


class NotifierTests(unittest.TestCase):
    def test_topics_are_long_and_random(self):
        a, b = new_topic(), new_topic()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^planespotter-[a-z0-9]{14}$")

    def test_off_without_a_topic(self):
        post = FakePost()
        self.assertFalse(PushNotifier(make_config(), post=post).send("t", "m"))
        self.assertEqual(post.sent, [])

    def test_plane_over_home(self):
        cfg = make_config(ntfy_topic="planespotter-test")
        post = FakePost()
        watcher, feed, mailer = make_watcher(ntfy_topic="planespotter-test")
        watcher.notifier = PushNotifier(cfg, map_url="http://192.168.1.5:8765", post=post)
        feed.planes = [plane(callsign="DAL573")]
        t = ts(2026, 11, 1, 12)
        new = watcher.poll(t)
        watcher.store.set_info(new[0], FlightInfo(airline="Delta Air Lines", origin="FLL", destination="LAX",
                                                  photo_url="https://example.com/p.jpg"))
        self.assertTrue(watcher.send_alerts(new, t))
        url, payload = post.sent[0]
        self.assertEqual(url, "https://ntfy.sh/")
        self.assertEqual(payload["topic"], "planespotter-test")
        self.assertEqual(payload["title"], "✈ DAL573 over Test House")
        self.assertIn("FLL → LAX", payload["message"])
        self.assertEqual(payload["click"], f"http://192.168.1.5:8765/?flight={new[0]}")
        self.assertTrue(payload["icon"].endswith("/DAL.png"))
        self.assertEqual(payload["attach"], "https://example.com/p.jpg")
        self.assertEqual(len(mailer.sent), 1)  # the email still goes too

    def test_alert_counts_if_either_email_or_push_worked(self):
        watcher, feed, _ = make_watcher(ntfy_topic="planespotter-test")

        class BrokenMailer(FakeMailer):
            def send(self, email):
                raise OSError("smtp down")

        watcher.mailer = BrokenMailer()
        watcher.notifier = PushNotifier(watcher.cfg, post=FakePost())
        feed.planes = [plane()]
        t = ts(2026, 11, 1, 12)
        self.assertTrue(watcher.send_alerts(watcher.poll(t), t))

    def test_network_trouble_is_not_fatal(self):
        notifier = PushNotifier(make_config(ntfy_topic="x"), post=FakePost(fail=True))
        self.assertFalse(notifier.send("t", "m"))


if __name__ == "__main__":
    unittest.main()
