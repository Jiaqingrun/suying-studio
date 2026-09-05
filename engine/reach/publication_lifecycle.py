"""Single idempotent publication-group and retirement contract.

An output leaves the general candidate pool as soon as any frozen target starts.
Only targets in that same group may continue. Published facts are immutable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from engine.catalog.db import (
    AutomationOccurrence,
    Customer,
    Job,
    PublicationGroup,
    PublicationTarget,
    ReachPublishReservation,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachQueueItem,
    RenderOutput,
)

ACTIVE_GROUP_STATUSES = frozenset(
    {"open", "isolated", "retired_pending_archive", "archive_failed"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target_key(platform: str, account_key: str = "") -> tuple[str, str]:
    plat = str(platform or "").strip().lower()
    account = str(account_key or "").strip()
    if not plat:
        raise ValueError("发布目标缺少 platform")
    return plat, account


def _active_group(
    session: Session, *, customer_id: int, output_id: int
) -> PublicationGroup | None:
    return session.scalar(
        select(PublicationGroup)
        .where(
            PublicationGroup.customer_id == customer_id,
            PublicationGroup.output_id == output_id,
            PublicationGroup.status.in_(ACTIVE_GROUP_STATUSES),
        )
        .order_by(PublicationGroup.id.desc())
    )


def output_has_active_group(
    session: Session, *, customer_id: int, output_id: int
) -> bool:
    return _active_group(session, customer_id=customer_id, output_id=output_id) is not None


def freeze_publication_group(
    session: Session,
    *,
    customer_id: int,
    output_id: int,
    targets: Iterable[dict[str, Any]],
    source: str,
) -> tuple[PublicationGroup, dict[tuple[str, str], PublicationTarget]]:
    """Create an immutable target set, or reuse only an exact/subset active group."""
    output = session.get(RenderOutput, int(output_id))
    if not output:
        raise ValueError(f"成片 {output_id} 不存在")
    job_customer = session.scalar(select(Job.customer_id).where(Job.id == output.job_id))
    if job_customer != customer_id:
        raise ValueError("发布成片不属于当前客户")
    normalized = {
        _target_key(str(item.get("platform") or ""), str(item.get("account_key") or ""))
        for item in targets
    }
    if not normalized:
        raise ValueError("发布组至少需要一个目标平台")
    group = _active_group(session, customer_id=customer_id, output_id=output.id)
    if group:
        rows = session.scalars(
            select(PublicationTarget).where(PublicationTarget.group_id == group.id)
        ).all()
        by_key = {(row.platform, row.account_key): row for row in rows}
        # A platform-only manual target is the frozen occurrence for any later
        # selected account on that platform.
        missing = {
            key
            for key in normalized
            if key not in by_key and (key[0], "") not in by_key
        }
        if missing:
            raise ValueError(
                f"成片 {output.id} 已冻结到发布组 {group.group_key}，"
                f"不得加入新目标: {sorted(missing)}"
            )
        return group, by_key
    if output.state != "ready":
        raise ValueError(f"成片 {output.id} 状态 {output.state} 不可创建发布组")
    if ((output.qc_json or {}).get("ready_gate") or {}).get("ok") is not True:
        raise ValueError(f"成片 {output.id} 未明确通过 READY_GATE")
    if output.pack_status != "ready" or not output.pack_dir:
        raise ValueError(f"成片 {output.id} 发布物料未就绪")
    group = PublicationGroup(
        customer_id=customer_id,
        output_id=output.id,
        group_key=uuid.uuid4().hex,
        status="open",
        source=source or "manual",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(group)
    session.flush()
    by_key: dict[tuple[str, str], PublicationTarget] = {}
    for platform, account in sorted(normalized):
        target = PublicationTarget(
            group_id=group.id,
            customer_id=customer_id,
            output_id=output.id,
            platform=platform,
            account_key=account,
            status="pending",
            fact_json={},
            updated_at=_now(),
        )
        session.add(target)
        session.flush()
        by_key[(platform, account)] = target
    return group, by_key


def resolve_target(
    session: Session,
    *,
    group_id: int,
    platform: str,
    account_key: str = "",
) -> PublicationTarget:
    plat, account = _target_key(platform, account_key)
    target = session.scalar(
        select(PublicationTarget).where(
            PublicationTarget.group_id == group_id,
            PublicationTarget.platform == plat,
            PublicationTarget.account_key == account,
        )
    )
    if target is None and account:
        target = session.scalar(
            select(PublicationTarget).where(
                PublicationTarget.group_id == group_id,
                PublicationTarget.platform == plat,
                PublicationTarget.account_key == "",
            )
        )
    if target is None:
        raise ValueError("目标不属于冻结发布组")
    return target


def bind_queue_item(
    session: Session,
    item: ReachQueueItem,
    *,
    account_key: str = "",
    source: str = "manual_queue",
) -> PublicationTarget:
    if item.output_id is None:
        raise ValueError("发布队列缺少 output_id，旧 pack/路径推断禁止发布")
    if item.publication_group_id and item.publication_target_id:
        target = session.get(PublicationTarget, item.publication_target_id)
        if target and target.group_id == item.publication_group_id:
            return target
    group, _ = freeze_publication_group(
        session,
        customer_id=item.customer_id,
        output_id=item.output_id,
        targets=[{"platform": item.platform, "account_key": account_key}],
        source=source,
    )
    target = resolve_target(
        session,
        group_id=group.id,
        platform=item.platform,
        account_key=account_key,
    )
    item.publication_group_id = group.id
    item.publication_target_id = target.id
    return target


def assert_current_assets(
    session: Session,
    *,
    customer_id: int,
    output_id: int | None,
    pack_dir: str | None,
    video_path: str | None,
) -> RenderOutput:
    if output_id is None:
        raise ValueError("发布项缺少 output_id，禁止省略或从旧 publish_pack 猜测")
    output = session.get(RenderOutput, int(output_id))
    if not output:
        raise ValueError("发布成片不存在")
    job_customer = session.scalar(select(Job.customer_id).where(Job.id == output.job_id))
    if job_customer != customer_id:
        raise ValueError("发布成片不属于当前客户")
    if output.state != "ready":
        raise ValueError(f"成片状态 {output.state} 已退出发布候选")
    gate = (output.qc_json or {}).get("ready_gate") or {}
    if gate.get("ok") is not True:
        raise ValueError("成片缺少 READY_GATE 明确通过事实")
    if output.pack_status != "ready":
        raise ValueError(f"发布物料状态 {output.pack_status} 未就绪")
    if not output.pack_dir or not pack_dir:
        raise ValueError("缺少当前成片的持久化 publish_pack")
    if Path(output.pack_dir).expanduser().resolve() != Path(pack_dir).expanduser().resolve():
        raise ValueError("publish_pack 与 output_id 不匹配或已过期")
    if video_path and output.output_path:
        video = Path(video_path).expanduser().resolve()
        allowed = {
            Path(output.output_path).expanduser().resolve(),
            (Path(output.pack_dir).expanduser().resolve() / "video.mp4"),
        }
        if video not in allowed:
            raise ValueError("video_path 与 output_id/publish_pack 不匹配")
    return output


def claim_target_for_submission(
    session: Session,
    *,
    group_id: int,
    target_id: int,
    allow_existing: bool = False,
) -> PublicationTarget:
    """CAS pending -> submitting; published/unknown and concurrent claims fail closed."""
    group = session.get(PublicationGroup, group_id)
    target = session.get(PublicationTarget, target_id)
    if not group or not target or target.group_id != group.id:
        raise ValueError("发布组目标不存在")
    if group.status not in ("open", "isolated"):
        raise ValueError(f"发布组状态 {group.status} 只允许归档恢复")
    if allow_existing and target.status == "submitting":
        return target
    changed = session.execute(
        update(PublicationTarget)
        .where(
            PublicationTarget.id == target.id,
            PublicationTarget.status == "pending",
        )
        .values(status="submitting", started_at=_now(), updated_at=_now())
    ).rowcount
    if changed != 1:
        session.refresh(target)
        if target.status == "published":
            raise ValueError("该平台/账号已成功发布，永不重复")
        if target.status == "outcome_unknown":
            raise ValueError("发布结果不明，人工明确未发布前禁止重试")
        raise ValueError("该目标已被另一发布路径领取")
    group.status = "isolated"
    group.updated_at = _now()
    session.flush()
    session.refresh(target)
    return target


def release_unsubmitted_target(
    session: Session, *, group_id: int | None, target_id: int | None
) -> None:
    """Crash/skip recovery before submit; never releases unknown/published facts."""
    if not group_id or not target_id:
        return
    target = session.get(PublicationTarget, target_id)
    if target and target.group_id == group_id and target.status == "submitting":
        target.status = "pending"
        target.updated_at = _now()


def _invalidate_after_retirement(
    session: Session, *, group: PublicationGroup
) -> None:
    queues = session.scalars(
        select(ReachQueueItem).where(
            ReachQueueItem.customer_id == group.customer_id,
            ReachQueueItem.output_id == group.output_id,
            ReachQueueItem.status.not_in(("published", "cancelled")),
        )
    ).all()
    for row in queues:
        row.status = "cancelled"
        row.error = "成片全目标已成功，发布组进入退役归档"
        row.updated_at = _now()
    reservations = session.scalars(
        select(ReachPublishReservation).where(
            ReachPublishReservation.customer_id == group.customer_id,
            ReachPublishReservation.output_id == group.output_id,
            ReachPublishReservation.status == "reserved",
        )
    ).all()
    for row in reservations:
        row.status = "released"
        row.updated_at = _now()
    run_items = session.scalars(
        select(ReachPublishRunItem).where(
            ReachPublishRunItem.customer_id == group.customer_id,
            ReachPublishRunItem.output_id == group.output_id,
            ReachPublishRunItem.phase.not_in(("published", "cancelled")),
        )
    ).all()
    affected_runs: set[int] = set()
    for row in run_items:
        row.phase = "cancelled"
        row.outcome = "retired"
        row.retry_mode = ""
        row.retry_run_id = None
        row.error = "成片全目标已成功，其他运行项已失效"
        row.finished_at = _now()
        row.updated_at = _now()
        affected_runs.add(row.run_id)
    for run_id in affected_runs:
        run = session.get(ReachPublishRun, run_id)
        if run and run.status not in ("completed", "failed", "cancelled"):
            run.status = "completed"
            run.error = ""
            run.finished_at = _now()
            run.updated_at = _now()
    occurrences = session.scalars(
        select(AutomationOccurrence).where(
            AutomationOccurrence.customer_id == group.customer_id,
            AutomationOccurrence.output_id == group.output_id,
            AutomationOccurrence.status.not_in(("published", "completed", "cancelled")),
        )
    ).all()
    for occurrence in occurrences:
        occurrence.status = "published"
        occurrence.error = ""
        occurrence.finished_at = _now()
        occurrence.updated_at = _now()


def record_target_outcome(
    session: Session,
    *,
    group_id: int,
    target_id: int,
    outcome: str,
    evidence: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist one outcome and retire only after every frozen target succeeded."""
    normalized = outcome.strip().lower()
    if normalized not in ("published", "outcome_unknown", "not_published"):
        raise ValueError("publication outcome 非法")
    group = session.get(PublicationGroup, group_id)
    target = session.get(PublicationTarget, target_id)
    if not group or not target or target.group_id != group.id:
        raise ValueError("发布组目标不存在")
    if target.status == "published":
        return {"ok": True, "idempotent": True, "retired": group.status == "retired_published"}
    if normalized == "published":
        if target.status not in ("submitting", "outcome_unknown"):
            raise ValueError("目标尚未提交，不能记录发布成功")
        target.status = "published"
        target.published_at = _now()
        target.fact_json = {
            "outcome": "published",
            "at": target.published_at.isoformat(),
            "evidence": evidence or {},
            "note": note,
        }
    elif normalized == "outcome_unknown":
        if target.status not in ("submitting", "outcome_unknown"):
            raise ValueError("目标未处于提交中")
        target.status = "outcome_unknown"
        target.fact_json = {
            "outcome": "unknown",
            "at": _now().isoformat(),
            "evidence": evidence or {},
            "note": note,
        }
    else:
        if target.status != "outcome_unknown":
            raise ValueError("只有结果不明目标可由人工明确未发布后恢复")
        target.status = "pending"
        target.fact_json = {
            "outcome": "not_published",
            "confirmed_at": _now().isoformat(),
            "note": note,
        }
    target.updated_at = _now()
    group.status = "isolated"
    group.updated_at = _now()
    session.flush()
    targets = session.scalars(
        select(PublicationTarget).where(PublicationTarget.group_id == group.id)
    ).all()
    all_published = bool(targets) and all(row.status == "published" for row in targets)
    if not all_published:
        session.commit()
        return {"ok": True, "retired": False, "group_status": group.status}

    # Commit the isolation/invalidations before any filesystem operation.
    group.status = "retired_pending_archive"
    group.retired_at = _now()
    _invalidate_after_retirement(session, group=group)
    output = session.get(RenderOutput, group.output_id)
    if output:
        output.state = "retired_pending_archive"
    session.commit()
    return retry_group_archive(session, group_id=group.id)


