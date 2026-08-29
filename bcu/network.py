from __future__ import annotations

import json
import platform
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bcu import db
from bcu.config import load_config


def wifi_ssid() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":", 1)[1].strip() or None
            return None
        out = subprocess.check_output(
            ["iwgetid", "-r"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip() or None
    except Exception:
        return None


def online() -> bool:
    return wifi_ssid() is not None or _http_reachable()


def _http_reachable() -> bool:
    try:
        urllib.request.urlopen("https://www.google.com", timeout=2)
        return True
    except Exception:
        return False


def network_time() -> str | None:
    try:
        with urllib.request.urlopen("https://www.google.com", timeout=3) as resp:
            date_hdr = resp.headers.get("Date")
            if not date_hdr:
                return None
            dt = parsedate_to_datetime(date_hdr)
            return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def sync_system_clock() -> dict:
    """When Wi-Fi is up, refresh NTP (Linux) or record network time (elsewhere)."""
    cfg = load_config()
    ssid = wifi_ssid()
    result = {"ssid": ssid, "ntp": False, "network_time": None, "applied": False}
    if not cfg["network"].get("ntp_sync"):
        return result
    if not ssid and not online():
        return result
    net_time = network_time()
    result["network_time"] = net_time
    if platform.system() == "Linux":
        try:
            subprocess.run(
                ["timedatectl", "set-ntp", "true"],
                check=False,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            result["ntp"] = True
            result["applied"] = True
        except Exception:
            pass
    else:
        result["applied"] = net_time is not None
    return result


def maybe_upload() -> dict:
    cfg = load_config()
    ssid = wifi_ssid()
    allowed = set(cfg["network"].get("allowed_ssids") or [])
    url = (cfg["network"].get("upload_url") or "").strip()
    pending = db.pending_uploads()
    report = {
        "ssid": ssid,
        "allowed": ssid in allowed if ssid else False,
        "pending": len(pending),
        "uploaded": 0,
        "reason": None,
    }
    if not pending:
        report["reason"] = "nothing to upload"
        return report
    if not ssid or ssid not in allowed:
        report["reason"] = "not on an allowed depot SSID"
        return report
    if not url:
        # Keep local JSON copies until a server URL is configured.
        for shift in pending:
            db.dump_shift_json(shift["id"])
        report["reason"] = "no upload_url yet; wrote local JSON"
        return report
    try:
        payload = json.dumps({"unit_id": cfg["unit_id"], "shifts": pending}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                db.mark_uploaded([s["id"] for s in pending])
                report["uploaded"] = len(pending)
                return report
        report["reason"] = "server rejected upload"
    except urllib.error.URLError as exc:
        report["reason"] = str(exc.reason)
    except Exception as exc:
        report["reason"] = str(exc)
    return report
