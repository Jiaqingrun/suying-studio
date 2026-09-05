"""G5.BATCH durable serial publish run state machine."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Job,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachQueueItem,
    RenderOutput,
    get_session,
)
from engine.ops.audit_log import write_log
from engine.reach.browser import entry_for_platform, resolve_profile_platform
from engine.reach.business_scope import SCOPE_VIDEO
from engine.reach.cdp_publish import UPLOAD_URLS
from engine.reach.chrome_publish import UPLOAD_URL
from engine.reach.publish_verify import is_verified_success, recheck_or_pause
from engine.reach.queue import get_item, set_status

log = logging.getLogger("montage.publish_runner")

_lock = threading.Lock()
_run_create_lock = threading.Lock()
_worker: threading.Thread | None = None
_worker_run_id: str | None = None
_cancel_run_id: str | None = None
_active_chrome: Any | None = None
_active_profile: str | None = None

RUN_TERMINAL = frozenset(
    {"completed", "failed", "cancelled", "interrupted_system"}
)
ITEM_TERMINAL = frozenset(
    {"published", "failed", "skipped", "deferred", "awaiting_confirmation", "cancelled"}
)
HUMAN_PHASES = frozenset({"paused_human", "outcome_unknown", "waiting_login"})
ACTIVE_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "paused_human",
        "outcome_unknown",
        "waiting_login",
        "stopping",
    }
)
SAFE_SKIP_PHASES = frozenset(
    {
        "queued",
        "switching_profile",
        "waiting_login",
        "uploading",
        "filling_copy",
        "setting_cover",
        "paused_human",
    }
)
MAX_CONSECUTIVE_FAILURES = 3
# GCustomerUX 1B: login/verify/cover stuck → soft-skip after budget, round-retry ≤3, then human alert.
SOFT_SKIP_BUDGET_SEC = 15.0
# Channels: Cookie/shell may be OK while wujie form mounts slowly; use full budget.
CHANNELS_LOGIN_RESTORE_BUDGET_SEC = 40.0
MAX_ROUND_ATTEMPTS = 3
SOFT_SKIP_PHASE = "soft_skipped"
# Kinds that must not burn multi-round empty retries.
LOGIN_WALL_KINDS = frozenset({"login_required", "true_login_wall", "post_probe_false_pass"})
# form_not_ready may requeue once; true wall never.
REQUEUEABLE_SOFT_KINDS = frozenset({"form_not_ready", "feature_blocked", "verification_required", "sqlite_locked"})
# True login wall: bounded human scan window, then fail-forward (defer + release slot).
LOGIN_WALL_GRACE_SEC = 90.0
LOGIN_WALL_GRACE_CAP_SEC = 120.0
LOGIN_WALL_DEFER_RETRY_AFTER_SEC = 30.0
# Same-profile auto-defer cooldown after login-wall fail-forward (anti thrash).
LOGIN_WALL_PROFILE_COOLDOWN_SEC = 1800.0
# Batch end / profile switch: hard wall for Chrome stop before SIGKILL profile lock.
CHROME_STOP_TIMEOUT_SEC = 11.0
CHROME_FORCE_KILL_TIMEOUT_SEC = 4.0
# Channels needs longer cookie flush between account switches.
CHANNELS_PROFILE_FLUSH_SEC = 8.0
# Publish waits for a short message scan to finish instead of cancelling the batch.
PUBLISH_LEASE_WAIT_SEC = 90.0
# Stale human-held ACTIVE runs without a live worker (deploy / crash).
STALE_HUMAN_HOLD_STATUSES = frozenset(
    {"waiting_login", "paused_human", "outcome_unknown"}
)
RECONCILE_ACTIVE_STATUSES = frozenset(
    {"queued", "running", "stopping"}
) | STALE_HUMAN_HOLD_STATUSES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_round_attempt(run: ReachPublishRun) -> int:
    cfg = dict(run.config_json or {})
    try:
        return max(1, int(cfg.get("round_attempt") or 1))
    except (TypeError, ValueError):
        return 1


def _set_run_round_attempt(run: ReachPublishRun, value: int) -> None:
    cfg = dict(run.config_json or {})
    cfg["round_attempt"] = int(value)
    run.config_json = cfg


def _soft_skip_item(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    reason: str,
    stage: str,
    kind: str,
) -> None:
    """Mark item soft-skipped and continue the round (no whole-batch pause)."""
    item.phase = SOFT_SKIP_PHASE
    item.outcome = SOFT_SKIP_PHASE
    item.error = reason
    item.finished_at = _now()
    item.updated_at = _now()
    evidence = dict(item.evidence_json or {})
    evidence["soft_skip"] = {
        "kind": kind,
        "stage": stage,
        "reason": reason,
        "round_attempt": _run_round_attempt(run),
        "at": _now().isoformat(),
        "requeueable": kind in REQUEUEABLE_SOFT_KINDS,
    }
    item.evidence_json = evidence
    run.status = "running"
    run.error = ""
    run.updated_at = _now()
    _append_run_log(
        run,
        "soft_skip",
        item_id=item.id,
        kind=kind,
        stage=stage,
        reason=reason,
        round_attempt=_run_round_attempt(run),
    )
    _audit_publish(
        session,
        run,
        "publish_item_soft_skipped",
        reason,
        item=item,
        stage=stage,
        level="warning",
        details={"soft_skip_kind": kind, "round_attempt": _run_round_attempt(run)},
    )


def _soft_skip_kind(item: ReachPublishRunItem) -> str:
    ev = item.evidence_json or {}
    soft = ev.get("soft_skip") if isinstance(ev, dict) else None
    if isinstance(soft, dict):
        return str(soft.get("kind") or "")
    return ""


def _requeue_soft_skipped_for_next_round(session: Session, run: ReachPublishRun) -> int:
    """Reset soft_skipped items to queued for another round. Returns count requeued.

    True login walls are never requeued (operator must finish scan/login first).
    """
    count = 0
    for item in list_run_items(session, run):
        if item.phase != SOFT_SKIP_PHASE:
            continue
        kind = _soft_skip_kind(item)
        if kind in LOGIN_WALL_KINDS:
            continue
        if kind and kind not in REQUEUEABLE_SOFT_KINDS:
            # Unknown kinds: only requeue once if not already retried as wall.
            if kind == "login_required":
                continue
        item.phase = "queued"
        item.outcome = None
        item.error = ""
        item.finished_at = None
        item.started_at = None
        item.retry_count = 0
        item.updated_at = _now()
        count += 1
    return count


def login_wall_grace_sec() -> float:
    """Bounded human-scan window for true login walls (config-safe clamp)."""
    try:
        val = float(LOGIN_WALL_GRACE_SEC)
    except (TypeError, ValueError):
        val = 90.0
    return max(5.0, min(float(LOGIN_WALL_GRACE_CAP_SEC), val))


def _block_sibling_items_for_login_profile(
    session: Session,
    run: ReachPublishRun,
    *,
    profile: str,
    reason: str,
) -> int:
    """Soft-skip remaining items for the same profile after a true login wall."""
    count = 0
    for item in list_run_items(session, run):
        if item.phase not in ("queued",):
            continue
        if str(item.chrome_profile or "") != str(profile):
            continue
        _soft_skip_item(
            session,
            run,
            item,
            reason=f"blocked_by_login:{profile} · {reason}",
            stage="login",
            kind="true_login_wall",
        )
        count += 1
    return count


def _defer_item_pre_submit(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    reason: str,
    retry_mode: str = "auto",
    stage: str = "login",
) -> None:
    """Mark unsubmitted item deferred and release publication hold."""
    from engine.reach.publication_lifecycle import release_unsubmitted_target

    pub = (item.evidence_json or {}).get("publish_result") or {}
    if pub.get("pub_clicked") or item.phase in (
        "submitting",
        "verifying",
        "outcome_unknown",
    ):
        raise ValueError("post-click items cannot auto-defer for fail-forward")
    release_unsubmitted_target(
        session,
        group_id=item.publication_group_id,
        target_id=item.publication_target_id,
    )
    item.phase = "deferred"
    item.outcome = "deferred"
    item.retry_mode = retry_mode if retry_mode in ("manual", "auto", "none") else "auto"
    item.retry_after = (
        _now() + timedelta(seconds=LOGIN_WALL_DEFER_RETRY_AFTER_SEC)
        if item.retry_mode == "auto"
        else None
    )
    item.requested_action = ""
    item.error = reason
    item.finished_at = _now()
    item.updated_at = _now()
    evidence = dict(item.evidence_json or {})
    evidence["fail_forward"] = {
        "kind": "true_login_wall",
        "stage": stage,
        "at": _now().isoformat(),
        "retry_mode": item.retry_mode,
    }
    item.evidence_json = evidence
    _append_run_log(
        run,
        "item_deferred_login_wall",
        item_id=item.id,
        chrome_profile=item.chrome_profile,
        reason=reason,
        retry_mode=item.retry_mode,
    )


def _defer_true_login_wall_fail_forward(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    reason: str,
) -> int:
    """Timeout path: defer wall item + same-profile blocked siblings; release ACTIVE hold."""
    profile = str(item.chrome_profile or "")
    summary = (
        f"登录宽限已过：{profile or '账号'} 已跳过并排队补发，其它账号继续 · "
        f"{(reason or '')[:120]}"
    )
    deferred = 0
    if item.phase not in ITEM_TERMINAL:
        _defer_item_pre_submit(
            session,
            run,
            item,
            reason=summary,
            retry_mode="auto",
            stage="login_wall_timeout",
        )
        deferred += 1
    for sibling in list_run_items(session, run):
        if sibling.id == item.id:
            continue
        if str(sibling.chrome_profile or "") != profile:
            continue
        if sibling.phase in ITEM_TERMINAL:
            continue
        if sibling.phase not in (
            SOFT_SKIP_PHASE,
            "queued",
            "waiting_login",
            "switching_profile",
        ):
            continue
        if (
            sibling.phase == SOFT_SKIP_PHASE
            and _soft_skip_kind(sibling)
            and _soft_skip_kind(sibling) not in LOGIN_WALL_KINDS
        ):
            continue
        try:
            _defer_item_pre_submit(
                session,
                run,
                sibling,
                reason=f"blocked_by_login:{profile} · 登录宽限超时已随账号跳过",
                retry_mode="auto",
                stage="login_wall_timeout",
            )
            deferred += 1
        except ValueError:
            continue
    # Profile cooldown so auto-retry does not thrash the wall for half an hour.
    cfg = dict(run.config_json or {})
    cool = dict(cfg.get("login_wall_profile_cooldown") or {})
    if profile:
        cool[profile] = (
            _now() + timedelta(seconds=LOGIN_WALL_PROFILE_COOLDOWN_SEC)
        ).isoformat()
    cfg["login_wall_profile_cooldown"] = cool
    cfg["last_login_wall_fail_forward"] = {
        "at": _now().isoformat(),
        "profile": profile,
        "deferred": deferred,
    }
    run.config_json = cfg
    run.status = "running"
    run.error = summary
    run.updated_at = _now()
    run.heartbeat_at = _now()
    _append_run_log(
        run,
        "login_wall_fail_forward",
        chrome_profile=profile,
        deferred=deferred,
        grace_sec=login_wall_grace_sec(),
    )
    _audit_publish(
        session,
        run,
        "publish_login_wall_deferred",
        summary,
        item=item,
        stage="login",
        level="warning",
        details={"deferred": deferred, "chrome_profile": profile},
    )
    try:
        from engine.ops.human_alerts import create_alert, dispatch_due_alerts

        create_alert(
            session,
            alert_key=f"publish_login_deferred:{run.run_id}:{profile}",
            customer_id=run.customer_id,
            kind="login_required",
            source_type="publish_run",
            source_id=str(run.run_id),
            summary=summary[:220],
            deep_link="suying://publish",
        )
        dispatch_due_alerts(session, customer_id=run.customer_id)
    except Exception:
        log.exception("login wall fail-forward alert failed")
    return deferred


def _await_login_wall_resolution(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    grace_sec: float | None = None,
) -> str:
    """Block until resume, cancel, or grace timeout. Returns resumed|deferred|cancelled."""
    grace = float(grace_sec if grace_sec is not None else login_wall_grace_sec())
    deadline = time.monotonic() + max(1.0, grace)
    run_id = str(run.run_id)
    while time.monotonic() < deadline:
        if _cancelled(run_id):
            return "cancelled"
        try:
            session.refresh(run)
            session.refresh(item)
        except Exception:
            pass
        if str(run.status or "") == "stopping":
            return "cancelled"
        phase = str(item.phase or "")
        status = str(run.status or "")
        if phase == "queued" and status in ("running", "queued"):
            return "resumed"
        if phase in ITEM_TERMINAL:
            return "deferred"
        if status not in ("waiting_login", "running", "queued", "paused_human"):
            if status in RUN_TERMINAL:
                return "cancelled"
        run.heartbeat_at = _now()
        run.updated_at = _now()
        try:
            session.commit()
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
        time.sleep(0.5)
    # Timed out while still waiting.
    if _cancelled(run_id):
        return "cancelled"
    try:
        session.refresh(run)
        session.refresh(item)
    except Exception:
        pass
    if str(item.phase or "") == "queued" and str(run.status or "") in ("running", "queued"):
        return "resumed"
    _defer_true_login_wall_fail_forward(
        session,
        run,
        item,
        reason=item.error or "true_login_wall",
    )
    try:
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
    return "deferred"


def _halt_for_true_login_wall(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    reason: str,
    login: dict[str, Any],
) -> None:
    """Enter bounded waiting_login: Chrome stays open for grace; then fail-forward.

    Does not permanently pin the machine slot — worker awaits resume or grace timeout.
    """
    grace = login_wall_grace_sec()
    deadline_at = _now() + timedelta(seconds=grace)
    item.phase = "waiting_login"
    item.outcome = None
    item.error = reason
    item.finished_at = None
    item.updated_at = _now()
    evidence = dict(item.evidence_json or {})
    evidence["login_kind"] = "true_login_wall"
    evidence["login_wall_deadline"] = deadline_at.isoformat()
    evidence["login_wall_grace_sec"] = grace
    evidence["soft_skip"] = {
        "kind": "true_login_wall",
        "stage": "login",
        "reason": reason,
        "round_attempt": _run_round_attempt(run),
        "at": _now().isoformat(),
        "requeueable": False,
        "fail_forward_after": deadline_at.isoformat(),
    }
    probe = login.get("probe") if isinstance(login.get("probe"), dict) else {}
    evidence["timings"] = {
        **(evidence.get("timings") or {}),
        "login_kind": "true_login_wall",
        "probe_form": bool(probe.get("form")),
        "probe_shell": bool(probe.get("shellLoggedIn")),
        "cookie_state": login.get("cookie_state"),
        "navigated_force": login.get("navigated_force"),
        "login_tabs_closed": login.get("login_tabs_closed"),
    }
    item.evidence_json = evidence
    run.status = "waiting_login"
    run.error = reason
    run.updated_at = _now()
    run.heartbeat_at = _now()
    cfg = dict(run.config_json or {})
    cfg["login_wall_deadline"] = deadline_at.isoformat()
    cfg["login_wall_grace_sec"] = grace
    run.config_json = cfg
    _block_sibling_items_for_login_profile(
        session,
        run,
        profile=str(item.chrome_profile or ""),
        reason=reason,
    )
    _audit_publish(
        session,
        run,
        "publish_waiting_login",
        reason,
        item=item,
        stage="login",
        level="warning",
        details={
            "login_kind": "true_login_wall",
            "url": login.get("url"),
            "chrome_profile": item.chrome_profile,
            "grace_sec": grace,
            "deadline": deadline_at.isoformat(),
        },
    )
    try:
        from engine.ops.human_alerts import create_alert, dispatch_due_alerts

        create_alert(
            session,
            alert_key=f"publish_login:{run.run_id}:{item.chrome_profile}",
            customer_id=run.customer_id,
            kind="login_required",
            source_type="publish_run",
            source_id=str(run.run_id),
            summary=(
                f"需要重新扫码登录 · {item.chrome_profile or '账号'} · "
                f"约 {int(grace)} 秒内处理，超时将跳过该号并继续其它账号 · "
                f"{(login.get('url') or '')[:60]}"
            ),
            deep_link="suying://publish",
        )
        dispatch_due_alerts(session, customer_id=run.customer_id)
    except Exception:
        log.exception("create login alert failed")
    try:
        _alert_human(
            session,
            run,
            item,
            kind="login_required",
            summary=(
                f"需要重新扫码登录：{item.chrome_profile or '账号'}"
                f"（{int(grace)}s 宽限，超时自动跳过补发）"
            ),
        )
    except Exception:
        log.exception("alert_human login wall failed")


def _record_run_finish_summary(session: Session, run: ReachPublishRun) -> None:
    """Write one-shot run summary (no control-plane poll flood)."""
    items = list_run_items(session, run)
    soft_by_kind: dict[str, int] = {}
    for item in items:
        kind = _soft_skip_kind(item)
        if kind:
            soft_by_kind[kind] = soft_by_kind.get(kind, 0) + 1
        ev = item.evidence_json or {}
        ff = ev.get("fail_forward") if isinstance(ev, dict) else None
        if isinstance(ff, dict) and ff.get("kind"):
            k = f"fail_forward:{ff.get('kind')}"
            soft_by_kind[k] = soft_by_kind.get(k, 0) + 1
    summary = {
        "at": _now().isoformat(),
        "status": run.status,
        "round_attempt": _run_round_attempt(run),
        "published": sum(i.phase == "published" for i in items),
        "failed": sum(i.phase == "failed" for i in items),
        "deferred": sum(i.phase == "deferred" for i in items),
        "skipped": sum(i.phase == "skipped" for i in items),
        "awaiting_confirmation": sum(
            i.phase == "awaiting_confirmation" for i in items
        ),
        "soft_skip_by_kind": soft_by_kind,
        "login_wall_grace_used": bool(
            (run.config_json or {}).get("last_login_wall_fail_forward")
            or (run.config_json or {}).get("login_wall_deadline")
        ),
        "released_slot_reason": (run.config_json or {}).get(
            "last_login_wall_fail_forward"
        ),
    }
    cfg = dict(run.config_json or {})
    cfg["finish_summary"] = summary
    run.config_json = cfg
    _append_run_log(run, "run_finish_summary", **summary)


def _escalate_soft_skip_exhausted(session: Session, run: ReachPublishRun) -> None:
    stuck = [i for i in list_run_items(session, run) if i.phase == SOFT_SKIP_PHASE]
    if not stuck:
        return
    summary = (
        f"发布批次已自动跳过并重试 {_run_round_attempt(run)} 轮仍有 {len(stuck)} 条未完成"
        "（登录/验证/封面卡住），请人工介入"
    )
    run.error = summary
    _audit_publish(
        session,
        run,
        "publish_soft_skip_exhausted",
        summary,
        stage="completion",
        level="error",
        details={
            "stuck_count": len(stuck),
            "round_attempt": _run_round_attempt(run),
            "item_ids": [i.id for i in stuck[:40]],
        },
    )
    try:
        from engine.ops.human_alerts import create_alert

        create_alert(
            session,
            alert_key=f"publish_soft_skip:{run.run_id}:{_run_round_attempt(run)}",
            customer_id=run.customer_id,
            kind="publish_soft_skip_exhausted",
            source_type="publish_run",
            source_id=run.run_id,
            summary=summary,
            deep_link="suying://publish",
        )
    except Exception:
        log.exception("soft-skip exhausted alert failed run_id=%s", run.run_id)


def _append_run_log(run: ReachPublishRun, event: str, **payload: Any) -> None:
    logs = list(run.log_json or [])
    logs.append({"at": _now().isoformat(), "event": event, **payload})
    run.log_json = logs


def _audit_publish(
    session: Session,
    run: ReachPublishRun,
    event: str,
    message: str,
    *,
    item: ReachPublishRunItem | None = None,
    stage: str = "",
    level: str = "info",
    details: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit. Never blocks the publish Chrome path on SQLite locks."""
    del session  # intentionally unused — side session only
    run_id = str(getattr(run, "run_id", "") or "")
    customer_id = int(getattr(run, "customer_id", 0) or 0)
    item_id = int(item.id) if item is not None else None
    account = str(item.chrome_profile if item else "")
    platform = str(item.platform if item else "")
    retry_no = int(item.retry_count if item else 0)
    payload = {
        "run_id": run_id,
        "launch_mode": str(getattr(run, "launch_mode", "") or ""),
        "ordinal": item.ordinal if item else None,
        "output_id": item.output_id if item else None,
        "queue_id": item.queue_id if item else None,
        **(details or {}),
    }
    evidence_payload = evidence

    def _write() -> None:
        side: Session | None = None
        try:
            side = get_session()
            write_log(
                side,
                customer_id=customer_id,
                category="publish",
                event=event,
                stage=stage,
                message=message,
                level=level,
                source_type="publish_run_item" if item_id is not None else "publish_run",
                source_id=item_id if item_id is not None else run_id,
                correlation_id=f"publish:{run_id}",
                account=account,
                platform=platform,
                retry_no=retry_no,
                details=payload,
                evidence=evidence_payload,
                commit=True,
            )
        except Exception:
            log.warning(
                "publish audit skipped event=%s run_id=%s",
                event,
                run_id,
                exc_info=True,
            )
            if side is not None:
                try:
                    side.rollback()
                except Exception:
                    pass
        finally:
            if side is not None:
                try:
                    side.close()
                except Exception:
                    pass

    threading.Thread(
        target=_write, daemon=True, name=f"publish-audit-{event[:24]}"
    ).start()


