"""`python3 -m planespotter doctor`: will planespotter work on this computer and this network?

Each thing planespotter needs is tried, and marked ✔ (works), ⚠ (might not: something to know) or ✘ (won't
work here), with what to do about it. Networks differ: hotel, airport and school Wi-Fi often want you to sign
in on a web page first, share one internet connection between everyone (so adsb.lol's daily limit is shared
too), block some sites, or keep devices apart so a phone can't reach a Mac on the same Wi-Fi.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adsb import USER_AGENT, key_header
from .net import kind_of, lan_addresses

OK, MIGHT, WONT = "✔", "⚠", "✘"
SIGN_IN = ("This network is answering instead: it probably wants you to sign in first. Open Safari, go to any "
           "website, and accept its page. (Or it filters this site.)")


def probe(url: str, timeout: float = 8, headers: dict | None = None) -> dict:
    """{"status", "text" (the start of it), "type"} or {"error": why, "kind": "cert" | "offline"}."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "text": resp.read(600).decode("utf-8", "replace"),
                    "type": resp.headers.get("Content-Type", "")}
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read(600).decode("utf-8", "replace")
        except OSError:
            text = ""
        return {"status": exc.code, "text": text, "type": (exc.headers or {}).get("Content-Type", "")}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        cert = isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(exc).upper()
        return {"error": str(reason)[:120], "kind": "cert" if cert else "offline"}


def is_page(r: dict) -> bool:
    return "html" in r.get("type", "").lower() or r.get("text", "").lstrip().lower().startswith(("<!doctype", "<html"))


def facts(cfg) -> dict:
    """Things about this computer (not the network)."""
    from . import autostart
    from .budget import ApiBudget
    from .phonekit import SCRIPTABLE_ICLOUD

    out = {"addresses": lan_addresses(), "mac": sys.platform == "darwin", "firewall": None,
           "icloud": Path(SCRIPTABLE_ICLOUD).expanduser().is_dir(), "autostart": None, "used": None, "limit": None}
    if out["mac"]:
        try:
            fw = subprocess.run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                                capture_output=True, text=True, timeout=5)
            out["firewall"] = "enabled" in fw.stdout.lower()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        out["autostart"] = autostart.status() if autostart.supported() else None
    except Exception:  # not being able to ask is not a problem in itself
        pass
    try:
        if Path(cfg.db_path).exists():
            budget = ApiBudget(cfg.db_path, cfg.adsb_daily_limit)
            out["used"], out["limit"] = budget.used(), budget.limit
            budget.db.close()
    except Exception:
        pass
    return out


