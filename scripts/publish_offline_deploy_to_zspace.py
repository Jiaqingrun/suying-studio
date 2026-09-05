#!/usr/bin/env python3
"""Publish offline deploy assets (models + verify-tools) to T2S 速影/离线交付/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.t2s_paths import OFFLINE_DEPLOY_ROOT  # noqa: E402
from engine.ops.zspace_offline_deploy import (  # noqa: E402
    MANIFEST_NAME,
    README_NAME,
    build_manifest,
    client,
    models_remote,
    upload_tree,
    verify_tools_remote,
    write_readme,
)
from scripts.ollama_model_bundle import verify_bundle  # noqa: E402

DEFAULT_MODELS = Path.home() / "Suying/offline/速影-offline-models-macos-arm64"
DEFAULT_VERIFY_GLOB = "速影-offline-verify-python-*"
PROFILES = ("lite", "standard", "pro", "max")


def _find_verify_tools(offline_root: Path, profile: str) -> Path | None:
    # Prefer profile-suffixed dir, else any verify-python dir (legacy single profile)
    for pattern in (
        f"速影-offline-verify-python-{profile}",
        "速影-offline-verify-python-pro",
        "速影-offline-verify-python-*",
    ):
        for path in sorted(offline_root.glob(pattern)):
            if path.is_dir() and (path / "ollama" / "ollama").is_file():
                return path
    return None


def publish(
    *,
    arch: str,
    models_root: Path,
    offline_root: Path,
    profiles: list[str],
    dry_run: bool,
) -> dict[str, object]:
    sources: dict[str, object] = {}
    uploads: list[dict[str, object]] = []

    for profile in profiles:
        bundle = models_root / profile
        if not (bundle / "bundle.json").is_file():
            raise FileNotFoundError(f"缺少模型套件: {bundle}")
        verify_bundle(bundle, expected_profile=profile, expected_arch=arch)
        remote = models_remote(arch, profile)
        sources[f"models/{profile}"] = str(bundle)
        if dry_run:
            uploads.append({"kind": "models", "profile": profile, "remote": remote, "local": str(bundle)})
        else:
            uploads.append({"kind": "models", "profile": profile, **upload_tree(bundle, remote)})

        vt = _find_verify_tools(offline_root, profile)
        if vt is None:
            print(f"WARN: 无 verify-tools for {profile}，跳过", file=sys.stderr)
            continue
        remote_vt = verify_tools_remote(arch, profile)
        sources[f"verify-tools/{profile}"] = str(vt)
        if dry_run:
            uploads.append({"kind": "verify-tools", "profile": profile, "remote": remote_vt, "local": str(vt)})
        else:
            uploads.append({"kind": "verify-tools", "profile": profile, **upload_tree(vt, remote_vt)})

    manifest = build_manifest(arch=arch, profiles=profiles, sources=sources)
    if dry_run:
        return {"ok": True, "dry_run": True, "manifest": manifest, "uploads": uploads}

    c = client()
    c.ensure_remote_dir(OFFLINE_DEPLOY_ROOT)
    readme = Path("/tmp/suying-offline-deploy-readme.md")
    readme.write_text(write_readme(), encoding="utf-8")
    manifest_path = Path("/tmp/suying-offline-deploy-manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    c.upload(readme, f"{OFFLINE_DEPLOY_ROOT}/{README_NAME}")
    c.upload(manifest_path, f"{OFFLINE_DEPLOY_ROOT}/{MANIFEST_NAME}")

    return {"ok": True, "manifest": manifest, "uploads": uploads, "remote_root": OFFLINE_DEPLOY_ROOT}


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish offline deploy assets to T2S")
    parser.add_argument("--arch", default=__import__("platform").machine())
    parser.add_argument("--models-root", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--offline-root", type=Path, default=Path.home() / "Suying/offline")
    parser.add_argument("--profile", choices=PROFILES, action="append", dest="profiles")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-local",
        action="store_true",
        help="推送成功后删除 ~/Suying/offline 内模型/depot/verify 副本（保留 ~/.ollama）",
    )
    args = parser.parse_args()
    profiles = args.profiles or list(PROFILES)
    result = publish(
        arch=args.arch,
        models_root=args.models_root.expanduser().resolve(),
        offline_root=args.offline_root.expanduser().resolve(),
        profiles=profiles,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.prune_local and not args.dry_run and result.get("ok"):
        import shutil

        off = args.offline_root.expanduser().resolve()
        for name in (
            "速影-offline-models-macos-arm64",
            "速影-offline-depot-arm64-ready",
            "速影-offline-depot-arm64",
        ):
            p = off / name
            if p.is_dir():
                shutil.rmtree(p)
                print(f"pruned {p}", file=sys.stderr)
        for p in off.glob("速影-offline-verify-python-*"):
            if p.is_dir():
                shutil.rmtree(p)
                print(f"pruned {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
