"""The weekly email: the last seven days of planes over home, compared with the week before."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

from .config import Config
from .emails import (INK, LINE, MUTED, Email, _day, _flight_cell, _h2, _page, _table, _tile, _two_line, aircraft,
                     fmt_alt, fmt_dist, label, short_route, track_url)
from .catchup import offline_summary
from .store import Store

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Week:
    start: float
    end: float
    passes: list
    previous_count: int
    busiest_day: tuple[date, int] | None = None
    busiest_hour: tuple[int, int] | None = None
    top_airlines: list[tuple[str, int]] = field(default_factory=list)
    top_routes: list[tuple[str, int]] = field(default_factory=list)
    lowest: object = None
    closest: object = None
    rarest: tuple[object, int] | None = None    # (pass, how many times that type has ever been seen)
    firsts: list[tuple[object, str]] = field(default_factory=list)
    specials: list[tuple[str, int]] = field(default_factory=list)
    offline: dict = field(default_factory=dict)  # planes found by the offline log (catchup.offline_summary)
    games: list = field(default_factory=list)    # lines about bingo, guess the plane and punctuality


def build_week(store: Store, cfg: Config, today: date) -> Week:
    end = cfg.at(today, cfg.digest_time)
    start = cfg.at(today - timedelta(days=7), cfg.digest_time)
    before = cfg.at(today - timedelta(days=14), cfg.digest_time)
    passes = store.passes_between(start, end)
    week = Week(start=start, end=end, passes=passes, previous_count=len(store.passes_between(before, start)),
                offline=offline_summary(store, start, end), games=games_lines(store, cfg, start, end))
    if not passes:
        return week

    days = Counter(cfg.local(p["first_seen"]).date() for p in passes)
    week.busiest_day = max(days.items(), key=lambda kv: (kv[1], kv[0]))
    hours = Counter(cfg.local(p["first_seen"]).hour for p in passes)
    week.busiest_hour = max(hours.items(), key=lambda kv: (kv[1], -kv[0]))
    week.top_airlines = Counter(p["airline"] for p in passes if p["airline"]).most_common(5)
    week.top_routes = Counter(f"{p['origin']} → {p['destination']}" for p in passes
                              if p["origin"] and p["destination"]).most_common(5)
    with_alt = [p for p in passes if p["closest_alt_ft"] is not None]
    week.lowest = min(with_alt, key=lambda p: p["closest_alt_ft"]) if with_alt else None
    week.closest = min(passes, key=lambda p: p["closest_miles"])

    # The rarest plane: the type you've seen least often, ever.
    ever = Counter(r[0] for r in store.db.execute("SELECT type_code FROM passes WHERE type_code != ''"))
    typed = [p for p in passes if p["type_code"]]
    if typed:
        rarest = min(typed, key=lambda p: (ever[p["type_code"]], -p["first_seen"]))
        week.rarest = (rarest, ever[rarest["type_code"]])

    week.firsts = [(p, f) for p in passes for f in json.loads(p["firsts"] or "[]")]
    kinds = {"emergency": "Emergencies", "military": "Military aircraft", "type": "Rare big planes",
             "helicopter": "Helicopters overhead"}
    rows = store.db.execute("SELECT reason, COUNT(*) FROM special_seen WHERE at >= ? AND at < ? GROUP BY reason",
                            (start, end)).fetchall()
    week.specials = [(kinds.get(r[0], r[0]), r[1]) for r in rows]
    return week


def games_lines(store, cfg, start: float, end: float) -> list[str]:
    """Bingo, guess the plane and your regular flights' punctuality, as a few short lines."""
    from .games import bingo, quiz_score
    from .schedule import punctuality

    lines = []
    if store.passes_between(start, end):
        card = bingo(store, cfg, end - 1)
        lines.append(f"🎱 Spotting bingo: {card['filled']} of 9 squares"
                     + (f", {card['lines']} line{'s' if card['lines'] != 1 else ''}" if card["lines"] else "")
                     + (" (full house!)" if card["full_house"] else ""))
    score = quiz_score(store, start)
    if score["answered"]:
        lines.append(f"🎯 Guess the plane: {score['right']} of {score['answered']} right ({score['percent']}%)")
    p = punctuality(store, cfg, start, end)
    if p:
        best, worst = p["best"], p["worst"]
        lines.append(f"⏱️ Your regular flights were on time {p['on_time']} of {p['flights']} times. "
                     f"Most punctual: {best['callsign']} (usually within {abs(best['average'])} min). "
                     + (f"Latest: {worst['callsign']} (on average {worst['average']} min late)."
                        if worst["average"] > 10 and worst["callsign"] != best["callsign"] else ""))
    return lines


