"""Verification and materialization for the signed offline CAS depot."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from engine.security.depot_compliance import verify_legal_closure
from engine.security.update_manifest import (
    DepotComponent,
    OfflineDepotManifest,
    load_trusted_keys,
    sha256_file,
    verify_document,
)


def verify_depot(root: Path) -> OfflineDepotManifest:
    depot = root.expanduser().resolve()
    payload = (depot / "depot.json").read_bytes()
    manifest = OfflineDepotManifest.model_validate_json(payload)
    verify_document(
        payload,
        (depot / "depot.json.sig").read_text(encoding="ascii"),
        key_id=manifest.key_id,
        trusted_keys=load_trusted_keys(),
    )
    if len({item.component_id for item in manifest.components}) != len(manifest.components):
        raise ValueError("离线仓存在重复 component_id")
    referenced: set[str] = set()
    for component in manifest.components:
        if not set(component.executable_files).issubset(component.files):
            raise ValueError(f"组件可执行文件不在 files 中: {component.component_id}")
        for digest in component.files.values():
            if digest not in manifest.objects:
                raise ValueError(f"组件引用不存在的 CAS 对象: {digest}")
            referenced.add(digest)
    if referenced != set(manifest.objects):
        raise ValueError("离线仓包含未引用或缺失的 CAS 对象")
    actual_objects = {
        path.name
        for path in (depot / "objects" / "sha256").glob("*")
        if path.is_file()
    }
    if actual_objects != set(manifest.objects):
        raise ValueError("离线仓 CAS 文件集合与签名清单不一致")
    for digest, item in manifest.objects.items():
        expected_object_path = f"objects/sha256/{digest}"
        if item.sha256 != digest or item.object_path != expected_object_path:
            raise ValueError(f"离线仓 CAS 对象元数据不一致: {digest}")
        path = depot / item.object_path
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item.size
            or sha256_file(path) != digest
        ):
            raise ValueError(f"离线仓 CAS 对象损坏: {digest}")
    by_digest = manifest.objects
    verify_legal_closure(
        manifest,
        lambda component, relative: (
            depot / by_digest[component.files[relative]].object_path
        ).read_bytes(),
    )
    return manifest


def select_components(
    manifest: OfflineDepotManifest,
    component_ids: list[str],
    *,
    arch: str,
) -> list[DepotComponent]:
    by_id = {item.component_id: item for item in manifest.components}
    selected: list[DepotComponent] = []
    for component_id in component_ids:
        component = by_id.get(component_id)
        if component is None:
            raise ValueError(f"离线仓缺少组件: {component_id}")
        if component.arch not in {"any", arch}:
            raise ValueError(
                f"离线组件架构不匹配: {component_id}={component.arch}, host={arch}"
            )
        selected.append(component)
    return selected


def required_bytes(
    manifest: OfflineDepotManifest,
    components: list[DepotComponent],
) -> int:
    digests = {digest for component in components for digest in component.files.values()}
    return sum(manifest.objects[digest].size for digest in digests)


def materialize_component(
    depot_root: Path,
    manifest: OfflineDepotManifest,
    component: DepotComponent,
    output: Path,
) -> Path:
    depot = depot_root.expanduser().resolve()
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        for relative, digest in component.files.items():
            source = depot / manifest.objects[digest].object_path
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o755 if relative in component.executable_files else 0o644)
            if sha256_file(target) != digest:
                raise ValueError(f"物化组件 SHA256 校验失败: {component.component_id}/{relative}")
        previous = destination.with_name(f".{destination.name}.previous-{os.getpid()}")
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(staging, destination)
        except Exception:
            if previous.exists():
                os.replace(previous, destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
