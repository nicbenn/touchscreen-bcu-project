from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from bcu import db
from bcu.config import load_config

_OFFLINE = "No network found — continuing offline"
_connect_lock = threading.Lock()


def wifi_ssid() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    return line.split(":", 1)[1].strip() or None
            return None
        try:
            out = subprocess.check_output(
                ["iwgetid", "-r"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if out.strip():
                return out.strip()
        except Exception:
            pass
        nmcli = shutil.which("nmcli")
        if not nmcli:
            return None
        out = subprocess.check_output(
            [nmcli, "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        for line in out.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1].strip() or None
        return None
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


def network_status() -> dict:
    ssid = wifi_ssid()
    return {
        "ssid": ssid,
        "connected": bool(ssid),
        "online": online(),
        "message": f"Connected to {ssid}" if ssid else "Offline",
    }


def connect_configured(timeout: float = 12) -> dict:
    """Join a configured SSID, or report offline if none are in range."""
    with _connect_lock:
        return _connect_configured(timeout)


def _connect_configured(timeout: float) -> dict:
    wanted = _wanted_ssids()
    current = wifi_ssid()
    if current and current in wanted:
        return _ok(current)
    targets = _credential_targets()
    if not targets:
        if current:
            return _ok(current)
        return _offline(_OFFLINE)
    visible = _scan_ssids()
    names = [ssid for ssid, _pw in targets]
    if visible is not None and not any(ssid in visible for ssid in names):
        return _not_found(current)
    deadline = time.monotonic() + max(4.0, timeout)
    for ssid, password in targets:
        if visible is not None and ssid not in visible:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            break
        if _associate(ssid, password, remaining):
            joined = wifi_ssid() or ssid
            return _ok(joined)
    current = wifi_ssid()
    if current and current in wanted:
        return _ok(current)
    if current or online():
        return _not_found(current)
    return _offline("Could not connect — continuing offline")


def _ok(ssid: str) -> dict:
    return {
        "ok": True,
        "connected": True,
        "offline": False,
        "ssid": ssid,
        "message": f"Connected to {ssid}",
    }


def _offline(message: str) -> dict:
    return {
        "ok": False,
        "connected": False,
        "offline": True,
        "ssid": wifi_ssid(),
        "message": message,
    }


def _not_found(current: str | None) -> dict:
    if current:
        return {
            "ok": True,
            "connected": True,
            "offline": False,
            "ssid": current,
            "message": "Update network not found — continuing",
        }
    if online():
        return {
            "ok": True,
            "connected": False,
            "offline": False,
            "ssid": None,
            "message": "Update network not found — continuing",
        }
    return _offline(_OFFLINE)


def _wanted_ssids() -> set[str]:
    net = load_config().get("network") or {}
    names: set[str] = set()
    for ssid in net.get("allowed_ssids") or []:
        text = str(ssid or "").strip()
        if text:
            names.add(text)
    creds = net.get("credentials") or {}
    if isinstance(creds, dict):
        for ssid in creds:
            text = str(ssid or "").strip()
            if text:
                names.add(text)
    return names


def _credential_targets() -> list[tuple[str, str]]:
    creds = (load_config().get("network") or {}).get("credentials") or {}
    if not isinstance(creds, dict):
        return []
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ssid, password in creds.items():
        name = str(ssid or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        targets.append((name, str(password or "")))
    return targets


def _scan_ssids() -> set[str] | None:
    try:
        if platform.system() == "Windows":
            return _scan_windows()
        return _scan_linux()
    except Exception:
        return None


def _scan_windows() -> set[str] | None:
    result = _cmd(["netsh", "wlan", "show", "networks"], timeout=10)
    if result is None or result.returncode != 0:
        return None
    found: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("SSID") and "BSSID" not in stripped:
            name = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if name:
                found.add(name)
    return found


def _scan_linux() -> set[str] | None:
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return None
    _cmd([nmcli, "device", "wifi", "rescan"], timeout=6)
    time.sleep(1.2)
    result = _cmd([nmcli, "-t", "-f", "SSID", "device", "wifi", "list"], timeout=8)
    if result is None or result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _associate(ssid: str, password: str, timeout: float) -> bool:
    try:
        if platform.system() == "Windows":
            return _associate_windows(ssid, password, timeout)
        return _associate_linux(ssid, password, timeout)
    except Exception:
        return False


def _associate_windows(ssid: str, password: str, timeout: float) -> bool:
    profile = _wlan_profile_xml(ssid, password)
    path = None
    try:
        handle = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
        path = Path(handle.name)
        handle.write(profile)
        handle.close()
        _cmd(["netsh", "wlan", "add", "profile", f"filename={path}"], timeout=8)
        _cmd(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"], timeout=8)
        return _wait_for_ssid(ssid, timeout)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _associate_linux(ssid: str, password: str, timeout: float) -> bool:
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return False
    wait = str(max(3, int(timeout)))
    up = _cmd([nmcli, "-w", wait, "connection", "up", ssid], timeout=timeout + 2)
    if up is not None and up.returncode == 0:
        return _wait_for_ssid(ssid, 3)
    args = [nmcli, "-w", wait, "device", "wifi", "connect", ssid]
    if password:
        args.extend(["password", password])
    joined = _cmd(args, timeout=timeout + 3)
    if joined is None or joined.returncode != 0:
        return False
    return _wait_for_ssid(ssid, 3)


def _wait_for_ssid(ssid: str, timeout: float) -> bool:
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if wifi_ssid() == ssid:
            return True
        time.sleep(0.4)
    return wifi_ssid() == ssid


def _wlan_profile_xml(ssid: str, password: str) -> str:
    name = escape(ssid)
    key = escape(password)
    return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{name}</name>
  <SSIDConfig>
    <SSID>
      <name>{name}</name>
    </SSID>
  </SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{key}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>
"""


def _cmd(args: list[str], timeout: float = 8) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="timeout")
