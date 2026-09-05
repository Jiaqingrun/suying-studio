"""T2S 更新包说明：目录、签名 notes 短文、sidecar Markdown。

权威数据：``packaging/release_notes.json``。
历史包只追加 ``更新说明.md``，不得改写已签名的 ``release.json`` / ZIP。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "packaging" / "release_notes.json"
NOTES_FILENAME = "更新说明.md"
SIGNED_NOTES_MAX = 4000


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or CATALOG_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    if int(data.get("schema_version") or 0) != 1:
        raise ValueError("release_notes.json schema_version 必须为 1")
    versions = data.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ValueError("release_notes.json 缺少 versions")
    return data


def get_entry(version: str, *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    data = catalog or load_catalog()
    entry = (data.get("versions") or {}).get(version)
    if not isinstance(entry, dict):
        raise KeyError(f"未登记版本说明: {version}")
    return entry


def compact_notes(
    version: str,
    *,
    catalog: dict[str, Any] | None = None,
    build_date: str | None = None,
) -> str:
    """Signed release.json ``notes``（≤4000）。"""
    entry = get_entry(version, catalog=catalog)
    title = str(entry.get("title") or "").strip()
    summary = str(entry.get("summary") or "").strip()
    highlights = [str(x).strip() for x in (entry.get("highlights") or []) if str(x).strip()]
    extra = ""
    if build_date:
        build_meta = ((entry.get("builds") or {}).get(build_date) or {})
        extra = str(build_meta.get("extra") or "").strip()
    parts = [p for p in (title, summary, extra) if p]
    if highlights:
        parts.append("；".join(highlights))
    text = "。".join(parts).replace("。。", "。").strip()
    if not text.endswith("。"):
        text += "。"
    return text[:SIGNED_NOTES_MAX]


def render_notes_markdown(
    version: str,
    *,
    catalog: dict[str, Any] | None = None,
    build_date: str | None = None,
    release_seq: int | None = None,
    flavor: str | None = None,
    backfilled: bool = False,
) -> str:
    data = catalog or load_catalog()
    entry = get_entry(version, catalog=data)
    product = str(data.get("product") or "速影 Studio")
    title = str(entry.get("title") or version).strip()
    summary = str(entry.get("summary") or "").strip()
    highlights = [str(x).strip() for x in (entry.get("highlights") or []) if str(x).strip()]
    build_meta = {}
    if build_date:
        build_meta = dict((entry.get("builds") or {}).get(build_date) or {})
    flavor = flavor or str(build_meta.get("flavor") or entry.get("flavor") or "core")
    extra = str(build_meta.get("extra") or "").strip()

    lines = [
        f"# {product} {version} 更新说明",
        "",
        f"**{title}**",
        "",
    ]
    meta = []
    if release_seq is not None:
        meta.append(f"发布序号 `{release_seq}`")
    if build_date:
        meta.append(f"构建 `{build_date}`")
    meta.append(f"形态 `{flavor}`")
    lines.append("- " + " · ".join(meta))
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if extra:
        lines.append(extra)
        lines.append("")
    if highlights:
        lines.append("## 本版要点")
        lines.append("")
        for item in highlights:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("更新包不含客户配置、Cookie、密钥、数据库或片库。")
    if backfilled:
        lines.append("")
        lines.append(
            "本说明为后补归档，**不改动**已签名的 `release.json` / ZIP；"
            "客户端验签与防降级仍以签名清单为准。"
        )
    lines.append("")
    return "\n".join(lines)


def render_changelog_markdown(
    *,
    catalog: dict[str, Any] | None = None,
    for_t2s: bool = False,
) -> str:
    data = catalog or load_catalog()
    product = str(data.get("product") or "速影 Studio")
    versions = data.get("versions") or {}

    def _key(ver: str) -> tuple[int, ...]:
        return tuple(int(p) for p in ver.split("."))

    ordered = sorted(versions, key=_key, reverse=True)
    lines = [
        f"# {product} 更新说明",
        "",
    ]
    if for_t2s:
        lines.extend(
            [
                "各 `releases/<版本>/` 与构建目录内另有同文 `更新说明.md`。",
                "旧 ZIP 与已签名 `release.json` 不得覆盖；本说明可后补。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "> 权威数据：[`packaging/release_notes.json`](../packaging/release_notes.json)。",
                "> T2S 各历史包目录另有同文 `更新说明.md`（不改签名清单）。",
                "> 冲突时：`DEV_LOCK` / `HARD_LOCKS` / [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) > 本文。",
                "",
                "正式更新仓：T2S `/nvme11/my/data/速影/更新包/releases/<版本>/<构建>/`。",
                "旧 ZIP 与 `release.json` 不得覆盖；本说明可后补。",
                "",
            ]
        )
    for ver in ordered:
        entry = versions[ver]
        title = str(entry.get("title") or ver)
        summary = str(entry.get("summary") or "").strip()
        highlights = [str(x).strip() for x in (entry.get("highlights") or []) if str(x).strip()]
        lines.append(f"## {ver} · {title}")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        for item in highlights:
            lines.append(f"- {item}")
        if highlights:
            lines.append("")
    return "\n".join(lines)
