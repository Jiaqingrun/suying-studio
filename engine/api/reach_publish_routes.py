"""G5.BATCH publish run and schedule API routes."""

from __future__ import annotations

import random
import secrets
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import (
    AutomationOccurrence,
    AutomationPlan,
    HumanAlert,
    ReachPublishSchedule,
    get_session,
)
from engine.config.settings import load_settings
from engine.reach.publish_runner import (
    cancel_run,
    confirm_item_outcome,
    create_run,
    get_run,
    get_status,
    list_deferred_items,
    preflight_items,
    retry_deferred_items,
    resume_run,
    run_to_dict,
    start_run,
    stop_after_current,
    skip_current_item,
    run_item_to_dict,
)
from engine.reach.publish_schedule import (
    ensure_window_triggers,
    next_trigger_at,
    schedule_to_dict,
    tick_schedules,
    trigger_to_dict,
)
from engine.runtime.pause_coordinator import assert_runtime_active

router = APIRouter(prefix="/reach/publish", tags=["reach-publish"])


class RunItemSpec(BaseModel):
    queue_id: int | None = None
    output_id: int | None = None
    publication_group_id: int | None = None
    publication_target_id: int | None = None
    platform: str
    chrome_profile: str
    title: str = ""
    body: str = ""
    video_path: str = ""
    pack_dir: str | None = None
    group_label: str = ""
    gate_snapshot: dict[str, Any] = Field(default_factory=dict)


class CreateRunRequest(BaseModel):
    items: list[RunItemSpec]
    accept_risk: bool = False


class ImmediateAccountSpec(BaseModel):
    platform: str
    chrome_profile: str


class ImmediateBatchRequest(BaseModel):
    accounts: list[ImmediateAccountSpec]
    total_count: int = Field(ge=1, le=200)
    allocation_mode: str = "auto_even"
    manual_counts: dict[str, int] = Field(default_factory=dict)
    content_mode: str = "random_unique"
    output_ids: list[int] = Field(default_factory=list)
    seed: int | None = None
    accept_risk: bool = False
    # Temporary override: allow READY 成片 that already have published queue rows.
    allow_published: bool = False


class ConfirmOutcomeRequest(BaseModel):
    item_id: int
    outcome: str
    note: str = ""
    retry_mode: str = "manual"


class SkipItemRequest(BaseModel):
    item_id: int
    retry_mode: str = "manual"


class RetryDeferredRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)


class ScheduleCreateRequest(BaseModel):
    name: str = ""
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    chrome_profile: str
    platform: str
    content_source: str = "manual_ready"
    source_config: dict[str, Any] = Field(default_factory=dict)
    times: list[dict[str, Any]] = Field(default_factory=list)
    windows: list[dict[str, Any]] = Field(default_factory=list)
    items_per_trigger: int = 1
    repeat_count: int = 1
    accept_risk: bool = False


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    timezone: str | None = None
    chrome_profile: str | None = None
    platform: str | None = None
    content_source: str | None = None
    source_config: dict[str, Any] | None = None
    times: list[dict[str, Any]] | None = None
    windows: list[dict[str, Any]] | None = None
    items_per_trigger: int | None = None
    repeat_count: int | None = None


class ScheduleTaskTarget(BaseModel):
    platform: str
    chrome_profile: str
    publish_count: int = Field(default=1, ge=1, le=20)


class ScheduleTaskCreateRequest(BaseModel):
    name: str = ""
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    targets: list[ScheduleTaskTarget] = Field(min_length=1, max_length=20)
    windows: list[dict[str, Any]] = Field(min_length=1)
    weekdays: list[int] = Field(min_length=1)
    auto_production: bool = True
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    min_gap_minutes: int = Field(default=0, ge=0, le=1440)
    accept_risk: bool = False


class ScheduleTaskUpdateRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None


class AutomationPlanRequest(BaseModel):
    name: str = ""
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    target_ready_count: int = Field(default=1, ge=1, le=100)
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    publish_schedule_id: int | None = None
    review_policy: str = "ready_gate"
    max_production_attempts: int = Field(default=3, ge=1, le=10)
    max_replacement_attempts: int = Field(default=2, ge=0, le=5)
    config: dict[str, Any] = Field(default_factory=dict)


class AutomationPlanUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    timezone: str | None = None
    target_ready_count: int | None = Field(default=None, ge=1, le=100)
    template_name: str | None = None
    theme: str | None = None
    category: str | None = None
    publish_schedule_id: int | None = None
    review_policy: str | None = None
    max_production_attempts: int | None = Field(default=None, ge=1, le=10)
    max_replacement_attempts: int | None = Field(default=None, ge=0, le=5)
    config: dict[str, Any] | None = None


def _active_customer(session):
    settings = load_settings()
    return require_active_customer(session, settings)


def _active_customer_id() -> int:
    session = get_session()
    try:
        return int(_active_customer(session).id)
    finally:
        session.close()


