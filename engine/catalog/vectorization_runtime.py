"""Durable oldest-first, machine-wide single-slot vectorization executor."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text

from engine.catalog.db import (
    Asset,
    Cliplet,
    VectorizationQueue,
    VectorizationRun,
    get_session,
)

log = logging.getLogger("montage.vectorization")

ACTIVE_STATES = ("IDLE", "RUNNING", "PAUSE_REQUESTED", "PAUSED", "BLOCKED")
CLAIM_STALE_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_vector(
    session: Any,
    *,
    customer_id: int | None,
    event: str,
    message: str,
    run_id: str = "",
    asset_id: int | None = None,
    level: str = "info",
    stage: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit that never changes vectorization outcome."""
    try:
        from engine.ops.audit_log import write_log

        with session.begin_nested():
            write_log(
                session,
                customer_id=customer_id,
                category="vector",
                event=event,
                message=message,
                level=level,
                stage=stage,
                source_type="vector_asset" if asset_id is not None else "vector_run",
                source_id=asset_id if asset_id is not None else run_id,
                correlation_id=f"vector:{run_id}" if run_id else "",
                details={"asset_id": asset_id, **(details or {})},
            )
    except Exception:
        log.exception("vector audit write failed event=%s run=%s asset=%s", event, run_id, asset_id)


def _asset_has_gap(session: Any, asset_id: int) -> bool:
    total = int(
        session.scalar(
            select(func.count()).select_from(Cliplet).where(Cliplet.asset_id == asset_id)
        )
        or 0
    )
    pending = int(
        session.scalar(
            select(func.count()).select_from(Cliplet).where(
                Cliplet.asset_id == asset_id,
                Cliplet.status == "usable",
                Cliplet.embedding_json.is_(None),
            )
        )
        or 0
    )
    return total == 0 or pending > 0


def enqueue_asset(session: Any, asset: Asset) -> VectorizationQueue | None:
    """Idempotently enqueue one ready asset without running model work."""
    if asset.status != "ready" or asset.customer_id is None:
        return None
    from engine.ingest.orientation import asset_may_vectorize

    # L16 HARD: no vectorization without orientation gate (audited or display-consistent legacy).
    if not asset_may_vectorize(asset):
        return None
    if not _asset_has_gap(session, int(asset.id)):
        return None
    orientation = str(asset.orientation or "").lower()
    if orientation not in {"portrait", "landscape"}:
        # Do not invent portrait from sideways coded dimensions without rotation.
        return None
    row = session.scalar(
        select(VectorizationQueue).where(
            VectorizationQueue.customer_id == int(asset.customer_id),
            VectorizationQueue.asset_id == int(asset.id),
        )
    )
    now = _now()
    created = row is None
    if created:
        eligible = asset.created_at or now
        row = VectorizationQueue(
            customer_id=int(asset.customer_id),
            asset_id=int(asset.id),
            orientation=orientation,
            status="QUEUED",
            eligible_at=eligible,
            enqueued_at=eligible,
            updated_at=now,
        )
        session.add(row)
    elif row.status in ("COMPLETED", "FAILED") and _asset_has_gap(session, int(asset.id)):
        row.status = "QUEUED"
        row.run_id = None
        row.claim_token = None
        row.claimed_at = None
        row.error = None
        row.completed_at = None
        row.updated_at = now
    if row is not None:
        row.orientation = orientation
    if created:
        _audit_vector(
            session,
            customer_id=int(asset.customer_id),
            event="vector_asset_queued",
            message="素材已加入向量队列",
            asset_id=int(asset.id),
            stage="queue",
        )
    session.commit()
    session.refresh(row)
    return row


def enqueue_customer_gaps(session: Any, customer_id: int) -> int:
    """Repeated scans are safe; queue age is never refreshed."""
    assets = session.scalars(
        select(Asset)
        .where(Asset.customer_id == customer_id, Asset.status == "ready")
        .order_by(Asset.created_at.asc(), Asset.id.asc())
    ).all()
    changed = 0
    for asset in assets:
        before = session.scalar(
            select(VectorizationQueue.id).where(
                VectorizationQueue.customer_id == customer_id,
                VectorizationQueue.asset_id == asset.id,
            )
        )
        row = enqueue_asset(session, asset)
        if row is not None and before is None:
            changed += 1
    return changed


