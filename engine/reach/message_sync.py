"""G7 serial, read-only message scan state machine."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from engine.catalog.db import (
    ReachMessage,
    ReachMessageAccount,
    ReachMessageScan,
    ReachNotificationEvent,
    get_session,
)
from engine.reach.chrome_runtime import (
    ChromeRuntimeError,
    ManagedChrome,
    PublishPriorityError,
    forget_managed,
    is_publish_active,
    operation,
    start_or_reuse_managed,
)
from engine.reach.messages_adapters import (
    MessageAdapterError,
    adapter_for,
    canonical_message_url,
    validate_official_url,
)

MESSAGE_SCAN_INTERVAL_SEC = 1800
# Scheduler poll interval (must be << account cooldown so due accounts enqueue promptly).
TICK_SEC = 30
DEFAULT_COOLDOWN_SEC = MESSAGE_SCAN_INTERVAL_SEC
# Chrome start + CDP navigate + SPA settle can exceed 45s on cold profiles.
SCAN_TIMEOUT_SEC = 90
# Do not block scan queues for full publish runs; fail fast and retry shortly.
CHROME_LEASE_WAIT_SEC = 12.0
MESSAGE_HISTORY_DAYS = 30
MESSAGE_HISTORY_LIMIT = 200
MESSAGE_REDACTION_VERSION = "v1"
notification_claim_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def account_to_dict(row: ReachMessageAccount) -> dict[str, Any]:
    from engine.reach.messages_adapters import spec_for

    try:
        spec = spec_for(row.platform)
    except ValueError:
        spec = None
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "business_scope": getattr(row, "business_scope", None) or "video",
        "platform": row.platform,
        "profile_name": row.profile_name,
        "display_name": row.display_name,
        "purpose": getattr(row, "purpose", None) or "legacy",
        "profile_role": getattr(row, "profile_role", None) or "shared_legacy",
        "provisioning_status": getattr(row, "provisioning_status", None)
        or "legacy_unverified",
        "explicit_created_at": (
            row.explicit_created_at.isoformat()
            if getattr(row, "explicit_created_at", None)
            else None
        ),
        "enabled": row.enabled,
        "cooldown_sec": MESSAGE_SCAN_INTERVAL_SEC,
        "message_url": row.message_url,
        "adapter_version": getattr(row, "adapter_version", None) or "",
        "readonly_verified": bool(spec and spec.readonly_verified),
        "reply_supported": bool(spec and spec.reply_supported),
        "supported_kinds": list(spec.kinds) if spec else [],
        "last_attempt_at": (
            row.last_attempt_at.isoformat()
            if getattr(row, "last_attempt_at", None)
            else None
        ),
        "last_success_at": (
            row.last_success_at.isoformat()
            if getattr(row, "last_success_at", None)
            else None
        ),
        "success_cursor": getattr(row, "success_cursor", None) or "",
        "last_scanned_at": row.last_scanned_at.isoformat() if row.last_scanned_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def is_active_message_account(row: ReachMessageAccount) -> bool:
    status = str(getattr(row, "provisioning_status", "") or "")
    role = str(getattr(row, "profile_role", "") or "shared_legacy")
    name = str(row.profile_name or "")
    return (
        status != "deleted"
        and role != "archived"
        and not name.startswith("deleted-")
    )


def create_message_account(
    session,
    *,
    customer_id: int,
    business_scope: str,
    platform: str,
    profile_name: str = "",
    display_name: str = "",
    enabled: bool = True,
    message_url: str = "",
) -> tuple[ReachMessageAccount, dict[str, Any]]:
    """Create a dedicated message Chrome profile and its DB account.

    Publishing profiles are never reused or copied.  A caller-supplied
    ``profile_name`` is only a requested name for the new empty profile.
    """
    from engine.reach.browser import create_chrome_profiles
    from engine.reach.business_scope import assert_platform_in_scope, normalize_scope
    from engine.reach.messages_adapters import spec_for

    scope = normalize_scope(business_scope)
    plat = assert_platform_in_scope(platform, scope)
    spec = spec_for(plat)
    created = create_chrome_profiles(
        customer_id=int(customer_id),
        business_scope=scope,
        platform=plat,
        count=1,
        name_prefix=profile_name.strip() or None,
        purpose="message",
    )
    item = (created.get("created") or [None])[0]
    if not isinstance(item, dict) or not item.get("name"):
        raise ValueError("独立消息 Chrome 配置创建失败")
    name = str(item["name"])
    url = validate_official_url(plat, message_url or spec.message_url)
    row = ReachMessageAccount(
        customer_id=int(customer_id),
        business_scope=scope,
        platform=plat,
        profile_name=name,
        display_name=(display_name or name)[:128],
        purpose="message",
        profile_role="message",
        provisioning_status="explicit",
        explicit_created_at=_now(),
        enabled=bool(enabled),
        cooldown_sec=DEFAULT_COOLDOWN_SEC,
        message_url=url,
        adapter_version=str(getattr(spec, "adapter_version", "") or ""),
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    try:
        session.flush()
    except Exception:
        # Do not leave an empty profile after a failed DB binding. This only
        # removes the newly-created directory; no Cookie was copied into it.
        import shutil
        from pathlib import Path

        created_path = str(item.get("path") or "").strip()
        if created_path:
            shutil.rmtree(Path(created_path), ignore_errors=True)
        raise
    return row, created


def delete_message_accounts(
    session,
    *,
    customer_id: int,
    business_scope: str,
    account_ids: list[int],
) -> dict[str, Any]:
    """Remove message-account bindings; keep Chrome profiles and historical messages."""
    ids = sorted({int(value) for value in account_ids if int(value) > 0})
    if not ids:
        raise ValueError("请至少选择一个消息账号")
    rows = list(
        session.scalars(
            select(ReachMessageAccount).where(
                ReachMessageAccount.customer_id == int(customer_id),
                ReachMessageAccount.business_scope == business_scope,
                ReachMessageAccount.id.in_(ids),
            )
        ).all()
    )
    if not rows:
        raise ValueError("未找到可删除的消息账号")
    deleted: list[dict[str, Any]] = []
    for row in rows:
        if not is_active_message_account(row):
            continue
        old_name = row.profile_name
        row.enabled = False
        row.provisioning_status = "deleted"
        row.profile_role = "archived"
        row.profile_name = f"deleted-{row.id}-{old_name}"[:128]
        row.updated_at = _now()
        deleted.append(
            {
                "id": row.id,
                "platform": row.platform,
                "profile_name": old_name,
                "display_name": row.display_name,
            }
        )
    session.commit()
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


def message_to_dict(row: ReachMessage) -> dict[str, Any]:
    from engine.reach.notifications import redact_summary

    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "account_id": row.account_id,
        "business_scope": getattr(row, "business_scope", None) or "video",
        "platform": row.platform,
        "kind": getattr(row, "kind", None) or "other",
        "sender": row.sender,
        "summary": row.summary,
        "notification_sender": redact_summary(row.sender)[:80],
        "notification_summary": redact_summary(row.summary),
        "reply_url": row.reply_url,
        "unread": row.unread,
        "source": row.source,
        "confidence": row.confidence,
        "platform_event_at": (
            row.platform_event_at.isoformat()
            if getattr(row, "platform_event_at", None)
            else None
        ),
        "identity_source": getattr(row, "identity_source", None) or "",
        "platform_unread": getattr(row, "platform_unread", None),
        "redaction_version": getattr(row, "redaction_version", None) or "",
        "purged_at": (
            row.purged_at.isoformat() if getattr(row, "purged_at", None) else None
        ),
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
    }


class MessageSync:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scheduler: threading.Thread | None = None
        self._queue: list[tuple[int, bool, str]] = []
        self._retry_counts: dict[int, int] = {}
        self._status: dict[str, Any] = {
            "active": False,
            "phase": "idle",
            "task_id": None,
            "account_id": None,
            "error": None,
            "updated_at": _now().isoformat(),
        }

    def start_scheduler(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker_loop, name="reach-message-worker", daemon=True)
            self._scheduler = threading.Thread(
                target=self._scheduler_loop, name="reach-message-scheduler", daemon=True
            )
            self._thread.start()
            self._scheduler.start()
        # Startup catch-up: honor the 1800-second lock, but do not wait one
        # whole interval before checking accounts that are already due.
        self._enqueue_due_accounts()

    def stop_scheduler(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        for thread in (self._scheduler, self._thread):
            if thread and thread.is_alive():
                thread.join(timeout=3)
        # Best-effort: even if a Chrome scan thread is still winding down,
        # do not leave phase=cancelling forever — system pause quiesce treats
        # that as a hard blocker (PAUSED_BLOCKED / 一键上传 423).
        with self._lock:
            worker_alive = bool(self._thread and self._thread.is_alive())
            if not worker_alive or self._status.get("phase") in {
                "cancelling",
                "running",
                "scanning",
                "starting_chrome",
                "dry_run",
            }:
                self._status.update(
                    {
                        "active": False,
                        "phase": "stopped" if not worker_alive else "interrupted_system",
                        "account_id": None,
                        "queued": len(self._queue),
                        "updated_at": _now().isoformat(),
                    }
                )

    def enqueue(self, account_ids: list[int], *, dry_run: bool = False, reason: str = "manual") -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            existing = {account_id for account_id, _, _ in self._queue}
            added = 0
            for account_id in account_ids:
                if account_id in existing:
                    continue
                self._queue.append((account_id, dry_run, task_id))
                existing.add(account_id)
                added += 1
            self._status.update(
                {
                    "phase": "queued" if added else self._status.get("phase", "idle"),
                    "task_id": task_id if added else self._status.get("task_id"),
                    "reason": reason,
                    "queued": len(self._queue),
                    "updated_at": _now().isoformat(),
                }
            )
        self._wake.set()
        return {"task_id": task_id, "queued": added, "dry_run": dry_run}

    def cancel(self, account_ids: set[int] | None = None) -> dict[str, Any]:
        """Cancel only the caller-owned accounts; None is reserved for shutdown/admin use."""
        with self._lock:
            active_account_id = self._status.get("account_id")
            cancel_active = bool(self._status.get("active")) and (
                account_ids is None or active_account_id in account_ids
            )
            if cancel_active:
                self._cancel.set()
            before = len(self._queue)
            if account_ids is None:
                self._queue.clear()
            else:
                self._queue = [
                    item for item in self._queue if item[0] not in account_ids
                ]
            discarded = before - len(self._queue)
            self._status.update(
                {
                    "phase": (
                        "cancelling"
                        if cancel_active
                        else ("idle" if not self._queue else self._status.get("phase", "idle"))
                    ),
                    "queued": len(self._queue),
                    "updated_at": _now().isoformat(),
                }
            )
        return {"cancel_requested": cancel_active, "discarded": discarded}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._status,
                "queued": len(self._queue),
                "scheduler_running": bool(self._scheduler and self._scheduler.is_alive()),
                "tick_sec": TICK_SEC,
                "default_cooldown_sec": DEFAULT_COOLDOWN_SEC,
                "timeout_sec": SCAN_TIMEOUT_SEC,
            }

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)
            self._status["queued"] = len(self._queue)
            self._status["updated_at"] = _now().isoformat()

    def _schedule_transient_retry(self, account_id: int) -> None:
        attempt = int(self._retry_counts.get(account_id) or 0) + 1
        if attempt > 2:
            self._retry_counts.pop(account_id, None)
            return
        self._retry_counts[account_id] = attempt
        delay = 15 if attempt == 1 else 60

        def enqueue_retry() -> None:
            if not self._stop.is_set():
                self.enqueue(
                    [account_id],
                    reason=f"transient_retry_{attempt}",
                )

        timer = threading.Timer(delay, enqueue_retry)
        timer.daemon = True
        timer.start()

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(TICK_SEC):
            self._enqueue_due_accounts()

    def _enqueue_due_accounts(self) -> None:
        session = get_session()
        try:
            now = _now()
            rows = session.scalars(
                select(ReachMessageAccount).where(
                    ReachMessageAccount.enabled.is_(True)
                )
            ).all()
            due: list[int] = []
            for row in rows:
                if not is_active_message_account(row):
                    continue
                role = str(getattr(row, "profile_role", "") or "shared_legacy")
                if role not in {"message", "shared_legacy"}:
                    continue
                if (
                    row.business_scope == "content"
                    and (
                        getattr(row, "purpose", "") not in {"message", "article"}
                        or getattr(row, "provisioning_status", "") != "explicit"
                    )
                ):
                    continue
                latest = session.scalar(
                    select(ReachMessageScan)
                    .where(ReachMessageScan.account_id == row.id)
                    .order_by(ReachMessageScan.id.desc())
                    .limit(1)
                )
                if latest is not None and latest.status == "needs_human":
                    continue
                if row.last_scanned_at is None or now - _as_utc(
                    row.last_scanned_at
                ) >= timedelta(seconds=MESSAGE_SCAN_INTERVAL_SEC):
                    due.append(row.id)
            if due:
                self.enqueue(due, reason="scheduler")
        finally:
            session.close()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    if not self._queue:
                        break
                    account_id, dry_run, task_id = self._queue.pop(0)
                self._cancel.clear()
                self._scan_one(account_id, dry_run=dry_run, task_id=task_id)
        self._set(active=False, phase="stopped")

    def _scan_one(self, account_id: int, *, dry_run: bool, task_id: str) -> None:
        session = get_session()
        scan: ReachMessageScan | None = None
        runtime: ManagedChrome | None = None
        keep_chrome_open = False
        account: ReachMessageAccount | None = None
        started = time.monotonic()
        try:
            account = session.get(ReachMessageAccount, account_id)
            if not account or not account.enabled or not is_active_message_account(account):
                return
            account.last_attempt_at = _now()
            account.updated_at = _now()
            scan = ReachMessageScan(
                customer_id=account.customer_id,
                account_id=account.id,
                status="running",
                started_at=_now(),
            )
            session.add(scan)
            session.commit()
            self._set(
                active=True,
                phase="dry_run" if dry_run else "starting_chrome",
                task_id=task_id,
                account_id=account.id,
                platform=account.platform,
                error=None,
            )
            spec_url = canonical_message_url(account.platform)
            stored_url = str(account.message_url or "").strip()
            if stored_url != spec_url:
                account.message_url = spec_url
                account.updated_at = _now()
                session.commit()
            message_url = spec_url
            if dry_run:
                result: dict[str, Any] = {
                    "messages": [],
                    "source": "dry_run",
                    "confidence": 0.0,
                }
            else:
                # Publish owns Chrome for the whole batch — yield immediately.
                if is_publish_active():
                    raise PublishPriorityError(
                        "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）"
                    )
                # Brief wait only — long holds freeze the whole message queue
                # behind publish. Transient busy → deferred + short retry.
                with operation(
                    f"message_scan:{account.id}",
                    wait_timeout_sec=CHROME_LEASE_WAIT_SEC,
                ):
                    if is_publish_active():
                        raise PublishPriorityError(
                            "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）"
                        )
                    scope = getattr(account, "business_scope", None) or "video"
                    # Reuse an already-open login window when present; otherwise
                    # the locked 1800-second patrol starts official Chrome headless.
                    runtime, info = start_or_reuse_managed(
                        account.profile_name,
                        message_url,
                        customer_id=account.customer_id,
                        business_scope=scope,
                        headless=True,
                        timeout_sec=25.0,
                    )
                    self._set(phase="scanning", port=info.get("port"))
                    if self._cancel.is_set():
                        raise InterruptedError("用户取消")
                    if is_publish_active():
                        raise PublishPriorityError(
                            "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）"
                        )
                    port = int(info.get("port") or 0)
                    if not port:
                        raise ChromeRuntimeError("消息巡检未获得 CDP 端口")
                    result = adapter_for(account.platform).scan(
                        f"http://127.0.0.1:{port}"
                    )
            if time.monotonic() - started > SCAN_TIMEOUT_SEC:
                raise TimeoutError(f"单账号扫描超过 {SCAN_TIMEOUT_SEC} 秒")
            new_message_ids = self._upsert_messages(
                session, account, result.get("messages") or []
            )
            self._apply_history_retention(session, account)
            found = len(new_message_ids)
            account.last_scanned_at = _now()
            readonly_verified = bool(
                result.get("readonly_verified", dry_run)
            )
            if readonly_verified:
                account.last_success_at = account.last_scanned_at
            adapter_version = str(result.get("adapter_version") or "")
            if adapter_version:
                account.adapter_version = adapter_version[:64]
            sync_complete = bool(
                result.get("complete", result.get("boundary_reached", True))
            )
            cursor = result.get("cursor")
            if readonly_verified and sync_complete and cursor is not None:
                account.success_cursor = str(cursor)
            account.updated_at = _now()
            scan.status = "completed" if readonly_verified else "readonly_unverified"
            scan.source = str(result.get("source") or "")
            scan.found_count = found
            scan.finished_at = _now()
            from engine.ops.audit_log import write_log

            write_log(
                session,
                customer_id=account.customer_id,
                category="message",
                event=(
                    "message_scan_completed"
                    if readonly_verified
                    else "message_scan_readonly_unverified"
                ),
                message=(
                    f"{account.display_name or account.profile_name} 消息检查完成，新增 {found} 条"
                    if readonly_verified
                    else f"{account.display_name or account.profile_name} 已读取摘要，但平台无副作用校准未完成"
                ),
                stage=scan.status,
                source_type="message_scan",
                source_id=scan.id,
                account=account.display_name or account.profile_name,
                platform=account.platform,
                duration_ms=int((time.monotonic() - started) * 1000),
                details={"found_count": found, "source": scan.source, "dry_run": dry_run},
            )
            session.commit()
            if readonly_verified:
                self._dispatch_ntfy(session, account, message_ids=new_message_ids)
            self._retry_counts.pop(account.id, None)
            try:
                from engine.ops.human_alerts import resolve_source

                resolve_source(
                    session,
                    source_type="message_account",
                    source_id=str(account.id),
                )
            except Exception:
                pass
            self._set(active=False, phase=scan.status, found_count=found, error=None)
        except InterruptedError as exc:
            self._finish_error(session, scan, "cancelled", "cancelled", str(exc))
        except MessageAdapterError as exc:
            # Login / captcha: leave Chrome open for the human; page-load timeout
            # is transient and must not spam「需要人工」notifications.
            if exc.code in {"login_required", "verification_required"}:
                keep_chrome_open = True
                self._finish_error(session, scan, "needs_human", exc.code, str(exc))
                if account is not None:
                    from engine.ops.human_alerts import create_alert, dispatch_due_alerts

                    create_alert(
                        session,
                        alert_key=f"message:{account.id}:{exc.code}",
                        customer_id=account.customer_id,
                        kind=exc.code,
                        source_type="message_account",
                        source_id=str(account.id),
                        summary=(
                            f"{account.platform} · "
                            f"{account.display_name or account.profile_name} · {exc}"
                        ),
                        deep_link=(
                            f"suying://messages?scope={account.business_scope}"
                            f"&account={account.id}"
                        ),
                    )
                    dispatch_due_alerts(session, customer_id=account.customer_id)
            elif exc.code in {"page_load_timeout", "profile_busy"}:
                self._finish_error(session, scan, "failed", exc.code, str(exc))
                if account is not None:
                    self._schedule_transient_retry(account.id)
            else:
                self._finish_error(session, scan, "needs_human", exc.code, str(exc))
                if account is not None and exc.code == "adapter_changed":
                    from engine.ops.human_alerts import create_alert, dispatch_due_alerts

                    create_alert(
                        session,
                        alert_key=f"message:{account.id}:{exc.code}",
                        customer_id=account.customer_id,
                        kind=exc.code,
                        source_type="message_account",
                        source_id=str(account.id),
                        summary=(
                            f"{account.platform} · "
                            f"{account.display_name or account.profile_name} · {exc}"
                        ),
                        deep_link=(
                            f"suying://messages?scope={account.business_scope}"
                            f"&account={account.id}"
                        ),
                    )
                    dispatch_due_alerts(session, customer_id=account.customer_id)
        except PublishPriorityError as exc:
            self._finish_error(
                session,
                scan,
                "deferred",
                "publish_priority",
                str(exc),
            )
            if account is not None:
                self._schedule_transient_retry(account.id)
        except (ChromeRuntimeError, ValueError, TimeoutError, OSError) as exc:
            text = str(exc)
            low = text.lower()
            publish_priority = (
                "publish_priority" in low or isinstance(exc, PublishPriorityError)
            )
            chrome_busy = (
                publish_priority
                or "busy" in low
                or "lock" in low
                or "占用" in text
                or "正由" in text
                or "另一个速影任务" in text
                or "浏览器忙" in text
            )
            code = (
                "publish_priority"
                if publish_priority
                else ("chrome_busy" if chrome_busy else "transient_error")
            )
            error_text = text
            if chrome_busy and "打开登录" in text:
                # Drop legacy misleading hint that confuses operators.
                error_text = text.replace(
                    "；若账号需登录请点「打开登录」", ""
                ).replace("若账号需登录请点「打开登录」。", "")
            if chrome_busy and "非账号" not in error_text and "非登录" not in error_text:
                error_text = (
                    f"{error_text.rstrip('。')}。"
                    "这是浏览器被发布/其他任务占用，消息巡检已延后，不是账号登录损坏。"
                )
            self._finish_error(session, scan, "deferred", code, error_text)
            if account is not None:
                self._schedule_transient_retry(account.id)
        except Exception as exc:  # noqa: BLE001
            self._finish_error(session, scan, "failed", "unexpected", str(exc))
        finally:
            if runtime is not None:
                try:
                    if keep_chrome_open:
                        # Keep the official window so the operator can finish login.
                        forget_managed(runtime)
                    else:
                        runtime.stop()
                        forget_managed(runtime)
                except Exception:
                    try:
                        forget_managed(runtime)
                    except Exception:
                        pass
            session.close()

    def _finish_error(
        self,
        session: Any,
        scan: ReachMessageScan | None,
        status: str,
        code: str,
        error: str,
    ) -> None:
        if scan is not None:
            scan.status = status
            scan.error_code = code[:64]
            scan.error = error[:1000]
            scan.finished_at = _now()
            if scan.account_id is not None:
                account = session.get(ReachMessageAccount, scan.account_id)
                if account is not None:
                    account.last_scanned_at = _now()
                    account.updated_at = _now()
                    try:
                        from engine.ops.audit_log import write_log

                        write_log(
                            session,
                            customer_id=account.customer_id,
                            category="message",
                            event=f"message_scan_{status}",
                            message=f"{account.display_name or account.profile_name} 消息检查未完成",
                            level="warning" if status in {"cancelled", "needs_human"} else "error",
                            stage=status,
                            source_type="message_scan",
                            source_id=scan.id,
                            account=account.display_name or account.profile_name,
                            platform=account.platform,
                            details={"error_code": code, "error": error},
                        )
                    except Exception:
                        pass
            session.commit()
        self._set(active=False, phase=status, error=error, error_code=code)

    @staticmethod
    def _upsert_messages(
        session: Any, account: ReachMessageAccount, messages: list[dict[str, Any]]
    ) -> list[int]:
        from engine.reach.notifications import redact_summary

        now = _now()
        new_rows: list[ReachMessage] = []
        for item in messages:
            key = str(item.get("external_key") or "")[:160]
            if not key:
                continue
            row = session.scalar(
                select(ReachMessage).where(
                    ReachMessage.account_id == account.id,
                    ReachMessage.external_key == key,
                )
            )
            kind = str(item.get("kind") or "other").strip().lower()
            if kind not in ("comment", "dm", "notice", "like", "other"):
                kind = "other"
            if kind == "like":
                continue
            if row is None:
                row = ReachMessage(
                    customer_id=account.customer_id,
                    account_id=account.id,
                    business_scope=getattr(account, "business_scope", None) or "video",
                    platform=account.platform,
                    external_key=key,
                    first_seen_at=now,
                    unread=True,
                    kind=kind,
                    identity_source=str(
                        item.get("identity_source") or "adapter_external_key"
                    )[:64],
                    platform_unread=(
                        bool(item.get("platform_unread"))
                        if item.get("platform_unread") is not None
                        else None
                    ),
                    redaction_version=MESSAGE_REDACTION_VERSION,
                )
                session.add(row)
                new_rows.append(row)
            elif getattr(row, "purged_at", None) is not None:
                # Identity tombstones are intentionally never revived. Updating
                # last_seen_at keeps the key durable without restoring content.
                row.last_seen_at = now
                continue
            row.business_scope = getattr(account, "business_scope", None) or row.business_scope or "video"
            row.kind = kind
            row.sender = redact_summary(str(item.get("sender") or ""))[:128]
            row.summary = redact_summary(str(item.get("summary") or ""))[:280]
            row.reply_url = validate_official_url(account.platform, str(item.get("reply_url") or ""))
            row.source = str(item.get("source") or "dom_heuristic")[:32]
            row.confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            event_at = item.get("platform_event_at") or item.get("event_at")
            if isinstance(event_at, datetime):
                row.platform_event_at = _as_utc(event_at)
            elif event_at:
                try:
                    row.platform_event_at = _as_utc(
                        datetime.fromisoformat(str(event_at).replace("Z", "+00:00"))
                    )
                except ValueError:
                    pass
            if item.get("identity_source"):
                row.identity_source = str(item["identity_source"])[:64]
            if item.get("platform_unread") is not None:
                row.platform_unread = bool(item["platform_unread"])
            row.redaction_version = MESSAGE_REDACTION_VERSION
            row.last_seen_at = now
        session.flush()
        return [int(row.id) for row in new_rows]

    @staticmethod
    def _apply_history_retention(
        session: Any, account: ReachMessageAccount
    ) -> int:
        """Tombstone summaries outside the 30-day / newest-200 contract."""
        now = _now()
        cutoff = now - timedelta(days=MESSAGE_HISTORY_DAYS)
        rows = list(
            session.scalars(
                select(ReachMessage)
                .where(
                    ReachMessage.account_id == account.id,
                    ReachMessage.purged_at.is_(None),
                )
                .order_by(
                    ReachMessage.platform_event_at.desc(),
                    ReachMessage.last_seen_at.desc(),
                    ReachMessage.id.desc(),
                )
            ).all()
        )
        changed = 0
        for index, row in enumerate(rows):
            event_at = row.platform_event_at or row.last_seen_at or row.first_seen_at
            expired = _as_utc(event_at) < cutoff if event_at else True
            if not expired and index < MESSAGE_HISTORY_LIMIT:
                continue
            row.sender = ""
            row.summary = ""
            row.reply_url = ""
            row.unread = False
            row.read_at = row.read_at or now
            row.notification_claimed_at = row.notification_claimed_at or now
            row.purged_at = now
            changed += 1
        if changed:
            session.flush()
        return changed

    @staticmethod
    def _dispatch_ntfy(
        session: Any,
        account: ReachMessageAccount,
        *,
        message_ids: list[int],
    ) -> None:
        from engine.reach.notifications import redact_summary, send_ntfy_async

        if not message_ids:
            return
        customer_id = int(account.customer_id)
        messages = session.scalars(
            select(ReachMessage).where(
                ReachMessage.account_id == account.id,
                ReachMessage.id.in_(message_ids),
                ReachMessage.unread.is_(True),
                ReachMessage.purged_at.is_(None),
                ReachMessage.kind.in_(("comment", "dm", "notice", "other")),
            )
        ).all()
        pending: list[
            tuple[ReachNotificationEvent, str, str, str]
        ] = []
        for message in messages:
            existing = session.scalar(
                select(ReachNotificationEvent).where(
                    ReachNotificationEvent.customer_id == customer_id,
                    ReachNotificationEvent.message_id == message.id,
                    ReachNotificationEvent.channel == "ntfy",
                )
            )
            if existing is not None:
                continue
            event = ReachNotificationEvent(
                customer_id=customer_id,
                message_id=message.id,
                channel="ntfy",
                status="queued",
                is_test=False,
            )
            session.add(event)
            pending.append(
                (
                    event,
                    f"{account.platform} · {redact_summary(message.sender) or '新消息'}",
                    redact_summary(message.summary),
                    message.reply_url,
                )
            )
        if not pending:
            return
        # Persist all idempotency claims before worker threads can open their
        # own SQLite sessions. Per-message commits race with fast audit threads
        # and can leave the serial scanner waiting on a database lock.
        session.flush()
        deliveries = [
            (int(event.id), title, summary, reply_url)
            for event, title, summary, reply_url in pending
        ]
        session.commit()

        for event_id, title, summary, reply_url in deliveries:
            def audit(status: str, detail: str, *, current_event_id: int = event_id) -> None:
                audit_session = get_session()
                try:
                    current = audit_session.get(ReachNotificationEvent, current_event_id)
                    if current is not None:
                        current.status = status[:32]
                        current.detail = detail[:300]
                        current.updated_at = _now()
                        audit_session.commit()
                finally:
                    audit_session.close()

            send_ntfy_async(
                customer_id,
                title=title,
                summary=summary,
                reply_url=reply_url,
                audit=audit,
            )


message_sync = MessageSync()
