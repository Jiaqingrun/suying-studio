"""G7 serial, read-only message scan state machine."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from engine.catalog.db import (
    ReachMessage,
    ReachMessageAccount,
    ReachMessageScan,
    ReachNotificationEvent,
    get_session,
)
from engine.reach.chrome_runtime import ChromeRuntimeError, ManagedChrome, operation
from engine.reach.messages_adapters import MessageAdapterError, adapter_for, validate_official_url

MESSAGE_SCAN_INTERVAL_SEC = 1800
TICK_SEC = MESSAGE_SCAN_INTERVAL_SEC
DEFAULT_COOLDOWN_SEC = MESSAGE_SCAN_INTERVAL_SEC
SCAN_TIMEOUT_SEC = 45
notification_claim_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def account_to_dict(row: ReachMessageAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "platform": row.platform,
        "profile_name": row.profile_name,
        "display_name": row.display_name,
        "enabled": row.enabled,
        "cooldown_sec": MESSAGE_SCAN_INTERVAL_SEC,
        "message_url": row.message_url,
        "last_scanned_at": row.last_scanned_at.isoformat() if row.last_scanned_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def message_to_dict(row: ReachMessage) -> dict[str, Any]:
    from engine.reach.notifications import redact_summary

    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "account_id": row.account_id,
        "platform": row.platform,
        "sender": row.sender,
        "summary": row.summary,
        "notification_sender": redact_summary(row.sender)[:80],
        "notification_summary": redact_summary(row.summary),
        "reply_url": row.reply_url,
        "unread": row.unread,
        "source": row.source,
        "confidence": row.confidence,
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

    def stop_scheduler(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        for thread in (self._scheduler, self._thread):
            if thread and thread.is_alive():
                thread.join(timeout=3)

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
            cancel_active = account_ids is None or active_account_id in account_ids
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
                    "phase": "cancelling" if cancel_active else self._status.get("phase", "idle"),
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

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(TICK_SEC):
            session = get_session()
            try:
                now = _now()
                session.execute(
                    delete(ReachMessage).where(
                        ReachMessage.last_seen_at < now - timedelta(days=30)
                    )
                )
                session.commit()
                rows = session.scalars(
                    select(ReachMessageAccount).where(ReachMessageAccount.enabled.is_(True))
                ).all()
                due: list[int] = []
                for row in rows:
                    latest = session.scalar(
                        select(ReachMessageScan)
                        .where(ReachMessageScan.account_id == row.id)
                        .order_by(ReachMessageScan.id.desc())
                        .limit(1)
                    )
                    # Login, verification and adapter failures require a human-triggered
                    # retry; do not reopen Chrome every cooldown interval.
                    if latest is not None and latest.status in ("needs_human", "failed"):
                        continue
                    if row.last_scanned_at is None or now - _as_utc(
                        row.last_scanned_at
                    ) >= timedelta(
                        seconds=MESSAGE_SCAN_INTERVAL_SEC
                    ):
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
        started = time.monotonic()
        try:
            account = session.get(ReachMessageAccount, account_id)
            if not account or not account.enabled:
                return
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
            message_url = validate_official_url(
                account.platform, account.message_url or adapter_for(account.platform).spec.message_url
            )
            if dry_run:
                result: dict[str, Any] = {
                    "messages": [],
                    "source": "dry_run",
                    "confidence": 0.0,
                }
            else:
                with operation(f"message_scan:{account.id}"):
                    runtime = ManagedChrome(account.profile_name, message_url)
                    info = runtime.start()
                    self._set(phase="scanning", port=info["port"])
                    if self._cancel.is_set():
                        raise InterruptedError("用户取消")
                    result = adapter_for(account.platform).scan(
                        f"http://127.0.0.1:{info['port']}"
                    )
                    runtime.stop()
                    runtime = None
            if time.monotonic() - started > SCAN_TIMEOUT_SEC:
                raise TimeoutError(f"单账号扫描超过 {SCAN_TIMEOUT_SEC} 秒")
            found = self._upsert_messages(session, account, result.get("messages") or [])
            account.last_scanned_at = _now()
            account.updated_at = _now()
            scan.status = "completed"
            scan.source = str(result.get("source") or "")
            scan.found_count = found
            scan.finished_at = _now()
            session.commit()
            self._dispatch_ntfy(session, account)
            self._set(active=False, phase="completed", found_count=found, error=None)
        except InterruptedError as exc:
            self._finish_error(session, scan, "cancelled", "cancelled", str(exc))
        except MessageAdapterError as exc:
            self._finish_error(session, scan, "needs_human", exc.code, str(exc))
        except (ChromeRuntimeError, ValueError, TimeoutError, OSError) as exc:
            self._finish_error(session, scan, "failed", type(exc).__name__.lower(), str(exc))
        except Exception as exc:  # noqa: BLE001
            self._finish_error(session, scan, "failed", "unexpected", str(exc))
        finally:
            if runtime is not None:
                runtime.stop()
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
            session.commit()
        self._set(active=False, phase=status, error=error, error_code=code)

    @staticmethod
    def _upsert_messages(
        session: Any, account: ReachMessageAccount, messages: list[dict[str, Any]]
    ) -> int:
        now = _now()
        found = 0
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
            if row is None:
                row = ReachMessage(
                    customer_id=account.customer_id,
                    account_id=account.id,
                    platform=account.platform,
                    external_key=key,
                    first_seen_at=now,
                    unread=True,
                )
                session.add(row)
            row.sender = str(item.get("sender") or "")[:128]
            row.summary = str(item.get("summary") or "")[:280]
            row.reply_url = validate_official_url(account.platform, str(item.get("reply_url") or ""))
            row.source = str(item.get("source") or "dom_heuristic")[:32]
            row.confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            row.last_seen_at = now
            found += 1
        session.flush()
        return found

    @staticmethod
    def _dispatch_ntfy(session: Any, account: ReachMessageAccount) -> None:
        from engine.reach.notifications import redact_summary, send_ntfy_async

        messages = session.scalars(
            select(ReachMessage).where(
                ReachMessage.account_id == account.id,
                ReachMessage.unread.is_(True),
            )
        ).all()
        for message in messages:
            existing = session.scalar(
                select(ReachNotificationEvent).where(
                    ReachNotificationEvent.customer_id == account.customer_id,
                    ReachNotificationEvent.message_id == message.id,
                    ReachNotificationEvent.channel == "ntfy",
                )
            )
            if existing is not None:
                continue
            event = ReachNotificationEvent(
                customer_id=account.customer_id,
                message_id=message.id,
                channel="ntfy",
                status="queued",
                is_test=False,
            )
            session.add(event)
            session.commit()
            event_id = event.id

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
                account.customer_id,
                title=f"{account.platform} · {redact_summary(message.sender) or '新消息'}",
                summary=redact_summary(message.summary),
                reply_url=message.reply_url,
                audit=audit,
            )


message_sync = MessageSync()
