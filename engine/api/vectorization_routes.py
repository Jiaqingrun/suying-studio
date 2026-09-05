from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import get_session
from engine.catalog.vectorization_runtime import create_run, executor, status_snapshot
from engine.catalog.vectorization_schedule import (
    library_gap_totals,
    maybe_vectorization_policy_tick,
    normalize_hhmm,
    parse_hhmm,
    schedule_policy_fields,
)
from engine.config.settings import load_settings, save_settings
from engine.runtime.pause_coordinator import assert_runtime_active

router = APIRouter(prefix="/vectorization", tags=["vectorization"])


class VectorizationRunCreate(BaseModel):
    mode: Literal["count", "all"] = "count"
    count: int | None = Field(default=50, ge=1, le=100000)
    orientation: Literal["portrait", "landscape"] = "portrait"


class VectorizationScheduleUpdate(BaseModel):
    schedule_enabled: bool | None = None
    schedule_start: str | None = None
    schedule_end: str | None = None
    auto_stop_when_done: bool | None = None


def _attach_policy(result: dict, settings, session, customer_id: int) -> dict:
    policy = schedule_policy_fields(settings)
    result.update(policy)
    gaps = library_gap_totals(session, customer_id)
    result["gaps"] = gaps
    result["enabled"] = bool(settings.vectorization_enabled)
    result["vectorization_mode"] = str(
        getattr(settings, "vectorization_mode", None) or "incremental"
    )
    return result


@router.get("/status")
def vectorization_status(
    orientation: Literal["portrait", "landscape"] = "portrait",
) -> dict:
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        result = status_snapshot(
            session, customer_id=customer.id, orientation=orientation
        )
        result = _attach_policy(result, settings, session, int(customer.id))
        result["customer_name"] = customer.name
        return result
    finally:
        session.close()


@router.post("/runs")
def vectorization_create_run(body: VectorizationRunCreate) -> dict:
    assert_runtime_active("vectorization_run")
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        try:
            count = body.count
            if body.mode == "count" and (count is None or count == 50):
                # Prefer gate_profile vector_batch_size when client sends default.
                batch = int(getattr(settings, "vector_batch_size", 0) or 0)
                if batch > 0:
                    count = batch
            run = create_run(
                session,
                customer_id=customer.id,
                mode=body.mode,
                count=count if body.mode == "count" else None,
                orientation=body.orientation,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        settings.vectorization_enabled = True
        settings.vectorization_mode = "incremental"
        save_settings(settings)
        executor.wake()
        result = status_snapshot(
            session, customer_id=customer.id, orientation=body.orientation
        )
        result["run_id"] = run.run_id
        result = _attach_policy(result, settings, session, int(customer.id))
        result["customer_name"] = customer.name
        return result
    finally:
        session.close()


@router.post("/pause")
def vectorization_pause() -> dict:
    return executor.pause(owner="manual", timeout=2.0)


@router.post("/resume")
def vectorization_resume() -> dict:
    assert_runtime_active("vectorization_resume")
    settings = load_settings()
    settings.vectorization_enabled = True
    save_settings(settings)
    return executor.resume(owner="manual")


@router.post("/disable")
def vectorization_disable() -> dict:
    from engine.config.settings import disable_vectorization

    disable_vectorization()
    result = executor.disable()
    result["enabled"] = False
    result["vectorization_mode"] = "incremental"
    return result


@router.post("/enable-switch")
def vectorization_enable_switch(kick_reconcile: bool = False, limit: int = 50) -> dict:
    """运维页主开关：开启增量向量化；可选冻结一小批并唤醒执行器。"""
    assert_runtime_active("vector_reconcile")
    from engine.config.settings import enable_vectorization

    settings = enable_vectorization()
    out: dict = {
        "enabled": True,
        "vectorization_mode": str(settings.vectorization_mode or "incremental"),
        "message": "已开启增量向量化（保留已有向量，只补齐缺口）。",
    }
    if kick_reconcile:
        try:
            run = vectorization_create_run(
                VectorizationRunCreate(
                    mode="count", count=max(1, min(int(limit or 50), 100000))
                )
            )
            out.update(run)
            out["message"] = "已开启增量向量化，并冻结最老优先批次。"
            out["reconcile_started"] = True
        except HTTPException:
            raise
        except Exception as exc:
            out["reconcile_error"] = str(exc)
    else:
        executor.wake()
    return out


@router.post("/schedule")
def vectorization_update_schedule(body: VectorizationScheduleUpdate) -> dict:
    """Save daily window + auto-stop; evaluate edge once."""
    settings = load_settings()
    if body.schedule_start is not None:
        try:
            settings.vectorization_schedule_start = normalize_hhmm(body.schedule_start)
        except ValueError as exc:
            raise HTTPException(400, f"schedule_start 无效: {body.schedule_start}") from exc
    if body.schedule_end is not None:
        try:
            settings.vectorization_schedule_end = normalize_hhmm(body.schedule_end)
        except ValueError as exc:
            raise HTTPException(400, f"schedule_end 无效: {body.schedule_end}") from exc
    start = settings.vectorization_schedule_start
    end = settings.vectorization_schedule_end
    try:
        if parse_hhmm(start) == parse_hhmm(end):
            raise HTTPException(400, "定时起止不能相同（避免无窗口歧义）")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.schedule_enabled is not None:
        was = bool(settings.vectorization_schedule_enabled)
        settings.vectorization_schedule_enabled = bool(body.schedule_enabled)
        # Reset edge detector when turning schedule on so seed/enter can run cleanly.
        if body.schedule_enabled and not was:
            settings.vectorization_schedule_last_in_window = None
        if not body.schedule_enabled:
            settings.vectorization_schedule_last_in_window = None
    if body.auto_stop_when_done is not None:
        settings.vectorization_auto_stop_when_done = bool(body.auto_stop_when_done)
    save_settings(settings)

    policy = maybe_vectorization_policy_tick()
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        result = status_snapshot(session, customer_id=customer.id, orientation="portrait")
        result = _attach_policy(result, settings, session, int(customer.id))
        result["customer_name"] = customer.name
        result["policy_tick"] = policy
        result["message"] = (
            "定时窗口已保存"
            if settings.vectorization_schedule_enabled
            else "已关闭定时；手动开关与自动结束仍可独立使用"
        )
        return result
    finally:
        session.close()


@router.get("/orientation-lock")
def vectorization_orientation_lock() -> dict:
    """L16 hard orientation audit status (App 硬性展示入口)."""
    settings = load_settings()
    session = get_session()
    try:
        from engine.ingest.orientation import (
            assert_orientation_lock_integrity,
            orientation_lock_snapshot,
        )

        assert_orientation_lock_integrity()
        customer = require_active_customer(session, settings)
        snap = orientation_lock_snapshot(session, customer_id=customer.id)
        snap["customer_name"] = customer.name
        snap["hard_ui"] = True
        return snap
    finally:
        session.close()
