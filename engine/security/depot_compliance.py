"""Fail-closed legal closure checks for externally distributed offline depots."""

from __future__ import annotations

import json
from collections.abc import Callable

from engine.security.update_manifest import DepotComponent, OfflineDepotManifest

LEGAL_COMPONENT_ID = "legal"
LEGAL_MANIFEST_PATH = "THIRD_PARTY_MANIFEST.json"
LEGAL_NOTICE_PATH = "DELIVERY_NOTICE.zh-CN.md"
LEGAL_SCHEMA = "suying.third-party.v1"


def verify_legal_closure(
    manifest: OfflineDepotManifest,
    read_component_file: Callable[[DepotComponent, str], bytes],
) -> None:
    legal_components = [
        item
        for item in manifest.components
        if item.component_id == LEGAL_COMPONENT_ID or item.kind == "legal"
    ]
    if len(legal_components) != 1:
        raise ValueError("外部分发离线仓必须且只能包含一个 legal 组件")
    legal = legal_components[0]
    if (
        legal.component_id != LEGAL_COMPONENT_ID
        or legal.kind != "legal"
        or legal.arch != "any"
    ):
        raise ValueError("legal 组件必须为 component_id=legal、kind=legal、arch=any")
    mandatory = {LEGAL_MANIFEST_PATH, LEGAL_NOTICE_PATH}
    if not mandatory.issubset(legal.files):
        raise ValueError("legal 组件缺少 THIRD_PARTY_MANIFEST.json 或中文交付说明")

    try:
        document = json.loads(read_component_file(legal, LEGAL_MANIFEST_PATH))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("THIRD_PARTY_MANIFEST.json 无效") from error
    if not isinstance(document, dict) or document.get("schema_version") != LEGAL_SCHEMA:
        raise ValueError("THIRD_PARTY_MANIFEST schema 无效")
    if document.get("distribution_status") != "ready":
        raise ValueError("第三方许可证清单未达到 ready，拒绝外部分发")
    gaps = document.get("gaps")
    if gaps != []:
        raise ValueError("第三方许可证清单仍存在材料缺口")

    expected_components = {
        item.component_id for item in manifest.components if item.component_id != LEGAL_COMPONENT_ID
    }
    covered = document.get("covered_components")
    if not isinstance(covered, list) or set(covered) != expected_components:
        raise ValueError("legal 组件覆盖范围与 depot 组件闭包不一致")

    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("THIRD_PARTY_MANIFEST packages 为空")
    package_coverage: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("THIRD_PARTY_MANIFEST package 条目无效")
        required = ("name", "version", "license_expression", "source_url")
        if any(not isinstance(package.get(key), str) or not package[key].strip() for key in required):
            raise ValueError("第三方 package 缺少名称、版本、许可证或来源 URL")
        component_ids = package.get("component_ids")
        license_paths = package.get("license_paths")
        if not isinstance(component_ids, list) or not component_ids:
            raise ValueError(f"第三方 package 未绑定组件: {package.get('name')}")
        if not set(component_ids).issubset(expected_components):
            raise ValueError(f"第三方 package 引用了未知组件: {package.get('name')}")
        package_coverage.update(component_ids)
        _require_legal_files(legal, license_paths, f"{package.get('name')} license_paths")
        if package.get("source_required") is True:
            _require_legal_files(
                legal,
                package.get("corresponding_source_paths"),
                f"{package.get('name')} corresponding_source_paths",
            )
    if package_coverage != expected_components:
        raise ValueError("第三方 package 条目未覆盖全部 depot 组件")


def _require_legal_files(
    legal: DepotComponent,
    value: object,
    label: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} 为空")
    missing = [path for path in value if not isinstance(path, str) or path not in legal.files]
    if missing:
        raise ValueError(f"{label} 引用 legal 组件外文件: {missing[:3]}")
