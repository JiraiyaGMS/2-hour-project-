"""Which route a plane is on: two sources, every leg, and what the plane itself is doing."""

import unittest
from unittest import mock

from planespotter import adsb
from planespotter.adsb import Aircraft, Lookup
from planespotter.airports import airports
from planespotter.routes import (ADSBDB, STANDING, Leg, Place, arriving_at, callsign_spellings, choose, departing_from,
                                 fit, legs_from_adsbdb, legs_from_standing, place_from_airport)
from planespotter.store import Store


def ap(code: str) -> Place:
    return place_from_airport(airports().get(code))


def plane(lat, lon, alt=35000, track=None, vr=0.0, callsign="TST1") -> Aircraft:
    return Aircraft(hex="abc123", callsign=callsign, registration="", type_code="", lat=lat, lon=lon, alt_ft=alt,
                    speed_kt=450.0, track=track, vr=vr)


def standing(*codes) -> dict:
    """adsb.lol's standing data for a route through these airports."""
    stops = [airports().get(c) for c in codes]
    return {"callsign": "TST1", "airline_code": "DAL", "airport_codes": "-".join(a.icao for a in stops),
            "_airports": [{"icao": a.icao, "iata": a.code, "location": a.city, "lat": a.lat, "lon": a.lon,
                           "alt_feet": a.elevation_ft} for a in stops]}


def adsbdb(origin, destination, airline="Delta Air Lines") -> dict:
    def end(code):
        a = airports().get(code)
        return {"iata_code": a.code, "icao_code": a.icao, "municipality": a.city, "latitude": a.lat,
                "longitude": a.lon, "elevation": a.elevation_ft}
    return {"flightroute": {"airline": {"name": airline}, "origin": end(origin), "destination": end(destination)}}


# Halfway from Seattle to Oakland, over southern Oregon.
MID_SEA_OAK = dict(lat=42.6, lon=-122.3)


class LegsTests(unittest.TestCase):
    def test_a_route_with_stops_is_one_leg_per_hop(self):
        legs = legs_from_standing(standing("SEA", "SNA", "SEA"))
        self.assertEqual([(l.origin.code, l.destination.code) for l in legs], [("SEA", "SNA"), ("SNA", "SEA")])
        self.assertEqual({l.source for l in legs}, {STANDING})
        self.assertEqual(legs[0].airline, "Delta Air Lines")
        self.assertEqual(legs[0].origin.city, "Seattle")

    def test_adsbdb_and_things_that_are_not_routes(self):
        legs = legs_from_adsbdb(adsbdb("LHR", "JFK"))
        self.assertEqual([(l.origin.code, l.destination.code, l.source) for l in legs], [("LHR", "JFK", ADSBDB)])
        self.assertEqual(legs_from_adsbdb(None), [])
        self.assertEqual(legs_from_adsbdb({"flightroute": {"origin": {}, "destination": None}}), [])
        self.assertEqual(legs_from_standing({"callsign": "X"}), [])
        self.assertEqual(legs_from_standing("<html>"), [])

    def test_callsigns_are_listed_without_zeros_in_front(self):
        self.assertEqual(callsign_spellings("CAL011"), ["CAL011", "CAL11"])
        self.assertEqual(callsign_spellings("DAL123"), ["DAL123"])
        self.assertEqual(callsign_spellings(""), [])


