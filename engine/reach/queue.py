"""G5 Reach queue: enqueue / list / status transitions (no auto-publish)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import PublicationTarget, ReachQueueItem
from engine.reach.business_scope import VIDEO_PLATFORMS

PLATFORMS = tuple(sorted(VIDEO_PLATFORMS))

# Terminal / active states
STATUSES = (
    "queued",
    "awaiting_human",  # prefilled; waiting for human to click publish
    "published",  # human confirmed done
    "failed",
    "cancelled",
    "blocked",  # consecutive-failure circuit
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
        "publication_group_id": item.publication_group_id,
        "publication_target_id": item.publication_target_id,
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
    publication_group_id: int | None = None,
    publication_target_id: int | None = None,
) -> ReachQueueItem:
    """Add a publish task. Never triggers a real post."""
    platform = (platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError(f"不支持的平台: {platform}；可选 {', '.join(PLATFORMS)}")
    if status not in STATUSES:
        raise ValueError(f"非法状态: {status}")
    from engine.config.settings import load_settings
    from engine.reach.circuit import assert_circuit_closed

    settings = load_settings()
    assert_circuit_closed(
        session,
        customer_id=customer_id,
        fail_threshold=settings.reach_fail_threshold,
        platform=platform,
    )
    from engine.reach.publication_lifecycle import (
        assert_current_assets,
        freeze_publication_group,
        resolve_target,
    )

    assert_current_assets(
        session,
        customer_id=customer_id,
        output_id=output_id,
        pack_dir=pack_dir,
        video_path=video_path,
    )
    if publication_group_id and publication_target_id:
        target = session.get(PublicationTarget, publication_target_id)
        if (
            not target
            or target.group_id != publication_group_id
            or target.customer_id != customer_id
            or target.output_id != output_id
            or target.platform != platform
        ):
            raise ValueError("队列发布目标与冻结发布组不匹配")
    else:
        group, _ = freeze_publication_group(
            session,
            customer_id=customer_id,
            output_id=int(output_id),
            targets=[{"platform": platform, "account_key": ""}],
            source="queue",
        )
        target = resolve_target(session, group_id=group.id, platform=platform)
        publication_group_id = group.id
        publication_target_id = target.id
    item = ReachQueueItem(
        customer_id=customer_id,
        platform=platform,
        status=status,
        title=title or "",
        body=body or "",
        video_path=video_path or "",
        pack_dir=pack_dir,
        output_id=output_id,
        publication_group_id=publication_group_id,
        publication_target_id=publication_target_id,
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
    row = set_status(session, item, "published", note=note or item.note)
    from engine.reach.publication_lifecycle import (
        bind_queue_item,
        claim_target_for_submission,
        record_target_outcome,
    )

    target = bind_queue_item(session, row, source="manual_confirmation")
    if target.status == "pending":
        claim_target_for_submission(
            session,
            group_id=int(row.publication_group_id or 0),
            target_id=target.id,
        )
    record_target_outcome(
        session,
        group_id=int(row.publication_group_id or 0),
        target_id=target.id,
        outcome="published",
        evidence={"reach_item_id": row.id, "manual_confirmation": True},
        note=note,
    )
    return row


def cancel_item(session: Session, item: ReachQueueItem, *, note: str = "") -> ReachQueueItem:
    return set_status(session, item, "cancelled", note=note)
