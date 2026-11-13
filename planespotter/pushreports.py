"""The morning and weekly reports as phone notifications, for when emails are off (EMAILS=off).

Short enough to read on a lock screen, and to fit in one ntfy message.
"""

from __future__ import annotations

from collections import Counter

from .emails import fmt_alt, label

MAX_CHARS = 3500  # ntfy messages hold about 4,000 bytes


def _fit(lines: list[str]) -> str:
    out, size = [], 0
    for line in lines:
        if size + len(line.encode()) + 1 > MAX_CHARS:
            out.append("…")
            break
        out.append(line)
        size += len(line.encode()) + 1
    return "\n".join(out)


def digest_push(recap, forecast, cfg) -> tuple[str, str]:
    """('☀ Yesterday over Home: 142 planes', 'Busiest 5 pm (18) · Southwest 31, ...')."""
    n = len(recap.passes)
    title = f"☀ The last day over {cfg.home_name}: {n} plane{'s' if n != 1 else ''}"
    lines = []
    if recap.busiest_hour:
        hour, count = recap.busiest_hour
        lines.append(f"Busiest hour: {hour % 12 or 12} {'am' if hour < 12 else 'pm'} ({count} planes)")
    if recap.top_airlines:
        lines.append("Top airlines: " + ", ".join(f"{name} {count}" for name, count in recap.top_airlines[:3]))
    if recap.star:
        s = recap.star
        what = " · ".join(x for x in (s.get("label"), s.get("aircraft"), s.get("route")) if x)
        lines.append(f"⭐ Plane of the day: {what}" + (f" ({s['why']})" if s.get("why") else ""))
    if recap.lowest is not None:
        lines.append(f"Lowest: {label(recap.lowest)} at {fmt_alt(recap.lowest['closest_alt_ft'])}")
    if recap.quiet_count:
        lines.append(f"🌙 {recap.quiet_count} came over during quiet hours")
    if not forecast.learning and forecast.flights:
        lines.append(f"Today: {len(forecast.flights)} regular flights expected")
    lines += list(recap.notes)
    if not lines:
        lines.append("A quiet day: nothing came through your zone.")
    return title, _fit(lines)


def weekly_push(week, cfg) -> tuple[str, str]:
    """Your week of planes, in a few lines (including what the offline log found)."""
    n = len(week.passes)
    change = ""
    if week.previous_count:
        pct = round(100 * (n - week.previous_count) / week.previous_count)
        change = f" ({'+' if pct >= 0 else ''}{pct}% on last week)"
    title = f"📅 Your week over {cfg.home_name}: {n:,} plane{'s' if n != 1 else ''}{change}"
    lines = []
    if week.busiest_day:
        d, count = week.busiest_day
        lines.append(f"Busiest day: {d:%A} ({count} planes)")
    if week.top_airlines:
        lines.append("Top airlines: " + ", ".join(f"{name} {count}" for name, count in week.top_airlines[:3]))
    if week.top_routes:
        lines.append("Top routes: " + ", ".join(f"{r} ({count})" for r, count in week.top_routes[:2]))
    if week.rarest:
        p, times = week.rarest
        lines.append(f"Rarest: {label(p)} {p['type_code'] or ''}".rstrip()
                     + (" (your first ever!)" if times <= 1 else f" (seen {times} times ever)"))
    if week.firsts:
        lines.append(f"✨ New in your collection: " + ", ".join(f for _, f in week.firsts[:5])
                     + (f" and {len(week.firsts) - 5} more" if len(week.firsts) > 5 else ""))
    if week.specials:
        lines.append("Special planes: " + ", ".join(f"{k} {c}" for k, c in week.specials))
    lines += list(week.games)
    off = week.offline
    if off and (off["found"] or off["off_s"] >= 3600):
        found = off["found"]
        hours = off["off_s"] / 3600
        line = (f"📴 Offline log: planespotter was off for {hours:.0f} hour{'s' if round(hours) != 1 else ''}; "
                f"it found {len(found)} plane{'s' if len(found) != 1 else ''} that came over then")
        origins = Counter(p["origin_city"] or p["origin"] for p in found if p["origin_city"] or p["origin"])
        if origins:
            line += " (from " + ", ".join(f"{city} {count}" for city, count in origins.most_common(4)) + ")"
        lines.append(line)
    if not n and not lines:
        lines.append("No planes came through your zone this week.")
    return title, _fit(lines)