def recover_stale_claims(session: Any, *, stale_seconds: int = CLAIM_STALE_SECONDS) -> int:
    cutoff = _now() - timedelta(seconds=max(1, stale_seconds))
    result = session.execute(
        text(
            "UPDATE vectorization_queue SET status='QUEUED', claim_token=NULL, claimed_at=NULL, "
            "last_attempt_status='ABANDONED', "
            "updated_at=:now WHERE status='CLAIMED' AND (claimed_at IS NULL OR claimed_at < :cutoff)"
        ),
        {"now": _now(), "cutoff": cutoff},
    )
    session.commit()
    return int(result.rowcount or 0)


def recover_after_restart() -> None:
    """Never silently resume a persisted manual pause."""
    session = get_session()
    try:
        recover_stale_claims(session, stale_seconds=1)
        rows = session.scalars(
            select(VectorizationRun).where(
                VectorizationRun.status.in_(("RUNNING", "PAUSE_REQUESTED"))
            )
        ).all()
        for run in rows:
            run.status = "PAUSED"
            run.pause_owner = run.pause_owner or "restart"
            run.paused_at = _now()
            run.current_asset_id = None
            run.updated_at = _now()
        session.commit()
    finally:
        session.close()


def maybe_auto_incremental_tick(*, batch_size: int | None = None) -> dict[str, Any]:
    """Enabled 时：补齐 gap 队列；无活动批次且队列有就绪素材则自动冻结一小批并唤醒执行器。

    横/竖屏分队列；默认优先处理待处理量更大（或与 DJI 横屏同步更相关）的一侧。
    一次只开 machine-wide 一个 run。
    """
    from engine.catalog.customer_scope import require_active_customer
    from engine.config.settings import load_settings

    settings = load_settings()
    if not settings.vectorization_enabled:
        return {"ok": True, "skipped": "disabled"}
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        cid = int(customer.id)
        enqueued = enqueue_customer_gaps(session, cid)
        active = session.scalar(
            select(VectorizationRun).where(VectorizationRun.status.in_(ACTIVE_STATES))
        )
        if active:
            executor.wake()
            return {
                "ok": True,
                "enqueued": enqueued,
                "active_run": str(active.run_id),
                "orientation": active.orientation,
            }
        batch_default = int(getattr(settings, "vector_batch_size", 0) or 50)
        batch = max(1, min(int(batch_size or batch_default or 50), 200))
        # Count pending per orientation (ready assets only)
        pending_by_orient: dict[str, int] = {}
        for orientation in ("landscape", "portrait"):
            pending_by_orient[orientation] = int(
                session.scalar(
                    select(func.count())
                    .select_from(VectorizationQueue)
                    .join(Asset, Asset.id == VectorizationQueue.asset_id)
                    .where(
                        VectorizationQueue.customer_id == cid,
                        VectorizationQueue.orientation == orientation,
                        VectorizationQueue.status.in_(("QUEUED", "FAILED")),
                        VectorizationQueue.run_id.is_(None),
                        Asset.status == "ready",
                    )
                )
                or 0
            )
        # Prefer orientation with more pending (tie-break landscape for newer camera packs)
        order = sorted(
            pending_by_orient.items(),
            key=lambda kv: (kv[1], 1 if kv[0] == "landscape" else 0),
            reverse=True,
        )
        for orientation, pending in order:
            if pending <= 0:
                continue
            run = create_run(
                session,
                customer_id=cid,
                mode="count",
                count=min(batch, pending),
                orientation=orientation,
            )
            executor.wake()
            return {
                "ok": True,
                "enqueued": enqueued,
                "started_run": str(run.run_id),
                "orientation": orientation,
                "frozen": int(run.frozen_assets or 0),
                "pending_before": pending,
                "pending_by_orientation": pending_by_orient,
            }
        executor.wake()
        return {
            "ok": True,
            "enqueued": enqueued,
            "idle": True,
            "pending_by_orientation": pending_by_orient,
        }
    except Exception as exc:
        log.exception("auto incremental tick failed")
        return {"ok": False, "error": str(exc)}
    finally:
        session.close()


