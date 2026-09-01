#!/usr/bin/env python3
"""Bump BCU software_version in config.yaml (and bcu/__init__.py).

Default: 0.0.1 → 0.0.2 (patch).
Minor:   0.0.5 → 0.1.0  (use --minor or [minor] in a commit message)
Major:   0.1.4 → 1.0.0  (use --major or [major] in a commit message)

A suffix such as " Beta" is kept. GitHub Actions runs this on push to main.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
INIT_PATH = ROOT / "bcu" / "__init__.py"

VERSION_LINE = re.compile(
    r"^(software_version:\s*)([\"']?)(\d+)\.(\d+)\.(\d+)([^\"'\n]*)([\"']?)\s*$",
    re.MULTILINE,
)
INIT_VERSION = re.compile(r'^(__version__\s*=\s*)(["\'])([^"\']+)(["\'])\s*$', re.MULTILINE)


def parse_config_version(text: str) -> tuple[str, int, int, int, str, str]:
    match = VERSION_LINE.search(text)
    if not match:
        raise SystemExit("Could not find software_version: X.Y.Z in config.yaml")
    prefix, quote, major, minor, patch, suffix, _end_quote = match.groups()
    return prefix, int(major), int(minor), int(patch), suffix, quote


def format_version(major: int, minor: int, patch: int, suffix: str = "") -> str:
    return f"{major}.{minor}.{patch}{suffix}"


def bumped(major: int, minor: int, patch: int, level: str) -> tuple[int, int, int]:
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    if level == "patch":
        return major, minor, patch + 1
    raise SystemExit(f"Unknown bump level: {level}")


def replace_config_version(text: str, major: int, minor: int, patch: int, suffix: str, quote: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        prefix = match.group(1)
        return f"{prefix}{quote}{format_version(major, minor, patch, suffix)}{quote}"

    updated, count = VERSION_LINE.subn(_sub, text, count=1)
    if count != 1:
        raise SystemExit("Failed to update software_version in config.yaml")
    return updated


def replace_init_version(text: str, numeric: str) -> str:
    if INIT_VERSION.search(text):
        return INIT_VERSION.sub(rf'\1\2{numeric}\4', text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f'__version__ = "{numeric}"\n'


def level_from_messages(messages: list[str]) -> str | None:
    blob = "\n".join(messages).lower()
    if "[skip bump]" in blob or "[skip version]" in blob:
        return None
    if "[major]" in blob or "bump major" in blob:
        return "major"
    if "[minor]" in blob or "bump minor" in blob:
        return "minor"
    return "patch"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def messages_since(before: str) -> list[str]:
    if not before or set(before) <= {"0"}:
        result = git("log", "-1", "--format=%s")
        return [result.stdout.strip()] if result.returncode == 0 else []
    result = git("log", f"{before}..HEAD", "--format=%s")
    if result.returncode != 0:
        result = git("log", "-1", "--format=%s")
    return [line for line in result.stdout.splitlines() if line.strip()]


def version_already_changed(before: str) -> bool:
    if not before or set(before) <= {"0"}:
        return False
    result = git("diff", f"{before}..HEAD", "--", "config.yaml")
    if result.returncode != 0:
        return False
    return bool(re.search(r"^[-+]software_version:", result.stdout, re.MULTILINE))


def current_numeric() -> str:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    _, major, minor, patch, _, _ = parse_config_version(text)
    return f"{major}.{minor}.{patch}"


def apply(level: str) -> str:
    config_text = CONFIG_PATH.read_text(encoding="utf-8")
    _, major, minor, patch, suffix, quote = parse_config_version(config_text)
    major, minor, patch = bumped(major, minor, patch, level)
    numeric = f"{major}.{minor}.{patch}"
    CONFIG_PATH.write_text(
        replace_config_version(config_text, major, minor, patch, suffix, quote),
        encoding="utf-8",
    )
    if INIT_PATH.exists():
        INIT_PATH.write_text(
            replace_init_version(INIT_PATH.read_text(encoding="utf-8"), numeric),
            encoding="utf-8",
        )
    return format_version(major, minor, patch, suffix)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump BCU software_version")
    parser.add_argument("--print", action="store_true", dest="print_current", help="Print current numeric version")
    parser.add_argument("--apply", action="store_true", help="Write the bumped version to files")
    parser.add_argument("--patch", action="store_true", help="Increment 0.0.X")
    parser.add_argument("--minor", action="store_true", help="Increment 0.X.0 and reset patch")
    parser.add_argument("--major", action="store_true", help="Increment X.0.0 and reset minor/patch")
    parser.add_argument(
        "--git-range",
        metavar="BEFORE_SHA",
        help="Choose bump level from commit messages since this SHA; skip if version already changed",
    )
    args = parser.parse_args()

    if args.print_current:
        print(current_numeric())
        return 0

    level = "patch"
    if args.major:
        level = "major"
    elif args.minor:
        level = "minor"
    elif args.patch:
        level = "patch"

    if args.git_range is not None:
        if version_already_changed(args.git_range):
            print("skip: software_version already changed in this push")
            return 0
        messages = messages_since(args.git_range)
        chosen = level_from_messages(messages)
        if chosen is None:
            print("skip: [skip bump] in commit message")
            return 0
        level = chosen

    if not args.apply:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        _, major, minor, patch, suffix, _ = parse_config_version(text)
        major, minor, patch = bumped(major, minor, patch, level)
        print(format_version(major, minor, patch, suffix))
        return 0

    new_version = apply(level)
    print(new_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
