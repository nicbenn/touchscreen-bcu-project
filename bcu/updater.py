from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from bcu.config import ROOT, load_config, snapshot_kiosk_settings

RESTART_FLAG = ROOT / "data" / ".bcu-restart"
SHA_FILE = ROOT / "data" / "installed-sha.txt"
STATUS_FILE = ROOT / "data" / "update-last.json"
TOKEN_FILE = ROOT / "data" / "github.token"
GIT_CANDIDATES = [
    "git",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]

_label_cache: tuple[str, float] = ("", 0.0)
_poll_started = False


def last_update_label(tz) -> str:
    """dd/mm/YYYY HH:MM of the commit currently installed on this unit."""
    global _label_cache
    now = time.monotonic()
    cached, at = _label_cache
    if cached and now - at < 15:
        return cached
    label = _format_commit_time(tz) or ""
    _label_cache = (label, now)
    return label


def start_origin_poll() -> None:
    global _poll_started
    if _poll_started:
        return
    _poll_started = True
    threading.Thread(target=_origin_poll_loop, name="git-origin", daemon=True).start()


def schedule_restart() -> None:
    RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
    RESTART_FLAG.write_text("1", encoding="utf-8")

    def _die() -> None:
        time.sleep(1.2)
        os._exit(0)

    threading.Thread(target=_die, name="bcu-restart", daemon=True).start()


def _origin_poll_loop() -> None:
    while True:
        time.sleep(120)
        git = _git_bin()
        if git and _is_git_checkout():
            _run(git, "fetch", "--quiet", "origin")


def _format_commit_time(tz) -> str | None:
    iso = _installed_commit_iso()
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def _installed_commit_iso() -> str | None:
    git = _git_bin()
    if git and _is_git_checkout():
        return _commit_iso(git, "HEAD")
    cfg = load_config().get("updates") or {}
    repo = str(cfg.get("repo") or "").strip()
    branch = str(cfg.get("branch") or "main")
    current = SHA_FILE.read_text(encoding="utf-8").strip() if SHA_FILE.exists() else ""
    if repo and current:
        return _github_commit_iso(repo, current)
    if repo:
        return _github_commit_iso(repo, branch)
    return None


def _commit_iso(git: str, rev: str) -> str | None:
    result = _run(git, "log", "-1", "--format=%cI", rev, timeout=15)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _github_commit_iso(repo: str, ref: str) -> str | None:
    payload = _github_commit(repo, ref)
    if not payload:
        return None
    return (
        ((payload.get("commit") or {}).get("committer") or {}).get("date")
        or ((payload.get("commit") or {}).get("author") or {}).get("date")
    )


def check() -> dict:
    try:
        cfg = load_config().get("updates") or {}
        if not cfg.get("enabled", True):
            return _record({"ok": True, "available": False, "message": "Updates disabled"})
        git = _git_bin()
        if git and _is_git_checkout():
            return _record(_check_git(git, str(cfg.get("branch") or "main")))
        repo = str(cfg.get("repo") or "").strip()
        if repo:
            result = _check_github(repo, str(cfg.get("branch") or "main"))
            result["available"] = False
            result["message"] = "This unit is not a git clone — cannot install updates"
            return _record(result)
        return _record({"ok": True, "available": False, "message": "No update source configured"})
    except Exception:
        return _record({"ok": True, "available": False, "message": "Update check skipped"})


def apply() -> dict:
    cfg = load_config().get("updates") or {}
    if not cfg.get("enabled", True):
        return {"ok": False, "error": "Updates disabled"}
    git = _git_bin()
    if not git or not _is_git_checkout():
        return {"ok": False, "error": "This unit is not a git checkout"}
    branch = str(cfg.get("branch") or "main")
    snapshot_kiosk_settings()
    _run(git, "checkout", "--", "config.yaml", timeout=15)
    if _has_blocking_changes(git):
        return {
            "ok": False,
            "error": "Local code changes on this unit — update skipped",
        }
    fetched = _run(git, "fetch", "--quiet", "origin")
    if fetched.returncode != 0:
        return {"ok": False, "error": _git_error(fetched)}
    pulled = _run(git, "merge", "--ff-only", f"origin/{branch}")
    if pulled.returncode != 0:
        return {"ok": False, "error": _git_error(pulled) or "Could not apply update"}
    _install_requirements()
    SHA_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHA_FILE.write_text(_rev_parse(git, "HEAD") or "", encoding="utf-8")
    global _label_cache
    _label_cache = ("", 0.0)
    schedule_restart()
    return {"ok": True, "restart": True, "message": "Update installed — restarting"}


