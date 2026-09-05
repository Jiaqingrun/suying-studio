#!/usr/bin/env python3
"""Build a signed, content-addressed offline installation depot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from engine.security.update_manifest import (  # noqa: E402
    DepotComponent,
    DepotObject,
    OfflineDepotManifest,
    public_key_id,
    sha256_file,
    write_signed_document,
)
from engine.security.offline_depot import verify_depot  # noqa: E402
from scripts.sign_update_release import (  # noqa: E402
    DEFAULT_ACCOUNT,
    load_keychain_private_key,
)
from scripts.ollama_model_bundle import verify_bundle  # noqa: E402


def _verify_ffmpeg_bundle_legal(source: Path) -> None:
    if not source.is_dir():
        raise ValueError("ffmpeg 组件必须是 build_relocatable_macos_tool 生成的目录 bundle")
    path = source / "legal" / "FFMPEG_BUNDLE_MANIFEST.json"
    if not path.is_file():
        raise ValueError("ffmpeg bundle 缺少 legal/FFMPEG_BUNDLE_MANIFEST.json")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("ffmpeg bundle 法务清单无效") from error
    if (
        document.get("schema_version") != "suying.ffmpeg-bundle-legal.v1"
        or document.get("distribution_status") != "ready"
        or document.get("gaps") != []
        or not document.get("packages")
    ):
        raise ValueError("ffmpeg bundle 法务材料未达到 ready")


def _copy_object(source: Path, objects: Path, digest: str) -> Path:
    target = objects / "sha256" / digest
    if target.exists():
        if target.stat().st_size != source.stat().st_size or sha256_file(target) != digest:
            raise ValueError(f"CAS 已有对象损坏: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{digest}.{os.getpid()}")
    shutil.copy2(source, temp)
    if sha256_file(temp) != digest:
        temp.unlink(missing_ok=True)
        raise ValueError(f"CAS 写入校验失败: {source}")
    os.replace(temp, target)
    target.chmod(0o644)
    return target


def _parse_component(value: str) -> tuple[str, str, str, Path]:
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise ValueError("component 格式必须是 id:kind:arch:path")
    component_id, kind, arch, raw_path = parts
    if kind not in {"app", "ollama", "ffmpeg", "ffprobe", "model_profile", "legal"}:
        raise ValueError(f"不支持的离线组件类型: {kind}")
    if arch not in {"arm64", "x86_64", "any"}:
        raise ValueError(f"不支持的离线组件架构: {arch}")
    return component_id, kind, arch, Path(raw_path).expanduser().resolve()


def build_depot(
    output: Path,
    *,
    depot_seq: int,
    component_specs: list[str],
    account: str = DEFAULT_ACCOUNT,
    private_key: Ed25519PrivateKey | None = None,
) -> OfflineDepotManifest:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    signing_key = private_key or load_keychain_private_key(account)
    objects: dict[str, DepotObject] = {}
    components: list[DepotComponent] = []
    try:
        for spec in component_specs:
            component_id, kind, arch, source = _parse_component(spec)
            if not source.exists():
                raise FileNotFoundError(f"离线组件不存在: {source}")
            if source.is_symlink():
                raise ValueError(f"离线组件不得是符号链接: {source}")
            if kind == "legal" and (component_id != "legal" or arch != "any"):
                raise ValueError("legal 组件必须使用 legal:legal:any:path")
            if kind == "model_profile":
                if not source.is_dir():
                    raise ValueError(f"模型 profile 必须是目录: {source}")
                prefix = "models-"
                if not component_id.startswith(prefix):
                    raise ValueError(f"模型 profile component_id 必须以 models- 开头: {component_id}")
                verify_bundle(
                    source,
                    expected_profile=component_id[len(prefix) :],
                    expected_arch=arch,
                )
            if kind == "ffmpeg":
                _verify_ffmpeg_bundle_legal(source)
            paths = [source] if source.is_file() else sorted(source.rglob("*"))
            files: dict[str, str] = {}
            executables: list[str] = []
            for path in paths:
                if path.is_symlink():
                    raise ValueError(f"离线组件不得包含符号链接: {path}")
                if not path.is_file():
                    continue
                relative = path.name if source.is_file() else path.relative_to(source).as_posix()
                digest = sha256_file(path)
                _copy_object(path, staging / "objects", digest)
                objects.setdefault(
                    digest,
                    DepotObject(
                        size=path.stat().st_size,
                        sha256=digest,
                        object_path=f"objects/sha256/{digest}",
                    ),
                )
                files[relative] = digest
                if os.access(path, os.X_OK):
                    executables.append(relative)
            components.append(
                DepotComponent(
                    component_id=component_id,
                    kind=kind,
                    arch=arch,
                    files=dict(sorted(files.items())),
                    executable_files=sorted(executables),
                )
            )
        manifest = OfflineDepotManifest(
            depot_seq=depot_seq,
            created_at=datetime.now(timezone.utc),
            key_id=public_key_id(signing_key.public_key()),
            objects=dict(sorted(objects.items())),
            components=components,
        )
        write_signed_document(
            manifest,
            private_key=signing_key,
            path=staging / "depot.json",
        )
        verify_depot(staging)
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
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--depot-seq", type=int, required=True)
    parser.add_argument(
        "--component",
        action="append",
        required=True,
        help="id:kind:arch:path，可重复",
    )
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    args = parser.parse_args()
    manifest = build_depot(
        args.output,
        depot_seq=args.depot_seq,
        component_specs=args.component,
        account=args.account,
    )
    print(
        f"offline depot ready: {args.output} "
        f"components={len(manifest.components)} objects={len(manifest.objects)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