def _commit_with_retry(
    session: Session,
    *,
    attempts: int = 20,
    delay_sec: float = 0.5,
    reapply: Callable[[], None] | None = None,
) -> None:
    """Commit through SQLite lock storms without killing the publish worker.

    After a locked failure we rollback (SQLAlchemy session is invalid). Callers
    that mutate ORM objects must pass ``reapply`` so those writes are restored
    before the next commit attempt — otherwise retries commit an empty txn.
    """
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            if attempt > 0 and reapply is not None:
                reapply()
            session.commit()
            return
        except Exception as exc:
            last = exc
            try:
                session.rollback()
            except Exception:
                pass
            message = str(exc).lower()
            if "database is locked" not in message and "locked" not in message:
                raise
            time.sleep(delay_sec * (attempt + 1))
    if last is not None:
        raise last
    raise RuntimeError("commit failed")


def _mark_queue_published(
    session: Session, queue_item: ReachQueueItem, *, note: str
) -> None:
    if queue_item.status == "queued":
        set_status(session, queue_item, "awaiting_human", note="batch_prefilled")
    if queue_item.status != "published":
        set_status(session, queue_item, "published", note=note)


def _alert_human(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    kind: str,
    summary: str,
) -> None:
    from engine.ops.human_alerts import create_alert, dispatch_due_alerts

    create_alert(
        session,
        alert_key=f"publish:{run.run_id}:{item.id}:{kind}",
        customer_id=run.customer_id,
        kind=kind,
        source_type="publish_run",
        source_id=run.run_id,
        summary=f"{item.platform} · {item.chrome_profile} · {summary}",
        deep_link=f"suying://publish?run={run.run_id}&item={item.id}",
    )
    dispatch_due_alerts(session, customer_id=run.customer_id)


def _enqueue_due_message_scans(run_id: str) -> None:
    from datetime import timedelta

    from engine.catalog.db import ReachMessageAccount
    from engine.reach.message_sync import MESSAGE_SCAN_INTERVAL_SEC, message_sync

    session = get_session()
    try:
        run = get_run(session, run_id)
        if not run or run.status != "completed":
            return
        ids: list[int] = []
        for item in list_run_items(session, run):
            if item.phase != "published":
                continue
            account = session.scalar(
                select(ReachMessageAccount).where(
                    ReachMessageAccount.customer_id == run.customer_id,
                    ReachMessageAccount.business_scope == SCOPE_VIDEO,
                    ReachMessageAccount.profile_name == item.chrome_profile,
                )
            )
            if account and (
                account.last_scanned_at is None
                or _now()
                - (
                    account.last_scanned_at.replace(tzinfo=timezone.utc)
                    if account.last_scanned_at.tzinfo is None
                    else account.last_scanned_at.astimezone(timezone.utc)
                )
                >= timedelta(seconds=MESSAGE_SCAN_INTERVAL_SEC)
            ):
                ids.append(account.id)
        if ids:
            message_sync.enqueue(ids, reason="publish_completed")
    finally:
        session.close()


def run_item_to_dict(
    item: ReachPublishRunItem, *, include_evidence: bool = True
) -> dict[str, Any]:
    publish_result = (item.evidence_json or {}).get("publish_result") or {}
    was_clicked = bool(publish_result.get("pub_clicked"))
    requires_confirmation = item.phase in ("outcome_unknown", "awaiting_confirmation") or (
        was_clicked and item.phase not in ("published",)
    )
    return {
        "id": item.id,
        "ordinal": item.ordinal,
        "queue_id": item.queue_id,
        "output_id": item.output_id,
        "publication_group_id": item.publication_group_id,
        "publication_target_id": item.publication_target_id,
        "platform": item.platform,
        "chrome_profile": item.chrome_profile,
        "group_label": item.group_label,
        "phase": item.phase,
        "outcome": item.outcome,
        "retry_count": item.retry_count,
        "requested_action": item.requested_action,
        "retry_mode": item.retry_mode,
        "retry_after": item.retry_after.isoformat() if item.retry_after else None,
        "retry_of_item_id": item.retry_of_item_id,
        "retry_run_id": item.retry_run_id,
        "error": item.error,
        "note": item.note,
        "title": item.title,
        "evidence": (item.evidence_json or {}) if include_evidence else {},
        "gate_snapshot": item.gate_snapshot_json or {},
        "assignment": item.assignment_json or {},
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "can_skip": item.phase in SAFE_SKIP_PHASES and not was_clicked,
        "can_retry": item.phase in ("deferred", "failed", "skipped") and not was_clicked,
        "requires_outcome_confirmation": requires_confirmation,
    }


