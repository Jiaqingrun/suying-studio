"""G5 Reach queue: enqueue / list / status transitions (no auto-publish)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem

PLATFORMS = (
    "douyin",
    "channels",
    "xhs",
    "kuaishou",
    "baijiahao",
    "toutiao",
    "zhihu",
)

# Terminal / active states
STATUSES = (
    "queued",
    "awaiting_human",  # prefilled; waiting for human to click publish
    "published",  # human confirmed done
    "failed",
    "cancelled",
    "blocked",  # quota / circuit (filled in later gate items)
)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"awaiting_human", "cancelled", "blocked", "failed"},
    "awaiting_human": {"published", "failed", "cancelled", "blocked"},
    "blocked": {"queued", "cancelled"},
    "failed": {"queued", "cancelled"},
    "published": set(),
    "cancelled": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_log(item: ReachQueueItem, event: str, **payload: Any) -> None:
    logs = list(item.log_json or [])
    logs.append({"at": _now().isoformat(), "event": event, **payload})
    item.log_json = logs


def item_to_dict(item: ReachQueueItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "customer_id": item.customer_id,
        "platform": item.platform,
        "status": item.status,
        "title": item.title,
        "body": item.body,
        "video_path": item.video_path,
        "pack_dir": item.pack_dir,
        "output_id": item.output_id,
        "copy": item.copy_json or {},
        "note": item.note,
        "error": item.error,
        "log": item.log_json or [],
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "human_in_loop": True,
        "auto_publish": False,
    }


def enqueue(
    session: Session,
    *,
    customer_id: int,
    platform: str,
    title: str = "",
    body: str = "",
    video_path: str = "",
    pack_dir: str | None = None,
    output_id: int | None = None,
    copy_json: dict[str, Any] | None = None,
    note: str = "",
    status: str = "queued",
) -> ReachQueueItem:
    """Add a publish task. Never triggers a real post."""
    platform = (platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError(f"不支持的平台: {platform}；可选 {', '.join(PLATFORMS)}")
    if status not in STATUSES:
        raise ValueError(f"非法状态: {status}")
    item = ReachQueueItem(
        customer_id=customer_id,
        platform=platform,
        status=status,
        title=title or "",
        body=body or "",
        video_path=video_path or "",
        pack_dir=pack_dir,
        output_id=output_id,
        copy_json=copy_json or {},
        note=note or "",
        error="",
        log_json=[],
        created_at=_now(),
        updated_at=_now(),
    )
    _append_log(item, "enqueued", platform=platform)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def list_items(
    session: Session,
    *,
    customer_id: int,
    status: str | None = None,
    platform: str | None = None,
    limit: int = 100,
) -> list[ReachQueueItem]:
    stmt = select(ReachQueueItem).where(ReachQueueItem.customer_id == customer_id)
    if status:
        stmt = stmt.where(ReachQueueItem.status == status)
    if platform:
        stmt = stmt.where(ReachQueueItem.platform == platform)
    stmt = stmt.order_by(ReachQueueItem.id.desc()).limit(max(1, min(limit, 500)))
    return list(session.scalars(stmt).all())


def get_item(session: Session, item_id: int, *, customer_id: int | None = None) -> ReachQueueItem | None:
    item = session.get(ReachQueueItem, item_id)
    if not item:
        return None
    if customer_id is not None and item.customer_id != customer_id:
        return None
    return item


def set_status(
    session: Session,
    item: ReachQueueItem,
    new_status: str,
    *,
    note: str | None = None,
    error: str | None = None,
) -> ReachQueueItem:
    new_status = (new_status or "").strip().lower()
    if new_status not in STATUSES:
        raise ValueError(f"非法状态: {new_status}")
    allowed = ALLOWED_TRANSITIONS.get(item.status, set())
    if new_status != item.status and new_status not in allowed:
        raise ValueError(f"状态不可从 {item.status} → {new_status}")
    old = item.status
    item.status = new_status
    item.updated_at = _now()
    if note is not None:
        item.note = note
    if error is not None:
        item.error = error
    _append_log(item, "status", from_status=old, to_status=new_status, note=note or "", error=error or "")
    session.commit()
    session.refresh(item)
    return item


def mark_awaiting_human(session: Session, item: ReachQueueItem) -> ReachQueueItem:
    """Prefill done — human must open platform and click publish."""
    return set_status(session, item, "awaiting_human")


def mark_published(session: Session, item: ReachQueueItem, *, note: str = "") -> ReachQueueItem:
    """Human confirms they clicked publish (we do not automate the click)."""
    return set_status(session, item, "published", note=note or item.note)


def cancel_item(session: Session, item: ReachQueueItem, *, note: str = "") -> ReachQueueItem:
    return set_status(session, item, "cancelled", note=note)