def _check_git(git: str, branch: str) -> dict:
    current = _rev_parse(git, "HEAD")
    fetched = _run(git, "fetch", "--quiet", "origin")
    if fetched.returncode != 0:
        return {
            "ok": True,
            "available": False,
            "current": current,
            "searched": False,
            "message": _git_error(fetched),
        }
    latest = _rev_parse(git, f"origin/{branch}")
    available = bool(current and latest and current != latest)
    if available:
        message = f"Update available ({_short(current)} → {_short(latest)})"
    else:
        message = "Software is up to date"
    return {
        "ok": True,
        "available": available,
        "searched": True,
        "current": current,
        "latest": latest,
        "message": message,
    }


def _check_github(repo: str, branch: str) -> dict:
    try:
        payload = _github_commit(repo, branch, raise_http=True)
    except urllib.error.HTTPError as exc:
        hint = " — add data/github.token on this unit" if exc.code in (401, 403, 404) and not _github_token() else ""
        return {
            "ok": True,
            "available": False,
            "searched": False,
            "message": f"GitHub returned {exc.code}{hint}",
        }
    except Exception:
        return {"ok": True, "available": False, "searched": False, "message": "Could not reach GitHub"}
    if not payload:
        return {"ok": True, "available": False, "searched": False, "message": "Could not reach GitHub"}
    latest = str(payload.get("sha") or "")[:40]
    current = SHA_FILE.read_text(encoding="utf-8").strip() if SHA_FILE.exists() else ""
    if not current:
        current = latest
        SHA_FILE.parent.mkdir(parents=True, exist_ok=True)
        SHA_FILE.write_text(current, encoding="utf-8")
        return {
            "ok": True,
            "available": False,
            "searched": True,
            "current": current,
            "latest": latest,
            "message": "Software is up to date",
        }
    available = bool(latest) and current != latest
    return {
        "ok": True,
        "available": available,
        "searched": True,
        "current": current,
        "latest": latest,
        "message": (
            f"Update available ({_short(current)} → {_short(latest)})"
            if available
            else "Software is up to date"
        ),
    }


def _github_commit(repo: str, ref: str, raise_http: bool = False) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/commits/{ref}"
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        if raise_http:
            raise
        return None
    except Exception:
        return None


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "touchscreen-bcu",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_token() -> str:
    env = (os.environ.get("BCU_GITHUB_TOKEN") or "").strip()
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    cfg = load_config().get("updates") or {}
    return str(cfg.get("token") or "").strip()


def _git_bin() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_CANDIDATES[1:]:
        if Path(candidate).exists():
            return candidate
    return None


def _is_git_checkout() -> bool:
    return (ROOT / ".git").exists()


def _rev_parse(git: str, rev: str) -> str | None:
    result = _run(git, "rev-parse", rev, timeout=15)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _has_blocking_changes(git: str) -> bool:
    result = _run(git, "status", "--porcelain", timeout=15)
    for line in result.stdout.splitlines():
        path = line[3:].strip().split(" -> ")[-1]
        if path in {"config.yaml", "config.local.yaml"}:
            continue
        return True
    return False


def _run(git: str, *args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env["GIT_ASKPASS"] = "echo"
    token = _github_token()
    if token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: bearer {token}"
    try:
        return subprocess.run(
            [git, "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=[git, *args], returncode=1, stdout="", stderr="timeout")


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip().replace("\r", " ").replace("\n", " ")
    token = _github_token()
    if token:
        text = text.replace(token, "***")
    lower = text.lower()
    if any(part in lower for part in ("authentication", "could not read username", "403", "401")):
        if not token:
            return "GitHub login failed — add data/github.token on this unit"
        return "GitHub login failed — check the update token"
    if "timeout" in lower:
        return "GitHub timed out"
    if any(part in lower for part in ("could not connect", "failed to connect", "unable to access", "could not resolve")):
        return "Could not reach GitHub"
    return text[:180] or "Could not reach GitHub"


def _short(sha: str | None) -> str:
    return (sha or "")[:7] or "unknown"


def _record(payload: dict) -> dict:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        dump = dict(payload)
        dump["checked_at"] = datetime.now().isoformat(timespec="seconds")
        STATUS_FILE.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    except Exception:
        pass
    return payload


def _install_requirements() -> None:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    python = sys.executable
    subprocess.run(
        [python, "-m", "pip", "install", "-r", str(req)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
