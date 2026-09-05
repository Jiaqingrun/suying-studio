"""Resolve + gate publish assets: video, copy body, cover slots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.reach.business_scope import VIDEO_PLATFORMS, assert_platform_in_scope


class PublishAssetsError(ValueError):
    """Raised when hard gate fails (missing copy or covers)."""


def _load_copy(pack_dir: Path, platform: str, locale: str = "zh") -> dict[str, str]:
    """Load title/body for platform from copy.{locale}.json or paste card."""
    plat = (platform or "").strip().lower()
    copy_path = pack_dir / f"copy.{locale}.json"
    title = ""
    body = ""
    if copy_path.is_file():
        try:
            data = json.loads(copy_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        platforms = data.get("platforms") or {}
        entry = platforms.get(plat) or {}
        title = str(entry.get("title") or data.get("source_title") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not body:
            # some packs put caption under platforms
            body = str(entry.get("caption") or "").strip()
    if not title or not body:
        paste = pack_dir / f"reach_paste_{plat}.txt"
        if paste.is_file():
            text = paste.read_text(encoding="utf-8")
            mode = None
            buf: list[str] = []
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                if line.startswith("标题:"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode, buf = "title", []
                    continue
                if line.startswith("正文:"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode, buf = "body", []
                    continue
                if line.startswith("视频路径:") or line.startswith("封面"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode = None
                    continue
                if mode and not line.startswith("请用") and not line.startswith("本产品"):
                    buf.append(line)
            if mode:
                parsed[mode] = "\n".join(buf).strip()
            title = title or parsed.get("title", "")
            body = body or parsed.get("body", "")
    return {"title": title.strip(), "body": body.strip()}


def validate_pack_contract(pack_dir: str | Path) -> dict[str, Any]:
    """Validate persistent, cover-independent assets for all video platforms."""
    from engine.pack.publish import PACK_VERSION, validate_publish_video

    pdir = Path(pack_dir)
    if not pdir.is_dir():
        raise PublishAssetsError(f"publish_pack 不存在: {pdir}")
    video = pdir / "video.mp4"
    try:
        video_contract = validate_publish_video(video)
    except ValueError as exc:
        raise PublishAssetsError(str(exc)) from exc
    try:
        manifest = json.loads((pdir / "manifest.json").read_text(encoding="utf-8"))
        compliance = json.loads((pdir / "compliance.json").read_text(encoding="utf-8"))
        copy_doc = json.loads((pdir / "copy.zh.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishAssetsError(f"发布物料缺少合同文件: {exc.filename}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishAssetsError(f"发布物料合同文件不可读: {exc}") from exc
    if manifest.get("version") != PACK_VERSION:
        raise PublishAssetsError(
            f"publish_pack 版本过旧: {manifest.get('version') or 'unknown'}，要求 {PACK_VERSION}"
        )
    if compliance.get("passed") is not True:
        raise PublishAssetsError("publish_pack 合规检查未通过")
    declared = set(manifest.get("platforms") or [])
    if declared != set(VIDEO_PLATFORMS):
        raise PublishAssetsError(
            f"视频平台集合不完整: {sorted(declared)}，要求 {sorted(VIDEO_PLATFORMS)}"
        )
    statuses: dict[str, Any] = {}
    copies = copy_doc.get("platforms") if isinstance(copy_doc.get("platforms"), dict) else {}
    copy_platforms = set(copies)
    if copy_platforms != set(VIDEO_PLATFORMS):
        raise PublishAssetsError(
            f"视频文案平台集合不完整或含非视频平台: {sorted(copy_platforms)}，"
            f"要求 {sorted(VIDEO_PLATFORMS)}"
        )
    for platform in sorted(VIDEO_PLATFORMS):
        entry = copies.get(platform) if isinstance(copies.get(platform), dict) else {}
        title = str(entry.get("title") or "").strip()
        body = str(entry.get("body") or entry.get("caption") or "").strip()
        hashtags = [str(tag).strip() for tag in (entry.get("hashtags") or []) if str(tag).strip()]
        platform_manifest = pdir / "platforms" / platform / "manifest.json"
        platform_compliance = pdir / "platforms" / platform / "compliance.json"
        missing = [
            name
            for name, value in (
                ("title", title),
                ("body", body),
                ("hashtags", hashtags),
                ("manifest", platform_manifest.is_file()),
                ("compliance", platform_compliance.is_file()),
            )
            if not value
        ]
        if not missing:
            try:
                compliance_doc = json.loads(platform_compliance.read_text(encoding="utf-8"))
                if compliance_doc.get("passed") is not True:
                    missing.append("compliance_failed")
            except (OSError, json.JSONDecodeError):
                missing.append("compliance_unreadable")
        statuses[platform] = {
            "status": "ready" if not missing else "blocked",
            "error": "、".join(missing),
            "title": bool(title),
            "body": bool(body),
            "hashtags": bool(hashtags),
            "manifest": str(platform_manifest),
            "compliance": str(platform_compliance),
        }
    blocked = [
        f"{platform}: {status['error']}"
        for platform, status in statuses.items()
        if status["status"] != "ready"
    ]
    if blocked:
        raise PublishAssetsError("四平台发布物料不完整：" + "；".join(blocked))
    return {
        "ok": True,
        "pack_dir": str(pdir),
        "pack_version": PACK_VERSION,
        "video_contract": video_contract,
        "platform_asset_status": statuses,
    }


def require_publish_assets(
    *,
    platform: str,
    pack_dir: str | Path,
    data_root: Path,
    title: str | None = None,
    body: str | None = None,
    template_id: str | None = None,
    locale: str = "zh",
) -> dict[str, Any]:
    """Hard gate: video + non-empty body + all cover slots.

    Returns dict with video, covers, title, body, cover_meta.
    Raises PublishAssetsError on failure.
    """
    try:
        plat = assert_platform_in_scope(platform, "video")
    except ValueError as exc:
        raise PublishAssetsError(str(exc)) from exc
    pdir = Path(pack_dir)
    contract = validate_pack_contract(pdir)
    video = pdir / "video.mp4"

    from engine.pack.publish import (
        ensure_ai_generated_disclosure,
        limit_platform_hashtags,
    )

    copy = _load_copy(pdir, plat, locale=locale)
    final_title = (title if title is not None else copy["title"]).strip()
    final_body = (body if body is not None else copy["body"]).strip()
    if plat == "kuaishou":
        final_body, _ = limit_platform_hashtags(plat, final_body)
    final_body = ensure_ai_generated_disclosure(final_body)
    if not final_title:
        raise PublishAssetsError(f"缺文案：平台 {plat} 标题为空，禁止发布")
    if not final_body:
        raise PublishAssetsError(f"缺文案：平台 {plat} 正文/描述为空，禁止发布")

    from engine.reach.cover_templates import (
        load_index,
        resolve_cover_store_for_settings,
        resolve_covers,
        validate_vertical_cover,
    )

    # Cover templates live in per-customer 05-品牌/封面模板, not work-area data_root.
    store = Path(data_root)
    try:
        preferred = resolve_cover_store_for_settings()
        if preferred.is_dir():
            store = preferred
    except Exception:
        pass
    # Explicit store path (has index.json) wins — used by smoke / callers that pass the store.
    if (Path(data_root) / "index.json").is_file():
        store = Path(data_root)

    # Every video platform must use the one vertical cover from App's selected template.
    selected = (load_index(store).get("selected_id") or "").strip() or None
    if not selected:
        if plat == "xhs":
            return {
                "ok": True,
                "platform": plat,
                "pack_dir": str(pdir),
                "video": str(video),
                "covers": [],
                "title": final_title,
                "body": final_body,
                "cover_optional": True,
                "cover_meta": {"ok": True, "source": "none", "optional": True},
                "contract": contract,
            }
        raise PublishAssetsError("请先在 App「封面设置」选择当前发布封面，禁止使用物料包封面代替")
    if template_id and template_id.strip() != selected:
        raise PublishAssetsError(
            f"请求封面模板「{template_id}」与 App 当前选用「{selected}」不一致"
        )
    effective_tid = selected
    cover_meta = resolve_covers(
        store,
        platform=plat,
        pack_dir=None,
        template_id=effective_tid,
    )
    if cover_meta.get("source") != "template" or cover_meta.get("template_id") != effective_tid:
        if plat == "xhs":
            return {
                "ok": True,
                "platform": plat,
                "pack_dir": str(pdir),
                "video": str(video),
                "covers": [],
                "title": final_title,
                "body": final_body,
                "cover_optional": True,
                "cover_meta": {**cover_meta, "ok": True, "optional": True},
                "contract": contract,
            }
        raise PublishAssetsError(
            f"必须使用 App 当前选用封面模板「{effective_tid}」的竖版封面，"
            f"当前来源={cover_meta.get('source')} template={cover_meta.get('template_id')}；"
            "请在 App「封面设置」补齐该平台竖版封面后再发"
        )
    if not cover_meta.get("ok"):
        if plat == "xhs":
            return {
                "ok": True,
                "platform": plat,
                "pack_dir": str(pdir),
                "video": str(video),
                "covers": [],
                "title": final_title,
                "body": final_body,
                "cover_optional": True,
                "cover_meta": {**cover_meta, "ok": True, "optional": True},
                "contract": contract,
            }
        raise PublishAssetsError(
            cover_meta.get("error")
            or f"App 封面模板 {effective_tid} 竖版封面未设置（{plat}）"
        )
    if not cover_meta.get("ok"):
        raise PublishAssetsError(cover_meta.get("error") or f"封面槽位不齐（{plat}）")

    covers = [Path(p) for p in (cover_meta.get("covers") or [])]
    if len(covers) != 1:
        raise PublishAssetsError(f"平台 {plat} 必须且只能使用 1 张 App 竖版封面")
    for i, c in enumerate(covers):
        if not c.is_file():
            raise PublishAssetsError(f"封面槽位 {i} 文件不存在: {c}")
        try:
            validate_vertical_cover(c)
        except ValueError as exc:
            raise PublishAssetsError(str(exc)) from exc

    return {
        "ok": True,
        "platform": plat,
        "pack_dir": str(pdir),
        "video": str(video),
        "covers": [str(c) for c in covers],
        "title": final_title or final_body.split("\n", 1)[0][:30],
        "body": final_body,
        "cover_meta": cover_meta,
        "contract": contract,
    }
