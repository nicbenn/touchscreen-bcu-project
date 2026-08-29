"""Build data/routes.json from Adelaide Metro GTFS (bus, train, tram)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIP_PATH = ROOT / "data" / "google_transit.zip"
OUT_PATH = ROOT / "data" / "routes.json"
METRO_TYPES = {"0": "tram", "2": "train", "3": "bus", "4": "ferry"}


def _hhmm(raw: str) -> str | None:
    parts = (raw or "").split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return None
    h = h % 24
    return f"{h:02d}:{m:02d}"


def build() -> dict:
    if not ZIP_PATH.exists():
        raise SystemExit(f"Missing {ZIP_PATH}")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        with zf.open("routes.txt") as handle:
            routes_rows = list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")))
        by_id = {}
        for row in routes_rows:
            mode = METRO_TYPES.get(row["route_type"])
            if not mode:
                continue
            by_id[row["route_id"]] = {
                "code": (row.get("route_short_name") or "").strip(),
                "name": (row.get("route_long_name") or "").strip(),
                "mode": mode,
                "variants": {},
            }

        trip_meta = {}
        with zf.open("trips.txt") as handle:
            for trip in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")):
                route = by_id.get(trip["route_id"])
                if not route:
                    continue
                direction = "Out" if trip.get("direction_id") != "1" else "In"
                headsign = (trip.get("trip_headsign") or route["name"]).strip() or route["name"]
                key = (headsign, direction)
                if key not in route["variants"]:
                    route["variants"][key] = {"headsign": headsign, "direction": direction, "times": set()}
                trip_meta[trip["trip_id"]] = (trip["route_id"], key)

        with zf.open("stop_times.txt") as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
            for stop in reader:
                if stop.get("stop_sequence") not in {"1", "01"}:
                    continue
                meta = trip_meta.get(stop["trip_id"])
                if not meta:
                    continue
                route_id, key = meta
                hhmm = _hhmm(stop.get("departure_time") or stop.get("arrival_time") or "")
                if hhmm:
                    by_id[route_id]["variants"][key]["times"].add(hhmm)

    packed = []
    for route in by_id.values():
        variants = []
        for i, variant in enumerate(sorted(route["variants"].values(), key=lambda v: (v["direction"], v["headsign"])), start=1):
            times = sorted(variant["times"], key=lambda t: (int(t[:2]) * 60 + int(t[3:])))
            variants.append(
                {
                    "headsign": variant["headsign"],
                    "direction": variant["direction"],
                    "section": i,
                    "times": times[:96],
                }
            )
        if not variants:
            variants = [{"headsign": route["name"], "direction": "Out", "section": 1, "times": []}]
        packed.append({"code": route["code"], "name": route["name"], "mode": route["mode"], "variants": variants})

    packed.sort(key=lambda r: (r["mode"] != "bus", len(r["code"]), r["code"]))
    return {"source": "Adelaide Metro GTFS", "routes": packed}


if __name__ == "__main__":
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(payload['routes'])} routes to {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")