def run_to_dict(
    run: ReachPublishRun,
    *,
    items: list[ReachPublishRunItem] | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    rows = items or []
    counts = {
        "total": len(rows),
        "published": sum(i.phase == "published" for i in rows),
        "failed": sum(i.phase == "failed" for i in rows),
        "deferred": sum(i.phase in ("deferred", "awaiting_confirmation") for i in rows),
        "cancelled": sum(i.phase == "cancelled" for i in rows),
        "remaining": sum(i.phase not in ITEM_TERMINAL for i in rows),
    }
    return {
        "run_id": run.run_id,
        "customer_id": run.customer_id,
        "status": run.status,
        "source": run.source,
        "launch_mode": run.launch_mode,
        "requested_total": run.requested_total,
        "allocation_mode": run.allocation_mode,
        "content_mode": run.content_mode,
        "selection_seed": run.selection_seed,
        "config": run.config_json or {},
        "notification": run.notification_json or {},
        "schedule_id": run.schedule_id,
        "trigger_id": run.trigger_id,
        "accept_risk": run.accept_risk,
        "stop_after_current": run.stop_after_current,
        "requested_action": run.requested_action,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "consecutive_failures": run.consecutive_failures,
        "circuit_break": run.circuit_break,
        "chrome_port": run.chrome_port,
        "chrome_profile": run.chrome_profile,
        "error": run.error,
        "log": [] if compact else (run.log_json or []),
        "items": [
            run_item_to_dict(i, include_evidence=not compact) for i in rows
        ],
        "counts": counts,
        "can_stop": run.status not in RUN_TERMINAL,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def get_active_run(session: Session, *, customer_id: int | None = None) -> ReachPublishRun | None:
    stmt = select(ReachPublishRun).where(
        ReachPublishRun.status.in_(ACTIVE_RUN_STATUSES)
    )
    if customer_id is not None:
        stmt = stmt.where(ReachPublishRun.customer_id == customer_id)
    return session.scalar(stmt.order_by(ReachPublishRun.id.desc()))


def get_run(session: Session, run_id: str, *, customer_id: int | None = None) -> ReachPublishRun | None:
    stmt = select(ReachPublishRun).where(ReachPublishRun.run_id == run_id)
    if customer_id is not None:
        stmt = stmt.where(ReachPublishRun.customer_id == customer_id)
    return session.scalar(stmt)


def list_run_items(session: Session, run: ReachPublishRun) -> list[ReachPublishRunItem]:
    return list(
        session.scalars(
            select(ReachPublishRunItem)
            .where(ReachPublishRunItem.run_id == run.id)
            .order_by(ReachPublishRunItem.ordinal.asc())
        ).all()
    )


def preflight_items(
    session: Session,
    *,
    customer_id: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate account/platform pairing and gate assets before run creation."""
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets
    from engine.reach.cover_templates import resolve_cover_store_for_settings

    data_root = resolve_cover_store_for_settings()
    problems: list[dict[str, Any]] = []
    ok_items: list[dict[str, Any]] = []
    for idx, spec in enumerate(items):
        spec = dict(spec)
        plat = str(spec.get("platform") or "").strip().lower()
        profile = str(spec.get("chrome_profile") or "").strip()
        try:
            resolved_plat = resolve_profile_platform(
                profile, plat, customer_id=customer_id, business_scope=SCOPE_VIDEO
            )
        except ValueError as exc:
            problems.append({"ordinal": idx, "error": str(exc), "profile": profile, "platform": plat})
            continue
        if resolved_plat != plat:
            problems.append(
                {
                    "ordinal": idx,
                    "error": f"账号 {profile} 绑定平台 {resolved_plat} 与条目平台 {plat} 不匹配",
                    "profile": profile,
                    "platform": plat,
                }
            )
            continue
        pack_dir = spec.get("pack_dir")
        video_path = spec.get("video_path") or ""
        title = spec.get("title") or ""
        body = spec.get("body") or ""
        if spec.get("queue_id"):
            q = session.get(ReachQueueItem, int(spec["queue_id"]))
            if q and q.customer_id == customer_id:
                title = title or q.title or ""
                body = body or q.body or ""
                pack_dir = pack_dir or q.pack_dir
                video_path = video_path or q.video_path or ""
                spec["output_id"] = spec.get("output_id") or q.output_id
                spec["publication_group_id"] = (
                    spec.get("publication_group_id") or q.publication_group_id
                )
                spec["publication_target_id"] = (
                    spec.get("publication_target_id") or q.publication_target_id
                )
            else:
                problems.append(
                    {"ordinal": idx, "error": "队列项不存在或不属于当前客户"}
                )
                continue
        from engine.reach.publication_lifecycle import (
            assert_current_assets,
            bind_queue_item,
            freeze_publication_group,
            resolve_target,
        )

        try:
            assert_current_assets(
                session,
                customer_id=customer_id,
                output_id=spec.get("output_id"),
                pack_dir=pack_dir,
                video_path=video_path,
            )
            if spec.get("queue_id"):
                q = session.get(ReachQueueItem, int(spec["queue_id"]))
                target = bind_queue_item(
                    session, q, account_key=profile, source="publish_run"
                )
                spec["publication_group_id"] = q.publication_group_id
                spec["publication_target_id"] = target.id
            elif not (
                spec.get("publication_group_id") and spec.get("publication_target_id")
            ):
                group, _ = freeze_publication_group(
                    session,
                    customer_id=customer_id,
                    output_id=int(spec["output_id"]),
                    targets=[{"platform": plat, "account_key": profile}],
                    source="publish_run",
                )
                target = resolve_target(
                    session,
                    group_id=group.id,
                    platform=plat,
                    account_key=profile,
                )
                spec["publication_group_id"] = group.id
                spec["publication_target_id"] = target.id
        except ValueError as exc:
            problems.append({"ordinal": idx, "error": str(exc), "platform": plat})
            continue
        if not pack_dir:
            problems.append(
                {"ordinal": idx, "error": "缺少持久化 publish_pack，禁止创建发布批次", "platform": plat}
            )
            continue
        try:
            require_publish_assets(
                platform=plat,
                pack_dir=Path(str(pack_dir)),
                data_root=data_root,
                title=title,
                body=body,
            )
        except PublishAssetsError as exc:
            oid = spec.get("output_id")
            healed = False
            if oid:
                try:
                    from engine.reach.publish_asset_heal import (
                        refresh_queue_and_item_from_pack,
                        repair_output_pack,
                    )

                    pack_res = repair_output_pack(
                        session,
                        customer_id=customer_id,
                        output_id=int(oid),
                        force=True,
                    )
                    if pack_res.get("ok"):
                        pack_dir = str(pack_res.get("pack_dir") or pack_dir)
                        refresh = refresh_queue_and_item_from_pack(
                            session,
                            platform=plat,
                            pack_dir=pack_dir,
                            queue_id=spec.get("queue_id"),
                            item=None,
                        )
                        if refresh.get("ok"):
                            title = str(refresh.get("title") or title)
                            body = str(refresh.get("body") or body)
                            spec["pack_dir"] = pack_dir
                            spec["title"] = title
                            require_publish_assets(
                                platform=plat,
                                pack_dir=Path(str(pack_dir)),
                                data_root=data_root,
                                title=title,
                                body=body,
                            )
                            healed = True
                except PublishAssetsError as exc2:
                    problems.append(
                        {"ordinal": idx, "error": str(exc2), "platform": plat}
                    )
                    continue
                except Exception:
                    log.exception("preflight asset heal failed output=%s", oid)
            if not healed:
                problems.append(
                    {"ordinal": idx, "error": str(exc), "platform": plat}
                )
                continue
        ok_items.append(
            {
                **spec,
                "platform": plat,
                "chrome_profile": profile,
                "title": title,
                "pack_dir": pack_dir,
            }
        )
    return {"ok": not problems, "items": ok_items, "problems": problems}


def reconcile_stale_runs(session: Session) -> int:
    """Release DB-active runs that have no worker in this engine process.

    Includes human-held statuses (waiting_login / paused_human / outcome_unknown)
    so deploy/restart cannot pin the machine publish slot forever.
    """
    rows = session.scalars(
        select(ReachPublishRun).where(
            ReachPublishRun.status.in_(tuple(RECONCILE_ACTIVE_STATUSES))
        )
    ).all()
    changed = 0
    now = _now()
    for run in rows:
        if _worker_alive_for(run.run_id):
            continue
        # Freshly queued runs often have a short gap before the worker thread
        # registers; UI/status polling must not treat that as a crash.
        if run.status == "queued" and run.created_at is not None:
            created = run.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (now - created.astimezone(timezone.utc)).total_seconds()
            if age < 120:
                continue
        # Human-held states: allow a short settle after heartbeat before steal.
        if run.status in STALE_HUMAN_HOLD_STATUSES:
            anchor = run.heartbeat_at or run.updated_at or run.started_at or run.created_at
            if anchor is not None:
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
                age = (now - anchor.astimezone(timezone.utc)).total_seconds()
                # Must exceed grace so live grace-waits with a live worker are not
                # confused; when worker is dead, free after at most grace + margin.
                if age < max(30.0, login_wall_grace_sec() + 15.0):
                    continue
        was_login_wall = str(run.status or "") == "waiting_login"
        for item in list_run_items(session, run):
            if item.phase in ITEM_TERMINAL:
                continue
            pub = (item.evidence_json or {}).get("publish_result") or {}
            if pub.get("pub_clicked") or item.phase in (
                "submitting",
                "verifying",
                "outcome_unknown",
            ):
                item.phase = "awaiting_confirmation"
                item.outcome = "unknown"
                item.retry_mode = ""
                item.error = item.error or "引擎中断后发布结果待确认"
                if item.publication_group_id and item.publication_target_id:
                    from engine.reach.publication_lifecycle import record_target_outcome

                    record_target_outcome(
                        session,
                        group_id=item.publication_group_id,
                        target_id=item.publication_target_id,
                        outcome="outcome_unknown",
                        evidence=pub,
                        note="worker_crash_after_submit",
                    )
            else:
                was_wall_item = (
                    was_login_wall
                    or str(item.phase or "") == "waiting_login"
                    or _soft_skip_kind(item) in LOGIN_WALL_KINDS
                )
                item.phase = "deferred"
                item.outcome = "deferred"
                # Login-wall zombies should re-enter auto makeup when free.
                if was_wall_item:
                    item.retry_mode = "auto"
                    item.retry_after = _now() + timedelta(
                        seconds=LOGIN_WALL_DEFER_RETRY_AFTER_SEC
                    )
                    item.error = item.error or (
                        "登录等待被引擎中断，已跳过并排队补发"
                    )
                else:
                    item.retry_mode = item.retry_mode or "manual"
                    item.error = item.error or "引擎中断，已转为待补发"
                from engine.reach.publication_lifecycle import release_unsubmitted_target

                release_unsubmitted_target(
                    session,
                    group_id=item.publication_group_id,
                    target_id=item.publication_target_id,
                )
            item.requested_action = ""
            item.finished_at = _now()
            item.updated_at = _now()
        run.status = "interrupted_system"
        run.error = (
            "发布 worker 已中断（含登录等待/人工暂停无进程持有），"
            "未完成条目已进入待确认或待补发"
        )
        run.requested_action = ""
        run.finished_at = _now()
        run.updated_at = _now()
        _append_run_log(run, "stale_run_reconciled", previous_human_hold=was_login_wall)
        try:
            from engine.runtime.resource_gate import gate as resource_gate

            resource_gate.release("publish", f"publish:{run.run_id}")
        except Exception:
            pass
        changed += 1
    if changed:
        session.commit()
    return changed


def _create_run_unlocked(
    session: Session,
    *,
    customer_id: int,
    items: list[dict[str, Any]],
    source: str = "manual",
    schedule_id: int | None = None,
    trigger_id: int | None = None,
    accept_risk: bool = False,
    launch_mode: str = "manual",
    requested_total: int = 0,
    allocation_mode: str = "",
    content_mode: str = "",
    selection_seed: str = "",
    config: dict[str, Any] | None = None,
) -> ReachPublishRun:
    if not accept_risk:
        raise ValueError("须 accept_risk=true（G5.BATCH 风控自负）")
    last: Exception | None = None
    for attempt in range(20):
        try:
            # Machine-wide slot: customers share one physical browser/upload channel.
            reconcile_stale_runs(session)
            active = get_active_run(session)
            if active:
                raise ValueError(f"已有进行中的发布批次 {active.run_id}")
            check = preflight_items(session, customer_id=customer_id, items=items)
            if not check.get("ok"):
                raise ValueError(f"预检失败: {check.get('problems')}")
            run_id = uuid.uuid4().hex[:16]
            run = ReachPublishRun(
                customer_id=customer_id,
                run_id=run_id,
                status="queued",
                source=source,
                launch_mode=launch_mode,
                requested_total=requested_total or len(items),
                allocation_mode=allocation_mode,
                content_mode=content_mode,
                selection_seed=selection_seed,
                config_json=config or {},
                schedule_id=schedule_id,
                trigger_id=trigger_id,
                accept_risk=True,
                cursor_handoff_enabled=False,
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(run)
            session.flush()
            for idx, spec in enumerate(check["items"]):
                row = ReachPublishRunItem(
                    run_id=run.id,
                    customer_id=customer_id,
                    ordinal=idx,
                    queue_id=spec.get("queue_id"),
                    output_id=spec.get("output_id"),
                    publication_group_id=spec.get("publication_group_id"),
                    publication_target_id=spec.get("publication_target_id"),
                    platform=spec["platform"],
                    chrome_profile=spec["chrome_profile"],
                    group_label=str(spec.get("group_label") or ""),
                    phase="queued",
                    gate_snapshot_json=spec.get("gate_snapshot") or {},
                    assignment_json=spec.get("assignment") or {},
                    retry_of_item_id=spec.get("retry_of_item_id"),
                    title=str(spec.get("title") or ""),
                    video_path=str(spec.get("video_path") or ""),
                    pack_dir=spec.get("pack_dir"),
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(row)
            _append_run_log(run, "created", item_count=len(check["items"]))
            _audit_publish(
                session,
                run,
                "publish_run_created",
                f"发布批次已冻结，共 {len(check['items'])} 条",
                stage="assignment",
                details={
                    "requested_total": run.requested_total,
                    "allocation_mode": run.allocation_mode,
                    "content_mode": run.content_mode,
                    "selection_seed": run.selection_seed,
                },
            )
            session.commit()
            session.refresh(run)
            return run
        except ValueError:
            try:
                session.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            last = exc
            try:
                session.rollback()
            except Exception:
                pass
            message = str(exc).lower()
            if "database is locked" not in message and "locked" not in message:
                raise
            time.sleep(0.5 * (attempt + 1))
    if last is not None:
        raise last
    raise RuntimeError("create_run failed")


def create_run(
    session: Session,
    *,
    customer_id: int,
    items: list[dict[str, Any]],
    source: str = "manual",
    schedule_id: int | None = None,
    trigger_id: int | None = None,
    accept_risk: bool = False,
    launch_mode: str = "manual",
    requested_total: int = 0,
    allocation_mode: str = "",
    content_mode: str = "",
    selection_seed: str = "",
    config: dict[str, Any] | None = None,
) -> ReachPublishRun:
    with _run_create_lock:
        return _create_run_unlocked(
            session,
            customer_id=customer_id,
            items=items,
            source=source,
            schedule_id=schedule_id,
            trigger_id=trigger_id,
            accept_risk=accept_risk,
            launch_mode=launch_mode,
            requested_total=requested_total,
            allocation_mode=allocation_mode,
            content_mode=content_mode,
            selection_seed=selection_seed,
            config=config,
        )


def list_deferred_items(
    session: Session, *, customer_id: int
) -> list[ReachPublishRunItem]:
    rows = list(
        session.scalars(
            select(ReachPublishRunItem)
            .where(
                ReachPublishRunItem.customer_id == customer_id,
                ReachPublishRunItem.phase.in_(("deferred", "failed", "skipped")),
                ReachPublishRunItem.retry_run_id.is_(None),
            )
            .order_by(ReachPublishRunItem.updated_at.asc())
        ).all()
    )
    return [
        row
        for row in rows
        if not bool(
            ((row.evidence_json or {}).get("publish_result") or {}).get(
                "pub_clicked"
            )
        )
    ]


def _is_no_auto_makeup_error(error: str) -> bool:
    """True only for hard human/login failures — asset gaps are healable/auto."""
    from engine.reach.publish_asset_heal import is_hard_no_auto_error

    return is_hard_no_auto_error(error)


def _item_has_fail_forward_signal(item: ReachPublishRunItem) -> bool:
    ev = item.evidence_json or {}
    if isinstance(ev.get("fail_forward"), dict):
        return True
    soft = ev.get("soft_skip") if isinstance(ev, dict) else None
    if isinstance(soft, dict) and str(soft.get("kind") or "") in LOGIN_WALL_KINDS:
        return True
    err = str(item.error or "")
    return any(
        x in err
        for x in (
            "登录宽限已过",
            "已跳过并排队补发",
            "true_login_wall",
            "需要重新扫码",
            "引擎中断",
            "no_file_input",
        )
    )


def repair_deferred_auto_eligibility(
    session: Session, *, customer_id: int
) -> int:
    """Normalize pre-submit deferred so automatic chain can resume.

    1) Rebuild packs for 缺文案 / 物料不齐 items (budgeted)
    2) Hard-permanent errors → retry_mode=none
    3) safe deferred with empty/manual → auto (keep future retry_after as cool)
    """
    changed = 0
    now = _now()
    try:
        from engine.reach.publish_asset_heal import heal_deferred_assets

        stats = heal_deferred_assets(session, customer_id=customer_id)
        changed += int(stats.get("healed") or 0) + int(stats.get("promoted_auto") or 0)
    except Exception:
        log.exception("heal_deferred_assets failed customer_id=%s", customer_id)

    for row in list_deferred_items(session, customer_id=customer_id):
        if row.phase != "deferred":
            continue
        if _is_no_auto_makeup_error(str(row.error or "")):
            if row.retry_mode != "none":
                row.retry_mode = "none"
                row.retry_after = None
                row.updated_at = now
                changed += 1
            continue
        mode = str(row.retry_mode or "")
        if mode in ("", "manual") or (
            mode == "auto" and _item_has_fail_forward_signal(row)
        ):
            if mode != "auto":
                row.retry_mode = "auto"
                changed += 1
            if row.retry_after is None:
                row.retry_after = now
                changed += 1
            row.updated_at = now
    return changed


def retry_deferred_items(
    session: Session,
    *,
    customer_id: int,
    item_ids: list[int] | None = None,
    automatic: bool = False,
) -> ReachPublishRun | None:
    """Create one auditable retry run from explicitly safe deferred items."""
    reconcile_stale_runs(session)
    # Auto and manual: heal packs / promote 缺文案 before preflight.
    try:
        repair_deferred_auto_eligibility(session, customer_id=customer_id)
        session.commit()
    except Exception:
        log.exception("repair before deferred retry failed")
        try:
            session.rollback()
        except Exception:
            pass
    if get_active_run(session):
        if automatic:
            return None
        raise ValueError("当前发布槽仍在使用，请稍后补发")
    rows = list_deferred_items(session, customer_id=customer_id)
    if item_ids:
        wanted = {int(value) for value in item_ids}
        rows = [row for row in rows if row.id in wanted]
    if automatic:
        now = _now()
        # Auto chain only reclaims phase=deferred + retry_mode=auto (not failed junk).
        eligible: list[ReachPublishRunItem] = []
        for row in rows:
            if row.phase != "deferred":
                continue
            if str(row.retry_mode or "") != "auto":
                continue
            if _is_no_auto_makeup_error(str(row.error or "")):
                continue
            if row.retry_after is not None:
                after = row.retry_after
                if after.tzinfo is None:
                    after = after.replace(tzinfo=timezone.utc)
                else:
                    after = after.astimezone(timezone.utc)
                if after > now:
                    continue
            eligible.append(row)
        # Prefer same-platform continuity to reduce Chrome thrash within the batch.
        eligible.sort(
            key=lambda r: (
                str(r.platform or ""),
                str(r.chrome_profile or ""),
                int(r.id or 0),
            )
        )
        rows = eligible[:12]
    if not rows:
        if automatic:
            return None
        raise ValueError("没有可安全补发的条目")
    items = [
        {
            "queue_id": row.queue_id,
            "output_id": row.output_id,
            "publication_group_id": row.publication_group_id,
            "publication_target_id": row.publication_target_id,
            "platform": row.platform,
            "chrome_profile": row.chrome_profile,
            "group_label": row.group_label,
            "title": row.title,
            "video_path": row.video_path,
            "pack_dir": row.pack_dir,
            "gate_snapshot": {
                **(row.gate_snapshot_json or {}),
                "retry_of_item_id": row.id,
            },
            "assignment": row.assignment_json or {},
            "retry_of_item_id": row.id,
        }
        for row in rows
    ]
    # Drop items that still fail asset preflight so one bad row cannot crash the tick.
    check = preflight_items(session, customer_id=customer_id, items=items)
    if check.get("problems"):
        bad = {
            int(p.get("ordinal"))
            for p in (check.get("problems") or [])
            if p.get("ordinal") is not None
        }
        ok_items = [
            item
            for idx, item in enumerate(items)
            if idx not in bad
        ]
        log.warning(
            "deferred preflight dropped %s/%s items: %s",
            len(bad),
            len(items),
            check.get("problems"),
        )
        items = ok_items
    if not items:
        if automatic:
            return None
        raise ValueError(f"预检失败: {check.get('problems')}")
    run = create_run(
        session,
        customer_id=customer_id,
        items=items,
        source="deferred_auto" if automatic else "deferred_manual",
        launch_mode="deferred",
        requested_total=len(items),
        accept_risk=True,
        config={"retry_item_ids": [row.id for row in rows]},
    )
    # Start the worker before bookkeeping writes — SQLite lock storms must not
    # leave a queued run idle until reconcile_stale_runs cancels it.
    started = start_run(run.run_id)
    for row in rows:
        row.retry_run_id = run.run_id
        row.updated_at = _now()
    try:
        _commit_with_retry(session)
    except Exception:
        log.warning(
            "deferred retry bookkeeping commit failed run_id=%s",
            run.run_id,
            exc_info=True,
        )
        try:
            session.rollback()
        except Exception:
            pass
    if not started.get("ok"):
        for row in rows:
            row.retry_run_id = None
            if automatic:
                row.retry_after = _now() + timedelta(seconds=30)
        try:
            _commit_with_retry(session)
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
        return None if automatic else run
    return run


def _kick_deferred_chain(customer_id: int) -> None:
    """After a publish slot fully frees, immediately try next auto deferred batch."""
    try:
        session = get_session()
    except Exception:
        return
    try:
        if get_active_run(session):
            return
        repair_deferred_auto_eligibility(session, customer_id=customer_id)
        session.commit()
        retry_deferred_items(session, customer_id=customer_id, automatic=True)
    except Exception:
        log.exception("kick deferred chain failed customer_id=%s", customer_id)
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        try:
            session.close()
        except Exception:
            pass


def start_run(run_id: str) -> dict[str, Any]:
    global _worker, _worker_run_id
    with _lock:
        if _worker and _worker.is_alive():
            if _worker_run_id == run_id:
                return {"ok": True, "run_id": run_id, "already_running": True}
            busy = True
        else:
            busy = False
        if not busy:
            _worker = threading.Thread(target=_worker_loop, args=(run_id,), daemon=True, name=f"publish-run-{run_id}")
            _worker_run_id = run_id
            _worker.start()
    if busy:
        session = get_session()
        try:
            run = get_run(session, run_id)
            if run and run.status == "queued":
                run.status = "cancelled"
                run.error = "worker_busy_before_start"
                run.finished_at = _now()
                run.updated_at = _now()
                for item in list_run_items(session, run):
                    if item.phase == "queued":
                        item.phase = "deferred"
                        item.outcome = "deferred"
                        item.retry_mode = item.retry_mode or "auto"
                        item.retry_after = _now() + timedelta(seconds=30)
                        item.finished_at = _now()
                        item.updated_at = _now()
                _append_run_log(run, "start_failed_worker_busy")
                session.commit()
        finally:
            session.close()
        return {"ok": False, "error": "worker_busy"}
    return {"ok": True, "run_id": run_id}


def _worker_alive_for(run_id: str) -> bool:
    with _lock:
        return bool(_worker and _worker.is_alive() and _worker_run_id == run_id)


def _finish_cancelled(session: Session, run: ReachPublishRun) -> None:
    if run.requested_action == "interrupt_system":
        _finish_system_interrupted(session, run)
        return
    for item in list_run_items(session, run):
        if item.phase not in ITEM_TERMINAL:
            from engine.reach.publication_lifecycle import release_unsubmitted_target

            pub = (item.evidence_json or {}).get("publish_result") or {}
            if not pub.get("pub_clicked"):
                release_unsubmitted_target(
                    session,
                    group_id=item.publication_group_id,
                    target_id=item.publication_target_id,
                )
            item.phase = "cancelled"
            item.outcome = "cancelled"
            item.finished_at = _now()
            item.updated_at = _now()
    run.status = "cancelled"
    run.finished_at = _now()
    run.updated_at = _now()
    run.heartbeat_at = _now()
    _append_run_log(run, "cancelled")


def _finish_system_interrupted(session: Session, run: ReachPublishRun) -> None:
    """Stop a side-effecting run without turning it into an auto-retryable cancel."""
    from engine.catalog.db import PublicationTarget
    from engine.reach.publication_lifecycle import release_unsubmitted_target

    for item in list_run_items(session, run):
        if item.phase in ITEM_TERMINAL:
            continue
        publish_result = (item.evidence_json or {}).get("publish_result") or {}
        clicked = bool(publish_result.get("pub_clicked"))
        target = (
            session.get(PublicationTarget, item.publication_target_id)
            if item.publication_target_id
            else None
        )
        if clicked or item.phase in ("submitting", "verifying", "outcome_unknown"):
            item.phase = "awaiting_confirmation"
            item.outcome = "outcome_unknown"
            item.retry_mode = "manual"
            item.error = "系统暂停发生在提交阶段，请先到平台作品列表确认结果"
            if target and target.status != "published":
                target.status = "outcome_unknown"
                target.fact_json = {
                    "outcome": "unknown",
                    "at": _now().isoformat(),
                    "evidence": {"system_interrupted": True, "pub_clicked": clicked},
                    "note": "system_interrupted",
                }
                target.updated_at = _now()
        else:
            release_unsubmitted_target(
                session,
                group_id=item.publication_group_id,
                target_id=item.publication_target_id,
            )
            item.phase = "deferred"
            item.outcome = "interrupted_system"
            item.retry_mode = "manual"
            item.error = "系统暂停，已停止本条；需要你确认后再继续"
        item.requested_action = ""
        item.finished_at = _now()
        item.updated_at = _now()
    run.status = "interrupted_system"
    run.error = "系统暂停中断；未完成条目仅允许人工确认后继续"
    run.requested_action = ""
    run.finished_at = _now()
    run.updated_at = _now()
    run.heartbeat_at = _now()
    _append_run_log(run, "interrupted_system")
    _audit_publish(
        session,
        run,
        "publish_system_interrupted",
        run.error,
        stage="system",
        level="warning",
    )


def _stop_active_managed_chrome() -> None:
    _stop_active_chrome(force=True)


def cancel_run(run_id: str, *, customer_id: int | None = None) -> dict[str, Any]:
    global _cancel_run_id
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run:
            return {"ok": False, "error": "not_found"}
        if run.status in RUN_TERMINAL:
            return {"ok": True, "already_terminal": True}
        run.requested_action = "cancel"
        _cancel_run_id = run_id
        run.status = "stopping"
        run.updated_at = _now()
        _append_run_log(run, "cancel_requested")
        if not _worker_alive_for(run_id):
            _finish_cancelled(session, run)
            _stop_active_managed_chrome()
        session.commit()
        return {"ok": True, "run_id": run_id, "status": run.status}
    finally:
        session.close()


def interrupt_run_for_system(
    run_id: str, *, customer_id: int | None = None
) -> dict[str, Any]:
    """Atomically request a non-replayable system interruption."""
    global _cancel_run_id
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run:
            return {"ok": False, "error": "not_found"}
        if run.status in RUN_TERMINAL:
            return {"ok": True, "already_terminal": True, "status": run.status}
        run.requested_action = "interrupt_system"
        _cancel_run_id = run_id
        run.status = "stopping"
        run.error = "系统正在安全暂停发布"
        run.updated_at = _now()
        _append_run_log(run, "system_interrupt_requested")
        if not _worker_alive_for(run_id):
            _finish_system_interrupted(session, run)
            _stop_active_managed_chrome()
        session.commit()
        return {"ok": True, "run_id": run_id, "status": run.status}
    finally:
        session.close()


def stop_after_current(run_id: str, *, customer_id: int | None = None) -> dict[str, Any]:
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run:
            return {"ok": False, "error": "not_found"}
        run.stop_after_current = True
        run.requested_action = "stop_after_current"
        run.updated_at = _now()
        _append_run_log(run, "stop_after_current")
        session.commit()
        return {"ok": True, "run_id": run_id}
    finally:
        session.close()


def skip_current_item(
    run_id: str,
    item_id: int,
    *,
    retry_mode: str = "manual",
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Safely defer the current item and let the serial run continue."""
    mode = retry_mode.strip().lower()
    if mode not in ("manual", "auto", "none"):
        raise ValueError("retry_mode 须为 manual | auto | none")
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run or run.status in RUN_TERMINAL:
            raise ValueError("批次不存在或已结束")
        item = session.get(ReachPublishRunItem, item_id)
        if not item or item.run_id != run.id or item.phase in ITEM_TERMINAL:
            raise ValueError("当前条目不存在或已结束")
        pub = (item.evidence_json or {}).get("publish_result") or {}
        clicked = bool(pub.get("pub_clicked"))
        if clicked or item.phase in ("submitting", "verifying", "outcome_unknown"):
            item.phase = "awaiting_confirmation"
            item.outcome = "unknown"
            item.retry_mode = ""
            item.requested_action = ""
            item.error = item.error or "发布结果待人工确认，确认未发布前禁止补发"
            item.finished_at = _now()
            item.updated_at = _now()
            _append_run_log(run, "item_deferred_confirmation", item_id=item.id)
        elif item.phase not in SAFE_SKIP_PHASES:
            raise ValueError(f"当前阶段 {item.phase} 不能安全跳过")
        elif _worker_alive_for(run_id) and item.phase not in ("paused_human", "waiting_login"):
            item.requested_action = "skip"
            item.retry_mode = mode
            item.retry_after = _now() + timedelta(minutes=5) if mode == "auto" else None
            item.updated_at = _now()
            _append_run_log(run, "item_skip_requested", item_id=item.id, retry_mode=mode)
            session.commit()
            return {"ok": True, "run_id": run_id, "item_id": item.id, "pending": True}
        else:
            from engine.reach.publication_lifecycle import release_unsubmitted_target

            release_unsubmitted_target(
                session,
                group_id=item.publication_group_id,
                target_id=item.publication_target_id,
            )
            item.phase = "deferred" if mode != "none" else "skipped"
            item.outcome = "deferred" if mode != "none" else "skipped"
            item.retry_mode = mode
            item.retry_after = _now() + timedelta(minutes=5) if mode == "auto" else None
            item.requested_action = ""
            item.finished_at = _now()
            item.updated_at = _now()
            _append_run_log(run, "item_skipped", item_id=item.id, retry_mode=mode)
        run.status = "running"
        run.error = ""
        run.updated_at = _now()
        session.commit()
    finally:
        session.close()
    started = start_run(run_id)
    return {"ok": True, "run_id": run_id, "item_id": item_id, "started": started}