class FitTests(unittest.TestCase):
    def test_on_the_way_and_pointing_there(self):
        leg = Leg(ap("SEA"), ap("OAK"), STANDING)
        self.assertIsNotNone(fit(plane(**MID_SEA_OAK, track=180), leg))
        # The same spot, heading north: that's the flight back.
        self.assertIsNone(fit(plane(**MID_SEA_OAK, track=0), leg))
        self.assertIsNotNone(fit(plane(**MID_SEA_OAK, track=0), Leg(ap("OAK"), ap("SEA"), STANDING)))

    def test_hundreds_of_miles_off_the_line_is_another_flight(self):
        # Over Lake Michigan heading east-south-east (Minneapolis to Cleveland), not Portland to Atlanta.
        self.assertIsNone(fit(plane(43.5, -87.5, track=110), Leg(ap("PDX"), ap("ATL"), ADSBDB)))

    def test_cruising_right_over_an_airport_is_not_landing_there(self):
        tls = ap("TLS")
        over_toulouse = plane(tls.lat + 0.1, tls.lon, alt=38000, track=30)  # Malaga to Frankfurt passes overhead
        self.assertIsNone(fit(over_toulouse, Leg(ap("MUC"), tls, ADSBDB)))
        self.assertIsNotNone(fit(over_toulouse, Leg(ap("AGP"), ap("FRA"), STANDING)))

    def test_low_and_climbing_is_near_where_it_started(self):
        sea = ap("SEA")
        climbing_out = plane(sea.lat - 0.15, sea.lon, alt=6000, track=180, vr=2500)
        self.assertIsNotNone(fit(climbing_out, Leg(sea, ap("OAK"), STANDING)))
        self.assertIsNone(fit(climbing_out, Leg(ap("OAK"), sea, STANDING)))  # it isn't arriving
        self.assertIsNone(fit(climbing_out, Leg(ap("SNA"), ap("OAK"), ADSBDB)))  # nor did it leave from there

    def test_low_and_coming_down_is_near_where_it_is_going(self):
        oak = ap("OAK")
        coming_in = plane(oak.lat + 0.2, oak.lon, alt=5000, track=180, vr=-1200)
        self.assertIsNotNone(fit(coming_in, Leg(ap("SEA"), oak, STANDING)))
        self.assertIsNone(fit(coming_in, Leg(oak, ap("SEA"), STANDING)))

    def test_a_route_without_positions_cannot_be_checked(self):
        self.assertIsNone(fit(plane(**MID_SEA_OAK), Leg(Place("XXX"), Place("YYY"), ADSBDB)))