def create_run(
    session: Any,
    *,
    customer_id: int,
    mode: str,
    count: int | None = None,
    orientation: str = "portrait",
) -> VectorizationRun:
    if mode not in ("count", "all"):
        raise ValueError("mode 须为 count 或 all")
    if mode == "count" and (count is None or count < 1):
        raise ValueError("count 模式须指定正整数")
    orientation = str(orientation or "portrait").lower()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("orientation 须为 portrait 或 landscape")
    existing = session.scalar(
        select(VectorizationRun).where(VectorizationRun.status.in_(ACTIVE_STATES))
    )
    if existing:
        raise RuntimeError(f"machine_slot_busy:{existing.run_id}")

    enqueue_customer_gaps(session, customer_id)
    cutoff = _now()
    query = (
        select(VectorizationQueue)
        .join(Asset, Asset.id == VectorizationQueue.asset_id)
        .where(
            VectorizationQueue.customer_id == customer_id,
            VectorizationQueue.orientation == orientation,
            VectorizationQueue.status.in_(("QUEUED", "FAILED")),
            VectorizationQueue.run_id.is_(None),
            Asset.status == "ready",
            Asset.created_at <= cutoff,
        )
        .order_by(VectorizationQueue.enqueued_at.asc(), VectorizationQueue.asset_id.asc())
    )
    if mode == "count":
        query = query.limit(max(1, int(count or 0)) * 3)  # overscan; L16 drops invalid
    candidates = list(session.scalars(query).all())
    from engine.ingest.orientation import asset_may_vectorize

    frozen = []
    for item in candidates:
        asset = session.get(Asset, int(item.asset_id))
        if asset is None or not asset_may_vectorize(asset):
            item.status = "FAILED"
            item.error = "orientation_hard_gate"
            item.run_id = None
            item.updated_at = cutoff
            continue
        frozen.append(item)
        if mode == "count" and len(frozen) >= int(count or 0):
            break
    run_id = str(uuid.uuid4())
    run = VectorizationRun(
        run_id=run_id,
        customer_id=customer_id,
        orientation=orientation,
        status="IDLE",
        mode=mode,
        requested_count=count if mode == "count" else None,
        cutoff_at=cutoff,
        frozen_assets=len(frozen),
        requested_at=cutoff,
        updated_at=cutoff,
    )
    session.add(run)
    for item in frozen:
        item.run_id = run_id
        item.status = "QUEUED"
        item.error = None
        item.updated_at = cutoff
    _audit_vector(
        session,
        customer_id=customer_id,
        event="vector_run_empty" if not frozen else "vector_run_created",
        message=(
            "当前没有需要处理的素材"
            if not frozen
            else f"已创建向量任务，共 {len(frozen)} 个素材"
        ),
        run_id=run_id,
        stage="created",
        details={
            "mode": mode,
            "orientation": orientation,
            "requested_count": count,
            "frozen_assets": len(frozen),
        },
    )
    session.commit()
    session.refresh(run)
    return run


def _claim_next(session: Any, run: VectorizationRun) -> VectorizationQueue | None:
    recover_stale_claims(session)
    token = uuid.uuid4().hex
    now = _now()
    result = session.execute(
        text(
            "UPDATE vectorization_queue SET status='CLAIMED', claim_token=:token, "
            "claimed_at=:now, attempts=attempts+1, last_attempt_id=:token, "
            "last_attempt_status='RUNNING', updated_at=:now "
            "WHERE id=(SELECT id FROM vectorization_queue "
            "WHERE run_id=:run_id AND customer_id=:customer_id AND status='QUEUED' "
            "ORDER BY enqueued_at ASC, asset_id ASC LIMIT 1) AND status='QUEUED'"
        ),
        {
            "token": token,
            "now": now,
            "run_id": run.run_id,
            "customer_id": run.customer_id,
        },
    )
    session.commit()
    if not result.rowcount:
        return None
    return session.scalar(
        select(VectorizationQueue).where(VectorizationQueue.claim_token == token)
    )


