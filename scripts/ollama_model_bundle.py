#!/usr/bin/env python3
"""Create, verify, and atomically import exact Ollama manifests and blobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "suying.ollama.offline-bundle.v1"
PROFILE_MODELS = {
    "lite": ["nomic-embed-text"],
    "standard": ["nomic-embed-text", "qwen3.5:9b"],
    "pro": ["nomic-embed-text", "qwen3.5:9b"],
    "max": ["nomic-embed-text", "qwen3.5:9b"],
}
NARRATION_MODELS = {
    "lite": "qwen2.5:3b",
    "standard": "qwen2.5:7b",
    "pro": "qwen2.5:14b",
    "max": "qwen2.5:32b",
}
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
BLOB_NAME_RE = re.compile(r"^sha256-[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def models_root(value: str | None = None) -> Path:
    return Path(value or os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama/models").expanduser()


def parse_model(name: str) -> tuple[str, str, str, str]:
    raw = name.strip()
    if (
        not raw
        or raw.startswith(("/", "\\"))
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise ValueError(f"无效模型名: {name!r}")
    registry = "registry.ollama.ai"
    namespace = "library"
    model_tag = raw
    if "/" in raw:
        parts = raw.split("/")
        if "." in parts[0] or ":" in parts[0]:
            registry = parts.pop(0)
        if len(parts) > 1:
            namespace = parts.pop(0)
        model_tag = "/".join(parts)
    model, sep, tag = model_tag.rpartition(":")
    if not sep:
        model, tag = model_tag, "latest"
    if not model or not tag or "/" in model:
        raise ValueError(f"无效模型名: {name!r}")
    return registry, namespace, model, tag


def manifest_relative(name: str) -> Path:
    registry, namespace, model, tag = parse_model(name)
    return Path("manifests") / registry / namespace / model / tag


def referenced_digests(manifest: dict[str, Any]) -> list[str]:
    values: list[str] = []
    config = manifest.get("config") or {}
    if config.get("digest"):
        values.append(str(config["digest"]))
    for layer in manifest.get("layers") or []:
        if isinstance(layer, dict) and layer.get("digest"):
            values.append(str(layer["digest"]))
    unique: list[str] = []
    for value in values:
        if not value.startswith("sha256:") or not SHA256_HEX_RE.fullmatch(value[7:]):
            raise ValueError(f"不支持的 Ollama digest: {value}")
        if value not in unique:
            unique.append(value)
    return unique


def _safe_relative(value: Any, *, kind: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"离线模型套件含非法{kind}路径: {value!r}")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"离线模型套件含非法{kind}路径: {value!r}")
    if path.as_posix() != value:
        raise ValueError(f"离线模型套件含非规范{kind}路径: {value!r}")
    return path


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"离线模型套件不得是符号链接: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"离线模型套件不得包含符号链接: {path.relative_to(root)}")


def _validate_manifest_path(path: Path) -> None:
    parts = path.parts
    if len(parts) != 5 or parts[0] != "manifests":
        raise ValueError(f"非法 Ollama manifest 路径: {path.as_posix()}")
    if any(not part or part in {".", ".."} for part in parts[1:]):
        raise ValueError(f"非法 Ollama manifest 路径: {path.as_posix()}")


def _validate_blob_path(path: Path) -> None:
    if len(path.parts) != 2 or path.parts[0] != "blobs" or not BLOB_NAME_RE.fullmatch(path.name):
        raise ValueError(f"非法 Ollama blob 路径: {path.as_posix()}")


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    cloned = False
    if platform.system() == "Darwin":
        cloned = subprocess.run(
            ["/bin/cp", "-c", str(source), str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    if not cloned:
        shutil.copy2(source, target)
    target.chmod(0o644)


def create_bundle(profile: str, output: Path, source_root: Path, *, include_narration: bool = False) -> dict[str, Any]:
    if profile not in PROFILE_MODELS:
        raise ValueError(f"未知 Install Profile: {profile}")
    requested = list(PROFILE_MODELS[profile])
    if include_narration:
        requested.append(NARRATION_MODELS[profile])
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent))
    files: dict[str, str] = {}
    model_entries: list[dict[str, Any]] = []
    try:
        for requested_name in requested:
            relative = manifest_relative(requested_name)
            source_manifest = source_root / relative
            if not source_manifest.is_file():
                raise FileNotFoundError(f"本机 Ollama 缺少模型 manifest: {requested_name} ({source_manifest})")
            raw = source_manifest.read_bytes()
            manifest = json.loads(raw)
            target_manifest = temp / relative
            target_manifest.parent.mkdir(parents=True, exist_ok=True)
            target_manifest.write_bytes(raw)
            target_manifest.chmod(0o644)
            files[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
            digests = referenced_digests(manifest)
            for digest in digests:
                blob_relative = Path("blobs") / digest.replace(":", "-")
                source_blob = source_root / blob_relative
                if not source_blob.is_file():
                    raise FileNotFoundError(f"manifest 引用的 blob 不存在: {digest}")
                actual = sha256_file(source_blob)
                if actual != digest.removeprefix("sha256:"):
                    raise ValueError(f"本机 blob digest 不匹配: {source_blob}")
                target_blob = temp / blob_relative
                if not target_blob.exists():
                    _copy_file(source_blob, target_blob)
                files[blob_relative.as_posix()] = actual
            registry, namespace, model, tag = parse_model(requested_name)
            model_entries.append({
                "name": requested_name,
                "resolved_name": f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}",
                "manifest": relative.as_posix(),
                "digests": digests,
            })
        metadata = {
            "schema_version": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_id": profile,
            "source_arch": platform.machine(),
            "models": model_entries,
            "narration_included": include_narration,
            "files": dict(sorted(files.items())),
        }
        (temp / "bundle.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (temp / "bundle.json").chmod(0o644)
        verify_bundle(temp, expected_profile=profile, expected_arch=platform.machine())
        previous = output.with_name(f".{output.name}.previous-{os.getpid()}")
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(temp, output)
        except BaseException:
            if previous.exists():
                os.replace(previous, output)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return metadata
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def verify_bundle(bundle: Path, *, expected_profile: str | None = None, expected_arch: str | None = None) -> dict[str, Any]:
    if not bundle.is_dir():
        raise ValueError(f"离线模型套件目录不存在: {bundle}")
    _reject_symlinks(bundle)
    metadata_path = bundle / "bundle.json"
    if not metadata_path.is_file():
        raise ValueError("离线模型套件缺少 bundle.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("离线模型套件 metadata 无效")
    if metadata.get("schema_version") != SCHEMA:
        raise ValueError("离线模型套件 schema 不兼容")
    if expected_profile and metadata.get("profile_id") != expected_profile:
        raise ValueError(f"模型套件档位不匹配: {metadata.get('profile_id')} != {expected_profile}")
    if expected_arch and metadata.get("source_arch") != expected_arch:
        raise ValueError(f"模型套件架构不匹配: {metadata.get('source_arch')} != {expected_arch}")
    files = metadata.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("离线模型套件没有文件清单")
    normalized: dict[str, Path] = {}
    for relative, expected in files.items():
        safe = _safe_relative(relative, kind="文件")
        if safe.parts[0] == "blobs":
            _validate_blob_path(safe)
        elif safe.parts[0] == "manifests":
            _validate_manifest_path(safe)
        else:
            raise ValueError(f"离线模型套件含非法文件类型: {relative}")
        if not isinstance(expected, str) or not SHA256_HEX_RE.fullmatch(expected):
            raise ValueError(f"离线模型文件 SHA256 清单无效: {relative}")
        normalized[relative] = safe
    actual_files = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path != metadata_path
    }
    if actual_files != set(normalized):
        missing = sorted(set(normalized) - actual_files)
        extra = sorted(actual_files - set(normalized))
        raise ValueError(f"离线模型套件文件清单不闭合: missing={missing}, extra={extra}")
    for relative, safe in normalized.items():
        path = bundle / safe
        if not path.is_file() or sha256_file(path) != files[relative]:
            raise ValueError(f"离线模型文件 SHA256 校验失败: {relative}")
    models = metadata.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("离线模型套件没有模型清单")
    referenced_manifests: set[str] = set()
    referenced_blobs: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            raise ValueError("离线模型 model 条目无效")
        manifest_relative_path = _safe_relative(item.get("manifest"), kind="manifest")
        _validate_manifest_path(manifest_relative_path)
        manifest_name = manifest_relative_path.as_posix()
        if manifest_name not in files:
            raise ValueError(f"模型 manifest 未列入文件清单: {manifest_name}")
        manifest_path = bundle / manifest_relative_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
            raise ValueError(f"模型 manifest 格式无效: {item.get('name')}")
        digests = referenced_digests(manifest)
        if digests != item.get("digests"):
            raise ValueError(f"模型 manifest 引用清单不匹配: {item.get('name')}")
        referenced_manifests.add(manifest_name)
        for digest in digests:
            blob_name = f"blobs/{digest.replace(':', '-')}"
            if blob_name not in files:
                raise ValueError(f"模型 manifest 引用未打包 blob: {digest}")
            referenced_blobs.add(blob_name)
    declared_manifests = {name for name in files if name.startswith("manifests/")}
    declared_blobs = {name for name in files if name.startswith("blobs/")}
    if referenced_manifests != declared_manifests or referenced_blobs != declared_blobs:
        raise ValueError("离线模型套件包含未引用的 manifest/blob")
    return metadata


def import_bundle(bundle: Path, target_root: Path, *, expected_profile: str, expected_arch: str) -> dict[str, Any]:
    metadata = verify_bundle(bundle, expected_profile=expected_profile, expected_arch=expected_arch)
    if target_root.is_symlink():
        raise ValueError("目标 Ollama 模型目录不得是符号链接")
    target_root.mkdir(parents=True, exist_ok=True)
    blob_files = [name for name in metadata["files"] if name.startswith("blobs/")]
    manifest_files = [name for name in metadata["files"] if name.startswith("manifests/")]
    ordered_files = blob_files + manifest_files
    pending: list[str] = []
    # Complete target preflight before creating any import file.
    for relative in ordered_files:
        safe = _safe_relative(relative, kind="文件")
        target = target_root / safe
        current = target_root
        for part in safe.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"目标 Ollama 路径包含符号链接: {current}")
        expected = metadata["files"][relative]
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or sha256_file(target) != expected:
                raise ValueError(f"目标 Ollama 内容寻址文件冲突: {target}")
        else:
            pending.append(relative)

    stage = Path(tempfile.mkdtemp(prefix=".suying-model-import-", dir=target_root))
    imported: list[str] = []
    try:
        for relative in pending:
            source = bundle / _safe_relative(relative, kind="文件")
            staged = stage / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged, follow_symlinks=False)
            staged.chmod(0o644)
            if sha256_file(staged) != metadata["files"][relative]:
                raise ValueError(f"导入 staging 文件 SHA256 校验失败: {relative}")
        # Commit only after the entire staging tree has passed verification.
        for relative in pending:
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / relative, target)
            imported.append(relative)
    except BaseException:
        for relative in reversed(imported):
            (target_root / relative).unlink(missing_ok=True)
        for root, dirs, _ in os.walk(target_root, topdown=False):
            for directory in dirs:
                path = Path(root) / directory
                try:
                    path.rmdir()
                except OSError:
                    pass
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    for root, dirs, _ in os.walk(target_root):
        Path(root).chmod(0o755)
        for directory in dirs:
            (Path(root) / directory).chmod(0o755)
    return {"ok": True, "profile_id": expected_profile, "models": metadata["models"], "imported_files": imported}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--profile", choices=PROFILE_MODELS, action="append", required=True)
    create.add_argument("--output-root", type=Path, required=True)
    create.add_argument("--models-dir")
    create.add_argument("--include-narration", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--expected-profile", choices=PROFILE_MODELS)
    verify.add_argument("--expected-arch")
    install = sub.add_parser("import")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--models-dir")
    install.add_argument("--expected-profile", choices=PROFILE_MODELS, required=True)
    install.add_argument("--expected-arch", required=True)
    args = parser.parse_args()
    if args.command == "create":
        results = []
        for profile_name in args.profile:
            results.append(create_bundle(profile_name, args.output_root / profile_name, models_root(args.models_dir), include_narration=args.include_narration))
        print(json.dumps({"ok": True, "bundles": results}, ensure_ascii=False))
    elif args.command == "verify":
        print(json.dumps(verify_bundle(args.bundle, expected_profile=args.expected_profile, expected_arch=args.expected_arch), ensure_ascii=False))
    else:
        print(json.dumps(import_bundle(args.bundle, models_root(args.models_dir), expected_profile=args.expected_profile, expected_arch=args.expected_arch), ensure_ascii=False))


if __name__ == "__main__":
    main()
