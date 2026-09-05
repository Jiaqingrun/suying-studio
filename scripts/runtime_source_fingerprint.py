#!/usr/bin/env python3
"""Fingerprint embedded studio tree; must match embed rsync excludes + remote-install."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SKIP_EXACT = {
    "scripts/quality_sample.py",
    "scripts/purge_blur_catalog.py",
    "scripts/suying_batch10_qa_loop.py",
    "scripts/bootstrap_shifeng.py",
    "scripts/convert_shifeng_keyword_pack.py",
    "scripts/sign_update_release.py",
    "scripts/build_runtime_manifest.py",
    "scripts/build_offline_depot.py",
    "scripts/build_legal_component.py",
    "scripts/build_relocatable_macos_tool.py",
}
SKIP_NAME_PREFIXES_IN_SCRIPTS = (
    "publish_",
    "safari_",
    "chrome_",
    "xhs_",
    "smoke_",
    "qa_",
)


def excluded(relative: Path) -> bool:
    if "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}:
        return True
    if "fixtures" in relative.parts:
        return True
    posix = relative.as_posix()
    if posix in SKIP_EXACT or relative.name in {
        "bootstrap_shifeng.py",
        "convert_shifeng_keyword_pack.py",
    }:
        return True
    # rsync scripts/foo_*.py — only one level under scripts/
    if relative.parent.as_posix() == "scripts" and relative.suffix == ".py":
        name = relative.name
        if any(name.startswith(p) for p in SKIP_NAME_PREFIXES_IN_SCRIPTS):
            return True
        if name.startswith("suying_") and name.endswith("_tick.py"):
            return True
    return False


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / "requirements.txt", root / "pyproject.toml"]
    for tree in (root / "engine", root / "scripts"):
        if tree.is_dir():
            paths.extend(path for path in tree.rglob("*") if path.is_file())
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if excluded(relative):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    print(fingerprint(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