class VectorizationExecutor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._client_lock = threading.Lock()
        self._current_client: Any = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        recover_after_restart()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="vectorization-single-slot"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._cancel_request()
        if self._thread:
            self._thread.join(timeout=2)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_running(self) -> bool:
        session = get_session()
        try:
            return bool(
                session.scalar(
                    select(func.count())
                    .select_from(VectorizationRun)
                    .where(VectorizationRun.status.in_(("RUNNING", "PAUSE_REQUESTED")))
                )
            )
        finally:
            session.close()

    def has_active_run(self) -> bool:
        session = get_session()
        try:
            return bool(
                session.scalar(
                    select(func.count())
                    .select_from(VectorizationRun)
                    .where(VectorizationRun.status.in_(ACTIVE_STATES))
                )
            )
        finally:
            session.close()

    def wake(self) -> None:
        self._wake.set()

    def register_client(self, client: Any | None) -> None:
        with self._client_lock:
            self._current_client = client
            if client is not None and self._cancel.is_set():
                try:
                    client.close()
                except Exception:
                    pass

    def _cancel_request(self) -> None:
        self._cancel.set()
        with self._client_lock:
            client = self._current_client
        if client is not None:
            def _close() -> None:
                try:
                    client.close()
                except Exception:
                    pass

            threading.Thread(
                target=_close, daemon=True, name="vectorization-request-close"
            ).start()

    def pause(self, *, owner: str = "manual", timeout: float = 2.0) -> dict[str, Any]:
        session = get_session()
        try:
            run = session.scalar(
                select(VectorizationRun).where(VectorizationRun.status.in_(ACTIVE_STATES))
            )
            if not run:
                return status_snapshot(session)
            if run.status not in ("PAUSED", "COMPLETED", "FAILED"):
                run.status = "PAUSE_REQUESTED"
                run.pause_owner = owner
                run.updated_at = _now()
                _audit_vector(
                    session,
                    customer_id=run.customer_id,
                    event="vector_run_pause_requested",
                    message="向量任务正在安全暂停",
                    run_id=str(run.run_id),
                    stage="pause",
                    details={"owner": owner},
                )
                session.commit()
        finally:
            session.close()
        self._cancel_request()
        self._wake.set()
        deadline = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < deadline:
            session = get_session()
            try:
                snap = status_snapshot(session)
            finally:
                session.close()
            if snap["status"] == "PAUSED":
                return snap
            time.sleep(0.02)
        # The request has been closed already. Persist PAUSED even if a broken
        # fake ignores close; its result cannot commit because cancel is set.
        session = get_session()
        try:
            run = session.scalar(
                select(VectorizationRun).where(VectorizationRun.status == "PAUSE_REQUESTED")
            )
            if run:
                self._return_current_to_head(session, run)
                run.status = "PAUSED"
                run.paused_at = _now()
                run.current_asset_id = None
                run.updated_at = _now()
                session.commit()
            return status_snapshot(session)
        finally:
            session.close()

    def resume(self, *, owner: str = "manual") -> dict[str, Any]:
        session = get_session()
        try:
            run = session.scalar(
                select(VectorizationRun).where(
                    VectorizationRun.status.in_(("PAUSED", "BLOCKED"))
                )
            )
            if not run:
                return status_snapshot(session)
            # System wake may only release a system-owned hold.
            if owner == "system" and run.pause_owner != "system":
                return status_snapshot(session)
            run.status = "IDLE"
            run.pause_owner = None
            run.error = None
            run.updated_at = _now()
            _audit_vector(
                session,
                customer_id=run.customer_id,
                event="vector_run_resumed",
                message="向量任务已继续",
                run_id=str(run.run_id),
                stage="resume",
                details={"owner": owner},
            )
            session.commit()
        finally:
            session.close()
        self._cancel.clear()
        self.wake()
        session = get_session()
        try:
            return status_snapshot(session)
        finally:
            session.close()

    def disable(self) -> dict[str, Any]:
        return self.pause(owner="manual")

    def _return_current_to_head(self, session: Any, run: VectorizationRun) -> None:
        if run.current_asset_id is None:
            return
        row = session.scalar(
            select(VectorizationQueue).where(
                VectorizationQueue.run_id == run.run_id,
                VectorizationQueue.asset_id == run.current_asset_id,
            )
        )
        if row and row.status == "CLAIMED":
            row.status = "QUEUED"
            row.claim_token = None
            row.claimed_at = None
            row.error = "abandoned:pause"
            row.last_attempt_status = "ABANDONED"
            row.updated_at = _now()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self._tick()
            except Exception:
                log.exception("vectorization executor tick failed")

    def _tick(self) -> None:
        from engine.config.settings import load_settings
        from engine.runtime.pause_coordinator import coordinator

        if not load_settings().vectorization_enabled or not coordinator.should_claim_jobs():
            return
        while not self._stop.is_set():
            session = get_session()
            try:
                run = session.scalar(
                    select(VectorizationRun)
                    .where(VectorizationRun.status.in_(("IDLE", "RUNNING", "PAUSE_REQUESTED")))
                    .order_by(VectorizationRun.requested_at.asc())
                )
                if not run:
                    return
                if run.status == "PAUSE_REQUESTED" or self._cancel.is_set():
                    self._return_current_to_head(session, run)
                    run.status = "PAUSED"
                    run.paused_at = _now()
                    run.current_asset_id = None
                    run.updated_at = _now()
                    _audit_vector(
                        session,
                        customer_id=run.customer_id,
                        event="vector_run_paused",
                        message="向量任务已暂停，未完成素材已退回队列",
                        run_id=str(run.run_id),
                        stage="paused",
                    )
                    session.commit()
                    return
                item = _claim_next(session, run)
                if item is None:
                    failed = int(
                        session.scalar(
                            select(func.count())
                            .select_from(VectorizationQueue)
                            .where(
                                VectorizationQueue.run_id == run.run_id,
                                VectorizationQueue.status == "FAILED",
                            )
                        )
                        or 0
                    )
                    run.status = "FAILED" if failed else "COMPLETED"
                    run.finished_at = _now()
                    run.current_asset_id = None
                    run.updated_at = _now()
                    _audit_vector(
                        session,
                        customer_id=run.customer_id,
                        event="vector_run_failed" if failed else "vector_run_completed",
                        message=(
                            f"向量任务结束，失败 {failed} 个素材"
                            if failed
                            else f"向量任务完成，共处理 {run.completed_assets} 个素材"
                        ),
                        run_id=str(run.run_id),
                        stage="completed",
                        level="error" if failed else "info",
                        details={
                            "failed_assets": failed,
                            "completed_assets": run.completed_assets,
                            "completed_cliplets": run.completed_cliplets,
                        },
                    )
                    session.commit()
                    return
                starting = run.status == "IDLE"
                run.status = "RUNNING"
                run.started_at = run.started_at or _now()
                run.current_asset_id = item.asset_id
                run.updated_at = _now()
                run_id = str(run.run_id)
                if starting:
                    _audit_vector(
                        session,
                        customer_id=run.customer_id,
                        event="vector_run_started",
                        message="向量任务已开始",
                        run_id=run_id,
                        stage="running",
                        details={"frozen_assets": run.frozen_assets},
                    )
                session.commit()
                asset_id = int(item.asset_id)
                claim_token = str(item.claim_token)
            finally:
                session.close()
            self._process_asset(run_id, asset_id, claim_token)

    def _process_asset(self, run_id: str, asset_id: int, claim_token: str) -> None:
        from engine.catalog.vector_index import EmbeddingCancelled, index_asset_cliplets

        session = get_session()
        try:
            run = session.scalar(
                select(VectorizationRun).where(VectorizationRun.run_id == run_id)
            )
            item = session.scalar(
                select(VectorizationQueue).where(
                    VectorizationQueue.claim_token == claim_token,
                    VectorizationQueue.asset_id == asset_id,
                )
            )
            asset = session.get(Asset, asset_id)
            if not run or not item or not asset or asset.customer_id != run.customer_id:
                return
            try:
                result = index_asset_cliplets(
                    session,
                    asset,
                    cancel_event=self._cancel,
                    client_callback=self.register_client,
                )
                if self._cancel.is_set() or run.status == "PAUSE_REQUESTED":
                    raise EmbeddingCancelled("pause_requested")
                item.status = "COMPLETED"
                item.completed_cliplets = int(result["completed_cliplets"])
                item.completed_at = _now()
                item.claim_token = None
                item.claimed_at = None
                item.error = None
                item.last_attempt_status = "COMPLETED"
                run.completed_assets += 1
                run.completed_cliplets += int(result["completed_cliplets"])
                run.current_asset_id = None
                run.updated_at = _now()
                _audit_vector(
                    session,
                    customer_id=run.customer_id,
                    event="vector_asset_completed",
                    message=f"素材向量处理完成，共 {int(result['completed_cliplets'])} 个片段",
                    run_id=run_id,
                    asset_id=asset_id,
                    stage="asset_completed",
                    details={"completed_cliplets": int(result["completed_cliplets"])},
                )
                session.commit()
            except EmbeddingCancelled:
                session.rollback()
                run = session.scalar(
                    select(VectorizationRun).where(VectorizationRun.run_id == run_id)
                )
                item = session.scalar(
                    select(VectorizationQueue).where(
                        VectorizationQueue.run_id == run_id,
                        VectorizationQueue.asset_id == asset_id,
                    )
                )
                if item:
                    item.status = "QUEUED"
                    item.claim_token = None
                    item.claimed_at = None
                    item.error = "abandoned:pause"
                    item.last_attempt_status = "ABANDONED"
                if run:
                    run.status = "PAUSED"
                    run.paused_at = _now()
                    run.current_asset_id = None
                    run.updated_at = _now()
                    _audit_vector(
                        session,
                        customer_id=run.customer_id,
                        event="vector_attempt_abandoned",
                        message="当前素材已停止，已完成向量保留",
                        run_id=run_id,
                        asset_id=asset_id,
                        stage="abandoned",
                        level="warning",
                    )
                session.commit()
            except Exception as exc:
                session.rollback()
                run = session.scalar(
                    select(VectorizationRun).where(VectorizationRun.run_id == run_id)
                )
                item = session.scalar(
                    select(VectorizationQueue).where(
                        VectorizationQueue.run_id == run_id,
                        VectorizationQueue.asset_id == asset_id,
                    )
                )
                error = f"{type(exc).__name__}: {exc}"[:1000]
                if item:
                    item.status = "FAILED"
                    item.claim_token = None
                    item.claimed_at = None
                    item.error = error
                    item.last_attempt_status = "FAILED"
                if run:
                    run.status = "FAILED"
                    run.error = error
                    run.current_asset_id = None
                    run.finished_at = _now()
                    run.updated_at = _now()
                    _audit_vector(
                        session,
                        customer_id=run.customer_id,
                        event="vector_asset_failed",
                        message="素材向量处理失败",
                        run_id=run_id,
                        asset_id=asset_id,
                        stage="failed",
                        level="error",
                        details={"error": error},
                    )
                session.commit()
        finally:
            self.register_client(None)
            session.close()