def weekly_email(week: Week, cfg: Config) -> Email:
    n = len(week.passes)
    change = ""
    if week.previous_count:
        pct = round(100 * (n - week.previous_count) / week.previous_count)
        change = f"{'+' if pct >= 0 else ''}{pct}% on the week before"
    first_day, last_day = cfg.local(week.start), cfg.local(week.end - 1)
    span = f"{_day(first_day)} – {_day(last_day)}"
    subject = f"Your week of planes over {cfg.home_name}: {n:,} plane{'s' if n != 1 else ''}" + (
        f" ({change.split(' ')[0]})" if change else "")

    html = [
        '<h1 style="font-size:24px;margin:0">Your week of planes</h1>'
        f'<p style="font-size:14px;color:{MUTED};margin:4px 0 0">{escape(cfg.home_name)} · {escape(span)}</p>'
    ]
    text = [f"Your week of planes over {cfg.home_name} ({span})", ""]
    if not week.passes:
        html.append(f'<p style="font-size:15px">No planes came through your {cfg.radius_miles:g} mi zone this week.</p>')
        text.append("No planes came through your zone this week.")
        return Email(subject=subject, text="\n".join(text), html=_page(subject, "".join(html)))

    tiles = [_tile("Planes", f"{n:,}", change)]
    if week.busiest_day:
        d, count = week.busiest_day
        tiles.append(_tile("Busiest day", f"{d:%a}", f"{count} planes"))
    if week.busiest_hour:
        hour, count = week.busiest_hour
        tiles.append(_tile("Busiest hour", f"{hour:02d}:00", f"{count} planes"))
    if week.lowest is not None:
        tiles.append(_tile("Lowest", fmt_alt(week.lowest["closest_alt_ft"]), label(week.lowest)))
    spacer = '<td width="8" style="width:8px"></td>'
    html.append('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                f'style="border-collapse:separate;margin:18px 0 0"><tr>{spacer.join(tiles)}</tr></table>')
    text.append(f"{n} planes" + (f" ({change})" if change else ""))

    if week.rarest:
        p, times = week.rarest
        note = "the first one you've ever seen" if times <= 1 else f"seen {times} times ever"
        html.append(_h2("Rarest plane", note))
        html.append(_table(["When", "Flight", "Closest"], [[
            cfg.local(p["first_seen"]).strftime("%a %H:%M"),
            _flight_cell(label(p), short_route(p), p["airline"], aircraft(p), track_url(p["hex"])),
            _two_line(fmt_alt(p["closest_alt_ft"]), fmt_dist(p["closest_miles"])),
        ]], right={2}))
        text.append(f"Rarest: {label(p)} ({aircraft(p)}), {note}")

    if week.firsts:
        html.append(_h2("New in your collection"))
        html.append("".join(f'<p style="font-size:14px;margin:4px 0">✨ {escape(f)} <span style="color:{MUTED}">'
                            f'({escape(label(p))}, {cfg.local(p["first_seen"]):%a})</span></p>' for p, f in week.firsts[:12]))
        text += [f"New: {f} ({label(p)})" for p, f in week.firsts[:12]]

    if week.specials:
        html.append(_h2("Special planes nearby"))
        html.append("".join(f'<p style="font-size:14px;margin:4px 0">{escape(k)}: <strong>{c}</strong></p>'
                            for k, c in week.specials))
        text += [f"{k}: {c}" for k, c in week.specials]

    if week.games:
        html.append(_h2("Games"))
        html.append("".join(f'<p style="font-size:14px;margin:4px 0">{escape(x)}</p>' for x in week.games))
        text += [""] + week.games

    off = week.offline
    if off and (off["found"] or off["off_s"] >= 3600):
        hours = off["off_s"] / 3600
        found = off["found"]
        times = f" ({off['gaps']} times)" if off["gaps"] > 1 else ""
        intro = f"planespotter wasn't running for {hours:.0f} hour{'s' if round(hours) != 1 else ''} this week{times}. "
        if found:
            intro += f"It looked those hours up in adsb.lol's records and found {len(found)} plane{'s' if len(found) != 1 else ''} that came over:"
        else:
            intro += "It found no planes over your zone in the part it could look up."
        html.append(_h2("📴 Found while planespotter was off", f"{len(found)} plane{'s' if len(found) != 1 else ''}"))
        html.append(f'<p style="font-size:14px;margin:0 0 8px">{escape(intro)}</p>')
        text += ["", f"FOUND WHILE PLANESPOTTER WAS OFF: {len(found)}", intro]
        if found:
            rows = []
            for p in found[:15]:
                origin = p["origin_city"] or p["origin"]
                rows.append([cfg.local(p["first_seen"]).strftime("%a %H:%M"),
                             _flight_cell(label(p), short_route(p), p["airline"], aircraft(p), track_url(p["hex"])),
                             _two_line(fmt_alt(p["closest_alt_ft"]), fmt_dist(p["closest_miles"]))])
                text.append(f"  {cfg.local(p['first_seen']):%a %H:%M}  {label(p):<8} {short_route(p):<12} "
                            f"{('from ' + origin) if origin else ''}")
            html.append(_table(["When", "Flight", "Closest"], rows, right={2}))
            if len(found) > 15:
                html.append(f'<p style="font-size:13px;color:{MUTED}">…and {len(found) - 15} more.</p>')
            origins = Counter(p["origin_city"] or p["origin"] for p in found if p["origin_city"] or p["origin"])
            if origins:
                where = ", ".join(f"{city} ({n})" for city, n in origins.most_common(6))
                html.append(f'<p style="font-size:14px;margin:8px 0 0"><strong>Where they came from:</strong> {escape(where)}</p>')
                text.append(f"Where they came from: {where}")
        missed = (off["off_s"] - off["checked_s"]) / 3600
        if missed >= 1:
            note = (f"The other {missed:.0f} hour{'s' if round(missed) != 1 else ''} were too long ago to look up "
                    "(adsb.lol remembers about a day), so start planespotter again within a day to catch everything.")
            html.append(f'<p style="font-size:13px;color:{MUTED};margin:8px 0 0">{escape(note)}</p>')
            text.append(note)

    if week.top_routes:
        html.append(_h2("Busiest routes overhead"))
        html.append(_table(["Route", "Flights"], [[escape(r), str(c)] for r, c in week.top_routes], right={1}))
        text += ["Busiest routes:"] + [f"  {r}  {c}" for r, c in week.top_routes]
    if week.top_airlines:
        html.append(_h2("Top airlines"))
        html.append(_table(["Airline", "Flights"], [[escape(a), str(c)] for a, c in week.top_airlines], right={1}))
        text += ["Top airlines:"] + [f"  {a}  {c}" for a, c in week.top_airlines]
    if week.closest is not None:
        p = week.closest
        html.append(f'<p style="font-size:13px;color:{MUTED};margin-top:18px;border-top:1px solid {LINE};'
                    f'padding-top:10px">Closest of the week: <strong style="color:{INK}">{escape(label(p))}</strong>, '
                    f'{fmt_dist(p["closest_miles"])} away at {fmt_alt(p["closest_alt_ft"])}.</p>')
    return Email(subject=subject, text="\n".join(text), html=_page(subject, "".join(html)))
