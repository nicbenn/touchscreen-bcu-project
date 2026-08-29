from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

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
        _deep_merge(data, loaded)
    return data


def save_display(brightness: int, volume: int) -> dict:
    cfg = load_config()
    cfg["display"]["brightness"] = max(1, min(5, int(brightness)))
    cfg["display"]["volume"] = max(1, min(5, int(volume)))
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(cfg, handle, sort_keys=False, allow_unicode=True)
    return cfg


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