def _asset_orientation_clause(orientation: str):
    """Match stored orientation, or derive from width/height when unset."""
    from sqlalchemy import and_, or_

    orient = str(orientation or "portrait").lower()
    explicit = Asset.orientation == orient
    blank = or_(
        Asset.orientation.is_(None),
        Asset.orientation == "",
        Asset.orientation == "unknown",
    )
    if orient == "landscape":
        inferred = and_(blank, Asset.width > Asset.height)
    else:
        # portrait default for square / missing dimensions
        inferred = and_(
            blank,
            or_(
                Asset.height >= Asset.width,
                and_(Asset.width.is_(None), Asset.height.is_(None)),
                and_(Asset.width == 0, Asset.height == 0),
            ),
        )
    return or_(explicit, inferred)


def status_snapshot(
    session: Any,
    *,
    customer_id: int | None = None,
    orientation: str | None = None,
) -> dict[str, Any]:
    stmt = select(VectorizationRun)
    if customer_id is not None:
        stmt = stmt.where(VectorizationRun.customer_id == customer_id)
    if orientation in {"portrait", "landscape"}:
        stmt = stmt.where(VectorizationRun.orientation == orientation)
    run = session.scalar(stmt.order_by(VectorizationRun.requested_at.desc(), VectorizationRun.id.desc()))
    true_pending = (
        select(func.count())
        .select_from(Cliplet)
        .join(Asset, Asset.id == Cliplet.asset_id)
        .where(
            Asset.status == "ready",
            Cliplet.status == "usable",
            Cliplet.embedding_json.is_(None),
        )
    )
    true_done_clips = (
        select(func.count())
        .select_from(Cliplet)
        .join(Asset, Asset.id == Cliplet.asset_id)
        .where(
            Asset.status == "ready",
            Cliplet.status == "usable",
            Cliplet.embedding_json.is_not(None),
        )
    )
    clip_count_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(Cliplet.asset_id == Asset.id)
        .correlate(Asset)
        .scalar_subquery()
    )
    pending_clip_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(
            Cliplet.asset_id == Asset.id,
            Cliplet.status == "usable",
            Cliplet.embedding_json.is_(None),
        )
        .correlate(Asset)
        .scalar_subquery()
    )
    gap_assets = (
        select(func.count())
        .select_from(Asset)
        .where(Asset.status == "ready")
        .where((clip_count_sq == 0) | (pending_clip_sq > 0))
    )
    complete_assets = (
        select(func.count())
        .select_from(Asset)
        .where(Asset.status == "ready")
        .where(clip_count_sq > 0)
        .where(pending_clip_sq == 0)
    )
    if customer_id is not None:
        true_pending = true_pending.where(Asset.customer_id == customer_id)
        true_done_clips = true_done_clips.where(Asset.customer_id == customer_id)
        gap_assets = gap_assets.where(Asset.customer_id == customer_id)
        complete_assets = complete_assets.where(Asset.customer_id == customer_id)
    if orientation in {"portrait", "landscape"}:
        orient_clause = _asset_orientation_clause(orientation)
        true_pending = true_pending.where(orient_clause)
        true_done_clips = true_done_clips.where(orient_clause)
        gap_assets = gap_assets.where(orient_clause)
        complete_assets = complete_assets.where(orient_clause)
    pending_cliplets = int(session.scalar(true_pending) or 0)
    pending_assets = int(session.scalar(gap_assets) or 0)
    completed_assets = int(session.scalar(complete_assets) or 0)
    completed_cliplets = int(session.scalar(true_done_clips) or 0)
    total_assets = completed_assets + pending_assets
    if not run:
        return {
            "status": "IDLE",
            "run_id": None,
            "customer_id": customer_id,
            "orientation": orientation or "portrait",
            "enabled": False,
            "current_asset_id": None,
            "frozen_assets": 0,
            "run_completed_assets": 0,
            "completed_assets": completed_assets,
            "completed_cliplets": completed_cliplets,
            "pending_assets": pending_assets,
            "pending_cliplets": pending_cliplets,
            "total_assets": total_assets,
            "error": None,
        }
    persisted_cliplets = int(
        session.scalar(
            select(func.count())
            .select_from(Cliplet)
            .join(VectorizationQueue, VectorizationQueue.asset_id == Cliplet.asset_id)
            .where(
                VectorizationQueue.run_id == run.run_id,
                Cliplet.status == "usable",
                Cliplet.embedding_json.is_not(None),
            )
        )
        or 0
    )
    frozen_cliplets = int(
        session.scalar(
            select(func.count())
            .select_from(Cliplet)
            .join(VectorizationQueue, VectorizationQueue.asset_id == Cliplet.asset_id)
            .where(
                VectorizationQueue.run_id == run.run_id,
                Cliplet.status == "usable",
            )
        )
        or 0
    )
    return {
        "status": run.status,
        "run_id": run.run_id,
        "customer_id": run.customer_id,
        "orientation": run.orientation,
        "mode": run.mode,
        "requested_count": run.requested_count,
        "cutoff_at": run.cutoff_at.isoformat() if run.cutoff_at else None,
        "enabled": True,
        "current_asset_id": run.current_asset_id,
        "frozen_assets": run.frozen_assets,
        "run_completed_assets": int(run.completed_assets or 0),
        # Library-scoped totals (UI uses these for 已完成 / 待处理)
        "completed_assets": completed_assets,
        "completed_cliplets": completed_cliplets,
        "run_completed_cliplets": persisted_cliplets,
        "frozen_cliplets": frozen_cliplets,
        "pending_assets": pending_assets,
        "pending_cliplets": pending_cliplets,
        "total_assets": total_assets,
        "requested_at": run.requested_at.isoformat() if run.requested_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "paused_at": run.paused_at.isoformat() if run.paused_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "pause_owner": run.pause_owner,
        "error": run.error,
    }


executor = VectorizationExecutor()
