from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from planespotter.adsb import Aircraft, FlightInfo
from planespotter.config import Config
from planespotter.store import Store
from planespotter.watcher import Watcher

UTC = ZoneInfo("UTC")
HOME = (51.5000, -0.1000)


def make_config(**overrides) -> Config:
    values = dict(
        home_lat=HOME[0], home_lon=HOME[1], home_name="Test House",
        radius_miles=3.0, poll_seconds=20, timezone=UTC, digest_time=time(7, 0),
        db_path=":memory:",
    )
    values.update(overrides)
    return Config(**values)


def ts(year, month, day, hour=0, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def plane(hex_code="abc123", lat=HOME[0], lon=HOME[1], alt=3000, callsign="BAW123", **kw) -> Aircraft:
    return Aircraft(
        hex=hex_code, callsign=callsign, registration=kw.get("registration", "G-TEST"),
        type_code=kw.get("type_code", "A320"), lat=lat, lon=lon, alt_ft=alt,
        speed_kt=kw.get("speed_kt", 250.0), track=kw.get("track", 90.0),
    )


class FakeFeed:
    def __init__(self):
        self.planes: list[Aircraft] = []

    def aircraft_near(self, lat, lon, radius_nm):
        return list(self.planes)


class FakeLookup:
    def info(self, aircraft, now):
        return FlightInfo(airline="Test Air", origin="LHR", origin_city="London",
                          destination="JFK", destination_city="New York")


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, email):
        self.sent.append(email)


def make_watcher(**overrides) -> tuple[Watcher, FakeFeed, FakeMailer]:
    cfg = make_config(**overrides)
    feed, mailer = FakeFeed(), FakeMailer()
    watcher = Watcher(cfg, Store(":memory:"), feed, FakeLookup(), mailer)
    return watcher, feed, mailer
