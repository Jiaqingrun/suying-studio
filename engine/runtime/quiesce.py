"""Quiesce / restore owned runtime units for GSystemPause."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from engine.runtime import services as runtime_services
from engine.runtime.pause_coordinator import (
    SAFE_AUTO_RESUME_UNITS,
    SIDE_EFFECT_UNITS,
    coordinator,
)

log = logging.getLogger("montage.runtime.quiesce")

_cancel_scan = threading.Event()


def scan_cancel_requested() -> bool:
    return _cancel_scan.is_set()


def request_scan_cancel() -> None:
    _cancel_scan.set()


def clear_scan_cancel() -> None:
    _cancel_scan.clear()


def capture_owned_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {}
    try:
        from engine.jobs.worker import worker

        snap["worker"] = bool(worker.is_alive())
    except Exception:  # noqa: BLE001
        snap["worker"] = False

    w = runtime_services.get("watcher")
    snap["watcher"] = bool(w and getattr(w, "is_alive", lambda: False)())

    try:
        from engine.ops.scheduler import scheduler

        snap["scheduler"] = bool(scheduler.is_alive())
    except Exception:  # noqa: BLE001
        snap["scheduler"] = False

    try:
        from engine.reach.message_sync import message_sync

        message_state = message_sync.status()
        snap["message_scheduler"] = bool(message_state.get("scheduler_running"))
        snap["message_sync"] = bool(
            message_state.get("active") or int(message_state.get("queued") or 0) > 0
        )
    except Exception:  # noqa: BLE001
        snap["message_scheduler"] = False
        snap["message_sync"] = False
    try:
        from engine.ops.scan_state import scan_state

        snap["scan"] = bool(scan_state.get("running"))
    except Exception:  # noqa: BLE001
        snap["scan"] = False
    try:
        from engine.catalog.vectorization_runtime import executor

        snap["vector_reconcile"] = bool(executor.has_active_run())
    except Exception:  # noqa: BLE001
        snap["vector_reconcile"] = False

    try:
        from engine.reach.auto_upload import get_status

        st = get_status()
        snap["reach_auto_upload"] = bool(st.get("active"))
    except Exception:  # noqa: BLE001
        snap["reach_auto_upload"] = False

    try:
        from engine.reach.publish_runner import get_status as get_publish_status

        snap["publish_batch"] = bool(get_publish_status().get("active"))
    except Exception:  # noqa: BLE001
        snap["publish_batch"] = False

    try:
        from sqlalchemy import func, select

        from engine.catalog.db import ContentPublishJob, get_session

        session = get_session()
        try:
            snap["content_publish"] = bool(
                session.scalar(
                    select(func.count()).select_from(ContentPublishJob).where(
                        ContentPublishJob.status == "running"
                    )
                )
            )
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        snap["content_publish"] = False

    try:
        from engine.catalog import ollama_status

        pull = getattr(ollama_status, "_pull_state", None) or {}
        snap["ollama_pull"] = bool(isinstance(pull, dict) and pull.get("running"))
    except Exception:  # noqa: BLE001
        snap["ollama_pull"] = False
    return snap


def quiesce_units(*, timeout_sec: float = 15.0) -> dict[str, Any]:
    result: dict[str, Any] = {"actions": [], "errors": [], "side_effect_interrupted": []}
    owned = capture_owned_snapshot()
    coordinator.set_owned_units(owned)
    generation = int(coordinator.snapshot().get("generation") or 0)

    try:
        from sqlalchemy import select

        from engine.catalog.db import Job, get_session

        session = get_session()
        try:
            queued = session.scalars(select(Job).where(Job.status == "queued")).all()
            for job in queued:
                job.status = "paused_system"
                snap = dict(job.config_snapshot_json or {})
                snap["_system_pause_hold"] = {
                    "owner": "runtime",
                    "generation": generation,
                }
                job.config_snapshot_json = snap
                result["actions"].append(f"job:{job.id}:paused_system")
            session.commit()
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        log.warning("quiesce jobs failed: %s", e)
        result["errors"].append(f"jobs:{e}")

    try:
        from engine.ops.scheduler import scheduler

        if scheduler.is_alive():
            scheduler.stop()
            result["actions"].append("scheduler:stop")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"scheduler:{e}")

    w = runtime_services.get("watcher")
    try:
        if w and getattr(w, "is_alive", lambda: False)():
            w.stop()
            result["actions"].append("watcher:stop")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"watcher:{e}")

    if owned.get("scan"):
        request_scan_cancel()
        result["actions"].append("scan:cancel_requested")

    try:
        from engine.catalog.vectorization_runtime import executor

        vector_state = executor.pause(owner="system", timeout=min(2.0, timeout_sec))
        if owned.get("vector_reconcile") or vector_state.get("status") == "PAUSED":
            result["actions"].append("vector_reconcile:paused")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"vector_reconcile:{e}")

    try:
        from engine.reach.message_sync import message_sync

        message_sync.cancel()
        message_sync.stop_scheduler()
        result["actions"].append("message_sync:cancel+stop")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"message_sync:{e}")

    try:
        from engine.reach.auto_upload import cancel_job, get_status

        st = get_status()
        if st.get("active"):
            cancel_job()
            coordinator.mark_side_effect_interrupted(
                "reach_auto_upload",
                status="interrupted_system",
                detail="自动上传已取消，禁止自动重放",
            )
            result["side_effect_interrupted"].append("reach_auto_upload")
            result["actions"].append("reach_auto_upload:cancel")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"reach_auto_upload:{e}")

    if owned.get("publish_batch"):
        try:
            from engine.reach.publish_runner import (
                get_status as get_publish_status,
                interrupt_run_for_system,
            )

            publish_state = get_publish_status()
            run_id = str(publish_state.get("run_id") or "")
            if run_id:
                interrupt_run_for_system(run_id)
                coordinator.mark_side_effect_interrupted(
                    "publish_batch",
                    status="interrupted_system",
                    detail="发布批次已中断，禁止自动重发，需人工确认",
                )
                result["side_effect_interrupted"].append(f"publish_batch:{run_id}")
                result["actions"].append("publish_batch:interrupt_system")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"publish_batch:{e}")

    try:
        from sqlalchemy import select

        from engine.catalog.db import ContentPublishJob, get_session

        session = get_session()
        try:
            rows = session.scalars(
                select(ContentPublishJob).where(ContentPublishJob.status == "running")
            ).all()
            for row in rows:
                row.status = "need_human"
                row.error = "interrupted_system：系统暂停时发布结果不明，需人工确认"
                result["side_effect_interrupted"].append(f"content_publish:{row.id}")
            if rows:
                session.commit()
                coordinator.mark_side_effect_interrupted(
                    "content_publish",
                    status="need_human",
                    detail=f"{len(rows)} 个发布任务需人工确认",
                )
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"content_publish:{e}")

    if owned.get("ollama_pull"):
        try:
            from engine.catalog.ollama_status import cancel_pull

            cancel_pull()
            coordinator.mark_side_effect_interrupted(
                "ollama_pull",
                status="need_human",
                detail="模型拉取已终止，恢复后需人工确认是否重试",
            )
            result["side_effect_interrupted"].append("ollama_pull")
            result["actions"].append("ollama_pull:terminate")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"ollama_pull:{e}")

    for unit in SIDE_EFFECT_UNITS:
        if owned.get(unit) and unit not in (
            "reach_auto_upload",
            "content_publish",
            "ollama_pull",
        ):
            coordinator.mark_side_effect_interrupted(
                unit,
                status="need_human",
                detail="系统暂停期间任务状态不明，禁止自动重放",
            )

    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    pending: list[str] = []
    while True:
        pending = []
        try:
            from engine.jobs.worker import worker

            if worker.running_job_id() is not None:
                pending.append("worker_output")
        except Exception:  # noqa: BLE001
            pending.append("worker_state")
        try:
            from engine.ops.scan_state import scan_state

            if scan_state.get("running"):
                pending.append("scan")
        except Exception:  # noqa: BLE001
            pending.append("scan_state")
        try:
            from engine.ops.scheduler import scheduler

            if scheduler.is_alive():
                pending.append("scheduler")
        except Exception:  # noqa: BLE001
            pending.append("scheduler_state")
        try:
            from engine.catalog.vectorization_runtime import executor

            if executor.is_running():
                pending.append("vector_reconcile")
        except Exception:  # noqa: BLE001
            pending.append("vector_reconcile_state")
        if w and getattr(w, "is_alive", lambda: False)():
            pending.append("watcher")
        try:
            from engine.reach.message_sync import message_sync

            message_state = message_sync.status()
            if owned.get("message_scheduler") and message_state.get("scheduler_running"):
                pending.append("message_scheduler")
            # cancel()+stop_scheduler() already requested interrupt; phases like
            # cancelling/interrupted_system are best-effort and must not strand
            # the whole runtime in PAUSED_BLOCKED.
            phase = str(message_state.get("phase") or "")
            if (
                owned.get("message_sync")
                and bool(message_state.get("active"))
                and phase in {"running", "scanning", "starting_chrome", "dry_run"}
            ):
                pending.append("message_sync")
        except Exception:  # noqa: BLE001
            pending.append("message_sync_state")
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    if pending:
        result["errors"].append(f"quiesce_timeout:{','.join(sorted(set(pending)))}")

    if result["errors"]:
        # Pass generation so a late timeout after wake cannot re-block ACTIVE.
        coordinator.mark_blocked(
            ["quiesce_failed"],
            error="; ".join(result["errors"]),
            generation=generation,
        )
    else:
        coordinator.mark_paused(generation=generation)
    result["state"] = coordinator.snapshot()
    result["safe_units"] = sorted(SAFE_AUTO_RESUME_UNITS)
    result["side_effect_units"] = sorted(SIDE_EFFECT_UNITS)
    return result


def restore_units() -> dict[str, Any]:
    result: dict[str, Any] = {"actions": [], "errors": [], "skipped_side_effects": []}
    owned = coordinator.owned_units()
    snapshot = coordinator.snapshot()
    pending = snapshot.get("pending_resume") or {}
    if snapshot.get("state") != "RESUMING" or not pending:
        result["errors"].append("resume_cancelled")
        result["state"] = snapshot
        return result
    generation = int(pending.get("generation") or -1)
    clear_scan_cancel()

    try:
        from sqlalchemy import select

        from engine.catalog.db import Job, get_session

        session = get_session()
        try:
            rows = session.scalars(select(Job).where(Job.status == "paused_system")).all()
            for job in rows:
                hold = dict((job.config_snapshot_json or {}).get("_system_pause_hold") or {})
                if int(hold.get("generation") or -2) != generation:
                    continue
                job.status = "queued"
                snap = dict(job.config_snapshot_json or {})
                snap.pop("_system_pause_hold", None)
                job.config_snapshot_json = snap
                result["actions"].append(f"job:{job.id}:queued")
            session.commit()
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"jobs:{e}")

    if owned.get("scheduler"):
        try:
            from engine.ops.scheduler import scheduler

            scheduler.start()
            result["actions"].append("scheduler:start")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"scheduler:{e}")

    if owned.get("watcher"):
        w = runtime_services.get("watcher")
        try:
            if w:
                w.start()
                result["actions"].append("watcher:start")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"watcher:{e}")

    if owned.get("message_scheduler"):
        try:
            from engine.reach.message_sync import message_sync

            message_sync.start_scheduler()
            result["actions"].append("message_scheduler:start")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"message_scheduler:{e}")

    if owned.get("vector_reconcile"):
        try:
            from engine.catalog.vectorization_runtime import executor

            state = executor.resume(owner="system")
            if state.get("pause_owner") != "manual":
                result["actions"].append("vector_reconcile:resume_system")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"vector_reconcile:{e}")

    for unit in SIDE_EFFECT_UNITS:
        if owned.get(unit):
            result["skipped_side_effects"].append(unit)

    try:
        from engine.jobs.worker import worker

        if not worker.is_alive():
            worker.start()
            result["actions"].append("worker:start")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"worker:{e}")

    if result["errors"]:
        coordinator.mark_blocked(["restore_failed"], error="; ".join(result["errors"]))
    result["state"] = coordinator.snapshot()
    return result
