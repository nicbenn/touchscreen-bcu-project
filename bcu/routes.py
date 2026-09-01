from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from bcu.config import ROOT

ROUTES_PATH = ROOT / "data" / "routes.json"


@lru_cache(maxsize=1)
def all_routes() -> list[dict]:
    if ROUTES_PATH.exists():
        payload = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
        routes = payload.get("routes") or []
        if routes:
            return routes
    return _FALLBACK_ROUTES


def search(query: str, limit: int = 80) -> list[dict]:
    q = (query or "").strip().upper()
    if not q:
        return []
    scored: list[tuple[int, dict]] = []
    for route in all_routes():
        code = str(route.get("code") or "")
        rank = _rank(code, q)
        if rank is None:
            continue
        for variant in route.get("variants") or []:
            scored.append(
                (
                    rank,
                    {
                        "code": code,
                        "name": route.get("name") or "",
                        "mode": route.get("mode") or "bus",
                        "headsign": variant.get("headsign") or route.get("name") or code,
                        "direction": variant.get("direction") or "Out",
                        "section": int(variant.get("section") or 1),
                        "times": variant.get("times") or [],
                    },
                )
            )
    scored.sort(key=lambda item: (item[0], item[1]["code"], item[1]["direction"], item[1]["headsign"]))
    return [item[1] for item in scored[:limit]]


def _rank(code: str, q: str) -> int | None:
    code_u = code.upper()
    digits = "".join(ch for ch in code_u if ch.isdigit())
    if code_u == q:
        return 0
    if code_u.startswith(q):
        return 1
    if digits == q:
        return 2
    if digits.startswith(q):
        return 3
    if q in code_u:
        return 4
    return None


def _variant(headsign: str, direction: str, times: list[str], section: int = 1) -> dict:
    return {
        "headsign": headsign,
        "direction": direction,
        "section": section,
        "times": times,
    }


# Used when data/routes.json has not been built from GTFS yet.
_FALLBACK_ROUTES = [
    {
        "code": "G10",
        "name": "Golden Grove Interchange to City",
        "mode": "bus",
        "variants": [
            _variant("City", "In", ["07:00", "07:30", "08:00", "08:30", "09:00"]),
            _variant("Golden Grove Interchange", "Out", ["15:30", "16:00", "16:30", "17:00", "17:30"]),
        ],
    },
    {
        "code": "140",
        "name": "Glen Osmond to City",
        "mode": "bus",
        "variants": [
            _variant("City", "In", ["07:15", "07:45", "08:15", "08:45"]),
            _variant("Glen Osmond", "Out", ["16:10", "16:40", "17:10", "17:40"]),
        ],
    },
    {
        "code": "141",
        "name": "Stonyfell to City",
        "mode": "bus",
        "variants": [
            _variant("City", "In", ["07:20", "07:50", "08:20"]),
            _variant("Stonyfell", "Out", ["16:20", "16:50", "17:20"]),
        ],
    },
    {
        "code": "98A",
        "name": "City to Adelaide Airport",
        "mode": "bus",
        "variants": [
            _variant("Airport", "Out", ["06:30", "07:00", "07:30", "08:00", "08:30"]),
            _variant("City", "In", ["16:00", "16:30", "17:00", "17:30"]),
        ],
    },
    {
        "code": "GLNELG",
        "name": "Glenelg Tram",
        "mode": "tram",
        "variants": [
            _variant("Glenelg", "Out", ["07:00", "07:15", "07:30", "07:45", "08:00"]),
            _variant("Entertainment Centre", "In", ["16:00", "16:15", "16:30", "16:45", "17:00"]),
        ],
    },
]
