"""Builds the alert and digest emails (subject, plain text, HTML)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from html import escape

from .config import Config
from .digest import Forecast, Recap
from .geo import compass

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK = "#1f2933"
MUTED = "#5f6b7a"
LINE = "#e3e8ee"
ACCENT = "#1d6fd8"
TINT = "#f4f7fb"
DIGEST_ROW_LIMIT = 150


@dataclass
class Email:
    subject: str
    text: str
    html: str


# --- formatting helpers ------------------------------------------------------

def label(p) -> str:
    return p["callsign"] or p["registration"] or p["hex"].upper()


def fmt_alt(ft: int | None) -> str:
    return "—" if ft is None else f"{ft:,} ft"


def fmt_dist(miles: float) -> str:
    return f"{miles:.1f} mi"


def place(code: str, city: str) -> str:
    if city and code:
        return f"{city} ({code})"
    return city or code


def route(p) -> str:
    origin = place(p["origin"], p["origin_city"])
    dest = place(p["destination"], p["destination_city"])
    if origin or dest:
        return f"{origin or '?'} → {dest or '?'}"
    return ""


def short_route(p) -> str:
    if p["origin"] or p["destination"]:
        return f"{p['origin'] or '?'} → {p['destination'] or '?'}"
    return ""


def aircraft(p) -> str:
    return p["aircraft_desc"] or p["type_code"]


def track_url(hex_code: str) -> str:
    return f"https://globe.adsb.lol/?icao={hex_code.lstrip('~')}"


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:#ffffff">'
        f'<div style="font-family:{FONT};color:{INK};max-width:600px;margin:0 auto;padding:20px 16px">'
        f"{body}"
        f'<p style="font-size:12px;color:{MUTED};margin:24px 0 0;border-top:1px solid {LINE};padding-top:12px">'
        'Live data from <a href="https://adsb.lol" style="color:{0}">adsb.lol</a> (ODbL) · '
        'routes from <a href="https://www.adsbdb.com" style="color:{0}">adsbdb.com</a> · '
        "sent by planespotter</p></div></body></html>".format(MUTED)
    )


# --- alerts ------------------------------------------------------------------

def _stat(name: str, value: str) -> str:
    # inline-block so the stats wrap onto a second line on narrow phones
    return (
        f'<div style="display:inline-block;vertical-align:top;margin:12px 20px 0 0">'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:{MUTED}">{escape(name)}</div>'
        f'<div style="font-size:15px;font-weight:600;white-space:nowrap">{escape(value)}</div></div>'
    )


def _plane_card(p, cfg: Config) -> str:
    heading = f" heading {compass(p['track'])}" if p["track"] is not None else ""
    speed = f"{round(p['speed_kt'])} kt{heading}" if p["speed_kt"] is not None else heading.strip() or "—"
    details = " · ".join(x for x in (aircraft(p), p["registration"]) if x)
    photo = (
        f'<td width="104" style="padding-left:12px;vertical-align:top">'
        f'<img src="{escape(p["photo_url"])}" width="104" alt="" '
        f'style="display:block;width:104px;border-radius:6px"></td>'
        if p["photo_url"] else ""
    )
    lines = [f'<div style="font-size:22px;font-weight:700;line-height:1.2">{escape(label(p))}</div>']
    if p["airline"]:
        lines.append(f'<div style="font-size:14px;color:{MUTED}">{escape(p["airline"])}</div>')
    if route(p):
        lines.append(f'<div style="font-size:16px;margin-top:10px">{escape(route(p))}</div>')
    if details:
        lines.append(f'<div style="font-size:13px;color:{MUTED};margin-top:4px">{escape(details)}</div>')
    stats = (
        _stat("Distance", f"{fmt_dist(p['closest_miles'])} {compass(p['closest_bearing'])}")
        + _stat("Altitude", fmt_alt(p["closest_alt_ft"]))
        + _stat("Speed", speed)
    )
    link = (
        f'<div style="margin-top:14px"><a href="{track_url(p["hex"])}" '
        f'style="color:{ACCENT};font-size:14px;font-weight:600;text-decoration:none">Track it live →</a></div>'
    )
    header = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="vertical-align:top">{"".join(lines)}</td>{photo}</tr></table>'
    )
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border:1px solid {LINE};border-radius:10px;border-collapse:separate;margin:0 0 12px">'
        f'<tr><td style="padding:16px">{header}{stats}{link}</td></tr></table>'
    )


def alert_email(passes: list[sqlite3.Row], cfg: Config) -> Email:
    first = passes[0]
    when = cfg.local(min(p["first_seen"] for p in passes)).strftime("%H:%M")
    if len(passes) == 1:
        subject = (
            f"✈ {label(first)} over {cfg.home_name} — "
            f"{fmt_dist(first['closest_miles'])} {compass(first['closest_bearing'])}, {fmt_alt(first['closest_alt_ft'])}"
        )
        intro = f"A plane is flying over {cfg.home_name}"
    else:
        names = ", ".join(label(p) for p in passes[:4]) + ("…" if len(passes) > 4 else "")
        subject = f"✈ {len(passes)} planes over {cfg.home_name}: {names}"
        intro = f"{len(passes)} planes are flying over {cfg.home_name}"

    body = (
        f'<p style="font-size:14px;color:{MUTED};margin:0 0 14px">{escape(intro)} · {when} · '
        f"within {cfg.radius_miles:g} mi</p>"
        + "".join(_plane_card(p, cfg) for p in passes)
    )

    text_lines = [f"{intro} ({when}, within {cfg.radius_miles:g} mi)", ""]
    for p in passes:
        text_lines.append(label(p) + (f" — {p['airline']}" if p["airline"] else ""))
        if route(p):
            text_lines.append(f"  {route(p)}")
        if aircraft(p):
            text_lines.append(f"  {aircraft(p)} {p['registration']}".rstrip())
        text_lines.append(
            f"  {fmt_dist(p['closest_miles'])} {compass(p['closest_bearing'])} of {cfg.home_name}, "
            f"{fmt_alt(p['closest_alt_ft'])}"
        )
        text_lines.append(f"  Track: {track_url(p['hex'])}")
        text_lines.append("")
    return Email(subject=subject, text="\n".join(text_lines), html=_page(subject, body))


# --- digest ------------------------------------------------------------------

def _h2(text: str, sub: str = "") -> str:
    sub_html = f'<span style="font-weight:400;color:{MUTED}"> · {escape(sub)}</span>' if sub else ""
    return f'<h2 style="font-size:18px;margin:28px 0 10px">{escape(text)}{sub_html}</h2>'


def _table(headers: list[str], rows: list[list[str]], right: set[int] = frozenset()) -> str:
    def cell(i: int, html: str, tag: str = "td") -> str:
        align = "right" if i in right else "left"
        style = f"padding:7px 8px;border-bottom:1px solid {LINE};text-align:{align};vertical-align:top"
        if tag == "th":
            style += f";font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:{MUTED};font-weight:600"
        return f'<{tag} style="{style}">{html}</{tag}>'

    head = "<tr>" + "".join(cell(i, escape(h), "th") for i, h in enumerate(headers)) + "</tr>"
    body = "".join("<tr>" + "".join(cell(i, c) for i, c in enumerate(r)) + "</tr>" for r in rows)
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse;font-size:14px">{head}{body}</table>'
    )


def _two_line(top: str, bottom: str) -> str:
    out = f'<strong style="white-space:nowrap">{escape(top)}</strong>'
    if bottom:
        out += f'<div style="font-size:12px;color:{MUTED}">{escape(bottom)}</div>'
    return out


def _flight_cell(name: str, route_text: str, airline: str, plane_desc: str, link: str = "") -> str:
    """Callsign and route on top, airline and aircraft underneath."""
    top = f'<strong style="white-space:nowrap">{escape(name)}</strong>'
    if link:
        top = f'<a href="{link}" style="color:{INK};text-decoration:none">{top}</a>'
    if route_text:
        top += f' <span style="white-space:nowrap;color:{MUTED}">&nbsp;{escape(route_text)}</span>'
    # Each piece stays whole; lines only break between airline and aircraft.
    bottom = " · ".join(f'<span style="white-space:nowrap">{escape(x)}</span>' for x in (airline, plane_desc) if x)
    if bottom:
        top += f'<div style="font-size:12px;color:{MUTED}">{bottom}</div>'
    return top


def _day(d) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def _tile(name: str, value: str, note: str = "") -> str:
    note_html = f'<div style="font-size:12px;color:{MUTED}">{escape(note)}</div>' if note else ""
    return (
        f'<td width="25%" style="padding:12px;background:{TINT};border-radius:8px;vertical-align:top">'
        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:{MUTED}">{escape(name)}</div>'
        f'<div style="font-size:20px;font-weight:700;margin-top:2px">{escape(value)}</div>{note_html}</td>'
    )


def digest_email(recap: Recap, forecast: Forecast, cfg: Config) -> Email:
    n_yesterday = len(recap.passes)
    n_expected = len(forecast.flights)
    today_label = _day(forecast.day)
    yday_label = _day(recap.day)
    subject = (
        f"Sky report for {cfg.home_name}: {n_yesterday} plane{'s' if n_yesterday != 1 else ''} yesterday"
        + ("" if forecast.learning else f", {n_expected} expected today")
    )

    html = [
        f'<h1 style="font-size:24px;margin:0">Daily sky report</h1>'
        f'<p style="font-size:14px;color:{MUTED};margin:4px 0 0">{escape(cfg.home_name)} · '
        f"everything within {cfg.radius_miles:g} mi"
        + (f" and below {cfg.max_altitude_ft:,} ft" if cfg.max_altitude_ft else "")
        + "</p>"
    ]
    text = [f"Daily sky report — {cfg.home_name}", ""]

    # Expected today
    html.append(_h2("Expected overhead today", today_label))
    text.append(f"EXPECTED OVERHEAD TODAY ({today_label})")
    if forecast.learning:
        msg = (
            "Still learning your sky. Predictions start once planespotter has watched "
            "for a couple of days, because it learns which flights pass over regularly."
        )
        html.append(f'<p style="font-size:14px;color:{MUTED}">{escape(msg)}</p>')
        text.append(msg)
    elif not forecast.flights:
        msg = f"No regular flights spotted over the last {forecast.days_of_history} days."
        html.append(f'<p style="font-size:14px;color:{MUTED}">{escape(msg)}</p>')
        text.append(msg)
    else:
        rows = []
        for f in forecast.flights:
            r = {"origin": f.origin, "destination": f.destination}
            rows.append([
                f"~{f.typical_time}",
                _flight_cell(f.callsign, short_route(r), f.airline, f.aircraft),
                f'<span style="white-space:nowrap">{f.days_seen} of {forecast.days_of_history}</span>',
            ])
            text.append(
                f"  ~{f.typical_time}  {f.callsign:<8} {short_route(r):<12} "
                f"{f.airline}  (seen {f.days_seen} of {forecast.days_of_history} days)"
            )
        html.append(_table(["Time", "Flight", "Days seen"], rows, right={2}))
        html.append(
            f'<p style="font-size:12px;color:{MUTED};margin:8px 0 0">Predicted from flights that '
            f"passed over on at least 2 of the last {forecast.days_of_history} days, at their usual time. "
            "Delays, diversions and runway changes will shuffle things.</p>"
        )
    text.append("")

    # Yesterday
    html.append(_h2("Yesterday", yday_label))
    text.append(f"YESTERDAY ({yday_label}): {n_yesterday} planes")
    if not recap.passes:
        html.append(f'<p style="font-size:14px;color:{MUTED}">No planes passed through the zone.</p>')
    else:
        tiles = [_tile("Planes", str(n_yesterday))]
        if recap.busiest_hour:
            hour, count = recap.busiest_hour
            tiles.append(_tile("Busiest hour", f"{hour:02d}:00", f"{count} planes"))
        if recap.lowest is not None:
            tiles.append(_tile("Lowest", fmt_alt(recap.lowest["closest_alt_ft"]), label(recap.lowest)))
        if recap.closest is not None:
            tiles.append(_tile("Closest", fmt_dist(recap.closest["closest_miles"]), label(recap.closest)))
        spacer = '<td width="8" style="width:8px"></td>'
        html.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="border-collapse:separate;margin:0 0 12px"><tr>{spacer.join(tiles)}</tr></table>'
        )
        if recap.top_airlines:
            airlines = ", ".join(f"{name} ({n})" for name, n in recap.top_airlines)
            html.append(f'<p style="font-size:14px;margin:0 0 12px"><strong>Top airlines:</strong> {escape(airlines)}</p>')
            text.append(f"Top airlines: {airlines}")

        rows = []
        for p in recap.passes[:DIGEST_ROW_LIMIT]:
            when = cfg.local(p["first_seen"]).strftime("%H:%M")
            rows.append([
                when,
                _flight_cell(label(p), short_route(p), p["airline"], aircraft(p), track_url(p["hex"])),
                _two_line(fmt_alt(p["closest_alt_ft"]), fmt_dist(p["closest_miles"])),
            ])
            text.append(
                f"  {when}  {label(p):<8} {short_route(p):<12} {fmt_alt(p['closest_alt_ft']):>10}  "
                f"{fmt_dist(p['closest_miles'])}  {p['airline']}"
            )
        html.append(_table(["Time", "Flight", "Altitude"], rows, right={2}))
        if n_yesterday > DIGEST_ROW_LIMIT:
            more = f"…and {n_yesterday - DIGEST_ROW_LIMIT} more."
            html.append(f'<p style="font-size:13px;color:{MUTED}">{more}</p>')
            text.append(f"  {more}")

    return Email(subject=subject, text="\n".join(text), html=_page(subject, "".join(html)))


def test_email(cfg: Config) -> Email:
    subject = f"planespotter is set up for {cfg.home_name}"
    msg = (
        f"Emails are working. You'll get an alert when a plane comes within "
        f"{cfg.radius_miles:g} miles of {cfg.home_name}, and a sky report every day at "
        f"{cfg.digest_time.strftime('%H:%M')}."
    )
    body = f'<h1 style="font-size:22px;margin:0 0 8px">It works ✈</h1><p style="font-size:15px">{escape(msg)}</p>'
    return Email(subject=subject, text=msg, html=_page(subject, body))
