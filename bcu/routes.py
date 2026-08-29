from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from bcu.config import ROOT

ROUTES_PATH = ROOT / "data" / "routes.json"


@lru_cache(maxsize=1)
def all_routes() -> list[dict]:
    if not ROUTES_PATH.exists():
        return []
    payload = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
    return payload.get("routes") or []


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
