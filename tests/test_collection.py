import unittest

from planespotter.adsb import FlightInfo
from planespotter.collection import build_collection
from planespotter.countries import country_of, flag
from planespotter.store import Store

from helpers import make_config, plane, ts


class CountryTests(unittest.TestCase):
    def test_registration_prefixes(self):
        cases = {"N186DN": "US", "G-EUPT": "GB", "C-FIUA": "CA", "VH-OQA": "AU", "9V-SJD": "SG", "JA873J": "JP",
                 "HL7782": "KR", "B-18908": "TW", "B-1234": "CN", "B-KPA": "HK", "EI-DEO": "IE", "D-AIMA": "DE",
                 "A6-EDA": "AE", "XA-AMX": "MX", "PR-GXA": "BR", "VP-BJU": "BM", "ZS-SXA": "ZA", "Z-WPA": "ZW"}
        for reg, iso in cases.items():
            with self.subTest(reg=reg):
                self.assertEqual(country_of(reg)[0], iso)
        self.assertIsNone(country_of(""))
        self.assertIsNone(country_of("NOREG"))  # N followed by a letter isn't a US registration
        self.assertEqual(flag("US"), "🇺🇸")


class CollectionTests(unittest.TestCase):
    def test_collection_and_badges(self):
        cfg = make_config()
        store = Store(":memory:")
        planes = [("a1", "DAL1", "N1", "A320", 3000, 2.0, ts(2026, 11, 1, 5, 30)),
                  ("a2", "QFA11", "VH-OQA", "A388", 800, 0.2, ts(2026, 11, 1, 12)),
                  ("a3", "BAW1", "G-XLEA", "A388", 5000, 1.0, ts(2026, 11, 2, 2)),
                  ("a4", "N123AB", "N123AB", "C172", 2000, 1.5, ts(2026, 11, 2, 12))]
        for hex_code, cs, reg, typ, alt, miles, when in planes:
            pid, _ = store.record_sighting(plane(hex_code, callsign=cs, registration=reg, type_code=typ, alt=alt),
                                           miles, 0, when, gap=1800)
            store.set_info(pid, FlightInfo(airline={"DAL": "Delta", "QFA": "Qantas", "BAW": "British Airways"}.get(cs[:3], ""),
                                           aircraft_desc={"A388": "Airbus A380-800"}.get(typ, "")))
        store.log_special("ae1234", "military", ts(2026, 11, 2, 9))

        c = build_collection(store, cfg)
        self.assertEqual(c["totals"], {"planes": 4, "airlines": 3, "types": 3, "countries": 3, "badges": 7})
        self.assertEqual(c["types"][0], {"code": "A388", "name": "Airbus A380-800", "count": 2,
                                         "first_seen": ts(2026, 11, 1, 12)})
        self.assertEqual([x["code"] for x in c["countries"]], ["US", "AU", "GB"])
        self.assertEqual(c["hours"][5], 1)
        self.assertEqual(sum(c["weekdays"]), 4)
        earned = {b["key"] for b in c["badges"] if b["earned_at"]}
        self.assertEqual(earned, {"first", "a380", "low", "overhead", "night", "early", "military"})
        century = next(b for b in c["badges"] if b["key"] == "century")
        self.assertEqual(century["progress"], "4 / 100")

        # Badges stay earned even when the record behind them is tidied away.
        store.db.execute("DELETE FROM special_seen")
        self.assertIn("military", {b["key"] for b in build_collection(store, cfg)["badges"] if b["earned_at"]})


if __name__ == "__main__":
    unittest.main()
