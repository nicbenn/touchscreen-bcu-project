from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from bcu import db
from bcu.config import load_config

# Adelaide CBD — mock walk when no receiver is attached.
_MOCK_ORIGIN = (-34.9285, 138.6007)


@dataclass
class Fix:
    lat: float
    lon: float
    speed: float | None
    heading: float | None
    source: str
    at: str
    ok: bool


class GpsService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fix = Fix(
            lat=_MOCK_ORIGIN[0],
            lon=_MOCK_ORIGIN[1],
            speed=None,
            heading=None,
            source="none",
            at=datetime.now(timezone.utc).isoformat(),
            ok=False,
        )
        self._mock_t = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_trip_id: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gps", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_active_trip(self, trip_id: int | None) -> None:
        with self._lock:
            self._active_trip_id = trip_id

    def snapshot(self) -> dict:
        with self._lock:
            f = self._fix
            trip_id = self._active_trip_id
        try:
            at = datetime.fromisoformat(f.at)
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - at.astimezone(timezone.utc)).total_seconds()
        except ValueError:
            age = 999
        ok = f.ok and age < 8
        return {
            "lat": f.lat,
            "lon": f.lon,
            "speed": f.speed,
            "heading": f.heading,
            "source": f.source,
            "at": f.at,
            "ok": ok,
            "logging": trip_id is not None,
            "trip_id": trip_id,
        }

    def _loop(self) -> None:
        cfg = load_config()
        interval = max(1, int(cfg["gps"].get("interval_seconds") or 2))
        mode = (cfg["gps"].get("mode") or "auto").lower()
        reader = self._pick_reader(mode, cfg)
        while not self._stop.wait(interval):
            try:
                fix = reader()
            except Exception:
                fix = self._mock_fix(lost=True)
            with self._lock:
                self._fix = fix
                trip_id = self._active_trip_id
            if trip_id and fix.ok:
                try:
                    db.add_gps_point(trip_id, fix.lat, fix.lon, fix.speed, fix.heading, fix.source)
                except Exception:
                    pass

    def _pick_reader(self, mode: str, cfg: dict) -> Callable[[], Fix]:
        if mode == "mock":
            return lambda: self._mock_fix(lost=False)
        if mode in {"auto", "gpsd"}:
            gpsd = self._try_gpsd()
            if gpsd:
                return gpsd
        if mode in {"auto", "nmea"}:
            nmea = self._try_nmea(cfg)
            if nmea:
                return nmea
        return lambda: self._mock_fix(lost=False)

    def _try_gpsd(self) -> Callable[[], Fix] | None:
        try:
            from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE  # type: ignore
        except Exception:
            return None
        try:
            session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)
        except Exception:
            return None

        def read() -> Fix:
            for _ in range(8):
                report = session.next()  # type: ignore[attr-defined]
                if report.get("class") == "TPV" and hasattr(report, "lat"):
                    return Fix(
                        lat=float(report.lat),
                        lon=float(report.lon),
                        speed=getattr(report, "speed", None),
                        heading=getattr(report, "track", None),
                        source="gpsd",
                        at=datetime.now(timezone.utc).isoformat(),
                        ok=True,
                    )
            return self._mock_fix(lost=True)

        return read

    def _try_nmea(self, cfg: dict) -> Callable[[], Fix] | None:
        try:
            import serial  # type: ignore
        except Exception:
            return None
        port = cfg["gps"].get("serial_port") or "/dev/ttyUSB0"
        baud = int(cfg["gps"].get("baud") or 9600)
        try:
            ser = serial.Serial(port, baud, timeout=1)
        except Exception:
            return None

        def read() -> Fix:
            deadline = time.time() + 2
            while time.time() < deadline:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                    parsed = _parse_rmc(line)
                    if parsed:
                        lat, lon, speed, heading = parsed
                        return Fix(
                            lat=lat,
                            lon=lon,
                            speed=speed,
                            heading=heading,
                            source="nmea",
                            at=datetime.now(timezone.utc).isoformat(),
                            ok=True,
                        )
            return self._mock_fix(lost=True)

        return read

    def _mock_fix(self, lost: bool) -> Fix:
        self._mock_t += 0.08
        lat = _MOCK_ORIGIN[0] + 0.002 * math.sin(self._mock_t)
        lon = _MOCK_ORIGIN[1] + 0.003 * math.cos(self._mock_t * 0.7)
        return Fix(
            lat=lat,
            lon=lon,
            speed=8.3,
            heading=(self._mock_t * 40) % 360,
            source="mock",
            at=datetime.now(timezone.utc).isoformat(),
            ok=not lost,
        )


def _parse_rmc(line: str) -> tuple[float, float, float | None, float | None] | None:
    parts = line.split(",")
    if len(parts) < 10 or parts[2] != "A":
        return None
    try:
        lat = _nmea_coord(parts[3], parts[4])
        lon = _nmea_coord(parts[5], parts[6])
        speed = float(parts[7]) * 0.514444 if parts[7] else None
        heading = float(parts[8]) if parts[8] else None
        return lat, lon, speed, heading
    except (ValueError, IndexError):
        return None


def _nmea_coord(raw: str, hemi: str) -> float:
    if not raw:
        raise ValueError("empty")
    dot = raw.find(".")
    split = 2 if dot < 0 else max(2, dot - 2)
    deg = float(raw[:split])
    minutes = float(raw[split:])
    value = deg + minutes / 60.0
    if hemi in {"S", "W"}:
        value = -value
    return value
