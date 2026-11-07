import io
import os
import stat
import unittest
from contextlib import redirect_stdout
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

from planespotter.config import Config
from planespotter.wizard import Cancelled, Wizard


def scripted(answers):
    """A fake input() that replays answers and records the prompts it was shown."""
    answers = list(answers)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)

    ask.prompts = prompts
    return ask


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        quiet = redirect_stdout(io.StringIO())  # the wizard's explanations
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_wizard(self, answers, secret="abcd efgh ijkl mnop"):
        wizard = Wizard(self.env, ask=scripted(answers), ask_secret=lambda prompt: secret)
        wizard.save(wizard.run())
        return Config.load(self.env)

    def test_full_setup_with_gmail(self):
        cfg = self.run_wizard([
            "51.50071, -0.12462",   # pasted from Google Maps
            "My House",
            "2",
            "10,000",
            "10pm-7am",
            "",                      # keep the 07:00 report
            "me@gmail.com",
            "",                      # send from the same account
        ])
        self.assertEqual((cfg.home_lat, cfg.home_lon), (51.50071, -0.12462))
        self.assertEqual(cfg.home_name, "My House")
        self.assertEqual(cfg.radius_miles, 2)
        self.assertEqual(cfg.max_altitude_ft, 10000)
        self.assertEqual(cfg.quiet_hours, (time(22), time(7)))
        self.assertEqual(cfg.digest_time, time(7))
        self.assertEqual(cfg.smtp_host, "smtp.gmail.com")
        self.assertEqual(cfg.smtp_user, "me@gmail.com")
        self.assertEqual(cfg.smtp_password, "abcdefghijklmnop")
        self.assertFalse(cfg.dry_run)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_defaults_and_no_email(self):
        cfg = self.run_wizard(["40.7, -74.0", "", "", "", "", "", ""])
        self.assertEqual(cfg.home_name, "Home")
        self.assertEqual(cfg.radius_miles, 3)
        self.assertIsNone(cfg.max_altitude_ft)
        self.assertEqual(cfg.quiet_hours, (time(22), time(7)))
        self.assertTrue(cfg.dry_run)

    def test_bad_answers_are_asked_again(self):
        ask = scripted(["my house", "51.5 -0.1", "", "zero", "5", "", "late", "none", "", "nope", ""])
        wizard = Wizard(self.env, ask=ask, ask_secret=lambda p: "")
        wizard.save(wizard.run())
        cfg = Config.load(self.env)
        self.assertEqual((cfg.home_lat, cfg.home_lon), (51.5, -0.1))
        self.assertEqual(cfg.radius_miles, 5)
        self.assertIsNone(cfg.quiet_hours)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(sum("Coordinates" in p for p in ask.prompts), 2)

    def test_rerunning_keeps_existing_answers_and_extra_settings(self):
        self.run_wizard(["51.5, -0.1", "Flat", "4", "", "", "08:30", "me@icloud.com", ""])
        with self.env.open("a") as fh:
            fh.write("POLL_SECONDS=45\n")
        again = Wizard(self.env, ask=scripted([""] * 8), ask_secret=lambda p: "")
        again.save(again.run())
        cfg = Config.load(self.env)
        self.assertEqual(cfg.home_name, "Flat")
        self.assertEqual(cfg.radius_miles, 4)
        self.assertEqual(cfg.digest_time, time(8, 30))
        self.assertEqual(cfg.smtp_host, "smtp.mail.me.com")
        self.assertEqual(cfg.smtp_password, "abcdefghijklmnop")  # kept the saved one
        self.assertEqual(cfg.poll_seconds, 45)

    def test_ctrl_d_cancels_without_writing(self):
        wizard = Wizard(self.env, ask=scripted([]), ask_secret=lambda p: "")
        with self.assertRaises(Cancelled):
            wizard.run()
        self.assertFalse(self.env.exists())


if __name__ == "__main__":
    unittest.main()