def resume_run(run_id: str, *, customer_id: int | None = None) -> dict[str, Any]:
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run:
            raise ValueError("批次不存在")
        if run.status == "outcome_unknown":
            raise ValueError("发布结果不明，必须先人工确认结果，禁止自动重试")
        if run.status not in ("paused_human", "waiting_login"):
            raise ValueError(f"状态 {run.status} 不可恢复")
        item = next(
            (
                candidate
                for candidate in list_run_items(session, run)
                if candidate.phase in ("paused_human", "waiting_login")
            ),
            None,
        )
        if item is None:
            raise ValueError("批次没有可恢复的人工暂停条目")
        previous_error = item.error or run.status
        publish_result = (item.evidence_json or {}).get("publish_result") or {}
        upload_result = publish_result.get("upload") or {}
        reuse_existing_form = (
            run.status != "waiting_login"
            and (
                publish_result.get("phase") == "cover_failed"
                or (
                    publish_result.get("phase") == "uploading"
                    and upload_result.get("ok")
                )
            )
            and not publish_result.get("pub_clicked")
        )
        # True login wall: re-queue this item + blocked siblings for the same profile.
        if run.status == "waiting_login" or item.phase == "waiting_login":
            profile = str(item.chrome_profile or "")
            for sibling in list_run_items(session, run):
                kind = _soft_skip_kind(sibling)
                if sibling.id == item.id:
                    continue
                if sibling.phase != SOFT_SKIP_PHASE:
                    continue
                if kind != "true_login_wall":
                    continue
                if profile and str(sibling.chrome_profile or "") != profile:
                    continue
                sibling.phase = "queued"
                sibling.outcome = None
                sibling.error = ""
                sibling.finished_at = None
                sibling.started_at = None
                sibling.retry_count = 0
                sibling.updated_at = _now()
        item.phase = "queued"
        item.error = ""
        if reuse_existing_form:
            item.note = "resume_existing_form"
        item.updated_at = _now()
        run.status = "running"
        run.error = ""
        run.updated_at = _now()
        _append_run_log(
            run,
            "resumed",
            item_id=item.id,
            checkpoint="restart_current_item",
            previous_error=previous_error,
            reuse_existing_form=reuse_existing_form,
        )
        session.commit()
    finally:
        session.close()
    return start_run(run_id)


