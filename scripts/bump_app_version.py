#!/usr/bin/env python3
"""Bump 速影 App / engine version across package sources.

Single source for engine: engine/version.py (ENGINE_VERSION).
Also syncs apps/desktop package.json, package-lock, Cargo.toml, tauri.conf.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
PKG = DESKTOP / "package.json"
LOCK = DESKTOP / "package-lock.json"
CARGO = DESKTOP / "src-tauri" / "Cargo.toml"
TAURI = DESKTOP / "src-tauri" / "tauri.conf.json"
ENGINE_VERSION_PY = ROOT / "engine" / "version.py"


def _parse(v: str) -> tuple[int, int, int]:
    parts = (v or "").strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"version must be MAJOR.MINOR.PATCH, got {v!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _fmt(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def bump(kind: str, current: str) -> str:
    major, minor, patch = _parse(current)
    if kind == "major":
        return _fmt((major + 1, 0, 0))
    if kind == "minor":
        return _fmt((major, minor + 1, 0))
    if kind == "patch":
        return _fmt((major, minor, patch + 1))
    raise ValueError(f"unknown bump kind: {kind}")


def current_version() -> str:
    return json.loads(PKG.read_text(encoding="utf-8"))["version"]


def apply_version(new: str) -> dict[str, str]:
    _parse(new)
    old = current_version()

    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    pkg["version"] = new
    PKG.write_text(json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if LOCK.is_file():
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        lock["version"] = new
        packages = lock.get("packages")
        if isinstance(packages, dict) and "" in packages and isinstance(packages[""], dict):
            packages[""]["version"] = new
        LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cargo = CARGO.read_text(encoding="utf-8")
    cargo2, n = re.subn(
        r'(?m)^version\s*=\s*"[^"]+"',
        f'version = "{new}"',
        cargo,
        count=1,
    )
    if n != 1:
        raise RuntimeError("failed to patch Cargo.toml version")
    CARGO.write_text(cargo2, encoding="utf-8")

    tauri = json.loads(TAURI.read_text(encoding="utf-8"))
    tauri["version"] = new
    TAURI.write_text(json.dumps(tauri, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ev = ENGINE_VERSION_PY.read_text(encoding="utf-8")
    ev2, n = re.subn(
        r'(?m)^ENGINE_VERSION\s*=\s*"[^"]+"',
        f'ENGINE_VERSION = "{new}"',
        ev,
        count=1,
    )
    if n != 1:
        raise RuntimeError("failed to patch engine/version.py ENGINE_VERSION")
    ENGINE_VERSION_PY.write_text(ev2, encoding="utf-8")

    return {"old": old, "new": new}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    parser.add_argument("--set")
    parser.add_argument("--print-current", action="store_true")
    args = parser.parse_args()
    current = current_version()
    if args.print_current:
        print(current)
        return 0
    new = args.set.strip() if args.set else bump(args.bump, current)
    result = apply_version(new)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
