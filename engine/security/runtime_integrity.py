"""Python-side verifier for the signed embedded runtime manifest."""

from __future__ import annotations

import platform
from pathlib import Path

from engine.security.update_manifest import (
    RuntimeManifest,
    load_trusted_keys,
    sha256_file,
    verify_document,
)

MANIFEST_NAME = "RUNTIME_MANIFEST.json"
SIGNATURE_NAME = f"{MANIFEST_NAME}.sig"


def verify_runtime(runtime_root: Path, *, expected_arch: str | None = None) -> RuntimeManifest:
    root = runtime_root.expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    signature_path = root / SIGNATURE_NAME
    payload = manifest_path.read_bytes()
    manifest = RuntimeManifest.model_validate_json(payload)
    verify_document(
        payload,
        signature_path.read_text(encoding="ascii"),
        key_id=manifest.key_id,
        trusted_keys=load_trusted_keys(),
    )
    arch = expected_arch or platform.machine()
    if manifest.arch != arch:
        raise ValueError(f"runtime 架构不匹配: expect={arch}, got={manifest.arch}")

    listed: set[str] = set()
    for item in manifest.files:
        if item.path in listed:
            raise ValueError(f"runtime manifest 重复文件: {item.path}")
        listed.add(item.path)
        path = root / item.path
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"runtime 文件越界: {item.path}")
        if not path.is_file() or path.stat().st_size != item.size:
            raise ValueError(f"runtime 文件大小不匹配: {item.path}")
        if sha256_file(path) != item.sha256:
            raise ValueError(f"runtime 文件哈希不匹配: {item.path}")

    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative in {MANIFEST_NAME, SIGNATURE_NAME}:
            continue
        if path.is_symlink() and path.is_dir():
            raise ValueError(f"runtime 禁止目录符号链接: {relative}")
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"runtime 符号链接越界: {relative}")
            actual.add(relative)
    if actual != listed:
        missing = sorted(listed - actual)[:1]
        extra = sorted(actual - listed)[:1]
        raise ValueError(f"runtime 文件集合不匹配: missing={missing}, extra={extra}")
    return manifest