def confirm_item_outcome(
    run_id: str,
    item_id: int,
    *,
    outcome: str,
    note: str = "",
    retry_mode: str = "manual",
    customer_id: int | None = None,
) -> dict[str, Any]:
    outcome = outcome.strip().lower()
    if outcome not in ("published", "failed", "skip", "not_published"):
        raise ValueError("outcome 须为 published | not_published | failed | skip")
    if retry_mode not in ("manual", "auto", "none"):
        raise ValueError("retry_mode 须为 manual | auto | none")
    session = get_session()
    try:
        run = get_run(session, run_id, customer_id=customer_id)
        if not run:
            raise ValueError("批次不存在")
        item = session.get(ReachPublishRunItem, item_id)
        if not item or item.run_id != run.id:
            raise ValueError("条目不存在")
        if item.phase not in (
            "outcome_unknown",
            "awaiting_confirmation",
            "paused_human",
            "verifying",
        ):
            raise ValueError(f"条目 phase={item.phase} 不可人工确认")
        item.outcome = outcome
        if outcome == "published":
            item.phase = "published"
        elif outcome == "not_published":
            item.phase = "deferred" if retry_mode != "none" else "skipped"
            item.retry_mode = retry_mode
            item.retry_after = (
                _now() + timedelta(minutes=5) if retry_mode == "auto" else None
            )
        else:
            item.phase = "skipped" if outcome == "skip" else "failed"
        item.note = note or item.note
        item.finished_at = _now()
        item.updated_at = _now()
        if item.queue_id and outcome == "published":
            q = get_item(session, int(item.queue_id), customer_id=run.customer_id)
            if q and q.status != "published":
                _mark_queue_published(
                    session, q, note=note or "human_confirmed"
                )
        if outcome in ("published", "not_published"):
            from engine.reach.publication_lifecycle import record_target_outcome

            record_target_outcome(
                session,
                group_id=int(item.publication_group_id or 0),
                target_id=int(item.publication_target_id or 0),
                outcome=outcome,
                evidence=item.evidence_json or {},
                note=note or "human_confirmed",
            )
        pending = [
            candidate
            for candidate in list_run_items(session, run)
            if candidate.id != item.id and candidate.phase not in ITEM_TERMINAL
        ]
        run.status = "running" if pending else "completed"
        if not pending:
            run.finished_at = _now()
        run.updated_at = _now()
        _append_run_log(run, "item_confirmed", item_id=item_id, outcome=outcome)
        session.commit()
        if pending:
            start_run(run_id)
        return {"ok": True, "run_id": run_id, "item_id": item_id, "outcome": outcome}
    finally:
        session.close()


def get_status(
    run_id: str | None = None, *, customer_id: int | None = None
) -> dict[str, Any]:
    session = get_session()
    try:
        reconcile_stale_runs(session)
        if run_id:
            run = get_run(session, run_id, customer_id=customer_id)
        else:
            stmt = select(ReachPublishRun)
            if customer_id is not None:
                stmt = stmt.where(ReachPublishRun.customer_id == customer_id)
            run = session.scalar(stmt.order_by(ReachPublishRun.id.desc()))
        if not run:
            return {"ok": True, "active": False, "phase": "idle"}
        items = list_run_items(session, run)
        current = next((i for i in items if i.phase not in ITEM_TERMINAL), None)
        if current is None:
            current = next(
                (i for i in items if i.phase == "awaiting_confirmation"),
                None,
            )
        payload = run_to_dict(run, items=items, compact=True)
        payload["ok"] = True
        payload["active"] = run.status not in RUN_TERMINAL
        payload["current_item"] = (
            run_item_to_dict(current, include_evidence=False) if current else None
        )
        payload["phase"] = current.phase if current else run.status
        return payload
    finally:
        session.close()


def _stop_active_chrome(
    *, force: bool = True, flush_sec: float | None = None
) -> dict[str, Any]:
    """Stop managed Chrome for this worker with a hard timeout + profile force-kill."""
    global _active_chrome, _active_profile
    t0 = time.monotonic()
    result: dict[str, Any] = {
        "ok": True,
        "killed": False,
        "had_chrome": bool(_active_chrome),
        "profile": _active_profile or "",
        "elapsed_ms": 0,
    }
    if not _active_chrome:
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    chrome = _active_chrome
    profile = getattr(chrome, "profile", None)
    extra_flush = float(flush_sec or 0.0)
    try:
        from engine.reach.chrome_runtime import forget_managed

        stop_timeout = max(CHROME_STOP_TIMEOUT_SEC, extra_flush + 4.0)
        chrome.stop(timeout_sec=stop_timeout)
        if extra_flush > 0:
            # Give cookie / IndexedDB write a bit more wall after Browser.close.
            time.sleep(min(extra_flush, 12.0))
        forget_managed(chrome)
        if force and profile is not None:
            try:
                from engine.reach.chrome_runtime import (
                    _force_release_profile,
                    _profile_occupied,
                    _wait_profile_unlocked,
                )

                # Prefer waiting for SingletonLock release over instant force-kill.
                unlocked = _wait_profile_unlocked(
                    Path(profile), timeout_sec=max(2.0, min(extra_flush, 8.0))
                )
                if not unlocked and _profile_occupied(Path(profile)):
                    _force_release_profile(
                        Path(profile), timeout_sec=CHROME_FORCE_KILL_TIMEOUT_SEC
                    )
                    result["killed"] = True
            except Exception as force_exc:  # noqa: BLE001
                result["force_error"] = str(force_exc)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = str(exc)
        if force and profile is not None:
            try:
                from engine.reach.chrome_runtime import _force_release_profile

                _force_release_profile(
                    Path(profile), timeout_sec=CHROME_FORCE_KILL_TIMEOUT_SEC
                )
                result["killed"] = True
            except Exception as force_exc:  # noqa: BLE001
                result["force_error"] = str(force_exc)
    finally:
        _active_chrome = None
        _active_profile = None
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return result


def _cancelled(run_id: str) -> bool:
    return _cancel_run_id == run_id


def _ensure_chrome(
    run: ReachPublishRun,
    *,
    profile: str,
    url: str,
    customer_id: int,
    platform: str = "",
) -> tuple[Any, str]:
    """Return (ManagedChrome instance, cdp_http). Reuse same profile."""
    global _active_chrome, _active_profile
    from engine.reach.chrome_runtime import forget_managed, start_or_reuse_managed

    if _active_chrome and _active_profile == profile and run.chrome_port:
        return _active_chrome, f"http://127.0.0.1:{run.chrome_port}"

    if _active_chrome:
        flush = (
            CHANNELS_PROFILE_FLUSH_SEC
            if (platform == "channels" or "视频号" in str(_active_profile or ""))
            else None
        )
        try:
            stop_info = _stop_active_chrome(force=True, flush_sec=flush)
            cfg = dict(run.config_json or {})
            cfg["last_chrome_cleanup"] = stop_info
            run.config_json = cfg
        except Exception:
            try:
                if _active_chrome:
                    _active_chrome.stop(timeout_sec=CHROME_STOP_TIMEOUT_SEC)
                    forget_managed(_active_chrome)
            except Exception:
                pass
            _active_chrome = None
            _active_profile = None

    mc, info = start_or_reuse_managed(
        profile,
        url,
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
        headless=False,
        timeout_sec=20.0,
    )
    _active_chrome = mc
    _active_profile = profile
    run.chrome_port = int(info.get("port") or 0)
    run.chrome_profile = profile
    return mc, f"http://127.0.0.1:{run.chrome_port}"


def _wait_cdp_ready(cdp_http: str, timeout: float = 45) -> bool:
    import json
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{cdp_http}/json/version", timeout=2) as r:
                if r.status == 200:
                    with urllib.request.urlopen(f"{cdp_http}/json/list", timeout=2) as r2:
                        tabs = json.loads(r2.read().decode())
                        if any(t.get("type") == "page" for t in tabs):
                            return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _probe_login_ready_across_frames(sess: Any) -> dict[str, Any]:
    """Probe main document and child frames for login / publish readiness.

    视频号助手 keeps the real create form in a wujie micro iframe
    (``/micro/content/post/create``). The shell page body is only
    「视频号 · 助手」and must not be treated as logged-out.
    """
    from engine.reach.cdp_client import CdpError

    probe_js = """(() => {
      const u = location.href || '';
      const t = ((document.body && document.body.innerText) || '').slice(0, 4000);
      const html = (document.documentElement && document.documentElement.outerHTML) || '';
      const login = /扫码登录|手机号登录|短信登录|登录后免费|验证码登录|发送验证码|收不到验证码/.test(t)
        || /\\/login\\b|redirectReason=401|passport|accounts\\./i.test(u);
      const blocked = Array.from(
        document.querySelectorAll('.weui-desktop-dialog, .finder-common-dialog, .common-dialog, [role=dialog]')
      ).some((el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 20 || rect.height < 20) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        return /暂时无法使用该功能/.test(el.innerText || '');
      });
      const form = /短标题|视频描述|添加描述|上传时长|发表动态|作品描述|设置封面|发布笔记|上传视频|选择视频|从手机上传|拖拽视频|立即发表|定时发表|原创声明/.test(t)
        || !!document.querySelector(
          'input[type=file], .input-editor, [data-placeholder=\"添加描述\"], [contenteditable=\"true\"], textarea'
        );
      const avatar = Array.from(document.images || []).some(
        (img) => /finderhead|qlogo\\.cn|avatar/i.test(String(img.src || ''))
      );
      const shellLoggedIn = /channels\\.weixin\\.qq\\.com\\/(platform|micro)\\//.test(u)
        && !login
        && (avatar
            || /finder-page|MicroPost|side-bar|视频号\\s*[·.]\\s*助手/.test(html)
            || /\\/platform\\//.test(u));
      return {
        url: u,
        text: t.slice(0, 500),
        textLen: t.length,
        login: !!login,
        blocked: !!blocked,
        form: !!form,
        shellLoggedIn: !!shellLoggedIn,
        avatar: !!avatar,
      };
    })()"""

    probes: list[dict[str, Any]] = []
    main = sess.evaluate(probe_js) or {}
    if isinstance(main, dict):
        probes.append(main)
    try:
        sess.call("Page.enable")
        tree = sess.call("Page.getFrameTree") or {}
    except CdpError:
        tree = {}

    frames: list[dict[str, Any]] = []

    def _walk(node: dict[str, Any]) -> None:
        fr = node.get("frame") or {}
        if fr.get("id"):
            frames.append(fr)
        for child in node.get("childFrames") or []:
            _walk(child)

    _walk(tree.get("frameTree") or {})
    for fr in frames[1:]:
        fid = fr.get("id")
        if not fid:
            continue
        try:
            world = sess.call(
                "Page.createIsolatedWorld",
                {
                    "frameId": fid,
                    "worldName": "suying_login_probe",
                    "grantUniversalAccess": True,
                },
            )
            ctx = (world or {}).get("executionContextId")
            if not ctx:
                continue
            raw = sess.call(
                "Runtime.evaluate",
                {
                    "expression": probe_js,
                    "contextId": int(ctx),
                    "returnByValue": True,
                },
            )
            if not raw or raw.get("exceptionDetails"):
                continue
            val = (raw.get("result") or {}).get("value")
            if isinstance(val, dict):
                probes.append(val)
        except CdpError:
            continue

    if not probes:
        return {"ready": False, "login": False, "blocked": False, "probes": []}

    form = any(bool(p.get("form")) for p in probes)
    shell = any(bool(p.get("shellLoggedIn")) for p in probes)
    # Pure login frames (URL or text wall) without form — ignore shell noise.
    login_wall_frames: list[dict[str, Any]] = []
    for p in probes:
        url_l = str(p.get("url") or "").lower()
        text_login = bool(p.get("login"))
        url_login = "/login" in url_l or "login.html" in url_l
        if (text_login or url_login) and not p.get("form"):
            login_wall_frames.append(p)
    if form:
        # Real create form present → treat as logged in for readiness.
        login = False
    else:
        login = bool(login_wall_frames) or any(bool(p.get("login")) for p in probes)
    blocked = any(bool(p.get("blocked")) for p in probes)
    best = next((p for p in probes if p.get("form")), None) or next(
        (p for p in probes if p.get("shellLoggedIn") and not p.get("login")), None
    ) or (login_wall_frames[0] if login_wall_frames else probes[0])
    text_len = max(int(p.get("textLen") or 0) for p in probes)
    # form always ready; shell ok only without login wall; no plain textLen fast path.
    ready = (not login) and (form or (shell and not login_wall_frames))
    return {
        "ready": ready,
        "login": login,
        "blocked": blocked and not form,
        "form": form,
        "shellLoggedIn": shell,
        "url": str(best.get("url") or ""),
        "text_len": text_len,
        "probe": best,
        "probes": probes,
        "login_wall_frames": len(login_wall_frames),
    }


def _close_login_and_blank_tabs(cdp_http: str, *, keep_host: str = "") -> int:
    """Close leftover login.html / about:blank tabs so probe does not stick on dead walls."""
    import json
    import urllib.request

    from engine.reach.cdp_client import CdpSession, list_tabs

    closed = 0
    try:
        tabs = list_tabs(cdp_http)
    except Exception:
        try:
            with urllib.request.urlopen(f"{cdp_http}/json/list", timeout=2) as r:
                tabs = json.loads(r.read().decode())
        except Exception:
            return 0
    pages = [t for t in tabs if t.get("type") == "page"]
    keep_id = None
    for t in pages:
        u = str(t.get("url") or "").lower()
        if keep_host and keep_host in u and "/login" not in u:
            keep_id = t.get("id")
            break
    for t in pages:
        u = str(t.get("url") or "").lower()
        tid = t.get("id")
        if keep_id and tid == keep_id:
            continue
        is_junk = (
            u.startswith("about:blank")
            or "/login" in u
            or "login.html" in u
            or u in ("", "about:blank")
        )
        if not is_junk:
            continue
        if len(pages) - closed <= 1:
            break
        try:
            req = urllib.request.Request(
                f"{cdp_http}/json/close/{tid}", method="GET"
            )
            with urllib.request.urlopen(req, timeout=2):
                pass
            closed += 1
        except Exception:
            try:
                ws = t.get("webSocketDebuggerUrl")
                if not ws:
                    continue
                sess = CdpSession(str(ws), timeout=2.0)
                try:
                    sess.call("Page.close")
                    closed += 1
                finally:
                    sess.close()
            except Exception:
                pass
    return closed