class ChooseTests(unittest.TestCase):
    def test_the_leg_it_is_flying_out_of_several(self):
        legs = legs_from_standing(standing("SEA", "OAK", "SEA"))
        south = choose(plane(**MID_SEA_OAK, track=180), legs)
        north = choose(plane(**MID_SEA_OAK, track=0), legs)
        self.assertEqual((south.origin.code, south.destination.code), ("SEA", "OAK"))
        self.assertEqual((north.origin.code, north.destination.code), ("OAK", "SEA"))
        self.assertEqual(south.basis, "fits")
        self.assertFalse(south.sure)

    def test_the_standing_data_beats_adsbdb_and_agreement_beats_both(self):
        p = plane(**MID_SEA_OAK, track=180)
        both = legs_from_standing(standing("SEA", "OAK")) + legs_from_adsbdb(adsbdb("SNA", "OAK"))
        self.assertIsNone(fit(p, both[1]))  # adsbdb's guess doesn't fit anyway
        got = choose(p, both)
        self.assertEqual((got.origin.code, got.source, got.basis), ("SEA", STANDING, "fits"))
        agreed = choose(p, legs_from_standing(standing("SEA", "OAK")) + legs_from_adsbdb(adsbdb("SEA", "OAK")))
        self.assertEqual(agreed.basis, "agree")
        self.assertTrue(agreed.sure)

    def test_no_answer_is_better_than_a_wrong_one(self):
        got = choose(plane(**MID_SEA_OAK, track=180), legs_from_adsbdb(adsbdb("SNA", "OAK")))
        self.assertIsNone(got.origin)
        self.assertIsNone(got.destination)
        self.assertEqual(got.airline, "Delta Air Lines")  # who flies it is still known

    def test_its_track_overrules_the_databases(self):
        p = plane(**MID_SEA_OAK, track=180)
        legs = legs_from_standing(standing("SEA", "OAK")) + legs_from_adsbdb(adsbdb("PDX", "OAK"))
        # Its recorded track left the ground at Portland: the leg from there is the one.
        got = choose(p, legs, took_off_from=ap("PDX"))
        self.assertEqual((got.origin.code, got.destination.code, got.basis), ("PDX", "OAK", "track"))
        # No listed leg starts where it really took off: the origin is still known, the destination isn't.
        got = choose(p, legs_from_standing(standing("SEA", "OAK")), took_off_from=ap("LAS"))
        self.assertEqual((got.origin.code, got.destination, got.basis), ("LAS", None, "track"))

    def test_what_the_plane_is_plainly_doing(self):
        lhr = airports().get("LHR")
        # Six miles east of Heathrow at 2,000 ft, coming down, pointing at it.
        landing = plane(lhr.lat, lhr.lon + 0.14, alt=2000, track=270, vr=-750)
        self.assertEqual(arriving_at(landing).code, "LHR")
        self.assertIsNone(departing_from(landing))
        got = choose(landing, legs_from_adsbdb(adsbdb("JFK", "CDG")))  # a route for some other day
        self.assertEqual((got.origin, got.destination.code, got.basis), (None, "LHR", "shows"))
        self.assertTrue(got.sure)
        # Two miles west of it at 1,500 ft, climbing away.
        leaving = plane(lhr.lat, lhr.lon - 0.05, alt=1500, track=270, vr=2500)
        self.assertEqual(departing_from(leaving).code, "LHR")
        self.assertIsNone(arriving_at(leaving))
        # Up at 36,000 ft over it: neither.
        over = plane(lhr.lat, lhr.lon, alt=36000, track=270, vr=0)
        self.assertEqual((arriving_at(over), departing_from(over)), (None, None))


    def test_an_earlier_flights_origin_is_dropped_once_it_is_plainly_leaving_somewhere_else(self):
        # The same aircraft came in from Hannover an hour ago; now it's climbing out of Heathrow.
        lhr = airports().get("LHR")
        leaving = plane(lhr.lat, lhr.lon - 0.05, alt=1500, track=270, vr=2500)
        got = choose(leaving, legs_from_standing(standing("LHR", "DUB")), took_off_from=ap("HAJ"))
        self.assertEqual((got.origin.code, got.destination.code, got.basis), ("LHR", "DUB", "shows"))

    def test_nothing_is_read_into_a_private_plane_near_a_big_airport(self):
        # A light aircraft low and coming down, pointing at Heathrow: it may be bound for a field just short of it.
        lhr = airports().get("LHR")
        small = plane(lhr.lat, lhr.lon + 0.14, alt=2000, track=270, vr=-750, callsign="GABCD")
        self.assertEqual(choose(small, [], airliner=False).destination, None)
        lookup = Lookup(Store(":memory:"), "", routes_url="")  # no sources: only what the plane shows
        self.assertEqual(lookup.info(small, now=1000.0).destination, "")
        airline = plane(lhr.lat, lhr.lon + 0.14, alt=2000, track=270, vr=-750, callsign="BAW923")
        info = lookup.info(airline, now=1000.0)
        self.assertEqual((info.destination, info.destination_city, info.route_basis, info.airline),
                         ("LHR", "London", "shows", "British Airways"))