def retry_group_archive(session: Session, *, group_id: int) -> dict[str, Any]:
    group = session.get(PublicationGroup, group_id)
    if not group:
        raise ValueError("发布组不存在")
    if group.status == "retired_published":
        return {"ok": True, "retired": True, "idempotent": True}
    if group.status not in ("retired_pending_archive", "archive_failed"):
        raise ValueError("发布组尚未进入归档阶段")
    output = session.get(RenderOutput, group.output_id)
    customer = session.get(Customer, group.customer_id)
    if not output or not customer:
        raise ValueError("归档所需客户或成片不存在")
    from engine.config.settings import load_settings
    from engine.ops.published_archive import archive_published_output

    root = customer.output_root or load_settings().paths.output_root
    group.archive_attempts += 1
    group.updated_at = _now()
    try:
        result = archive_published_output(
            output,
            output_root=root,
            platform="publication_group",
            note=f"group={group.group_key}",
        )
        if not result.get("ok"):
            raise OSError(str(result.get("error") or "archive_failed"))
        group.status = "retired_published"
        group.archive_error = ""
        group.archive_manifest_path = result.get("manifest")
        output.state = "retired_published"
        session.commit()
        return {"ok": True, "retired": True, "archive": result}
    except Exception as exc:
        group.status = "archive_failed"
        group.archive_error = str(exc)
        output.state = "retired_pending_archive"
        session.commit()
        return {"ok": False, "retired": False, "archive_only_retry": True, "error": str(exc)}


def retry_pending_archives(
    session: Session, *, customer_id: int | None = None
) -> list[dict[str, Any]]:
    """Engine-start recovery: retry filesystem retirement, never publication."""
    stmt = select(PublicationGroup).where(
        PublicationGroup.status.in_(("retired_pending_archive", "archive_failed"))
    )
    if customer_id is not None:
        stmt = stmt.where(PublicationGroup.customer_id == customer_id)
    groups = session.scalars(stmt.order_by(PublicationGroup.id.asc())).all()
    results: list[dict[str, Any]] = []
    for group in groups:
        try:
            results.append(
                {"group_id": group.id, **retry_group_archive(session, group_id=group.id)}
            )
        except Exception as exc:
            session.rollback()
            results.append(
                {
                    "group_id": group.id,
                    "ok": False,
                    "archive_only_retry": True,
                    "error": str(exc),
                }
            )
    return results
