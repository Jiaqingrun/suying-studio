#!/usr/bin/env python3
"""Verify and prepare one machine profile from the signed offline depot."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.security.offline_depot import (  # noqa: E402
    materialize_component,
    required_bytes,
    select_components,
    verify_depot,
)


def prepare(
    depot: Path,
    *,
    profile: str,
    workspace: Path,
) -> dict[str, object]:
    arch = platform.machine()
    if arch not in {"arm64", "x86_64"}:
        raise ValueError(f"不支持的客户机架构: {arch}")
    manifest = verify_depot(depot)
    component_ids = ["app", "ollama", "ffmpeg", f"models-{profile}", "legal"]
    components = select_components(manifest, component_ids, arch=arch)
    needed = required_bytes(manifest, components)
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(workspace).free
    if free < needed + 10 * 1024**3:
        raise RuntimeError(
            f"磁盘空间不足: required={needed + 10 * 1024**3}, available={free}"
        )
    prepared: dict[str, str] = {}
    for component in components:
        target = workspace / component.component_id
        materialize_component(depot, manifest, component, target)
        prepared[component.component_id] = str(target)
    plan = {
        "ok": True,
        "depot_seq": manifest.depot_seq,
        "arch": arch,
        "profile": profile,
        "required_bytes": needed,
        "workspace": str(workspace),
        "components": prepared,
        "offline_only": True,
    }
    plan_path = workspace / "install-plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depot", type=Path, required=True)
    parser.add_argument("--profile", choices=("lite", "standard", "pro", "max"), required=True)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.home() / "Suying" / "runtime" / "offline-staging",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.depot, profile=args.profile, workspace=args.workspace),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
