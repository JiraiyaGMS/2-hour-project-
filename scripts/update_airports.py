"""Rebuild planespotter/data/airports.csv from OurAirports (public domain data).

    python3 scripts/update_airports.py

Keeps large and medium airports, plus small ones with an official IATA or ICAO
code, which is enough to name where almost any flight took off and landed.
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = Path(__file__).resolve().parent.parent / "planespotter" / "data" / "airports.csv"
KIND = {"large_airport": "L", "medium_airport": "M", "small_airport": "S"}


def main(source: str = SOURCE) -> None:
    if source.startswith("http"):
        req = urllib.request.Request(source, headers={"User-Agent": "planespotter/1.0 (airport list update)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        kind = KIND.get(r["type"])
        if not kind:
            continue
        icao = r["icao_code"] or (r["gps_code"] if len(r["gps_code"]) == 4 else "")
        if kind == "S" and not (r["iata_code"] or r["icao_code"]):
            continue
        code = r["iata_code"] or icao or r["ident"]
        try:
            lat, lon = round(float(r["latitude_deg"]), 4), round(float(r["longitude_deg"]), 4)
        except ValueError:
            continue
        elev = r["elevation_ft"] or "0"
        rows.append([code, icao, kind, r["name"], r["municipality"], r["iso_country"], lat, lon, elev])
    rows.sort(key=lambda row: (row[6], row[7]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# Airports from OurAirports (https://ourairports.com/data/), public domain.\n")
        writer = csv.writer(fh)
        writer.writerow(["code", "icao", "kind", "name", "city", "country", "lat", "lon", "elevation_ft"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} airports to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