def diagnose(cfg, probe=probe, facts: dict | None = None) -> list[tuple[str, str, str]]:
    """[(mark, what, detail)] in the order they're shown."""
    from .webmap import POINT_URL

    f = facts or {}
    port = cfg.map_port
    ips = f.get("addresses") or []
    urls = {
        "online": "http://captive.apple.com/hotspot-detect.html",
        "adsb": f"{cfg.adsb_api.rstrip('/')}/v2/point/{cfg.home_lat:.4f}/{cfg.home_lon:.4f}/1",
        "adsbdb": f"{cfg.adsbdb_api.rstrip('/')}/v0/callsign/UAL1",
        "ntfy": f"{cfg.ntfy_server.rstrip('/')}/v1/health" if cfg.ntfy_topic else "",
        "point": POINT_URL,
        "parts": "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css",
        "tiles": "https://tiles.openfreemap.org/styles/dark",
        "github": "https://github.com/",
        "gates": {"aeroapi": "https://aeroapi.flightaware.com/aeroapi/", "aviationstack": "https://api.aviationstack.com/"}
        .get(cfg.gates_api if cfg.gates_key else "", ""),
        "local": f"http://localhost:{port}/api/config",
        "lan": f"http://{ips[0]}:{port}/api/config" if ips else "",
    }
    heads = {"adsb": key_header(cfg.adsb_api_key)}
    with ThreadPoolExecutor(max_workers=8) as pool:  # all at once: about as long as the slowest one
        jobs = {k: pool.submit(probe, u, 4 if k in ("local", "lan") else 8, heads.get(k)) for k, u in urls.items() if u}
        got = {k: j.result() for k, j in jobs.items()}
    out: list[tuple[str, str, str]] = []

    def add(mark: str, what: str, detail: str = "") -> None:
        out.append((mark, what, detail))

    def reach(key: str, name: str, lost: str, mark: str = MIGHT) -> bool:
        """A site that only has to answer."""
        r = got.get(key)
        if r is None:
            return False
        if "error" in r:
            add(mark, f"{name}: can't be reached from this network", lost)
        elif is_page(r) and key not in ("point", "github") and r["status"] < 400:
            add(mark, f"{name}: something else answered", SIGN_IN + " " + lost)
        elif r["status"] in (403, 451):
            add(mark, f"{name}: blocked on this network", lost)
        else:
            add(OK, f"{name}: reachable")
            return True
        return False

    # --- the internet ---
    r = got["online"]
    online = "error" not in r and "Success" in r.get("text", "")
    if online:
        add(OK, "Internet: this computer is online")
    elif "error" in r and "error" in got["adsb"]:
        add(WONT, "Internet: this computer isn't online", "Join a Wi-Fi network (or your phone's Personal Hotspot). "
            "Until then there are no alerts; the widget and Siri on your phone carry on by themselves.")
    elif "error" not in r:
        add(WONT, "Internet: this Wi-Fi wants you to sign in first", "Open Safari, go to any website, and accept the "
            "Wi-Fi's page. Hotel and airport Wi-Fi often ask again every day. Until then nothing gets through.")

    # --- plane data ---
    r = got["adsb"]
    if "error" in r:
        if r["kind"] == "cert":
            add(WONT, "Plane data (adsb.lol): the secure connection was refused",
                "Either this network inspects secure connections (some work and school networks do), or Python's "
                "certificates aren't installed: on a Mac, double-click “Install Certificates.command” in "
                "Applications → Python 3.x.")
        else:
            add(WONT, "Plane data (adsb.lol): can't be reached from this network", f"({r['error']}) No alerts until it can. "
                "If other websites work, this network blocks it: try another network or your phone's hotspot.")
    elif r["status"] == 429:
        add(WONT, "Plane data (adsb.lol): this internet connection has asked too often",
            "adsb.lol allows about 500 requests a day per connection. On shared Wi-Fi (hotel, airport, school) everyone "
            "counts together, so it can be used up by other people. Alerts pause until it lets up (planespotter waits "
            "by itself). A free adsb.lol key (ADSB_API_KEY in your settings) gives you your own allowance.")
    elif r["status"] in (403, 451):
        add(WONT, "Plane data (adsb.lol): blocked on this network", "Try another network or your phone's hotspot.")
    elif is_page(r) or (r["status"] == 200 and '"ac"' not in r.get("text", "")):  # not adsb.lol's answer
        add(WONT, "Plane data (adsb.lol): something else answered", SIGN_IN)
    elif r["status"] >= 500:
        add(MIGHT, "Plane data (adsb.lol): it's having trouble right now", "Nothing to fix here: planespotter keeps trying.")
    else:
        add(OK, "Plane data (adsb.lol): working")
    used, limit = f.get("used"), f.get("limit")
    if used is not None and limit:
        add(MIGHT if used >= limit * 0.85 else OK, f"adsb.lol allowance: {used} of {limit} used in the last 24 hours",
            "Nearly used up: alerts slow to one check every 15 minutes until it frees up." if used >= limit * 0.85 else "")

    reach("adsbdb", "Routes and airline names (adsbdb)", "Alerts still work; they just won't say where planes are going.")
    if urls["ntfy"]:
        reach("ntfy", "Phone notifications (ntfy)", "Notifications, your Mac's info in the widget, and commands from "
              "your phone can't get through on this network.", WONT)
    else:
        add(MIGHT, "Phone notifications: not set up", "Run  python3 -m planespotter setup  and answer y at question 5.")
    if urls["gates"]:
        reach("gates", "Gate numbers", "Gate and take-off times won't work on this network.")
    reach("point", "Point page", "Point won't open on this network (your phone on the same Wi-Fi sees the same; "
          "mobile data will work).")
    parts = [k for k in ("parts", "tiles") if "error" in got[k] or got[k]["status"] >= 400 or (k == "parts" and is_page(got[k]))]
    if parts:
        add(MIGHT, "The map's drawing parts: can't be loaded on this network",
            "The map itself won't draw (unpkg.com or the map tiles are blocked); the lists and alerts still work.")
    else:
        add(OK, "The map's drawing parts: reachable")
    reach("github", "Updates (GitHub)", "python3 -m planespotter update  won't work on this network.")

    # --- this computer ---
    r = got["local"]
    running = "error" not in r and r["status"] == 200 and '"home"' in r.get("text", "")
    auto = f.get("autostart")
    if running:
        add(OK, f"planespotter is running (map: http://localhost:{port})")
    else:
        add(MIGHT, "planespotter isn't running on this computer right now",
            "Start it (and have it start by itself from now on):  python3 -m planespotter autostart")
    if auto is not None and not auto.get("installed"):
        add(MIGHT, "It isn't set to start by itself", "After a restart there'd be no alerts until you start it:  "
            "python3 -m planespotter autostart")
    if cfg.server:
        add(OK, "Phones: planespotter runs on your server, so the phone doesn't need this Mac")
    elif not cfg.map_lan:
        add(MIGHT, "Phones can't open the full app: MAP_LAN is off in your settings", "Set MAP_LAN=true to let phones "
            "on your Wi-Fi open it. (The phone's own map, the widget and Siri work without it.)")
    elif not ips:
        add(MIGHT, "Phones can't reach this computer: it has no network address", "Join your Wi-Fi. (The phone's own "
            "map, the widget and Siri work without the Mac.)")
    else:
        vpn = kind_of(ips[0]) != "lan"
        lan = got.get("lan") or {}
        lan_ok = "error" not in lan and lan.get("status") == 200
        if running and not lan_ok:
            add(WONT, f"Phones can't open the full app at http://{ips[0]}:{port}", "It answers on this computer but not on "
                "its network address. Restart it:  python3 -m planespotter autostart")
        elif vpn:
            add(MIGHT, f"A VPN seems to be on (this computer's address is {ips[0]})", "A phone on your Wi-Fi may not reach "
                "the full app while it's on. The phone then uses its own map.")
        else:
            add(OK, f"Phones on this Wi-Fi: the full app is at http://{ips[0]}:{port}",
                "If a phone on the same Wi-Fi still can't open it, the Wi-Fi keeps devices apart. Guest networks do, and "
                "so does most hotel, airport and school Wi-Fi. The phone then uses its own map, which needs no Mac.")
        if f.get("firewall"):
            add(MIGHT, "macOS's firewall is on", "If your phone can't open the full app: System Settings → Network → "
                "Firewall → Options, and allow incoming connections for Python.")
    if f.get("mac"):
        if f.get("icloud"):
            add(OK, "Scriptable's iCloud folder is on this Mac:  python3 -m planespotter phone --install  will work")
        else:
            add(MIGHT, "Scriptable's iCloud folder isn't on this Mac", "Putting the script on your phone needs copy and "
                "paste:  python3 -m planespotter phone --copy widget  (or turn on iCloud Drive with your iPhone's Apple ID).")
    return out


def cmd_doctor(cfg, args) -> int:
    print("Checking this computer and this network (about 10 seconds)…\n")
    results = diagnose(cfg, facts=facts(cfg))
    for mark, what, detail in results:
        print(f"{mark} {what}")
        if detail:
            print(f"    {detail}")
    wont = sum(1 for m, _, _ in results if m == WONT)
    might = sum(1 for m, _, _ in results if m == MIGHT)
    print()
    if wont:
        print(f"{WONT} {wont} thing{'s' if wont != 1 else ''} won't work here" + (f", and {might} might not" if might else "") + ".")
    elif might:
        print(f"{MIGHT} It works here, with {might} thing{'s' if might != 1 else ''} to know about (marked ⚠).")
    else:
        print(f"{OK} Everything works on this network.")
    return 1 if wont else 0
