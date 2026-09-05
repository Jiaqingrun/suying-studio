#!/usr/bin/env python3
"""Assemble the exact legal component referenced by THIRD_PARTY_MANIFEST."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DEFAULT_APP = (
    ROOT
    / "apps"
    / "desktop"
    / "src-tauri"
    / "target"
    / "release"
    / "bundle"
    / "macos"
    / "速影.app"
)


def _python_license_bundle(app: Path) -> str:
    python_root = app / "Contents" / "Resources" / "runtime" / "python"
    if not python_root.is_dir():
        raise FileNotFoundError(f"App 缺少内嵌 Python: {python_root}")
    if any(p.name.startswith("cursor_sdk") for p in python_root.rglob("*")):
        raise ValueError("内嵌 Python 仍包含 Cursor SDK")
    candidates = [python_root / "LICENSE.txt"]
    candidates.extend(
        path
        for path in python_root.rglob("*")
        if path.is_file()
        and ".dist-info" in path.as_posix()
        and any(part.lower() == "licenses" for part in path.parts)
    )
    sections: list[str] = []
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        relative = path.relative_to(python_root).as_posix()
        sections.append(f"\n===== {relative} =====\n")
        sections.append(path.read_text(encoding="utf-8", errors="replace").rstrip() + "\n")
    if len(sections) < 2:
        raise ValueError("未收集到内嵌 Python 许可证正文")
    return "".join(sections)


def build(output: Path, *, app: Path = DEFAULT_APP) -> Path:
    manifest_source = DOCS / "THIRD_PARTY_MANIFEST.json"
    document = json.loads(manifest_source.read_text(encoding="utf-8"))
    if document.get("distribution_status") != "ready" or document.get("gaps") != []:
        raise ValueError("第三方清单未达到 ready")

    referenced = {
        relative
        for package in document.get("packages", [])
        for field in ("license_paths", "corresponding_source_paths")
        for relative in package.get(field, [])
    }
    required = {
        "THIRD_PARTY_MANIFEST.json": manifest_source,
        "DELIVERY_NOTICE.zh-CN.md": DOCS / "legal" / "DELIVERY_NOTICE.zh-CN.md",
        "PROVENANCE.md": DOCS / "legal" / "PROVENANCE.md",
        "MATERIAL_SHA256.txt": DOCS / "legal" / "MATERIAL_SHA256.txt",
    }
    for relative in sorted(referenced):
        if relative == "legal/python-runtime/THIRD_PARTY_LICENSES.txt":
            continue
        source = DOCS / relative
        if not source.is_file():
            raise FileNotFoundError(f"legal 引用不存在: {relative}")
        required[relative] = source

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for relative, source in required.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        generated = staging / "legal" / "python-runtime" / "THIRD_PARTY_LICENSES.txt"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(_python_license_bundle(app.expanduser().resolve()), encoding="utf-8")
        previous = output.with_name(f".{output.name}.previous-{os.getpid()}")
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(staging, output)
        except Exception:
            if previous.exists():
                os.replace(previous, output)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return output
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    args = parser.parse_args()
    print(build(args.output, app=args.app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
