from __future__ import annotations

import socket
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import tzdata  # noqa: F401  # IANA zones on Windows
except ImportError:
    pass

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from bcu import db
from bcu import routes as routebook
from bcu.config import ROOT, asset_path, load_config, save_display
from bcu.gps import GpsService
from bcu.network import maybe_upload, sync_system_clock, wifi_ssid
from bcu import updater
from bcu.validators import snapshot as validator_snapshot

gps_service = GpsService()


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(ROOT / "web" / "templates"),
        static_folder=str(ROOT / "web" / "static"),
    )
    @app.errorhandler(Exception)
    def _unhandled(exc):
        if isinstance(exc, HTTPException):
            return exc
        log = ROOT / "data" / "bcu-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        app.logger.exception("BCU request failed")
        return ("Internal error. See data/bcu-error.log", 500)

    db.init_db()
    gps_service.start()
    trip = db.active_trip()
    if trip:
        gps_service.set_active_trip(trip["id"])

    @app.get("/")
    def index():
        return render_template("index.html", cfg=public_config())

    @app.get("/api/config")
    def api_config():
        return jsonify(public_config())

    @app.get("/splash")
    def splash():
        cfg = load_config()
        path = asset_path(cfg["splash"]["image"])
        if not path.exists():
            fallback = ROOT / "assets" / "splash.svg"
            if fallback.exists():
                return send_file(fallback)
            return ("", 404)
        return send_file(path)

    @app.get("/api/status")
    def api_status():
        cfg = load_config()
        tz = local_tz(cfg)
        now = datetime.now(tz)
        gps = gps_service.snapshot()
        shift = db.active_shift()
        trip = db.active_trip(shift["id"] if shift else None)
        return jsonify(
            {
                "now": now.isoformat(),
                "clock": now.strftime("%H:%M"),
                "datetime": now.strftime("%d/%m/%Y %H:%M"),
                "gps": gps,
                "validators": validator_snapshot(),
                "ssid": wifi_ssid(),
                "shift": shift,
                "trip": trip,
                "display": cfg["display"],
                "unit_id": cfg["unit_id"],
                "software_version": cfg["software_version"],
                "last_update": cfg.get("_last_update") or _last_update_label(cfg, tz),
            }
        )

    @app.post("/api/shift/start")
    def api_shift_start():
        body = request.get_json(force=True) or {}
        shift_number = str(body.get("shift_number") or "").strip()
        badge = str(body.get("badge") or "").strip()
        pin = str(body.get("pin") or "").strip()
        if not shift_number or not badge or not pin:
            return jsonify({"ok": False, "error": "Missing fields"}), 400
        operator = db.verify_operator(badge, pin)
        if not operator:
            return jsonify({"ok": False, "error": "Unknown badge or PIN"}), 401
        existing = db.active_shift()
        if existing:
            db.end_shift(existing["id"])
        shift = db.start_shift(shift_number, badge)
        return jsonify({"ok": True, "shift": shift, "operator": operator})

    @app.post("/api/shift/end")
    def api_shift_end():
        shift = db.active_shift()
        if not shift:
            return jsonify({"ok": False, "error": "No active shift"}), 400
        gps_service.set_active_trip(None)
        ended = db.end_shift(shift["id"])
        db.dump_shift_json(shift["id"])
        return jsonify({"ok": True, "shift": ended})

    @app.get("/api/routes")
    def api_routes():
        query = (request.args.get("q") or "").strip()
        return jsonify({"query": query, "routes": routebook.search(query)})

    @app.post("/api/trip/start")
    def api_trip_start():
        shift = db.active_shift()
        if not shift:
            return jsonify({"ok": False, "error": "Start a shift first"}), 400
        body = request.get_json(silent=True) or {}
        trip = db.start_trip(shift["id"], body)
        gps_service.set_active_trip(trip["id"])
        return jsonify({"ok": True, "trip": trip})

    @app.post("/api/trip/end")
    def api_trip_end():
        shift = db.active_shift()
        trip = db.active_trip(shift["id"] if shift else None)
        if not trip:
            return jsonify({"ok": False, "error": "No active trip"}), 400
        gps_service.set_active_trip(None)
        ended = db.end_trip(trip["id"])
        return jsonify({"ok": True, "trip": ended, "points": db.trip_points(trip["id"])})

    @app.get("/api/trip/track")
    def api_trip_track():
        trip_id = request.args.get("trip_id", type=int)
        if not trip_id:
            trip = db.active_trip()
            trip_id = trip["id"] if trip else None
        if not trip_id:
            return jsonify({"points": []})
        return jsonify({"trip_id": trip_id, "points": db.trip_points(trip_id)})

    @app.post("/api/settings")
    def api_settings():
        body = request.get_json(force=True) or {}
        cfg = save_display(body.get("brightness", 3), body.get("volume", 4))
        return jsonify({"ok": True, "display": cfg["display"]})

    @app.post("/api/sync")
    def api_sync():
        clock = sync_system_clock()
        upload = maybe_upload()
        return jsonify({"ok": True, "clock": clock, "upload": upload})

    @app.get("/api/update/check")
    def api_update_check():
        return jsonify(updater.check())

    @app.post("/api/update/apply")
    def api_update_apply():
        result = updater.apply()
        code = 200 if result.get("ok") else 400
        return jsonify(result), code

    return app


def public_config() -> dict:
    cfg = load_config()
    tz = local_tz(cfg)
    return {
        "unit_id": cfg["unit_id"],
        "software_version": cfg["software_version"],
        "splash_seconds": int(cfg["splash"].get("duration_seconds") or 5),
        "display": cfg["display"],
        "last_update": _last_update_label(cfg, tz),
    }


def local_tz(cfg: dict):
    name = cfg.get("timezone") or "Australia/Adelaide"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, OSError, ValueError):
        return timezone(timedelta(hours=9, minutes=30), name="ACST")


def _last_update_label(cfg: dict, tz) -> str:
    path = asset_path(cfg["splash"]["image"])
    if not path.exists():
        path = ROOT / "config.yaml"
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=tz)
    return stamp.strftime("%d/%m/%Y %H:%M")


def main() -> None:
    app = create_app()
    port = 8080
    print("BCU kiosk:")
    print(f"  http://127.0.0.1:{port}")
    for ip in _local_ips():
        print(f"  http://{ip}:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


if __name__ == "__main__":
    main()
