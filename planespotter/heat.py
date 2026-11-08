"""Heat map of the busiest flight paths.

The sky around home is split into a grid of cells about 280 m across. Every
time a plane flies through a cell, that cell's count for the day goes up by
one, so a busy approach path lights up and a one-off flight barely shows.

Each plane counts once per cell per trip, however slowly it flies, and the
line between two position reports is filled in, so fast jets leave a
continuous streak rather than dots.
"""

from __future__ import annotations

import json
import math
from typing import Iterable

from .geo import distance_miles, offset

DLAT = 0.0025  # cell height in degrees of latitude (about 280 m)


def _dlon(cy: int) -> float:
    """Cell width in degrees of longitude, so cells stay roughly square."""
    lat = (cy + 0.5) * DLAT
    return DLAT / max(0.2, math.cos(math.radians(round(lat))))


def cell_of(lat: float, lon: float) -> tuple[int, int]:
    cy = math.floor(lat / DLAT)
    return math.floor(lon / _dlon(cy)), cy


def cell_center(cx: int, cy: int) -> tuple[float, float]:
    """(lat, lon) of the middle of a cell."""
    return (cy + 0.5) * DLAT, (cx + 0.5) * _dlon(cy)


def cells_along(lat1: float, lon1: float, lat2: float, lon2: float) -> set[tuple[int, int]]:
    """Every cell the straight line between two positions passes through."""
    metres = distance_miles(lat1, lon1, lat2, lon2) * 1609.344
    steps = max(1, math.ceil(metres / 100))  # sample every ~100 m, finer than a cell
    return {
        cell_of(lat1 + (lat2 - lat1) * i / steps, lon1 + (lon2 - lon1) * i / steps)
        for i in range(steps + 1)
    }


def path_cells(path: list[list[float]]) -> set[tuple[int, int]]:
    """Cells covered by a recorded path of [lat, lon, alt, time] points."""
    cells: set[tuple[int, int]] = set()
    for a, b in zip(path, path[1:]):
        if b[3] - a[3] <= 120:  # don't draw a line across a gap in coverage
            cells |= cells_along(a[0], a[1], b[0], b[1])
    for p in path:
        cells.add(cell_of(p[0], p[1]))
    return cells


class HeatRecorder:
    """Counts every plane the watcher sees, not just the ones over your zone."""

    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        # hex -> (last time seen, cells already counted on this trip)
        self.visited: dict[str, tuple[float, set]] = {}

    def record(self, planes: Iterable, previous: dict[str, tuple], now: float) -> int:
        """Add this poll's planes. ``previous`` is hex -> (lat, lon, time, ...)."""
        cfg = self.cfg
        gap = cfg.pass_gap_minutes * 60
        day = cfg.local(now).date().isoformat()
        new_cells: list[tuple[int, int]] = []
        for ac in planes:
            if distance_miles(cfg.home_lat, cfg.home_lon, ac.lat, ac.lon) > cfg.view_radius_miles:
                continue
            last_seen, counted = self.visited.get(ac.hex, (0.0, set()))
            if now - last_seen > gap:
                counted = set()  # a new trip: it can count again
            prev = previous.get(ac.hex)
            if prev and now - prev[2] <= cfg.poll_seconds * 3:
                cells = cells_along(prev[0], prev[1], ac.lat, ac.lon)
            else:
                cells = {cell_of(ac.lat, ac.lon)}
            fresh = cells - counted
            counted |= fresh
            self.visited[ac.hex] = (now, counted)
            new_cells.extend(fresh)
        for hex_code in [h for h, (t, _) in self.visited.items() if now - t > gap]:
            del self.visited[hex_code]
        self.store.add_heat(day, new_cells)
        return len(new_cells)


def backfill(store, cfg) -> None:
    """First run only: seed the heat map from flights already in the history."""
    if store.get_meta("heat_backfilled"):
        return
    by_day: dict[str, list[tuple[int, int]]] = {}
    for row in store.recent_passes(0, limit=1_000_000):
        path = json.loads(row["path"] or "[]")
        if path:
            cells = path_cells(path)
        else:
            lat, lon = offset(cfg.home_lat, cfg.home_lon, row["closest_bearing"] or 0, row["closest_miles"])
            cells = {cell_of(lat, lon)}
        day = cfg.local(row["first_seen"]).date().isoformat()
        by_day.setdefault(day, []).extend(cells)
    for day, cells in by_day.items():
        store.add_heat(day, cells, commit=False)
    store.set_meta("heat_backfilled", "1")  # commits everything above too


def heat_json(store, cfg, now: float, days: int) -> dict:
    """Cells for the map, plus the most common routes over the zone."""
    from datetime import timedelta

    today = cfg.local(now).date()
    since_day = None if days <= 0 else (today - timedelta(days=days - 1))
    rows = store.heat_cells(since_day.isoformat() if since_day else "")
    cells = []
    for cx, cy, n in rows:
        lat, lon = cell_center(cx, cy)
        cells.append([round(lon, 5), round(lat, 5), n])
    since_ts = cfg.day_start(since_day) if since_day else 0.0
    routes = [
        {"origin": r["origin"], "origin_city": r["origin_city"], "destination": r["destination"],
         "destination_city": r["destination_city"], "flights": r["n"]}
        for r in store.top_routes(since_ts, limit=5)
    ]
    return {"cells": cells, "max": max((c[2] for c in cells), default=0), "routes": routes,
            "first_day": store.first_heat_day()}
