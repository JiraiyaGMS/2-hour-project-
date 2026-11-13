"""A calendar of things to look up for: space station passes, the best time to look up each day,
and flights you're following. As a feed your iPhone's Calendar can subscribe to (on home Wi-Fi),
and attached to the weekly email (tap it to add the events anywhere)."""

from __future__ import annotations

from datetime import datetime, timezone


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Calendar lines are at most 75 bytes; longer ones continue on the next line after a space."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while cut and (raw[cut] & 0xC0) == 0x80:  # don't split a character in two
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def event(uid: str, start: float, end: float, title: str, description: str = "", alarm_min: int | None = None,
          now: float = 0.0) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}@planespotter", f"DTSTAMP:{_stamp(now or start)}", f"DTSTART:{_stamp(start)}",
             f"DTEND:{_stamp(max(end, start + 60))}", f"SUMMARY:{_escape(title)}"]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if alarm_min is not None:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(title)}", f"TRIGGER:-PT{alarm_min}M",
                  "END:VALARM"]
    return lines + ["END:VEVENT"]


def build_calendar(cfg, now: float, passes: list[dict], best: list[dict], flights: list[dict]) -> bytes:
    """An .ics file with everything worth looking up for in the next few days."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//planespotter//look up//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape('planespotter · ' + cfg.home_name)}",
             "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    for p in passes:
        if not p["visible"] or p["max_el"] < 15:
            continue
        start = p["visible_from"] or p["start"]
        lines += event(f"pass-{p['number']}-{int(p['start'])}", start, p["visible_to"] or p["end"],
                       f"🛰️ {p['name']} flies over",
                       f"Look {p['start_dir']}: it climbs to {p['max_el']}° in the {p['max_dir']} and sets in the "
                       f"{p['end_dir']}. A bright, steady star moving fast, with no blinking lights.", 10, now)
    for b in best:
        lines += event(f"best-{int(b['start'])}", b["start"], b["end"], "👀 Best time to look up",
                       f"Usually about {b['planes']} planes over {cfg.home_name} in this hour ({b['sky']}).", None, now)
    for f in flights:
        if f["state"] in ("waiting", "airborne"):
            day = datetime.fromisoformat(f["day"])
            lines += ["BEGIN:VEVENT", f"UID:flight-{f['callsign']}-{f['day']}@planespotter", f"DTSTAMP:{_stamp(now)}",
                      f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"SUMMARY:{_escape('✈ ' + f['label'] + ' (planespotter is following it)')}",
                      "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(x) for x in lines) + "\r\n").encode("utf-8")