def _force_navigate_target(cdp_http: str, url: str) -> bool:
    """Navigate the preferred page target to ``url`` via CDP Page.navigate."""
    if not url:
        return False
    from urllib.parse import urlparse

    from engine.reach.cdp_client import CdpSession, list_tabs

    try:
        tabs = list_tabs(cdp_http)
    except Exception:
        return False
    pages = [
        t
        for t in tabs
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
    ]
    if not pages:
        return False
    host = (urlparse(url).hostname or "").lower()
    pages.sort(
        key=lambda t: (
            0 if host and host in str(t.get("url") or "").lower() else 1,
            0 if "/login" not in str(t.get("url") or "").lower() else 1,
        )
    )
    try:
        sess = CdpSession(str(pages[0]["webSocketDebuggerUrl"]), timeout=8.0)
        try:
            sess.call("Page.enable")
            sess.call("Page.navigate", {"url": url})
            time.sleep(0.8)
            return True
        finally:
            sess.close()
    except Exception:
        return False


def _wait_login_ready(
    cdp_http: str,
    host_hint: str,
    timeout: float,
    *,
    platform: str = "",
    target_url: str = "",
    require_form: bool = False,
) -> dict[str, Any]:
    """Wait until creator page is past login / 401 redirect.

    Chrome often lands on the launch URL first, then XHS 401-redirects to
    ``/login``. URL-only checks therefore false-pass within the first second;
    we require consecutive DOM probes that are not a login wall.

    Channels: pierce wujie child frames; require form for fast path when
    ``require_form``; may force-navigate ``target_url`` during restore window.
    """
    import urllib.request
    from urllib.parse import urlparse

    from engine.reach.cdp_client import CdpError, CdpSession, list_tabs
    from engine.reach.cdp_publish import _dismiss_channels_dialogs

    deadline = time.time() + timeout
    last_url = ""
    last_probe: dict[str, Any] = {}
    ready_streak = 0
    navigated_force = False
    login_tabs_closed = 0
    force_attempted = False
    is_channels = platform == "channels" or "channels.weixin" in (
        host_hint or ""
    )
    if is_channels:
        login_tabs_closed = _close_login_and_blank_tabs(
            cdp_http, keep_host=host_hint or "channels.weixin"
        )
        if target_url:
            navigated_force = _force_navigate_target(cdp_http, target_url)
            force_attempted = True

    while time.time() < deadline:
        tabs: list[dict[str, Any]] = []
        try:
            tabs = list_tabs(cdp_http)
        except Exception:
            try:
                with urllib.request.urlopen(f"{cdp_http}/json/list", timeout=2) as r:
                    import json as _json

                    tabs = _json.loads(r.read().decode())
            except Exception:
                tabs = []
        pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        pages.sort(
            key=lambda t: (
                0 if host_hint and host_hint in (t.get("url") or "") else 1,
                0 if "login" not in (t.get("url") or "").lower() else 1,
            )
        )
        if pages:
            last_url = pages[0].get("url") or ""
            ws = pages[0].get("webSocketDebuggerUrl") or ""
            try:
                sess = CdpSession(str(ws), timeout=5.0)
                try:
                    if is_channels or "channels.weixin" in (last_url or ""):
                        try:
                            _dismiss_channels_dialogs(sess)
                        except Exception:
                            pass
                    merged = _probe_login_ready_across_frames(sess)
                finally:
                    sess.close()
                url = str(merged.get("url") or last_url)
                last_url = url or last_url
                last_probe = merged
                if merged.get("login"):
                    ready_streak = 0
                    # One mid-wait force navigate for channels when still on login.
                    if (
                        is_channels
                        and target_url
                        and not force_attempted
                        and time.time() + 5 < deadline
                    ):
                        navigated_force = (
                            _force_navigate_target(cdp_http, target_url)
                            or navigated_force
                        )
                        force_attempted = True
                    time.sleep(0.5)
                    continue
                if merged.get("blocked"):
                    return {
                        "ok": False,
                        "reason": "feature_blocked",
                        "url": last_url,
                        "probe": last_probe,
                        "navigated_force": navigated_force,
                        "login_tabs_closed": login_tabs_closed,
                        "login_kind": "feature_blocked",
                    }
                low = (last_url or "").lower()
                host_ok = bool(host_hint and host_hint in low) or (
                    (last_url or "").startswith("http") and "/login" not in low
                )
                has_form = bool(merged.get("form"))
                if host_ok and merged.get("ready"):
                    if require_form or is_channels:
                        # Channels / strict: only form proves publisher ready.
                        # Do NOT early-exit form_not_ready while budget remains —
                        # SPA/wujie often needs 10–40s; field data showed ~1.8s false skip.
                        if has_form:
                            return {
                                "ok": True,
                                "url": last_url,
                                "probe": last_probe,
                                "fast": ready_streak <= 2,
                                "navigated_force": navigated_force,
                                "login_tabs_closed": login_tabs_closed,
                                "login_kind": "session_ready",
                            }
                        ready_streak += 1
                        # Shell-only create page: after ~8–10s of consecutive ready
                        # probes without form, hand off to upload wait (same as end).
                        if (
                            is_channels
                            and ready_streak >= 15
                            and last_probe.get("shellLoggedIn")
                            and not last_probe.get("login")
                            and not last_probe.get("blocked")
                        ):
                            return {
                                "ok": True,
                                "url": last_url,
                                "probe": last_probe,
                                "fast": False,
                                "navigated_force": navigated_force,
                                "login_tabs_closed": login_tabs_closed,
                                "login_kind": "session_ready_shell",
                                "reason": "session_ready_shell",
                            }
                        # Remount create page every ~5s while form missing.
                        if (
                            is_channels
                            and target_url
                            and ready_streak > 0
                            and ready_streak % 8 == 0
                            and time.time() + 2 < deadline
                        ):
                            navigated_force = (
                                _force_navigate_target(cdp_http, target_url)
                                or navigated_force
                            )
                        time.sleep(0.55)
                        continue
                    else:
                        if merged.get("form") or merged.get("shellLoggedIn"):
                            return {
                                "ok": True,
                                "url": last_url,
                                "probe": last_probe,
                                "fast": True,
                                "navigated_force": navigated_force,
                                "login_tabs_closed": login_tabs_closed,
                                "login_kind": "session_ready",
                            }
                        ready_streak += 1
                        if ready_streak >= 2:
                            return {
                                "ok": True,
                                "url": last_url,
                                "probe": last_probe,
                                "navigated_force": navigated_force,
                                "login_tabs_closed": login_tabs_closed,
                                "login_kind": "session_ready",
                            }
                else:
                    ready_streak = 0
            except (CdpError, OSError, TimeoutError) as exc:
                last_probe = {"error": str(exc), "url": last_url}
                ready_streak = 0
        low = (last_url or "").lower()
        if any(
            x in low
            for x in (
                "passport",
                "/login",
                "accounts.",
                "/captcha",
                "sso.",
                "redirectreason=401",
            )
        ):
            ready_streak = 0
            time.sleep(0.4)
            continue
        time.sleep(0.4)
    # Channels truth: shell on post/create + no login wall often means session OK while
    # wujie form text is still empty to CDP (field: 40s wait still form=false; same
    # profiles published same day with login_ms≈55 when shell-pass existed). Hand off to
    # upload stage which waits for file input / preview.
    low_end = (last_url or "").lower()
    on_login_wall = (
        "/login" in low_end
        or "login.html" in low_end
        or bool(last_probe.get("login"))
    )
    if (
        is_channels
        and not on_login_wall
        and last_probe.get("shellLoggedIn")
        and not last_probe.get("blocked")
    ):
        return {
            "ok": True,
            "url": last_url,
            "probe": last_probe,
            "fast": False,
            "navigated_force": navigated_force,
            "login_tabs_closed": login_tabs_closed,
            "login_kind": "session_ready_shell",
            "reason": "session_ready_shell",
        }
    login_kind = "true_login_wall" if on_login_wall else "login_timeout"
    if last_probe.get("shellLoggedIn") and not last_probe.get("form"):
        login_kind = "form_not_ready"
    return {
        "ok": False,
        "reason": login_kind,
        "url": last_url,
        "probe": last_probe,
        "navigated_force": navigated_force,
        "login_tabs_closed": login_tabs_closed,
        "login_kind": login_kind,
    }


def _profile_cookie_state(profile_name: str, platform: str, customer_id: int) -> str:
    """Non-secret cookie presence for channels login restore decisions."""
    try:
        from engine.reach.browser import (
            inspect_chrome_profile_storage,
            resolve_chrome_user_data_dir,
        )

        path = resolve_chrome_user_data_dir(
            profile_name,
            customer_id=customer_id,
            business_scope=SCOPE_VIDEO,
            require_existing=False,
        )
        info = inspect_chrome_profile_storage(path, platform)
        return str(info.get("login_data_state") or "unknown")
    except Exception:
        return "unknown"


def _set_item_phase(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    phase: str,
) -> None:
    started_at = item.started_at or _now()

    def _apply() -> None:
        item.phase = phase
        item.started_at = started_at
        item.updated_at = _now()
        run.heartbeat_at = _now()
        run.updated_at = _now()

    _apply()
    _commit_with_retry(session, reapply=_apply)


def _consume_item_skip(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
) -> dict[str, Any] | None:
    session.refresh(run)
    session.refresh(item)
    if run.requested_action == "cancel":
        return {"ok": False, "cancelled": True, "phase": "cancelled"}
    if item.requested_action != "skip":
        return None
    pub = (item.evidence_json or {}).get("publish_result") or {}
    if pub.get("pub_clicked"):
        return {
            "ok": False,
            "phase": "outcome_unknown",
            "error": "已点击发布，必须先确认结果",
            "pub": pub,
        }
    mode = item.retry_mode or "manual"
    item.phase = "deferred" if mode != "none" else "skipped"
    item.outcome = "deferred" if mode != "none" else "skipped"
    item.requested_action = ""
    item.retry_after = item.retry_after or (
        _now() + timedelta(minutes=5) if mode == "auto" else None
    )
    item.finished_at = _now()
    item.updated_at = _now()
    _audit_publish(
        session,
        run,
        "publish_item_deferred",
        "当前条已安全跳过，继续后续任务",
        item=item,
        stage="control",
        level="warning",
        details={"retry_mode": mode},
    )
    session.commit()
    return {"ok": False, "phase": item.phase, "deferred": True}


def _execute_item(
    session: Session,
    run: ReachPublishRun,
    item: ReachPublishRunItem,
    *,
    cdp_http: str,
) -> dict[str, Any]:
    from engine.reach.cdp_publish import publish_via_cdp
    from engine.reach.publish_assets import require_publish_assets
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.cdp_client import find_tab_ws
    from engine.reach.cdp_publish import PLATFORM_URL_HINT
    from engine.runtime.pause_coordinator import assert_runtime_active

    plat = item.platform
    data_root = resolve_cover_store_for_settings()
    pack_dir = Path(str(item.pack_dir or ""))
    body = ""
    if item.queue_id:
        q = get_item(session, int(item.queue_id), customer_id=run.customer_id)
        body = (q.body if q else "") or ""

    _set_item_phase(session, run, item, "uploading")
    _audit_publish(
        session, run, "publish_upload_started", "开始上传视频", item=item, stage="upload"
    )
    session.commit()
    controlled = _consume_item_skip(session, run, item)
    if controlled:
        return controlled

    try:
        assert_runtime_active("reach_publish_upload")
    except Exception as exc:
        return {
            "ok": False,
            "system_interrupted": True,
            "phase": "interrupted_system",
            "error": str(exc),
        }
    try:
        from engine.reach.publish_sources import (
            has_current_approved_review,
            has_verified_ready_gate,
        )

        output = session.get(RenderOutput, item.output_id) if item.output_id else None
        job = session.get(Job, output.job_id) if output else None
        if (
            not output
            or not job
            or job.customer_id != run.customer_id
            or output.state != "ready"
            or not has_verified_ready_gate(
                output.qc_json if isinstance(output.qc_json, dict) else None
            )
            or not has_current_approved_review(session, output.id)
        ):
            raise ValueError("发布成片必须属于当前客户且有唯一当前 approved 决策")
        assets = require_publish_assets(
            platform=plat,
            pack_dir=pack_dir,
            data_root=data_root,
            title=item.title,
            body=body,
        )
    except Exception as exc:
        _audit_publish(
            session,
            run,
            "publish_ready_gate_failed",
            str(exc),
            item=item,
            stage="ready_gate",
            level="error",
        )
        return {"ok": False, "retryable": True, "error": str(exc), "phase": "gate_failed"}

    _set_item_phase(session, run, item, "filling_copy")
    _audit_publish(
        session, run, "publish_copy_started", "开始填写平台文案", item=item, stage="copy"
    )
    session.commit()
    hint = PLATFORM_URL_HINT.get(plat) or plat
    try:
        ws, _ = find_tab_ws(url_substr=hint, cdp_http=cdp_http)
    except Exception as exc:
        _audit_publish(
            session,
            run,
            "publish_cdp_unavailable",
            str(exc),
            item=item,
            stage="cdp",
            level="error",
        )
        return {"ok": False, "retryable": True, "error": str(exc), "phase": "cdp_unavailable"}

    _set_item_phase(session, run, item, "setting_cover")
    _audit_publish(
        session, run, "publish_cover_started", "开始设置封面", item=item, stage="cover"
    )
    session.commit()
    controlled = _consume_item_skip(session, run, item)
    if controlled:
        return controlled
    try:
        assert_runtime_active("reach_publish_submit")
    except Exception as exc:
        return {
            "ok": False,
            "system_interrupted": True,
            "phase": "interrupted_system",
            "error": str(exc),
        }
    pub = publish_via_cdp(
        platform=plat,
        pack_dir=pack_dir,
        title=assets["title"],
        body=assets["body"],
        click_publish=True,
        cdp_http=cdp_http,
        data_root=data_root,
        template_id=None,
        upload_video=item.note != "resume_existing_form",
        reuse_existing_form=item.note == "resume_existing_form",
    )
    # Merge runner-side stage timings (chrome/login) with CDP-internal timings.
    prev_ev = dict(item.evidence_json or {})
    runner_timings = dict(prev_ev.get("timings") or {})
    cdp_timings = dict(pub.get("timings") or {})
    merged_timings = {**runner_timings, **cdp_timings}
    if merged_timings:
        pub = {**pub, "timings": merged_timings}
    item.evidence_json = {
        **prev_ev,
        "publish_result": pub,
        "timings": merged_timings,
    }
    _set_item_phase(session, run, item, "submitting")
    _audit_publish(
        session,
        run,
        "publish_submitted",
        "已执行发布提交",
        item=item,
        stage="submit",
        evidence={"phase": pub.get("phase"), "pub_clicked": pub.get("pub_clicked")},
    )
    session.commit()
    controlled = _consume_item_skip(session, run, item)
    if controlled:
        return controlled

    if pub.get("need_human") and pub.get("phase") == "need_sms_verify":
        return {"ok": False, "human": True, "phase": "paused_human", "pub": pub}
    if pub.get("need_human") and not pub.get("pub_clicked"):
        return {"ok": False, "human": True, "phase": "paused_human", "pub": pub}

    _set_item_phase(session, run, item, "verifying")
    _audit_publish(
        session, run, "publish_verify_started", "开始核验发布结果", item=item, stage="verify"
    )
    session.commit()
    try:
        assert_runtime_active("reach_publish_verify")
    except Exception as exc:
        return {
            "ok": False,
            "system_interrupted": True,
            "phase": "interrupted_system",
            "error": str(exc),
            "pub": pub,
        }
    if is_verified_success(pub):
        return {"ok": True, "phase": "published", "pub": pub}

    recheck = recheck_or_pause(platform=plat, title=item.title, cdp_http=cdp_http, pub_result=pub)
    return {"ok": recheck.get("phase") == "published", **recheck, "pub": pub}


