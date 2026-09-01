from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
LOCAL_PATH = ROOT / "config.local.yaml"
KIOSK_KEYS = ("unit_id", "timezone", "gps", "network", "display", "operators", "validators")
# Never take these from config.local.yaml — they must follow git / config.yaml.
TRACKED_KEYS = ("software_version", "splash")

_DEFAULTS = {
    "unit_id": "CPE020",
    "software_version": "6.20",
    "timezone": "Australia/Adelaide",
    "splash": {"image": "assets/splash.svg", "duration_seconds": 5},
    "gps": {
        "mode": "auto",
        "interval_seconds": 2,
        "serial_port": "/dev/ttyUSB0",
        "baud": 9600,
    },
    "network": {
        "allowed_ssids": ["Depot-WiFi"],
        "credentials": {},
        "upload_url": "",
        "ntp_sync": True,
    },
    "display": {"brightness": 3, "volume": 4},
    "operators": [{"badge": "7141", "pin": "8171", "name": "Driver"}],
    "validators": [{"id": 1, "link": "mock", "online": False}],
    "updates": {
        "enabled": True,
        "repo": "nicbenn/touchscreen-bcu-project",
        "branch": "main",
    },
}


def load_config() -> dict:
    data = deepcopy(_DEFAULTS)
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if isinstance(loaded, dict):
            _deep_merge(data, loaded)
    if LOCAL_PATH.exists():
        with LOCAL_PATH.open("r", encoding="utf-8") as handle:
            local = yaml.safe_load(handle) or {}
        if isinstance(local, dict):
            cleaned = _without_tracked(local)
            if cleaned != local:
                _dump_local(cleaned)
            _deep_merge(data, cleaned)
    return data


def snapshot_kiosk_settings() -> None:
    """Keep unit-specific settings out of git so kiosks can pull version bumps."""
    cfg = load_config()
    overlay = {key: deepcopy(cfg[key]) for key in KIOSK_KEYS if key in cfg}
    _write_local(overlay)


def save_display(brightness: int, volume: int) -> dict:
    cfg = load_config()
    cfg["display"]["brightness"] = max(1, min(5, int(brightness)))
    cfg["display"]["volume"] = max(1, min(5, int(volume)))
    _write_local({"display": cfg["display"]})
    return cfg


def _without_tracked(data: dict) -> dict:
    cleaned = deepcopy(data)
    for key in TRACKED_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _dump_local(data: dict) -> None:
    with LOCAL_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def _write_local(overlay: dict) -> None:
    existing: dict = {}
    if LOCAL_PATH.exists():
        with LOCAL_PATH.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if isinstance(loaded, dict):
            existing = loaded
    existing = _without_tracked(existing)
    _deep_merge(existing, _without_tracked(overlay))
    _dump_local(existing)


def asset_path(relative: str) -> Path:
    path = Path(relative)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