def _validate_schedule_request(
    *,
    customer_id: int,
    timezone_name: str,
    chrome_profile: str,
    platform: str,
    content_source: str,
    source_config: dict[str, Any],
    times: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    items_per_trigger: int,
) -> None:
    from engine.reach.browser import list_chrome_profiles
    from engine.reach.business_scope import SCOPE_VIDEO

    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"无效时区: {timezone_name}") from exc

    plat = platform.strip().lower()
    listed = list_chrome_profiles(
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
    )
    matched = any(
        profile.get("name") == chrome_profile
        and str(profile.get("platform") or "").strip().lower() == plat
        and profile.get("provisioning_status") == "explicit"
        and profile.get("login_status") == "verified_logged_in"
        for profile in listed.get("profiles") or []
    )
    if not matched:
        raise ValueError("发布账号不存在、未实时确认登录或与所选平台不匹配")

    allowed_sources = {"manual_ready", "eligible_ready_random", "generate_then_publish"}
    if content_source not in allowed_sources:
        raise ValueError("未知的定时发布内容来源")
    if content_source == "manual_ready" and not source_config.get("queue_ids"):
        raise ValueError("手选内容模式至少需要一条待发布视频")
    if content_source == "generate_then_publish" and not (
        source_config.get("template_name") or source_config.get("approved_types")
    ):
        raise ValueError("自动生成模式缺少已批准的视频类型")
    if not times and not windows:
        raise ValueError("请至少设置一个发布时间或时间段")
    if not 1 <= int(items_per_trigger) <= 20:
        raise ValueError("每次发布条数须为 1–20")

    for entry in times:
        hour = int(entry.get("hour", -1))
        minute = int(entry.get("minute", -1))
        if hour not in range(24) or minute not in range(60):
            raise ValueError("发布时间格式无效")
        weekdays = entry.get("weekdays") or []
        if weekdays and any(int(day) not in range(7) for day in weekdays):
            raise ValueError("星期设置无效")

    for entry in windows:
        start = str(entry.get("start") or "")
        end = str(entry.get("end") or "")
        for value in (start, end):
            parsed = None
            for pattern in ("%H:%M:%S", "%H:%M"):
                try:
                    parsed = datetime.strptime(value, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError("时间段格式应为 HH:MM:SS")
            if parsed.hour not in range(24):
                raise ValueError("时间段格式无效")
        if start == end:
            raise ValueError("最早和最晚发布时间不能相同")
        weekdays = entry.get("weekdays") or []
        if not weekdays or any(int(day) not in range(7) for day in weekdays):
            raise ValueError("请至少选择一个有效星期")
        count = int(entry.get("count") or 0)
        if count not in range(1, 21):
            raise ValueError("每天发布条数须为 1–20")
        if int(entry.get("min_gap_minutes") or 0) < 0:
            raise ValueError("最小间隔不能为负数")


def _release_future_reservations(session, *, schedule_id: int, customer_id: int) -> int:
    from sqlalchemy import select

    from engine.catalog.db import ReachPublishReservation

    rows = session.scalars(
        select(ReachPublishReservation).where(
            ReachPublishReservation.schedule_id == schedule_id,
            ReachPublishReservation.customer_id == customer_id,
            ReachPublishReservation.status == "reserved",
        )
    ).all()
    for reservation in rows:
        reservation.status = "released"
        reservation.updated_at = datetime.now(timezone.utc)
    return len(rows)


def _cancel_future_schedule_triggers(
    session, *, schedule_id: int, customer_id: int, reason: str
) -> int:
    from sqlalchemy import select

    from engine.catalog.db import ReachPublishTrigger
    from engine.reach.publish_schedule import release_trigger_reservations

    rows = session.scalars(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.schedule_id == schedule_id,
            ReachPublishTrigger.customer_id == customer_id,
            ReachPublishTrigger.status.in_(("pending", "blocked_reservation")),
            ReachPublishTrigger.claimed_at.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for trigger in rows:
        release_trigger_reservations(session, trigger)
        trigger.status = "cancelled"
        trigger.note = f"{trigger.note}\n{reason}".strip()
        trigger.updated_at = now
    return len(rows)


def _build_immediate_preview(
    session,
    *,
    customer_id: int,
    body: ImmediateBatchRequest,
) -> dict[str, Any]:
    from engine.reach.browser import list_chrome_profiles, resolve_profile_platform
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.publish_sources import (
        allocate_occurrences,
        assign_content_to_occurrences,
        immediate_asset_block_reasons,
        keep_eligible_account_occurrences,
        list_immediate_candidates,
    )

    accounts = [account.model_dump() for account in body.accounts]
    if not accounts:
        raise ValueError("请先添加并登录一个发布账号")
    listed = list_chrome_profiles(
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
    )
    explicit_accounts = {
        (str(profile.get("name") or ""), str(profile.get("platform") or "").strip().lower())
        for profile in listed.get("profiles") or []
        if profile.get("provisioning_status") == "explicit"
    }
    for account in accounts:
        requested = (
            account["chrome_profile"],
            account["platform"].strip().lower(),
        )
        if requested not in explicit_accounts:
            raise ValueError(
                f"发布账号 {account['chrome_profile']} 不存在、不是明确创建的账号或与平台不匹配"
            )
        resolved = resolve_profile_platform(
            account["chrome_profile"],
            account["platform"],
            customer_id=customer_id,
            business_scope=SCOPE_VIDEO,
        )
        if resolved != account["platform"].strip().lower():
            raise ValueError(f"账号 {account['chrome_profile']} 与平台不匹配")
    candidates = list_immediate_candidates(
        session,
        customer_id=customer_id,
        platforms=[account["platform"] for account in accounts],
        allow_published=bool(body.allow_published),
    )
    eligible_platforms = {
        platform
        for candidate in candidates
        for platform in candidate.get("eligible_platforms") or []
    }
    block_reasons = immediate_asset_block_reasons(
        session,
        customer_id=customer_id,
        platforms=[account["platform"] for account in accounts],
    )
    skipped_accounts = [
        {
            **account,
            "asset_error": block_reasons.get(account["platform"], "发布物料不完整"),
        }
        for account in accounts
        if account["platform"] not in eligible_platforms
    ]
    if len(skipped_accounts) == len(accounts):
        platforms_text = "、".join(sorted({account["platform"] for account in accounts}))
        details = "；".join(
            f"{account['platform']}：{account['asset_error']}" for account in skipped_accounts
        )
        raise ValueError(f"{platforms_text} 当前没有发布物料齐全的合格视频：{details}")
    requested_occurrences = allocate_occurrences(
        accounts,
        total_count=body.total_count,
        allocation_mode=body.allocation_mode,
        manual_counts=body.manual_counts,
    )
    occurrences, skipped_occurrences = keep_eligible_account_occurrences(
        requested_occurrences,
        eligible_platforms=eligible_platforms,
    )
    if not occurrences:
        raise ValueError("本次分配到的账号均缺少对应平台发布资产")
    seed = int(body.seed) if body.seed is not None else random.SystemRandom().randrange(1, 1 << 30)
    assignments = assign_content_to_occurrences(
        occurrences,
        content_mode=body.content_mode,
        candidate_outputs=candidates,
        selected_output_ids=body.output_ids,
        seed=seed,
    )
    return {
        "seed": seed,
        "algorithm": "python_random_shuffle_v1",
        "assignments": assignments,
        "candidate_count": len(candidates),
        "skipped_accounts": skipped_accounts,
        "skipped_occurrences": skipped_occurrences,
        "requested_count": body.total_count,
        "actual_count": len(assignments),
        "reserved_excluded": False,
        "allow_published": bool(body.allow_published),
    }


@router.post("/immediate/preview")
def preview_immediate_batch(body: ImmediateBatchRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_immediate_preview")
    if not body.accounts:
        raise HTTPException(
            409,
            detail={"code": "needs_account_setup", "message": "请先添加并登录一个发布账号"},
        )
    session = get_session()
    try:
        customer = _active_customer(session)
        return {"ok": True, **_build_immediate_preview(session, customer_id=customer.id, body=body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/immediate/start")
def start_immediate_batch(body: ImmediateBatchRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_immediate_start")
    if not body.accounts:
        raise HTTPException(
            409,
            detail={"code": "needs_account_setup", "message": "请先添加并登录一个发布账号"},
        )
    if not body.accept_risk:
        raise HTTPException(400, "须 accept_risk=true")
    from engine.reach.queue import enqueue
    from engine.reach.prefill import load_pack_prefill

    session = get_session()
    try:
        customer = _active_customer(session)
        preview = _build_immediate_preview(session, customer_id=customer.id, body=body)
        if preview["skipped_occurrences"]:
            skipped = "；".join(
                f"{account.get('chrome_profile') or account.get('platform')}："
                f"{account.get('asset_error') or '发布物料不完整'}"
                for account in preview["skipped_accounts"]
            )
            raise ValueError(
                f"所选账号发布物料阻断：{skipped}。"
                "本次未创建任何队列；请先补齐物料后重试。"
            )
        items: list[dict[str, Any]] = []
        from engine.reach.publication_lifecycle import (
            freeze_publication_group,
            resolve_target,
        )

        frozen: dict[int, Any] = {}
        for output_id in {
            int(assignment["output"]["output_id"])
            for assignment in preview["assignments"]
        }:
            assignments_for_output = [
                assignment
                for assignment in preview["assignments"]
                if int(assignment["output"]["output_id"]) == output_id
            ]
            group, _ = freeze_publication_group(
                session,
                customer_id=customer.id,
                output_id=output_id,
                targets=[
                    {
                        "platform": assignment["platform"],
                        "account_key": assignment["chrome_profile"],
                    }
                    for assignment in assignments_for_output
                ],
                source="immediate",
            )
            frozen[output_id] = group
        for assignment in preview["assignments"]:
            output = assignment["output"]
            output_id = int(output["output_id"])
            group = frozen[output_id]
            target = resolve_target(
                session,
                group_id=group.id,
                platform=assignment["platform"],
                account_key=assignment["chrome_profile"],
            )
            prefill = load_pack_prefill(output["pack_dir"])
            platform_copy = (prefill.get("platforms") or {}).get(assignment["platform"]) or {}
            title = str(platform_copy.get("title") or output.get("title") or "")
            from engine.pack.publish import ensure_ai_generated_disclosure

            body_text = ensure_ai_generated_disclosure(
                str(platform_copy.get("body") or "")
            )
            row = enqueue(
                session,
                customer_id=customer.id,
                platform=assignment["platform"],
                title=title,
                body=body_text,
                video_path=prefill.get("video_path") or output.get("video_path") or "",
                pack_dir=output.get("pack_dir"),
                output_id=output.get("output_id"),
                copy_json={"platform": platform_copy},
                note=f"immediate seed={preview['seed']}",
                publication_group_id=group.id,
                publication_target_id=target.id,
            )
            items.append(
                {
                    "queue_id": row.id,
                    "output_id": output.get("output_id"),
                    "publication_group_id": group.id,
                    "publication_target_id": target.id,
                    "platform": assignment["platform"],
                    "chrome_profile": assignment["chrome_profile"],
                    "title": title,
                    "body": body_text,
                    "video_path": prefill.get("video_path") or output.get("video_path") or "",
                    "pack_dir": output.get("pack_dir"),
                    "group_label": assignment["account_key"],
                    "gate_snapshot": {"ready_gate": True, "reserved_excluded": False},
                    "assignment": assignment,
                }
            )
        run = create_run(
            session,
            customer_id=customer.id,
            items=items,
            source="immediate",
            launch_mode="immediate",
            requested_total=len(preview["assignments"]),
            allocation_mode=body.allocation_mode,
            content_mode=body.content_mode,
            selection_seed=str(preview["seed"]),
            config={
                "accounts": [account.model_dump() for account in body.accounts],
                "manual_counts": body.manual_counts,
                "output_ids": body.output_ids,
                "algorithm": preview["algorithm"],
                "reserved_excluded": False,
                "allow_published": bool(body.allow_published),
                "requested_count": body.total_count,
                "actual_count": len(preview["assignments"]),
                "skipped_accounts": preview["skipped_accounts"],
            },
            accept_risk=True,
        )
        from engine.ops.audit_log import write_log

        write_log(
            session,
            customer_id=customer.id,
            category="publish",
            event="immediate_batch_frozen",
            stage="assignment",
            message=f"即时批次 {run.run_id} 已冻结 {len(preview['assignments'])} 个 occurrence",
            source_type="publish_run",
            source_id=run.run_id,
            correlation_id=f"publish:{run.run_id}",
            details={
                "allocation_mode": body.allocation_mode,
                "content_mode": body.content_mode,
                "seed": preview["seed"],
                "assignments": preview["assignments"],
                "requested_count": body.total_count,
                "actual_count": len(preview["assignments"]),
                "skipped_accounts": preview["skipped_accounts"],
                "reserved_excluded": False,
            },
            commit=True,
        )
        started = start_run(run.run_id)
        if not started.get("ok"):
            raise HTTPException(409, "发布槽正忙，条目已保留到待补发")
        return {"ok": True, **run_to_dict(run), "assignments": preview["assignments"]}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/runs/preflight")
def preflight_run(body: CreateRunRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_preflight")
    session = get_session()
    try:
        customer = _active_customer(session)
        result = preflight_items(
            session,
            customer_id=customer.id,
            items=[i.model_dump() for i in body.items],
        )
        return {"ok": True, **result}
    finally:
        session.close()


@router.post("/runs")
def create_publish_run(body: CreateRunRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_create")
    if not body.accept_risk:
        raise HTTPException(400, "须 accept_risk=true")
    session = get_session()
    try:
        customer = _active_customer(session)
        run = create_run(
            session,
            customer_id=customer.id,
            items=[i.model_dump() for i in body.items],
            accept_risk=True,
        )
        started = start_run(run.run_id)
        if not started.get("ok"):
            raise HTTPException(409, "发布槽正忙，条目已保留到待补发")
        return {"ok": True, **run_to_dict(run)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.get("/runs/{run_id}")
def get_publish_run(run_id: str, compact: bool = False) -> dict[str, Any]:
    session = get_session()
    try:
        customer = _active_customer(session)
        run = get_run(session, run_id, customer_id=customer.id)
        if not run:
            raise HTTPException(404, "批次不存在")
        from engine.reach.publish_runner import list_run_items

        items = list_run_items(session, run)
        current = next(
            (item for item in items if item.phase not in {
                "published",
                "failed",
                "skipped",
                "deferred",
                "awaiting_confirmation",
                "cancelled",
            }),
            None,
        )
        if current is None:
            current = next(
                (item for item in items if item.phase == "awaiting_confirmation"),
                None,
            )
        payload = run_to_dict(run, items=items, compact=compact)
        payload.update(
            {
                "ok": True,
                "active": run.status not in {
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted_system",
                },
                "current_item": (
                    run_item_to_dict(current, include_evidence=not compact)
                    if current
                    else None
                ),
                "phase": current.phase if current else run.status,
            }
        )
        return payload
    finally:
        session.close()


@router.get("/runs/active/status")
def active_publish_status() -> dict[str, Any]:
    return get_status(customer_id=_active_customer_id())


@router.post("/runs/{run_id}/resume")
def resume_publish_run(run_id: str) -> dict[str, Any]:
    assert_runtime_active("reach_publish_resume")
    try:
        return resume_run(run_id, customer_id=_active_customer_id())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/runs/{run_id}/stop-after-current")
def stop_publish_after_current(run_id: str) -> dict[str, Any]:
    return stop_after_current(run_id, customer_id=_active_customer_id())


@router.post("/runs/{run_id}/cancel")
def cancel_publish_run(run_id: str) -> dict[str, Any]:
    return cancel_run(run_id, customer_id=_active_customer_id())


@router.post("/runs/{run_id}/skip-current")
def skip_publish_current(run_id: str, body: SkipItemRequest) -> dict[str, Any]:
    try:
        return skip_current_item(
            run_id,
            body.item_id,
            retry_mode=body.retry_mode,
            customer_id=_active_customer_id(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/runs/{run_id}/confirm-outcome")
def confirm_publish_outcome(run_id: str, body: ConfirmOutcomeRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_confirm")
    try:
        return confirm_item_outcome(
            run_id,
            body.item_id,
            outcome=body.outcome,
            note=body.note,
            retry_mode=body.retry_mode,
            customer_id=_active_customer_id(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/deferred")
def deferred_publish_items() -> dict[str, Any]:
    session = get_session()
    try:
        customer = _active_customer(session)
        rows = list_deferred_items(session, customer_id=customer.id)
        return {
            "ok": True,
            "items": [
                run_item_to_dict(row, include_evidence=False) for row in rows
            ],
        }
    finally:
        session.close()


@router.get("/makeup")
def publish_makeup_list() -> dict[str, Any]:
    """Pending makeup: deferred items + missed/makeup_pending triggers."""
    from engine.reach.publish_schedule import list_makeup_work

    session = get_session()
    try:
        customer = _active_customer(session)
        return list_makeup_work(session, customer_id=customer.id)
    finally:
        session.close()


@router.post("/makeup/retry")
def publish_makeup_retry(body: RetryDeferredRequest) -> dict[str, Any]:
    """Human-triggered safe retry of deferred items only (no silent window re-open)."""
    assert_runtime_active("reach_publish_makeup_retry")
    session = get_session()
    try:
        customer = _active_customer(session)
        run = retry_deferred_items(
            session,
            customer_id=customer.id,
            item_ids=body.item_ids or None,
            automatic=False,
        )
        if run is None:
            raise HTTPException(409, "发布槽正忙，请稍后再试")
        return {"ok": True, **run_to_dict(run)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/deferred/retry")
def retry_publish_items(body: RetryDeferredRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_retry_deferred")
    session = get_session()
    try:
        customer = _active_customer(session)
        run = retry_deferred_items(
            session,
            customer_id=customer.id,
            item_ids=body.item_ids or None,
            automatic=False,
        )
        if run is None:
            raise HTTPException(409, "发布槽正忙，请稍后再试")
        return {"ok": True, **run_to_dict(run)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.get("/schedules")
def list_schedules() -> dict[str, Any]:
    from sqlalchemy import select
    from engine.catalog.db import ReachPublishTrigger

    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(ReachPublishSchedule).where(ReachPublishSchedule.customer_id == customer.id)
        ).all()
        out = []
        for row in rows:
            d = schedule_to_dict(row)
            nxt = session.scalar(
                select(ReachPublishTrigger.planned_at)
                .where(
                    ReachPublishTrigger.schedule_id == row.id,
                    ReachPublishTrigger.status.in_(
                        ("pending", "blocked_reservation")
                    ),
                )
                .order_by(ReachPublishTrigger.planned_at.asc())
                .limit(1)
            ) or next_trigger_at(row)
            if nxt:
                nxt_utc = (
                    nxt.replace(tzinfo=timezone.utc)
                    if nxt.tzinfo is None
                    else nxt.astimezone(timezone.utc)
                )
                d["next_trigger_at"] = nxt_utc.isoformat().replace("+00:00", "Z")
            else:
                d["next_trigger_at"] = None
            out.append(d)
        return {"ok": True, "schedules": out}
    finally:
        session.close()


@router.post("/schedules")
def create_schedule(body: ScheduleCreateRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_schedule_create")
    if not body.accept_risk:
        raise HTTPException(400, "请确认使用本人账号并接受自动发布风险")
    session = get_session()
    try:
        customer = _active_customer(session)
        platform = body.platform.strip().lower()
        content_source = body.content_source.strip().lower()
        _validate_schedule_request(
            customer_id=customer.id,
            timezone_name=body.timezone,
            chrome_profile=body.chrome_profile,
            platform=platform,
            content_source=content_source,
            source_config=body.source_config,
            times=body.times,
            windows=body.windows,
            items_per_trigger=body.items_per_trigger,
        )
        row = ReachPublishSchedule(
            customer_id=customer.id,
            name=body.name or f"{body.platform}-{body.chrome_profile}",
            enabled=body.enabled,
            timezone=body.timezone,
            chrome_profile=body.chrome_profile,
            platform=platform,
            content_source=content_source,
            source_config_json={
                **body.source_config,
                "accept_risk": True,
                "schedule_revision": int(
                    body.source_config.get("schedule_revision") or 1
                ),
            },
            times_json=body.times,
            windows_json=body.windows,
            items_per_trigger=max(1, body.items_per_trigger),
            repeat_count=max(1, body.repeat_count),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        if row.enabled:
            ensure_window_triggers(session, row)
        return {"ok": True, "schedule": schedule_to_dict(row)}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


def _task_group_id(row: ReachPublishSchedule) -> str:
    config = row.source_config_json if isinstance(row.source_config_json, dict) else {}
    return str(config.get("task_group_id") or f"legacy-{row.id}")


def _task_to_dict(rows: list[ReachPublishSchedule]) -> dict[str, Any]:
    first = rows[0]
    config = first.source_config_json if isinstance(first.source_config_json, dict) else {}
    targets = []
    for row in rows:
        windows = list(row.windows_json or [])
        publish_count = max(
            [int(window.get("count") or 1) for window in windows if isinstance(window, dict)]
            or [int(row.repeat_count or 1)]
        )
        targets.append(
            {
                "schedule_id": row.id,
                "platform": row.platform,
                "chrome_profile": row.chrome_profile,
                "publish_count": publish_count,
                "enabled": row.enabled,
            }
        )
    return {
        "id": _task_group_id(first),
        "name": str(config.get("task_name") or first.name),
        "enabled": any(row.enabled for row in rows),
        "timezone": first.timezone,
        "auto_production": bool(config.get("auto_production")),
        "windows": list(first.windows_json or []),
        "targets": targets,
        "schedules": [schedule_to_dict(row) for row in rows],
    }


@router.get("/tasks")
def list_schedule_tasks() -> dict[str, Any]:
    from collections import defaultdict
    from sqlalchemy import select

    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(ReachPublishSchedule)
            .where(ReachPublishSchedule.customer_id == customer.id)
            .order_by(ReachPublishSchedule.id.asc())
        ).all()
        groups: dict[str, list[ReachPublishSchedule]] = defaultdict(list)
        for row in rows:
            groups[_task_group_id(row)].append(row)
        return {
            "ok": True,
            "tasks": [_task_to_dict(group_rows) for group_rows in groups.values()],
        }
    finally:
        session.close()


@router.post("/tasks")
def create_schedule_task(body: ScheduleTaskCreateRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_task_create")
    if not body.accept_risk:
        raise HTTPException(400, "请确认使用本人账号并接受自动发布风险")
    if any(day not in range(7) for day in body.weekdays):
        raise HTTPException(400, "星期设置无效")
    session = get_session()
    try:
        customer = _active_customer(session)
        task_group_id = secrets.token_hex(8)
        source = "generate_then_publish" if body.auto_production else "eligible_ready_random"
        # Both sources gap-produce when ready is short; prefer generate_then_publish.
        rows: list[ReachPublishSchedule] = []
        seen: set[tuple[str, str]] = set()
        for target in body.targets:
            platform = target.platform.strip().lower()
            account_key = (platform, target.chrome_profile)
            if account_key in seen:
                raise ValueError(f"账号 {target.chrome_profile} 重复")
            seen.add(account_key)
            windows = []
            for window in body.windows:
                entry = dict(window)
                entry["weekdays"] = sorted(set(body.weekdays))
                entry["count"] = target.publish_count
                entry["min_gap_minutes"] = body.min_gap_minutes
                windows.append(entry)
            source_config = {
                "task_group_id": task_group_id,
                "task_name": body.name or "自动发布任务",
                "auto_production": body.auto_production,
                "template_name": body.template_name,
                "theme": body.theme,
                "category": body.category,
                "accept_risk": True,
                "schedule_revision": 1,
            }
            _validate_schedule_request(
                customer_id=customer.id,
                timezone_name=body.timezone,
                chrome_profile=target.chrome_profile,
                platform=platform,
                content_source=source,
                source_config=source_config,
                times=[],
                windows=windows,
                items_per_trigger=1,
            )
            row = ReachPublishSchedule(
                customer_id=customer.id,
                name=body.name or "自动发布任务",
                enabled=body.enabled,
                timezone=body.timezone,
                chrome_profile=target.chrome_profile,
                platform=platform,
                content_source=source,
                source_config_json=source_config,
                times_json=[],
                windows_json=windows,
                items_per_trigger=1,
                repeat_count=target.publish_count,
                random_algorithm="window_seconds_v2",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            rows.append(row)
        session.commit()
        for row in rows:
            session.refresh(row)
            if row.enabled:
                ensure_window_triggers(session, row)
        return {"ok": True, "task": _task_to_dict(rows)}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.patch("/tasks/{task_id}")
def update_schedule_task(
    task_id: str, body: ScheduleTaskUpdateRequest
) -> dict[str, Any]:
    from sqlalchemy import select

    assert_runtime_active("reach_publish_task_update")
    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(ReachPublishSchedule).where(
                ReachPublishSchedule.customer_id == customer.id
            )
        ).all()
        matched = [row for row in rows if _task_group_id(row) == task_id]
        if not matched:
            raise HTTPException(404, "自动任务不存在")
        now = datetime.now(timezone.utc)
        for row in matched:
            if body.enabled is not None:
                was_enabled = bool(row.enabled)
                row.enabled = body.enabled
                if body.enabled is False:
                    _cancel_future_schedule_triggers(
                        session,
                        schedule_id=row.id,
                        customer_id=customer.id,
                        reason="自动任务已停用",
                    )
                elif not was_enabled:
                    config = dict(row.source_config_json or {})
                    config["schedule_revision"] = int(
                        config.get("schedule_revision") or 1
                    ) + 1
                    row.source_config_json = config
            if body.name is not None:
                row.name = body.name.strip() or row.name
                config = dict(row.source_config_json or {})
                config["task_name"] = row.name
                row.source_config_json = config
            row.updated_at = now
        session.commit()
        if any(row.enabled for row in matched):
            for row in matched:
                if row.enabled:
                    ensure_window_triggers(session, row)
        return {"ok": True, "task": _task_to_dict(matched)}
    finally:
        session.close()


@router.delete("/tasks/{task_id}")
def delete_schedule_task(task_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    assert_runtime_active("reach_publish_task_delete")
    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(ReachPublishSchedule).where(
                ReachPublishSchedule.customer_id == customer.id
            )
        ).all()
        matched = [row for row in rows if _task_group_id(row) == task_id]
        if not matched:
            raise HTTPException(404, "自动任务不存在")
        cancelled = 0
        for row in matched:
            row.enabled = False
            cancelled += _cancel_future_schedule_triggers(
                session,
                schedule_id=row.id,
                customer_id=customer.id,
                reason="自动任务已删除",
            )
            row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True, "disabled": len(matched), "cancelled": cancelled}
    finally:
        session.close()


@router.patch("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleUpdateRequest) -> dict[str, Any]:
    assert_runtime_active("reach_publish_schedule_update")
    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(ReachPublishSchedule, schedule_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "计划不存在")
        was_enabled = bool(row.enabled)
        _validate_schedule_request(
            customer_id=customer.id,
            timezone_name=body.timezone if body.timezone is not None else row.timezone,
            chrome_profile=(
                body.chrome_profile if body.chrome_profile is not None else row.chrome_profile
            ),
            platform=body.platform if body.platform is not None else row.platform,
            content_source=(
                body.content_source if body.content_source is not None else row.content_source
            ),
            source_config=(
                body.source_config
                if body.source_config is not None
                else dict(row.source_config_json or {})
            ),
            times=body.times if body.times is not None else list(row.times_json or []),
            windows=body.windows if body.windows is not None else list(row.windows_json or []),
            items_per_trigger=(
                body.items_per_trigger
                if body.items_per_trigger is not None
                else row.items_per_trigger
            ),
        )
        for field, attr in (
            ("name", "name"),
            ("enabled", "enabled"),
            ("timezone", "timezone"),
            ("chrome_profile", "chrome_profile"),
            ("platform", "platform"),
            ("content_source", "content_source"),
            ("items_per_trigger", "items_per_trigger"),
            ("repeat_count", "repeat_count"),
        ):
            val = getattr(body, field)
            if val is not None:
                if field in ("platform", "content_source"):
                    val = str(val).strip().lower()
                setattr(row, attr, val)
        if body.source_config is not None:
            row.source_config_json = body.source_config
        if body.times is not None:
            row.times_json = body.times
        if body.windows is not None:
            row.windows_json = body.windows
        schedule_changed = any(
            key in body.model_fields_set
            for key in (
                "timezone",
                "chrome_profile",
                "platform",
                "content_source",
                "source_config",
                "times",
                "windows",
                "items_per_trigger",
                "repeat_count",
            )
        )
        if schedule_changed or body.enabled is False:
            _cancel_future_schedule_triggers(
                session,
                schedule_id=row.id,
                customer_id=customer.id,
                reason=(
                    "计划已停用"
                    if body.enabled is False
                    else "计划配置已更新，旧版未来随机时间已取消"
                ),
            )
        if schedule_changed or (
            body.enabled is True and not was_enabled
        ):
            config = dict(row.source_config_json or {})
            config["schedule_revision"] = int(
                config.get("schedule_revision") or 1
            ) + 1
            row.source_config_json = config
        row.updated_at = datetime.now(timezone.utc)
        if body.enabled is False:
            _release_future_reservations(
                session, schedule_id=row.id, customer_id=customer.id
            )
        session.commit()
        if row.enabled:
            ensure_window_triggers(session, row)
        return {"ok": True, "schedule": schedule_to_dict(row)}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int) -> dict[str, Any]:
    assert_runtime_active("reach_publish_schedule_delete")
    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(ReachPublishSchedule, schedule_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "计划不存在")
        row.enabled = False
        row.updated_at = datetime.now(timezone.utc)
        _cancel_future_schedule_triggers(
            session,
            schedule_id=row.id,
            customer_id=customer.id,
            reason="计划已停用",
        )
        released = _release_future_reservations(
            session, schedule_id=row.id, customer_id=customer.id
        )
        session.commit()
        return {"ok": True, "disabled": True, "id": schedule_id, "released": released}
    finally:
        session.close()


@router.get("/schedules/{schedule_id}/preview")
def preview_schedule(schedule_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(ReachPublishSchedule, schedule_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "计划不存在")
        from sqlalchemy import select
        from engine.catalog.db import ReachPublishTrigger

        future = session.scalars(
            select(ReachPublishTrigger)
            .where(
                ReachPublishTrigger.schedule_id == row.id,
                ReachPublishTrigger.status.in_(("pending", "blocked_reservation")),
            )
            .order_by(ReachPublishTrigger.planned_at.asc())
            .limit(100)
        ).all()
        nxt = future[0].planned_at if future else next_trigger_at(row)
        next_trigger_iso = None
        if nxt:
            nxt_utc = (
                nxt.replace(tzinfo=timezone.utc)
                if nxt.tzinfo is None
                else nxt.astimezone(timezone.utc)
            )
            next_trigger_iso = nxt_utc.isoformat().replace("+00:00", "Z")
        return {
            "ok": True,
            "schedule": schedule_to_dict(row),
            "next_trigger_at": next_trigger_iso,
            "triggers": [trigger_to_dict(trigger) for trigger in future[:100]],
        }
    finally:
        session.close()


@router.post("/schedules/{schedule_id}/run-once")
def run_schedule_once(schedule_id: int) -> dict[str, Any]:
    assert_runtime_active("reach_publish_schedule_run_once")
    from engine.reach.publish_sources import materialize_from_schedule

    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(ReachPublishSchedule, schedule_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "计划不存在")
        mat = materialize_from_schedule(session, row)
        if mat.get("pending_generation"):
            raise HTTPException(400, "生成任务已创建，成片未 ready")
        rows = mat.get("queue_items") or []
        if not rows:
            raise HTTPException(400, "无可用内容")
        items = [
            {
                "queue_id": r.id,
                "platform": r.platform,
                "chrome_profile": row.chrome_profile,
                "title": r.title,
                "video_path": r.video_path,
                "pack_dir": r.pack_dir,
                "gate_snapshot": mat.get("evidence") or {},
            }
            for r in rows
        ]
        run = create_run(
            session,
            customer_id=customer.id,
            items=items,
            source="schedule_manual",
            schedule_id=row.id,
            accept_risk=True,
        )
        started = start_run(run.run_id)
        if not started.get("ok"):
            raise HTTPException(409, "发布槽正忙，条目已保留到待补发")
        return {"ok": True, "run_id": run.run_id}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/schedules/tick")
def tick_publish_schedules() -> dict[str, Any]:
    assert_runtime_active("reach_publish_schedule_tick")
    session = get_session()
    try:
        customer = _active_customer(session)
        results = tick_schedules(session, customer_id=customer.id)
        return {"ok": True, "results": results}
    finally:
        session.close()


@router.get("/automation/plans")
def list_automation_plans() -> dict[str, Any]:
    from sqlalchemy import select

    from engine.ops.automation_loop import plan_to_dict

    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(AutomationPlan).where(AutomationPlan.customer_id == customer.id)
        ).all()
        return {"ok": True, "plans": [plan_to_dict(row) for row in rows]}
    finally:
        session.close()


@router.post("/automation/plans")
def create_automation_plan(body: AutomationPlanRequest) -> dict[str, Any]:
    from engine.ops.automation_loop import plan_to_dict

    assert_runtime_active("automation_plan_create")
    session = get_session()
    try:
        customer = _active_customer(session)
        if body.publish_schedule_id is not None:
            schedule = session.get(ReachPublishSchedule, body.publish_schedule_id)
            if not schedule or schedule.customer_id != customer.id:
                raise HTTPException(400, "发布计划不存在或不属于当前客户")
        row = AutomationPlan(
            customer_id=customer.id,
            name=body.name or "自动闭环计划",
            enabled=body.enabled,
            timezone=body.timezone,
            target_ready_count=body.target_ready_count,
            template_name=body.template_name,
            theme=body.theme,
            category=body.category,
            publish_schedule_id=body.publish_schedule_id,
            review_policy=body.review_policy,
            max_production_attempts=body.max_production_attempts,
            max_replacement_attempts=body.max_replacement_attempts,
            config_json=body.config,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "plan": plan_to_dict(row)}
    finally:
        session.close()


@router.patch("/automation/plans/{plan_id}")
def update_automation_plan(
    plan_id: int, body: AutomationPlanUpdateRequest
) -> dict[str, Any]:
    from engine.ops.automation_loop import plan_to_dict

    assert_runtime_active("automation_plan_update")
    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(AutomationPlan, plan_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "闭环计划不存在")
        values = body.model_dump(exclude_unset=True)
        if "publish_schedule_id" in values and values["publish_schedule_id"] is not None:
            schedule = session.get(ReachPublishSchedule, values["publish_schedule_id"])
            if not schedule or schedule.customer_id != customer.id:
                raise HTTPException(400, "发布计划不存在或不属于当前客户")
        for key, value in values.items():
            setattr(row, "config_json" if key == "config" else key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True, "plan": plan_to_dict(row)}
    finally:
        session.close()


@router.delete("/automation/plans/{plan_id}")
def disable_automation_plan(plan_id: int) -> dict[str, Any]:
    assert_runtime_active("automation_plan_disable")
    session = get_session()
    try:
        customer = _active_customer(session)
        row = session.get(AutomationPlan, plan_id)
        if not row or row.customer_id != customer.id:
            raise HTTPException(404, "闭环计划不存在")
        row.enabled = False
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True, "disabled": True}
    finally:
        session.close()


@router.get("/automation/occurrences")
def list_automation_occurrences(limit: int = 100) -> dict[str, Any]:
    from sqlalchemy import select

    from engine.ops.automation_loop import occurrence_to_dict

    session = get_session()
    try:
        customer = _active_customer(session)
        rows = session.scalars(
            select(AutomationOccurrence)
            .where(AutomationOccurrence.customer_id == customer.id)
            .order_by(AutomationOccurrence.id.desc())
            .limit(min(max(limit, 1), 500))
        ).all()
        return {
            "ok": True,
            "occurrences": [occurrence_to_dict(row) for row in rows],
        }
    finally:
        session.close()


@router.post("/automation/tick")
def tick_automation_loop() -> dict[str, Any]:
    from engine.ops.automation_loop import tick_automation

    assert_runtime_active("automation_tick")
    session = get_session()
    try:
        customer = _active_customer(session)
        return {"ok": True, "results": tick_automation(session, customer_id=customer.id)}
    finally:
        session.close()


@router.get("/human-alerts")
def list_human_alerts(status: str = "open") -> dict[str, Any]:
    from sqlalchemy import select

    from engine.ops.human_alerts import alert_to_dict, notification_preflight

    session = get_session()
    try:
        customer = _active_customer(session)
        stmt = select(HumanAlert).where(HumanAlert.customer_id == customer.id)
        if status:
            stmt = stmt.where(HumanAlert.status == status)
        rows = session.scalars(stmt.order_by(HumanAlert.id.desc()).limit(200)).all()
        return {
            "ok": True,
            "alerts": [alert_to_dict(row) for row in rows],
            "notification_preflight": notification_preflight(customer.id),
        }
    finally:
        session.close()


@router.post("/human-alerts/{alert_id}/acknowledge")
def acknowledge_human_alert(alert_id: int) -> dict[str, Any]:
    from engine.ops.human_alerts import acknowledge_alert, alert_to_dict

    session = get_session()
    try:
        customer = _active_customer(session)
        row = acknowledge_alert(session, alert_id, customer_id=customer.id)
        if not row:
            raise HTTPException(404, "提醒不存在")
        return {"ok": True, "alert": alert_to_dict(row)}
    finally:
        session.close()


