"""Minimal message hub for Reach (G5).

Unread summary + deep-link hints. No auto-reply, no inbox scraping,
no login bypass — local queue-derived notices only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem
from engine.reach.browser import OFFICIAL_ENTRY


def _deep_link(platform: str) -> dict[str, str]:
    entry = OFFICIAL_ENTRY.get(platform) or {}
    return {
        "platform": platform,
        "label": str(entry.get("label") or platform),
        "url": str(entry.get("url") or ""),
    }


def build_inbox(
    session: Session,
    *,
    customer_id: int,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Local 'inbox' from reach_queue states that need human attention.

    This is NOT a scraped platform message center — only studio-side reminders
    to open official portals and finish human publish / unblock circuit.
    """
    stmt = (
        select(ReachQueueItem)
        .where(
            ReachQueueItem.customer_id == customer_id,
            ReachQueueItem.status.in_(("awaiting_human", "blocked", "failed", "queued")),
        )
        .order_by(ReachQueueItem.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    rows = list(session.scalars(stmt).all())
    notices: list[dict[str, Any]] = []
    unread = 0
    for r in rows:
        if r.status in ("awaiting_human", "blocked", "failed"):
            unread += 1
        link = _deep_link(r.platform)
        if r.status == "awaiting_human":
            summary = f"待你在「{link['label']}」手动发布：{r.title or '(无标题)'}"
            kind = "awaiting_publish"
        elif r.status == "blocked":
            summary = f"触达已熔断/阻塞（{r.platform}）：{r.error or r.note or '需人工处理'}"
            kind = "blocked"
        elif r.status == "failed":
            summary = f"发布项失败（{r.platform}）：{r.error or '查看详情'}"
            kind = "failed"
        else:
            summary = f"队列中（{r.platform}）：{r.title or r.id}"
            kind = "queued"
        notices.append(
            {
                "id": f"reach-{r.id}",
                "reach_item_id": r.id,
                "kind": kind,
                "status": r.status,
                "summary": summary,
                "unread": r.status in ("awaiting_human", "blocked", "failed"),
                "deep_link": link,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )

    return {
        "ok": True,
        "unread_count": unread,
        "notices": notices,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auto_reply": False,
        "scrapes_platform_inbox": False,
        "human_in_loop": True,
        "note": "仅汇总本机触达队列提醒；不读取平台私信，不自动回复。",
    }
