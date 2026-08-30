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

from bcu.config import ROOT, load_config

RESTART_FLAG = ROOT / "data" / ".bcu-restart"
SHA_FILE = ROOT / "data" / "installed-sha.txt"
GIT_CANDIDATES = [
    "git",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]

_label_cache: tuple[str, float] = ("", 0.0)
_poll_started = False


def last_update_label(tz) -> str:
    """dd/mm/YYYY HH:MM of the latest commit on the git remote (or HEAD)."""
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


def _origin_poll_loop() -> None:
    global _label_cache
    while True:
        time.sleep(120)
        git = _git_bin()
        if git and _is_git_checkout():
            _run(git, "fetch", "--quiet", "origin")
            _label_cache = ("", 0.0)


def _format_commit_time(tz) -> str | None:
    iso = _latest_commit_iso()
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def _latest_commit_iso() -> str | None:
    cfg = load_config().get("updates") or {}
    branch = str(cfg.get("branch") or "main")
    git = _git_bin()
    if git and _is_git_checkout():
        return _commit_iso(git, f"origin/{branch}") or _commit_iso(git, "HEAD")
    repo = str(cfg.get("repo") or "").strip()
    if repo:
        return _github_commit_iso(repo, branch)
    return None


def _commit_iso(git: str, rev: str) -> str | None:
    result = _run(git, "log", "-1", "--format=%cI", rev)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _github_commit_iso(repo: str, branch: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "touchscreen-bcu"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return (
        ((payload.get("commit") or {}).get("committer") or {}).get("date")
        or ((payload.get("commit") or {}).get("author") or {}).get("date")
    )


def check() -> dict:
    try:
        cfg = (load_config().get("updates") or {})
        if not cfg.get("enabled", True):
            return {"ok": True, "available": False, "message": "Updates disabled"}
        git = _git_bin()
        if git and _is_git_checkout():
            return _check_git(git, str(cfg.get("branch") or "main"))
        repo = str(cfg.get("repo") or "").strip()
        if repo:
            return _check_github(repo, str(cfg.get("branch") or "main"))
        return {"ok": True, "available": False, "message": "No update source configured"}
    except Exception:
        return {"ok": True, "available": False, "message": "Update check skipped"}


def apply() -> dict:
    cfg = (load_config().get("updates") or {})
    if not cfg.get("enabled", True):
        return {"ok": False, "error": "Updates disabled"}
    git = _git_bin()
    if not git or not _is_git_checkout():
        return {"ok": False, "error": "This unit is not a git checkout"}
    branch = str(cfg.get("branch") or "main")
    fetched = _run(git, "fetch", "--quiet", "origin")
    if fetched.returncode != 0:
        return {"ok": False, "error": fetched.stderr.strip() or "Could not fetch updates"}
    pulled = _run(git, "merge", "--ff-only", f"origin/{branch}")
    if pulled.returncode != 0:
        return {"ok": False, "error": pulled.stderr.strip() or "Could not apply update"}
    _install_requirements()
    SHA_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHA_FILE.write_text(_rev_parse(git, "HEAD") or "", encoding="utf-8")
    global _label_cache
    _label_cache = ("", 0.0)
    return {"ok": True, "restart": False, "message": "Update installed"}


def _check_git(git: str, branch: str) -> dict:
    current = _rev_parse(git, "HEAD")
    fetched = _run(git, "fetch", "--quiet", "origin")
    if fetched.returncode != 0:
        return {
            "ok": True,
            "available": False,
            "current": current,
            "message": "Could not reach GitHub — continuing",
        }
    latest = _rev_parse(git, f"origin/{branch}")
    global _label_cache
    _label_cache = ("", 0.0)
    available = bool(current and latest and current != latest)
    return {
        "ok": True,
        "available": available,
        "current": current,
        "latest": latest,
        "message": "Update available" if available else "Software is up to date",
    }


def _check_github(repo: str, branch: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "touchscreen-bcu"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": True, "available": False, "message": f"GitHub returned {exc.code} — continuing"}
    except Exception:
        return {"ok": True, "available": False, "message": "Could not reach GitHub — continuing"}
    latest = str(payload.get("sha") or "")[:40]
    current = SHA_FILE.read_text(encoding="utf-8").strip() if SHA_FILE.exists() else ""
    if not current:
        current = latest
        SHA_FILE.parent.mkdir(parents=True, exist_ok=True)
        SHA_FILE.write_text(current, encoding="utf-8")
        return {"ok": True, "available": False, "current": current, "latest": latest, "message": "Software is up to date"}
    available = bool(latest) and current != latest
    return {
        "ok": True,
        "available": available,
        "current": current,
        "latest": latest,
        "message": "Update available" if available else "Software is up to date",
    }


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
    result = _run(git, "rev-parse", rev)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _run(git: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env["GIT_ASKPASS"] = "echo"
    try:
        return subprocess.run(
            [git, "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=8,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=[git, *args], returncode=1, stdout="", stderr="timeout")


def _install_requirements() -> None:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    python = sys.executable
    subprocess.run(
        [python, "-m", "pip", "install", "-r", str(req)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
