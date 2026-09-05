"""Heal incomplete publish materials; then gap-produce only when still short.

Contract:
- L2 (empty copy / damaged pack / unfinished cover contract) → re-export pack once/twice.
- L1 (not enough READY after heal) → create_job gap path (callers / materialize).
- L3 (login wall / unfinished chrome profile) → never production; never auto makeup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import Customer, ReachPublishRunItem, ReachQueueItem, RenderOutput

log = logging.getLogger("suying.reach.asset_heal")

ASSET_HEAL_BUDGET = 2

# Permanent: human or login — do not auto-makeup and do not trigger production.
_HARD_NO_AUTO_NEEDLES = (
    "配置尚未完成登录",
    "不完整: ",  # chrome profile incomplete paths
    "禁止使用未登录",
    "账号未登录或验证卡住",
    "需要重新扫码",
    "true_login_wall",
    "登录宽限已过",
    "CDP 不可用",
    "已有受管 Chrome 运行",
)

# Soft asset gaps: re-export pack / re-fill copy, then auto retry.
_ASSET_INCOMPLETE_NEEDLES = (
    "缺文案",
    "正文/描述为空",
    "标题为空",
    "发布物料",
    "物料阻断",
    "封面槽位",
    "publish_pack 不存在",
    "缺少合同文件",
    "合规检查未通过",
    "本作品由AI生成",
    "四平台发布物料不完整",
    "禁止使用物料包封面",
    "请先在 App「封面设置」",
)


def is_hard_no_auto_error(error: str) -> bool:
    text = str(error or "")
    return any(n in text for n in _HARD_NO_AUTO_NEEDLES)


def is_asset_incomplete_error(error: str) -> bool:
    text = str(error or "")
    if is_hard_no_auto_error(text):
        return False
    return any(n in text for n in _ASSET_INCOMPLETE_NEEDLES)


def repair_output_pack(
    session: Session,
    *,
    customer_id: int,
    output_id: int,
    force: bool = True,
) -> dict[str, Any]:
    """Force re-export mandatory four-platform pack for one ready/soft output."""
    from engine.catalog.review_auto import ensure_publish_pack

    customer = session.get(Customer, customer_id)
    output = session.get(RenderOutput, int(output_id))
    if not customer or not output:
        return {"ok": False, "error": "客户或成片不存在"}
    if output.state not in ("ready", "asset_blocked", "review"):
        return {"ok": False, "error": f"成片状态不可修包: {output.state}"}
    result = ensure_publish_pack(session, customer, output, force=force)
    return result if isinstance(result, dict) else {"ok": False, "error": "pack_failed"}


def _load_platform_copy(pack_dir: str, platform: str) -> tuple[str, str]:
    from engine.reach.prefill import load_pack_prefill

    prefill = load_pack_prefill(Path(pack_dir))
    platform_copy = (prefill.get("platforms") or {}).get(platform) or {}
    title = str(platform_copy.get("title") or "").strip()
    body = str(
        platform_copy.get("body") or platform_copy.get("caption") or ""
    ).strip()
    return title, body


def refresh_queue_and_item_from_pack(
    session: Session,
    *,
    platform: str,
    pack_dir: str,
    queue_id: int | None,
    item: ReachPublishRunItem | None,
) -> dict[str, Any]:
    """Rewrite title/body/pack_dir from pack prefill after successful export."""
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    title, body = _load_platform_copy(pack_dir, platform)
    try:
        assets = require_publish_assets(
            platform=platform,
            pack_dir=pack_dir,
            data_root=resolve_cover_store_for_settings(),
            title=title,
            body=body,
        )
        title = str(assets.get("title") or title).strip()
        body = str(assets.get("body") or body).strip()
    except PublishAssetsError as exc:
        return {"ok": False, "error": str(exc)}
    if queue_id:
        q = session.get(ReachQueueItem, int(queue_id))
        if q:
            q.pack_dir = pack_dir
            q.title = title
            q.body = body
            copy = dict(q.copy_json or {}) if isinstance(q.copy_json, dict) else {}
            platforms = dict(copy.get("platform") or copy.get("platforms") or {})
            platforms[platform] = {
                **(platforms.get(platform) if isinstance(platforms.get(platform), dict) else {}),
                "title": title,
                "body": body,
            }
            copy["platform"] = platforms
            q.copy_json = copy
    if item is not None:
        item.pack_dir = pack_dir
        item.title = title
        item.error = ""
    return {"ok": True, "title": title, "body": body, "pack_dir": pack_dir}


def heal_deferred_assets(session: Session, *, customer_id: int) -> dict[str, int]:
    """Rebuild packs for deferred/failed makeup items and promote to auto deferred.

    After this, callers should run repair_deferred_auto_eligibility + retry_deferred.
    """
    from datetime import datetime, timezone

    from engine.reach.publish_runner import list_deferred_items

    now = datetime.now(timezone.utc)
    stats = {
        "scanned": 0,
        "healed": 0,
        "failed_heal": 0,
        "promoted_auto": 0,
        "exhausted": 0,
        "skipped_hard": 0,
        "skipped_budget": 0,
    }
    rows = list_deferred_items(session, customer_id=customer_id)
    for row in rows:
        stats["scanned"] += 1
        err = str(row.error or "")
        if is_hard_no_auto_error(err):
            stats["skipped_hard"] += 1
            continue
        # Heal asset incomplete OR empty body patterns; also soft failed/skipped.
        need = is_asset_incomplete_error(err) or ("缺文案" in err)
        if not need:
            continue
        if not row.output_id:
            continue
        ev = dict(row.evidence_json or {})
        heal_count = int(ev.get("asset_heal_count") or 0)
        if heal_count >= ASSET_HEAL_BUDGET:
            stats["skipped_budget"] += 1
            if row.retry_mode != "none" and is_asset_incomplete_error(err):
                row.retry_mode = "none"
                stats["exhausted"] += 1
            continue
        pack = repair_output_pack(
            session,
            customer_id=customer_id,
            output_id=int(row.output_id),
            force=True,
        )
        ev["asset_heal_count"] = heal_count + 1
        ev["asset_heal_last"] = now.isoformat()
        if not pack.get("ok"):
            row.evidence_json = ev
            row.error = f"物料修复失败：{pack.get('error') or 'unknown'}"
            row.updated_at = now
            stats["failed_heal"] += 1
            if int(ev["asset_heal_count"]) >= ASSET_HEAL_BUDGET:
                row.retry_mode = "none"
                stats["exhausted"] += 1
            continue
        pack_dir = str(pack.get("pack_dir") or row.pack_dir or "")
        refreshed = refresh_queue_and_item_from_pack(
            session,
            platform=str(row.platform or ""),
            pack_dir=pack_dir,
            queue_id=row.queue_id,
            item=row,
        )
        if not refreshed.get("ok"):
            row.evidence_json = ev
            row.error = str(refreshed.get("error") or "物料仍不齐")
            row.updated_at = now
            stats["failed_heal"] += 1
            if int(ev["asset_heal_count"]) >= ASSET_HEAL_BUDGET:
                row.retry_mode = "none"
                stats["exhausted"] += 1
            continue
        # Success: leave as deferred auto ready for chain (clear cool for asset heals).
        row.phase = "deferred"
        row.retry_mode = "auto"
        row.retry_after = now
        row.retry_run_id = None
        row.error = ""
        row.evidence_json = {**ev, "asset_healed": True}
        row.updated_at = now
        stats["healed"] += 1
        stats["promoted_auto"] += 1
        log.info(
            "asset heal ok item=%s output=%s platform=%s",
            row.id,
            row.output_id,
            row.platform,
        )
    return stats


def heal_ready_pool_for_platform(
    session: Session,
    *,
    customer_id: int,
    platform: str,
    limit: int = 12,
) -> int:
    """Force-export packs for READY outputs that fail platform assets (before pick)."""
    from sqlalchemy import select

    from engine.catalog.db import Job
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.publication_lifecycle import output_has_active_group
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets
    from engine.reach.publish_sources import (
        _output_spec,
        _published_output_ids,
        has_current_approved_review,
        has_verified_ready_gate,
    )

    published = _published_output_ids(session, customer_id, platform)
    outputs = session.scalars(
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .where(RenderOutput.state.in_(("ready", "asset_blocked")), Job.customer_id == customer_id)
        .order_by(RenderOutput.id.desc())
        .limit(80)
    ).all()
    healed = 0
    for out in outputs:
        if healed >= limit:
            break
        if output_has_active_group(session, customer_id=customer_id, output_id=out.id):
            continue
        if out.id in published:
            continue
        if not has_verified_ready_gate(
            out.qc_json if isinstance(out.qc_json, dict) else None
        ):
            continue
        if not has_current_approved_review(session, out.id):
            continue
        spec = _output_spec(out)
        # Missing pack or asset fail → force rebuild.
        need = spec is None
        if not need and spec:
            try:
                require_publish_assets(
                    platform=platform,
                    pack_dir=spec["pack_dir"],
                    data_root=resolve_cover_store_for_settings(),
                )
            except PublishAssetsError:
                need = True
        if not need:
            continue
        res = repair_output_pack(
            session, customer_id=customer_id, output_id=out.id, force=True
        )
        if res.get("ok"):
            healed += 1
            session.flush()
    return healed