class LookupTests(unittest.TestCase):
    def lookup(self, answers: dict) -> tuple[Lookup, list]:
        asked = []

        def get_json(url, timeout=15, headers=None):
            asked.append(url)
            for part, body in answers.items():
                if part in url:
                    if body is None:
                        raise adsb.urllib.error.HTTPError(url, 404, "Not Found", None, None)
                    return body
            raise adsb.urllib.error.URLError("no connection")

        patch = mock.patch.object(adsb, "get_json", get_json)
        patch.start()
        self.addCleanup(patch.stop)
        return Lookup(Store(":memory:"), "http://adsbdb.test", routes_url="http://routes.test"), asked

    def test_both_sources_are_asked_and_remembered(self):
        lookup, asked = self.lookup({"/routes/TS/TST1.json": standing("SEA", "OAK"),
                                     "/v0/callsign/TST1": {"response": adsbdb("SNA", "OAK")},
                                     "/v0/aircraft/": {"response": {"aircraft": {"manufacturer": "Boeing", "type": "737-800",
                                                                               "registered_owner": "Test Air"}}}})
        info = lookup.info(plane(**MID_SEA_OAK, track=180), now=1000.0)
        self.assertEqual((info.origin, info.origin_city, info.destination, info.route_basis),
                         ("SEA", "Seattle", "OAK", "fits"))
        self.assertEqual((info.airline, info.aircraft_desc), ("Delta Air Lines", "Boeing 737-800"))
        self.assertEqual(len(asked), 3)
        self.assertEqual(sorted(u.split("/")[2] for u in asked[:2]), ["adsbdb.test", "routes.test"])  # both lists
        lookup.info(plane(**MID_SEA_OAK, track=180), now=1100.0)
        self.assertEqual(len(asked), 3)  # kept in the database: not asked again
        # The same answers, a plane heading the other way: nothing fits, so no route (but still the airline).
        back = lookup.info(plane(**MID_SEA_OAK, track=0), now=1200.0)
        self.assertEqual((back.origin, back.destination, back.airline), ("", "", "Delta Air Lines"))

    def test_a_callsign_nobody_lists_and_no_connection(self):
        lookup, asked = self.lookup({"/routes/": None, "/v0/callsign/": None, "/v0/aircraft/": None})
        info = lookup.info(plane(**MID_SEA_OAK, track=180, callsign="EZY012"), now=1000.0)
        self.assertEqual((info.origin, info.destination, info.airline), ("", "", "easyJet"))  # named from the callsign
        self.assertTrue(any("/routes/EZ/EZY12.json" in url for url in asked))  # tried without the zero too
        before = len(asked)
        lookup.info(plane(**MID_SEA_OAK, track=180, callsign="EZY012"), now=1100.0)
        self.assertEqual(len(asked), before)  # "not listed" is remembered for a while as well
        offline, _ = self.lookup({})
        self.assertEqual(offline.info(plane(**MID_SEA_OAK, track=180), now=1000.0).origin, "")

    def test_the_real_origin_picks_the_leg(self):
        lookup, _ = self.lookup({"/routes/TS/TST1.json": standing("SEA", "OAK", "SEA", "PDX"),
                                 "/v0/callsign/TST1": None, "/v0/aircraft/": None})
        info = lookup.info(plane(**MID_SEA_OAK, track=180), now=1000.0, took_off_from=airports().get("SEA"))
        self.assertEqual((info.origin, info.destination, info.route_basis), ("SEA", "OAK", "track"))
        self.assertTrue(info.route_checked)


class StoreTests(unittest.TestCase):
    def test_how_the_route_was_found_is_not_a_column_but_checked_is(self):
        from helpers import plane as near_home
        store = Store(":memory:")
        pid, _ = store.record_sighting(near_home(), 1.0, 0, 1000.0, gap=1800)
        store.set_info(pid, adsb.FlightInfo(airline="Test Air", origin="SEA", destination="OAK", route_basis="fits"))
        self.assertEqual(store.get_pass(pid)["route_checked"], 0)
        store.set_info(pid, adsb.FlightInfo(airline="Test Air", origin="SEA", destination="OAK", route_basis="shows"))
        row = store.get_pass(pid)
        self.assertEqual((row["origin"], row["destination"], row["route_checked"]), ("SEA", "OAK", 1))
        store.set_destination(pid, "SFO", "San Francisco")  # only fills a blank
        self.assertEqual(store.get_pass(pid)["destination"], "OAK")


if __name__ == "__main__":
    unittest.main()
