from __future__ import annotations

import socket
import time
from typing import Any

from bcu.config import load_config

_cache: list[dict] = []
_cache_at = 0.0
_CACHE_SEC = 2.0


def snapshot() -> list[dict]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache and now - _cache_at < _CACHE_SEC:
        return _cache
    cfg = load_config()
    raw = cfg.get("validators") or [{"id": 1}]
    result = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            vid = int(item.get("id") or index)
            ok = _probe(item)
        else:
            vid = index
            ok = False
        result.append({"id": vid, "ok": ok})
    result.sort(key=lambda v: v["id"])
    _cache = result
    _cache_at = now
    return result


def _probe(item: dict[str, Any]) -> bool:
    link = str(item.get("link") or "mock").lower()
    if link == "tcp":
        host = str(item.get("host") or "").strip()
        port = int(item.get("port") or 0)
        if not host or not port:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False
    if link == "serial":
        device = str(item.get("device") or item.get("serial_port") or "").strip()
        if not device:
            return False
        try:
            import serial  # type: ignore
        except Exception:
            return False
        try:
            ser = serial.Serial(device, int(item.get("baud") or 9600), timeout=0.2)
            ser.close()
            return True
        except Exception:
            return False
    return bool(item.get("online"))
