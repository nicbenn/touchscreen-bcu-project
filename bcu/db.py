from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from bcu.config import ROOT, load_config

DB_PATH = ROOT / "data" / "bcu.sqlite"
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS operators (
                badge TEXT PRIMARY KEY,
                pin_hash TEXT NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_number TEXT NOT NULL,
                badge TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                uploaded INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY (shift_id) REFERENCES shifts(id)
            );
            CREATE TABLE IF NOT EXISTS gps_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                speed REAL,
                heading REAL,
                source TEXT,
                FOREIGN KEY (trip_id) REFERENCES trips(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        _ensure_trip_columns(conn)
    _seed_operators()


def _ensure_trip_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trips)")}
    columns = {
        "route_code": "TEXT",
        "route_name": "TEXT",
        "headsign": "TEXT",
        "direction": "TEXT",
        "section": "INTEGER",
        "trip_time": "TEXT",
        "trip_missing": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, spec in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE trips ADD COLUMN {name} {spec}")
    conn.commit()


def _hash_pin(badge: str, pin: str) -> str:
    return hashlib.sha256(f"{badge}:{pin}".encode("utf-8")).hexdigest()


def _seed_operators() -> None:
    cfg = load_config()
    with _lock, _connect() as conn:
        for op in cfg.get("operators") or []:
            badge = str(op["badge"])
            pin_hash = _hash_pin(badge, str(op["pin"]))
            conn.execute(
                """
                INSERT INTO operators (badge, pin_hash, name)
                VALUES (?, ?, ?)
                ON CONFLICT(badge) DO UPDATE SET pin_hash=excluded.pin_hash, name=excluded.name
                """,
                (badge, pin_hash, op.get("name") or badge),
            )
        conn.commit()


def seed_operators() -> None:
    _seed_operators()


def verify_operator(badge: str, pin: str) -> dict | None:
    _seed_operators()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM operators WHERE badge = ?", (badge,)).fetchone()
    if not row:
        return None
    if row["pin_hash"] != _hash_pin(badge, pin):
        return None
    return {"badge": row["badge"], "name": row["name"]}


def start_shift(shift_number: str, badge: str) -> dict:
    now = _now()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO shifts (shift_number, badge, started_at) VALUES (?, ?, ?)",
            (shift_number, badge, now),
        )
        conn.commit()
        shift_id = cur.lastrowid
    return get_shift(shift_id)


def end_shift(shift_id: int) -> dict | None:
    now = _now()
    with _lock, _connect() as conn:
        active = conn.execute(
            "SELECT id FROM trips WHERE shift_id = ? AND ended_at IS NULL",
            (shift_id,),
        ).fetchone()
        if active:
            conn.execute("UPDATE trips SET ended_at = ? WHERE id = ?", (now, active["id"]))
        conn.execute("UPDATE shifts SET ended_at = ? WHERE id = ?", (now, shift_id))
        conn.commit()
    return get_shift(shift_id)


def get_shift(shift_id: int) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    return dict(row) if row else None


def active_shift() -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM shifts WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def start_trip(shift_id: int, meta: dict | None = None) -> dict:
    now = _now()
    meta = meta or {}
    fields = _trip_meta(meta)
    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM trips WHERE shift_id = ? AND ended_at IS NULL",
            (shift_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE trips SET route_code=?, route_name=?, headsign=?, direction=?,
                    section=?, trip_time=?, trip_missing=?
                WHERE id=?
                """,
                (*fields, existing["id"]),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM trips WHERE id = ?", (existing["id"],)).fetchone()
            return dict(row)
        cur = conn.execute(
            """
            INSERT INTO trips (
                shift_id, started_at, route_code, route_name, headsign, direction,
                section, trip_time, trip_missing
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (shift_id, now, *fields),
        )
        conn.commit()
        trip_id = cur.lastrowid
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return dict(row)


def _trip_meta(meta: dict) -> tuple:
    return (
        str(meta.get("route_code") or "").strip() or None,
        str(meta.get("route_name") or "").strip() or None,
        str(meta.get("headsign") or "").strip() or None,
        str(meta.get("direction") or "Out").strip(),
        int(meta.get("section") or 1),
        str(meta.get("trip_time") or "").strip() or None,
        1 if meta.get("trip_missing") else 0,
    )


def end_trip(trip_id: int) -> dict | None:
    now = _now()
    with _lock, _connect() as conn:
        conn.execute("UPDATE trips SET ended_at = ? WHERE id = ?", (now, trip_id))
        conn.commit()
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return dict(row) if row else None


def active_trip(shift_id: int | None = None) -> dict | None:
    with _lock, _connect() as conn:
        if shift_id is None:
            row = conn.execute(
                "SELECT * FROM trips WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM trips WHERE shift_id = ? AND ended_at IS NULL",
                (shift_id,),
            ).fetchone()
    return dict(row) if row else None


def add_gps_point(trip_id: int, lat: float, lon: float, speed, heading, source: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO gps_points (trip_id, recorded_at, lat, lon, speed, heading, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_now(), lat, lon, speed, heading, source),
        )
        conn.commit()


def trip_points(trip_id: int) -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM gps_points WHERE trip_id = ? ORDER BY id ASC",
            (trip_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def pending_uploads() -> list[dict]:
    with _lock, _connect() as conn:
        shifts = conn.execute(
            "SELECT * FROM shifts WHERE ended_at IS NOT NULL AND uploaded = 0"
        ).fetchall()
        payloads = []
        for shift in shifts:
            trips = conn.execute(
                "SELECT * FROM trips WHERE shift_id = ?", (shift["id"],)
            ).fetchall()
            trip_payloads = []
            for trip in trips:
                points = conn.execute(
                    "SELECT recorded_at, lat, lon, speed, heading, source FROM gps_points WHERE trip_id = ?",
                    (trip["id"],),
                ).fetchall()
                trip_payloads.append(
                    {
                        **dict(trip),
                        "points": [dict(p) for p in points],
                    }
                )
            payloads.append({**dict(shift), "trips": trip_payloads})
    return payloads


def mark_uploaded(shift_ids: list[int]) -> None:
    if not shift_ids:
        return
    with _lock, _connect() as conn:
        conn.executemany(
            "UPDATE shifts SET uploaded = 1 WHERE id = ?",
            [(sid,) for sid in shift_ids],
        )
        conn.commit()


def last_update_time() -> str | None:
    if not DB_PATH.exists():
        return None
    stamp = datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=timezone.utc)
    return stamp.isoformat()


def dump_shift_json(shift_id: int) -> Path | None:
    shift = get_shift(shift_id)
    if not shift:
        return None
    with _lock, _connect() as conn:
        trips = conn.execute("SELECT * FROM trips WHERE shift_id = ?", (shift_id,)).fetchall()
        payload = dict(shift)
        payload["trips"] = []
        for trip in trips:
            points = conn.execute(
                "SELECT * FROM gps_points WHERE trip_id = ?", (trip["id"],)
            ).fetchall()
            payload["trips"].append({**dict(trip), "points": [dict(p) for p in points]})
    out = ROOT / "data" / f"shift-{shift_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
