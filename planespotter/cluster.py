"""Several homes, one request: accounts near each other share what adsb.lol sends.

adsb.lol's daily limit is per internet connection, so on one server every account shares it. One request
can cover a circle up to 250 nautical miles across-and-a-half, though: homes within a couple of hundred
miles of each other (friends in the same part of the country) are put in one group, asked about with one
request, and each account gets the planes near its own home out of the answer. Homes far apart make
separate groups, and each group takes its turn.
"""

from __future__ import annotations

import threading
import time

from .adsb import LiveFeed, SharedFeed
from .geo import distance_miles

NM = 1.15078          # miles in a nautical mile
MAX_RADIUS_NM = 250   # the most adsb.lol answers for
JOIN_MILES = 150      # a home this close to a group's middle joins it


class Group:
    def __init__(self, live: LiveFeed, clock):
        self.homes: list[tuple[float, float, float]] = []  # lat, lon, radius (nm) each needs
        self.lat = self.lon = 0.0
        self.radius_nm = 0.0
        self.shared = SharedFeed(live, ttl=10.0, clock=clock)

    def add(self, lat: float, lon: float, radius_nm: float) -> None:
        self.homes.append((lat, lon, radius_nm))
        self.lat = sum(h[0] for h in self.homes) / len(self.homes)
        self.lon = sum(h[1] for h in self.homes) / len(self.homes)
        self.radius_nm = min(MAX_RADIUS_NM, max(distance_miles(self.lat, self.lon, h[0], h[1]) / NM + h[2]
                                                for h in self.homes))

    def fits(self, lat: float, lon: float, radius_nm: float) -> bool:
        return distance_miles(self.lat, self.lon, lat, lon) <= JOIN_MILES and \
            distance_miles(self.lat, self.lon, lat, lon) / NM + radius_nm <= MAX_RADIUS_NM - 20


class ClusterFeed:
    """Give it every home at the start; ask it for each home's view."""

    def __init__(self, live: LiveFeed, homes: list[tuple[float, float, float]], budget=None, clock=time.time):
        self.live, self.budget, self.clock = live, budget, clock
        self.lock = threading.Lock()
        self.groups: list[Group] = []
        self.of: dict[tuple[float, float], Group] = {}
        for lat, lon, radius_nm in homes:
            group = next((g for g in self.groups if g.fits(lat, lon, radius_nm)), None)
            if group is None:
                group = Group(live, clock)
                self.groups.append(group)
            group.add(lat, lon, radius_nm)
            self.of[(round(lat, 5), round(lon, 5))] = group

    @property
    def count(self) -> int:
        """How many requests one round of checks costs (what the daily limit is shared between)."""
        return max(1, len(self.groups))

    def gap(self) -> float:
        """Seconds a group's answer is good for: until its next turn (so the accounts in it don't each ask)."""
        every = self.budget.poll_interval() if self.budget else 60.0
        return max(10.0, every * self.count * 0.9)

    def view(self, lat: float, lon: float, radius_nm: float) -> "HomeView":
        group = self.of.get((round(lat, 5), round(lon, 5)))
        if group is None:  # a home added later (travel mode): a group of its own
            with self.lock:
                group = Group(self.live, self.clock)
                group.add(lat, lon, radius_nm)
                self.groups.append(group)
                self.of[(round(lat, 5), round(lon, 5))] = group
        return HomeView(self, group, lat, lon, radius_nm)


class HomeView:
    """One account's planes: what a watcher and a map expect of a feed."""

    def __init__(self, cluster: ClusterFeed, group: Group, lat: float, lon: float, radius_nm: float):
        self.cluster, self.group, self.lat, self.lon, self.radius_nm = cluster, group, lat, lon, radius_nm

    def _mine(self, planes, radius_nm: float):
        reach = max(radius_nm, self.radius_nm) * NM
        return [ac for ac in planes or [] if distance_miles(self.lat, self.lon, ac.lat, ac.lon) <= reach]

    def aircraft_near(self, lat: float, lon: float, radius_nm: float):
        g = self.group
        g.shared.ttl = self.cluster.gap()  # someone in the group asked lately: use that answer
        return self._mine(g.shared.aircraft_near(g.lat, g.lon, g.radius_nm), radius_nm)

    def latest(self):
        planes, at = self.group.shared.latest()
        return (None if planes is None else self._mine(planes, self.radius_nm)), at

    def newer_than(self, at: float) -> bool:
        """Has the group had a fresh answer since ``at``? (Another account's check brought one: use it now.)"""
        return self.group.shared.fetched_at > at

    def paused_for(self) -> float:
        return self.group.shared.paused_for()