def _worker_loop(run_id: str) -> None:
    global _cancel_run_id, _active_chrome, _active_profile, _worker, _worker_run_id
    session = get_session()
    run: ReachPublishRun | None = None
    operation_lease = None
    publish_token: str | None = None
    try:
        run = get_run(session, run_id)
        if not run:
            return
        if run.status in RUN_TERMINAL:
            return
        from engine.runtime.pause_coordinator import coordinator as pause_coordinator

        if not pause_coordinator.should_claim_jobs():
            run.status = "interrupted_system"
            run.error = "system_pause"
            run.finished_at = _now()
            run.updated_at = _now()
            session.commit()
            return

        from engine.runtime.resource_gate import gate as resource_gate

        publish_token = f"publish:{run_id}"
        resource_gate.sync_from_settings()
        try:
            from engine.runtime.workload_yield import acquire_workload

            acquire_workload(publish_token)
        except Exception:
            log.exception("workload yield acquire failed")
        if not resource_gate.try_acquire("publish", publish_token):
            for item in list_run_items(session, run):
                if item.phase not in ITEM_TERMINAL:
                    item.phase = "deferred"
                    item.outcome = "deferred"
                    item.retry_mode = "auto"
                    item.retry_after = _now() + timedelta(seconds=15)
                    item.error = "resource_gate_publish_busy"
                    item.finished_at = _now()
                    item.updated_at = _now()
            run.status = "cancelled"
            run.error = "resource_gate_publish_busy"
            run.finished_at = _now()
            run.updated_at = _now()
            _append_run_log(run, "resource_gate_publish_busy")
            session.commit()
            return

        from engine.reach.chrome_runtime import (
            acquire_operation,
            clear_publish_active,
            mark_publish_active,
        )

        publish_owner = f"publish_run:{run_id}"
        mark_publish_active(publish_owner)
        try:
            operation_lease = acquire_operation(
                publish_owner,
                wait_timeout_sec=PUBLISH_LEASE_WAIT_SEC,
            )
        except Exception as exc:
            clear_publish_active(publish_owner)
            resource_gate.release("publish", publish_token)
            for item in list_run_items(session, run):
                if item.phase not in ITEM_TERMINAL:
                    item.phase = "deferred"
                    item.outcome = "deferred"
                    item.retry_mode = "auto"
                    item.retry_after = _now() + timedelta(seconds=30)
                    item.error = str(exc)
                    item.finished_at = _now()
                    item.updated_at = _now()
            run.status = "cancelled"
            run.error = str(exc)
            run.finished_at = _now()
            run.updated_at = _now()
            _append_run_log(run, "chrome_operation_busy_deferred")
            session.commit()
            return

        run.status = "running"
        run.started_at = run.started_at or _now()
        run.heartbeat_at = _now()
        run.requested_action = ""
        run.updated_at = _now()
        if not (run.config_json or {}).get("round_attempt"):
            _set_run_round_attempt(run, 1)
        _audit_publish(
            session, run, "publish_run_started", "发布批次开始执行", stage="start"
        )
        session.commit()

        while True:
          items = [
              i
              for i in list_run_items(session, run)
              if i.phase not in ITEM_TERMINAL and i.phase != SOFT_SKIP_PHASE
          ]
          # Also process freshly requeued items (phase=queued)
          items = [i for i in list_run_items(session, run) if i.phase == "queued"] or items
          # Prefer platform then profile continuity to reduce Chrome thrash.
          items = sorted(
              items,
              key=lambda i: (
                  str(i.platform or ""),
                  str(i.chrome_profile or ""),
                  int(i.ordinal or 0),
                  int(i.id or 0),
              ),
          )
          round_done_early = False
          for item in items:
            session.refresh(run)
            if _cancelled(run_id) or run.requested_action == "cancel":
                _finish_cancelled(session, run)
                session.commit()
                break
            if run.circuit_break:
                run.status = "failed"
                run.error = "circuit_break"
                break
            if run.stop_after_current and item.phase in ITEM_TERMINAL:
                run.status = "completed"
                break
            if item.phase in ITEM_TERMINAL:
                continue
            if item.phase in HUMAN_PHASES:
                run.status = item.phase
                session.commit()
                return

            try:
                from engine.reach.publication_lifecycle import (
                    assert_current_assets,
                    claim_target_for_submission,
                )

                assert_current_assets(
                    session,
                    customer_id=run.customer_id,
                    output_id=item.output_id,
                    pack_dir=item.pack_dir,
                    video_path=item.video_path,
                )
                claim_target_for_submission(
                    session,
                    group_id=int(item.publication_group_id or 0),
                    target_id=int(item.publication_target_id or 0),
                    allow_existing=True,
                )
                session.commit()
            except ValueError as exc:
                item.phase = "failed"
                item.outcome = "failed"
                item.error = str(exc)
                item.finished_at = _now()
                item.updated_at = _now()
                run.consecutive_failures += 1
                session.commit()
                continue

            _set_item_phase(session, run, item, "switching_profile")
            _audit_publish(
                session,
                run,
                "publish_profile_switch",
                f"切换到账号 {item.chrome_profile}",
                item=item,
                stage="account",
            )
            _commit_with_retry(session)
            controlled = _consume_item_skip(session, run, item)
            if controlled:
                if controlled.get("cancelled"):
                    _finish_cancelled(session, run)
                    _commit_with_retry(session)
                    break
                continue

            plat = item.platform
            entry = entry_for_platform(plat)
            open_url = UPLOAD_URLS.get(plat) or (UPLOAD_URL if plat == "douyin" else entry["url"])
            try:
                # Keep SQLite transaction closed while Chrome boots / CDP waits.
                t_chrome0 = time.monotonic()
                _mc, cdp_http = _ensure_chrome(
                    run,
                    profile=item.chrome_profile,
                    url=open_url,
                    customer_id=run.customer_id,
                    platform=str(plat or ""),
                )
                chrome_ready_ms = int((time.monotonic() - t_chrome0) * 1000)
                ev0 = dict(item.evidence_json or {})
                ev0["timings"] = {**(ev0.get("timings") or {}), "chrome_ready_ms": chrome_ready_ms}
                item.evidence_json = ev0
                run.updated_at = _now()
                run.heartbeat_at = _now()
                _commit_with_retry(session)
                _audit_publish(
                    session,
                    run,
                    "publish_chrome_ready",
                    "受管 Chrome 已就绪",
                    item=item,
                    stage="chrome",
                    details={"cdp_port": run.chrome_port, "chrome_ready_ms": chrome_ready_ms},
                )
                _commit_with_retry(session)
            except Exception as exc:
                message = str(exc)
                if "Chrome profile 已被占用" in message or "退出码 0" in message:
                    item.phase = "paused_human"
                    item.error = "该账号 Chrome 窗口正在占用登录目录；关闭对应官方页后点继续"
                    item.updated_at = _now()
                    run.status = "paused_human"
                    run.error = item.error
                    run.updated_at = _now()
                    _audit_publish(
                        session,
                        run,
                        "publish_profile_busy",
                        item.error,
                        item=item,
                        stage="chrome",
                        level="warning",
                    )
                    _commit_with_retry(session)
                    _alert_human(
                        session,
                        run,
                        item,
                        kind="profile_busy",
                        summary="账号官方页占用登录目录，请关闭该账号 Chrome 窗口后继续",
                    )
                    return
                item.phase = "failed"
                item.error = message
                item.finished_at = _now()
                run.consecutive_failures += 1
                if run.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    run.circuit_break = True
                _audit_publish(
                    session,
                    run,
                    "publish_chrome_failed",
                    message,
                    item=item,
                    stage="chrome",
                    level="error",
                )
                _commit_with_retry(session)
                continue

            from urllib.parse import urlparse

            host = urlparse(open_url).netloc.replace("www.", "")
            item.phase = "waiting_login"
            item.updated_at = _now()
            _commit_with_retry(session)
            _audit_publish(
                session,
                run,
                "publish_login_check",
                "检查账号登录状态",
                item=item,
                stage="login",
            )
            _commit_with_retry(session)
            plat_s = str(plat or "")
            cookie_state = _profile_cookie_state(
                str(item.chrome_profile or ""), plat_s, int(run.customer_id)
            )
            budget = SOFT_SKIP_BUDGET_SEC
            if plat_s == "channels" and cookie_state == "present":
                budget = CHANNELS_LOGIN_RESTORE_BUDGET_SEC
            t_login0 = time.monotonic()
            login = _wait_login_ready(
                cdp_http,
                host.split(":")[0],
                timeout=budget,
                platform=plat_s,
                target_url=open_url,
                require_form=(plat_s == "channels"),
            )
            login_ms = int((time.monotonic() - t_login0) * 1000)
            probe = login.get("probe") if isinstance(login.get("probe"), dict) else {}
            ev_login = dict(item.evidence_json or {})
            login_kind = str(
                login.get("login_kind") or login.get("reason") or "unknown"
            )
            ev_login["timings"] = {
                **(ev_login.get("timings") or {}),
                "login_ms": login_ms,
                "login_fast": bool(login.get("fast")),
                "login_kind": login_kind,
                "cookie_state": cookie_state,
                "navigated_force": bool(login.get("navigated_force")),
                "login_tabs_closed": int(login.get("login_tabs_closed") or 0),
                "probe_form": bool(probe.get("form")),
                "probe_shell": bool(probe.get("shellLoggedIn")),
            }
            login["cookie_state"] = cookie_state
            item.evidence_json = ev_login
            if _cancelled(run_id):
                return
            if not login.get("ok"):
                reason_code = str(login.get("reason") or login_kind or "")
                if reason_code == "feature_blocked":
                    reason = (
                        f"视频号暂时无法发表（{login.get('url') or '未知页面'}）；"
                        f"已跳过本条，整轮结束后自动重试（第 {_run_round_attempt(run)}/{MAX_ROUND_ATTEMPTS} 轮）"
                    )
                    _soft_skip_item(
                        session,
                        run,
                        item,
                        reason=reason,
                        stage="login",
                        kind="feature_blocked",
                    )
                    session.commit()
                    continue
                if reason_code == "form_not_ready":
                    reason = (
                        f"已登录但发表表单未就绪（{login.get('url') or '未知页面'}）；"
                        f"已跳过本条，稍后自动再试（第 {_run_round_attempt(run)}/{MAX_ROUND_ATTEMPTS} 轮）"
                    )
                    _soft_skip_item(
                        session,
                        run,
                        item,
                        reason=reason,
                        stage="login",
                        kind="form_not_ready",
                    )
                    session.commit()
                    continue
                # True login wall: bounded human grace, then fail-forward (other accounts continue).
                reason = (
                    f"视频号需要重新扫码登录（{login.get('url') or '未知页面'}）；"
                    f"请在约 {int(login_wall_grace_sec())} 秒内扫码后点继续，"
                    "超时将跳过本账号并继续其它发布"
                )
                _halt_for_true_login_wall(
                    session, run, item, reason=reason, login=login
                )
                session.commit()
                wait_res = _await_login_wall_resolution(session, run, item)
                if wait_res == "cancelled":
                    return
                if wait_res == "deferred":
                    # Free login page immediately so next profile can start clean.
                    try:
                        _stop_active_chrome(force=True)
                    except Exception:
                        pass
                    continue
                # Resumed: re-check login once more (short budget) before upload.
                try:
                    session.refresh(item)
                    session.refresh(run)
                except Exception:
                    pass
                run.status = "running"
                run.error = ""
                item.phase = "waiting_login"
                item.updated_at = _now()
                _commit_with_retry(session)
                login = _wait_login_ready(
                    cdp_http,
                    host.split(":")[0],
                    timeout=min(budget, SOFT_SKIP_BUDGET_SEC + 5.0),
                    platform=plat_s,
                    target_url=open_url,
                    require_form=(plat_s == "channels"),
                )
                if not login.get("ok"):
                    reason2 = (
                        f"扫码后续检仍未通过（{login.get('url') or '未知页面'}）；"
                        "已跳过该账号并排队补发"
                    )
                    _defer_true_login_wall_fail_forward(
                        session, run, item, reason=reason2
                    )
                    session.commit()
                    try:
                        _stop_active_chrome(force=True)
                    except Exception:
                        pass
                    continue
            # Channels secondary form gate: only hard-stop when we never had shell either.
            # form may appear only after upload inject (see historical login_ms≈55 successes).
            if plat_s == "channels" and not probe.get("form"):
                if probe.get("shellLoggedIn") or cookie_state == "present":
                    ev_login["timings"] = {
                        **(ev_login.get("timings") or {}),
                        "probe_form": False,
                        "login_kind": str(login.get("login_kind") or "session_ready_shell"),
                        "secondary_form_skipped": True,
                        "secondary_skip_reason": "shell_or_cookie_present",
                    }
                    item.evidence_json = ev_login
                    _commit_with_retry(session)
                else:
                    second = _wait_login_ready(
                        cdp_http,
                        host.split(":")[0],
                        timeout=8.0,
                        platform=plat_s,
                        target_url=open_url,
                        require_form=True,
                    )
                    if not second.get("ok") or not (
                        (second.get("probe") or {}).get("form")
                        or (second.get("probe") or {}).get("shellLoggedIn")
                    ):
                        reason = (
                            f"发表表单二次核验未通过（{second.get('url') or login.get('url') or '未知'}）；"
                            f"请在约 {int(login_wall_grace_sec())} 秒内确认登录后点继续，"
                            "超时将跳过该账号"
                        )
                        second["cookie_state"] = cookie_state
                        _halt_for_true_login_wall(
                            session,
                            run,
                            item,
                            reason=reason,
                            login={**second, "login_kind": "post_probe_false_pass"},
                        )
                        session.commit()
                        wait_res = _await_login_wall_resolution(session, run, item)
                        if wait_res == "cancelled":
                            return
                        if wait_res == "deferred":
                            try:
                                _stop_active_chrome(force=True)
                            except Exception:
                                pass
                            continue
                        # Resume accepted — treat as form-ready shell path.
                        probe = second.get("probe") or probe
                        login = {**login, "ok": True, "probe": probe}
                        ev_login["timings"] = {
                            **(ev_login.get("timings") or {}),
                            "probe_form": bool(probe.get("form")),
                            "login_kind": "post_probe_resumed",
                            "secondary_form_resumed": True,
                        }
                        item.evidence_json = ev_login
                        item.phase = "waiting_login"
                        run.status = "running"
                        _commit_with_retry(session)
                        # fall through to upload after block
                    else:
                        probe = second.get("probe") or probe
                        login = second
                        ev_login["timings"] = {
                            **(ev_login.get("timings") or {}),
                            "probe_form": bool(probe.get("form")),
                            "login_kind": str(
                                login.get("login_kind") or "session_ready"
                            ),
                            "secondary_form_ok": True,
                        }
                        item.evidence_json = ev_login
                        _commit_with_retry(session)

            attempt = 0
            while attempt <= item.max_retries:
                if _cancelled(run_id):
                    break
                try:
                    result = _execute_item(session, run, item, cdp_http=cdp_http)
                except Exception as exc:
                    message = str(exc).lower()
                    if "database is locked" not in message and "locked" not in message:
                        raise
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    log.warning(
                        "publish item deferred after sqlite lock run_id=%s item_id=%s",
                        run_id,
                        item.id,
                        exc_info=True,
                    )
                    _soft_skip_item(
                        session,
                        run,
                        item,
                        reason=(
                            "数据库繁忙导致本条中断；已跳过本条，整轮结束后可补发"
                        ),
                        stage="db",
                        kind="sqlite_locked",
                    )
                    _commit_with_retry(session)
                    break
                phase = result.get("phase") or ("published" if result.get("ok") else "failed")
                if result.get("cancelled"):
                    _finish_cancelled(session, run)
                    session.commit()
                    return
                if result.get("deferred") or phase in ("deferred", "skipped"):
                    break
                if result.get("system_interrupted"):
                    item.phase = "paused_human"
                    item.error = result.get("error") or "系统暂停中断"
                    run.status = "interrupted_system"
                    run.error = "系统暂停中断，禁止自动重发"
                    run.finished_at = _now()
                    _audit_publish(
                        session,
                        run,
                        "publish_system_interrupted",
                        item.error,
                        item=item,
                        stage="system",
                        level="warning",
                    )
                    session.commit()
                    return
                if result.get("human"):
                    # GCustomerUX 1B: do not pause whole batch — soft-skip and continue round.
                    pub = result.get("pub") or {}
                    phase_name = str(pub.get("phase") or "")
                    raw_err = str(pub.get("error") or result.get("error") or "")
                    if phase_name == "need_login" or raw_err in ("need_login", "no_file_input"):
                        reason = (
                            f"账号未登录创作者中心（{raw_err or phase_name}）；"
                            "已停在当前窗口，请扫码后点继续"
                        )
                        _halt_for_true_login_wall(
                            session,
                            run,
                            item,
                            reason=reason,
                            login={
                                "url": (pub.get("url") or ""),
                                "login_kind": "post_probe_false_pass",
                                "probe": {"form": False},
                                "cookie_state": _profile_cookie_state(
                                    str(item.chrome_profile or ""),
                                    str(item.platform or ""),
                                    int(run.customer_id),
                                ),
                            },
                        )
                        session.commit()
                        return
                    if phase_name in ("need_sms_verify", "need_human"):
                        kind = "verification_required"
                        stage = "verification"
                    else:
                        kind = "verification_required"
                        stage = "verification"
                    reason = (
                        f"{raw_err or phase_name or '发布卡住'}；"
                        f"已跳过本条，整轮结束后自动重试（第 {_run_round_attempt(run)}/{MAX_ROUND_ATTEMPTS} 轮）"
                    )
                    _soft_skip_item(
                        session, run, item, reason=reason, stage=stage, kind=kind
                    )
                    session.commit()
                    break
                if phase == "published":
                    item.phase = "published"
                    item.outcome = "published"
                    item.finished_at = _now()
                    run.consecutive_failures = 0
                    if item.queue_id:
                        q = get_item(session, int(item.queue_id), customer_id=run.customer_id)
                        if q and q.status != "published":
                            _mark_queue_published(
                                session, q, note="batch_verified"
                            )
                    from engine.reach.publication_lifecycle import record_target_outcome

                    retirement = record_target_outcome(
                        session,
                        group_id=int(item.publication_group_id or 0),
                        target_id=int(item.publication_target_id or 0),
                        outcome="published",
                        evidence=result,
                        note="batch_verified",
                    )
                    evidence = dict(item.evidence_json or {})
                    evidence["retirement"] = retirement
                    item.evidence_json = evidence
                    _audit_publish(
                        session,
                        run,
                        "publish_item_verified",
                        "作品发布成功并通过核验",
                        item=item,
                        stage="verify",
                        evidence={
                            "phase": phase,
                            "verification": result.get("verification") or {},
                        },
                    )
                    session.commit()
                    break
                if phase == "outcome_unknown":
                    item.phase = "outcome_unknown"
                    run.status = "outcome_unknown"
                    from engine.reach.publication_lifecycle import record_target_outcome

                    record_target_outcome(
                        session,
                        group_id=int(item.publication_group_id or 0),
                        target_id=int(item.publication_target_id or 0),
                        outcome="outcome_unknown",
                        evidence=result,
                        note="verification_unknown",
                    )
                    _audit_publish(
                        session,
                        run,
                        "publish_outcome_unknown",
                        "发布结果仍不明确，已禁止盲目重发",
                        item=item,
                        stage="verify",
                        level="warning",
                        evidence={"phase": phase},
                    )
                    session.commit()
                    return
                attempt += 1
                item.retry_count = attempt
                if attempt > item.max_retries:
                    from engine.reach.publication_lifecycle import release_unsubmitted_target

                    pub_result = (item.evidence_json or {}).get("publish_result") or {}
                    if not pub_result.get("pub_clicked"):
                        release_unsubmitted_target(
                            session,
                            group_id=item.publication_group_id,
                            target_id=item.publication_target_id,
                        )
                    item.phase = "skipped"
                    item.outcome = "skipped"
                    item.error = result.get("error") or "重试耗尽"
                    item.finished_at = _now()
                    run.consecutive_failures += 1
                    if run.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        run.circuit_break = True
                        run.status = "failed"
                        run.error = "连续3条失败熔断"
                    _audit_publish(
                        session,
                        run,
                        "publish_item_skipped",
                        item.error,
                        item=item,
                        stage=phase,
                        level="error",
                    )
                    session.commit()
                    break
                _audit_publish(
                    session,
                    run,
                    "publish_item_retry",
                    result.get("error") or f"{phase} 可恢复失败，准备重试",
                    item=item,
                    stage=phase,
                    level="warning",
                    details={"next_retry": attempt},
                )
                session.commit()
                time.sleep(1.5)

            if run.stop_after_current:
                run.status = "completed"
                session.commit()
                round_done_early = True
                break

          if round_done_early or run.status in RUN_TERMINAL:
              break

          session.refresh(run)
          soft_stuck = [i for i in list_run_items(session, run) if i.phase == SOFT_SKIP_PHASE]
          round_n = _run_round_attempt(run)
          requeueable = [
              i
              for i in soft_stuck
              if _soft_skip_kind(i) in REQUEUEABLE_SOFT_KINDS
              or (
                  _soft_skip_kind(i)
                  and _soft_skip_kind(i) not in LOGIN_WALL_KINDS
              )
          ]
          if requeueable and round_n < MAX_ROUND_ATTEMPTS and run.status == "running":
              requeued = _requeue_soft_skipped_for_next_round(session, run)
              if requeued:
                  _set_run_round_attempt(run, round_n + 1)
                  run.status = "running"
                  run.finished_at = None
                  run.updated_at = _now()
                  _append_run_log(
                      run,
                      "round_retry",
                      from_round=round_n,
                      to_round=round_n + 1,
                      requeued=requeued,
                  )
                  _audit_publish(
                      session,
                      run,
                      "publish_round_retry",
                      f"第 {round_n} 轮结束，自动重试 {requeued} 条（进入第 {round_n + 1}/{MAX_ROUND_ATTEMPTS} 轮）",
                      stage="completion",
                      level="warning",
                      details={"requeued": requeued, "round_attempt": round_n + 1},
                  )
                  session.commit()
                  continue
          if soft_stuck and run.status == "running":
              # Non-requeueable stuck (e.g. leftover login wall soft-skips) → fail those only.
              wall_only = all(
                  _soft_skip_kind(i) in LOGIN_WALL_KINDS or not _soft_skip_kind(i)
                  for i in soft_stuck
              )
              if wall_only and any(
                  _soft_skip_kind(i) in LOGIN_WALL_KINDS for i in soft_stuck
              ):
                  # Fail-forward leftovers: defer wall softs instead of pinning waiting_login.
                  for stuck_item in soft_stuck:
                      if _soft_skip_kind(stuck_item) in LOGIN_WALL_KINDS:
                          try:
                              _defer_item_pre_submit(
                                  session,
                                  run,
                                  stuck_item,
                                  reason=stuck_item.error
                                  or "登录墙已跳过待补发",
                                  retry_mode="auto",
                                  stage="login_wall_soft_cleanup",
                              )
                          except ValueError:
                              stuck_item.phase = "failed"
                              stuck_item.outcome = "failed"
                              stuck_item.updated_at = _now()
                  run.status = "running"
                  run.updated_at = _now()
              else:
                  _escalate_soft_skip_exhausted(session, run)
                  for stuck_item in soft_stuck:
                      stuck_item.phase = "failed"
                      stuck_item.outcome = "failed"
                      stuck_item.updated_at = _now()
                  run.status = "failed"
                  run.finished_at = _now()
          break

        session.refresh(run)
        if run.status == "running":
            pending = [
                i
                for i in list_run_items(session, run)
                if i.phase not in ITEM_TERMINAL and i.phase != SOFT_SKIP_PHASE
            ]
            if pending:
                run.status = (
                    "paused_human"
                    if any(i.phase in HUMAN_PHASES for i in pending)
                    else "running"
                )
            else:
                failed = [i for i in list_run_items(session, run) if i.phase == "failed"]
                run.status = "failed" if failed else "completed"
                if failed and not run.error:
                    run.error = failed[0].error or "发布条目失败"
                run.finished_at = _now()
        # waiting_login must not survive worker end without defer path (fail-forward safety).
        if run.status == "waiting_login":
            wall_item = next(
                (
                    i
                    for i in list_run_items(session, run)
                    if i.phase == "waiting_login"
                ),
                None,
            )
            if wall_item is not None:
                _defer_true_login_wall_fail_forward(
                    session,
                    run,
                    wall_item,
                    reason=wall_item.error or "登录等待结束未续跑",
                )
            pending = [
                i
                for i in list_run_items(session, run)
                if i.phase not in ITEM_TERMINAL and i.phase != SOFT_SKIP_PHASE
            ]
            failed = [i for i in list_run_items(session, run) if i.phase == "failed"]
            if pending:
                run.status = "running"
            else:
                run.status = "failed" if failed else "completed"
                run.finished_at = _now()
        run.updated_at = _now()
        if run.status in ("completed", "failed"):
            try:
                _record_run_finish_summary(session, run)
            except Exception:
                log.exception("run finish summary failed run_id=%s", run_id)
            _audit_publish(
                session,
                run,
                "publish_run_finished",
                "发布批次完成" if run.status == "completed" else "发布批次失败",
                stage="completion",
                level="info" if run.status == "completed" else "error",
                details={
                    "status": run.status,
                    "succeeded": sum(
                        item.phase == "published" for item in list_run_items(session, run)
                    ),
                    "failed": sum(
                        item.phase == "failed" for item in list_run_items(session, run)
                    ),
                    "skipped": sum(
                        item.phase == "skipped" for item in list_run_items(session, run)
                    ),
                    "deferred": sum(
                        item.phase == "deferred" for item in list_run_items(session, run)
                    ),
                    "round_attempt": _run_round_attempt(run),
                },
            )
        # Notify for immediate AND schedule (silent schedule completion looked abandoned).
        if run.status in ("completed", "failed") and run.launch_mode in (
            "immediate",
            "manual",
            "deferred",
            "schedule",
            "scheduled",
        ):
            completed_items = list_run_items(session, run)
            from engine.ops.human_alerts import notify_batch_completion, resolve_source

            resolve_source(session, source_type="publish_run", source_id=run.run_id)
            notify_batch_completion(
                session,
                customer_id=run.customer_id,
                run_id=run.run_id,
                succeeded=sum(item.phase == "published" for item in completed_items),
                failed=sum(item.phase == "failed" for item in completed_items),
                skipped=sum(
                    item.phase in ("skipped", "deferred") for item in completed_items
                ),
            )
        session.commit()
        # legacy path previously notified only immediate and committed once; keep single commit
    except Exception:
        log.exception("publish run worker failed run_id=%s", run_id)
        try:
            run = get_run(session, run_id)
            if run:
                run.status = "failed"
                run.error = "worker_exception"
                run.finished_at = _now()
                _audit_publish(
                    session,
                    run,
                    "publish_worker_exception",
                    "发布 worker 发生未捕获异常",
                    stage="worker",
                    level="error",
                )
                session.commit()
        except Exception:
            pass
    finally:
        keep_status = ""
        try:
            if run is not None:
                keep_status = str(getattr(run, "status", "") or "")
        except Exception:
            keep_status = ""
        keep_chrome_for_human = keep_status in (
            "paused_human",
            "outcome_unknown",
            "waiting_login",
        )
        session.close()
        if not keep_chrome_for_human:
            cleanup = _stop_active_chrome(force=True)
            if run is not None and cleanup.get("had_chrome"):
                try:
                    # Best-effort: may already be closed; ignore refresh errors.
                    session2 = get_session()
                    try:
                        r2 = get_run(session2, run_id)
                        if r2:
                            cfg = dict(r2.config_json or {})
                            cfg["chrome_cleanup"] = cleanup
                            r2.config_json = cfg
                            r2.updated_at = _now()
                            session2.commit()
                    finally:
                        session2.close()
                except Exception:
                    pass
        _cancel_run_id = None
        if operation_lease is not None:
            try:
                from engine.reach.chrome_runtime import release_operation

                release_operation(operation_lease)
            except Exception:
                pass
        try:
            from engine.reach.chrome_runtime import clear_publish_active

            clear_publish_active(f"publish_run:{run_id}")
        except Exception:
            pass
        if publish_token is not None:
            try:
                from engine.runtime.resource_gate import gate as resource_gate

                resource_gate.release("publish", publish_token)
            except Exception:
                pass
            try:
                from engine.runtime.workload_yield import release_workload

                release_workload(publish_token)
            except Exception:
                pass
        with _lock:
            if _worker_run_id == run_id:
                _worker = None
                _worker_run_id = None
        # Free slot then immediately chain auto deferred (no multi-minute idle gap).
        if keep_status in ("completed", "failed", "cancelled", "interrupted_system"):
            cid = 0
            try:
                cid = int(getattr(run, "customer_id", 0) or 0) if run is not None else 0
            except Exception:
                cid = 0
            if cid:
                threading.Thread(
                    target=_kick_deferred_chain,
                    args=(cid,),
                    name=f"deferred-kick-{run_id[:8]}",
                    daemon=True,
                ).start()
        _enqueue_due_message_scans(run_id)
