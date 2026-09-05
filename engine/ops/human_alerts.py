"""Persistent human-required alerts with App/macOS/ntfy escalation."""

from __future__ import annotations

import shutil
import subprocess
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import HumanAlert

ESCALATION_MINUTES = (0, 5, 15, 30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(value: str, limit: int = 180) -> str:
    from engine.reach.notifications import redact_summary

    redacted = redact_summary(value or "")
    redacted = re.sub(
        r"(验证码|短信码|安全码)\s*[:：]?\s*[A-Za-z0-9-]{4,12}",
        r"\1[已隐藏]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted[:limit]


def alert_to_dict(row: HumanAlert) -> dict[str, Any]:
    return {
        "id": row.id,
        "alert_key": row.alert_key,
        "customer_id": row.customer_id,
        "kind": row.kind,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "status": row.status,
        "summary": row.safe_summary,
        "deep_link": row.deep_link,
        "channels": row.channels_json or {},
        "escalation_step": row.escalation_step,
        "next_escalation_at": (
            row.next_escalation_at.isoformat() if row.next_escalation_at else None
        ),
        "acknowledged_at": (
            row.acknowledged_at.isoformat() if row.acknowledged_at else None
        ),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_alert(
    session: Session,
    *,
    alert_key: str,
    customer_id: int,
    kind: str,
    source_type: str,
    source_id: str,
    summary: str,
    deep_link: str,
) -> HumanAlert:
    existing = session.scalar(
        select(HumanAlert).where(HumanAlert.alert_key == alert_key)
    )
    if existing:
        return existing
    row = HumanAlert(
        alert_key=alert_key,
        customer_id=customer_id,
        kind=kind,
        source_type=source_type,
        source_id=source_id,
        status="open",
        safe_summary=_safe(summary),
        deep_link=deep_link,
        channels_json={},
        escalation_step=0,
        next_escalation_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def acknowledge_alert(
    session: Session, alert_id: int, *, customer_id: int
) -> HumanAlert | None:
    row = session.get(HumanAlert, alert_id)
    if not row or row.customer_id != customer_id:
        return None
    row.status = "acknowledged"
    row.acknowledged_at = _now()
    row.next_escalation_at = None
    row.updated_at = _now()
    session.commit()
    return row


def resolve_source(
    session: Session, *, source_type: str, source_id: str
) -> int:
    rows = session.scalars(
        select(HumanAlert).where(
            HumanAlert.source_type == source_type,
            HumanAlert.source_id == source_id,
            HumanAlert.status == "open",
        )
    ).all()
    for row in rows:
        row.status = "resolved"
        row.resolved_at = _now()
        row.next_escalation_at = None
        row.updated_at = _now()
    if rows:
        session.commit()
    return len(rows)


def notification_preflight(customer_id: int) -> dict[str, Any]:
    from engine.reach.notifications import public_config

    ntfy = public_config(customer_id)
    return {
        "app": {"available": True},
        "macos": {
            "available": bool(shutil.which("osascript")),
            "permission": "system_managed",
        },
        "ntfy": {
            "available": bool(ntfy.enabled and ntfy.server_url and ntfy.topic),
            "configured": bool(ntfy.server_url and ntfy.topic),
            "enabled": ntfy.enabled,
        },
        "triple_channel_ready": bool(
            shutil.which("osascript")
            and ntfy.enabled
            and ntfy.server_url
            and ntfy.topic
        ),
    }


def _send_macos(
    summary: str, deep_link: str, *, title: str = "速影需要你处理"
) -> dict[str, Any]:
    script = (
        'display notification "{body}" with title "{title}" sound name "Glass"'
    ).format(
        body=_safe(f"{summary} · 打开速影查看", 180).replace('"', "”"),
        title=_safe(title, 60).replace('"', "”"),
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "ok": proc.returncode == 0,
            "detail": _safe(proc.stderr or proc.stdout or "delivered", 100),
            "deep_link": deep_link,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": _safe(str(exc), 100)}


def _send_ntfy(
    customer_id: int,
    summary: str,
    deep_link: str,
    *,
    title: str = "速影需要你回来处理",
) -> dict[str, Any]:
    from engine.reach.notifications import send_ntfy

    try:
        return send_ntfy(
            customer_id,
            title=title,
            summary=_safe(summary),
            reply_url=deep_link,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": _safe(str(exc), 100)}


def dispatch_due_alerts(
    session: Session, *, customer_id: int | None = None
) -> list[dict[str, Any]]:
    now = _now()
    stmt = select(HumanAlert).where(
        HumanAlert.status == "open",
        HumanAlert.next_escalation_at.is_not(None),
        HumanAlert.next_escalation_at <= now,
    )
    if customer_id is not None:
        stmt = stmt.where(HumanAlert.customer_id == customer_id)
    rows = session.scalars(stmt.order_by(HumanAlert.id.asc()).limit(50)).all()
    delivered: list[dict[str, Any]] = []
    for row in rows:
        step = min(int(row.escalation_step or 0), len(ESCALATION_MINUTES) - 1)
        history = dict(row.channels_json or {})
        event_key = f"step_{step}"
        if event_key not in history:
            history[event_key] = {
                "at": now.isoformat(),
                "app": {"ok": True, "state": "visible"},
                "macos": _send_macos(row.safe_summary, row.deep_link),
                "ntfy": _send_ntfy(
                    row.customer_id, row.safe_summary, row.deep_link
                ),
            }
        row.channels_json = history
        row.escalation_step = step + 1
        if step + 1 >= len(ESCALATION_MINUTES):
            row.next_escalation_at = None
        else:
            next_minute = ESCALATION_MINUTES[step + 1]
            row.next_escalation_at = row.created_at + timedelta(minutes=next_minute)
        row.updated_at = now
        delivered.append(alert_to_dict(row))
    if rows:
        session.commit()
    return delivered


def notify_batch_completion(
    session: Session,
    *,
    customer_id: int,
    run_id: str,
    succeeded: int,
    failed: int,
    skipped: int,
) -> HumanAlert:
    """Idempotent non-blocking App/macOS/ntfy completion notification."""
    from engine.catalog.db import Customer

    key = f"publish:{run_id}:completed"
    existing = session.scalar(select(HumanAlert).where(HumanAlert.alert_key == key))
    if existing:
        return existing
    customer = session.get(Customer, customer_id)
    summary = _safe(
        f"{customer.name if customer else f'客户#{customer_id}'} · 批次 {run_id} 已完成："
        f"成功 {succeeded} / 失败 {failed} / 跳过 {skipped}"
    )
    deep_link = f"suying://publish?run={run_id}"
    now = _now()
    row = HumanAlert(
        alert_key=key,
        customer_id=customer_id,
        kind="batch_completed",
        source_type="publish_run",
        source_id=run_id,
        status="resolved",
        safe_summary=summary,
        deep_link=deep_link,
        channels_json={
            "completion": {
                "at": now.isoformat(),
                "app": {"ok": True, "state": "visible"},
                "macos": _send_macos(summary, deep_link, title="速影批次已完成"),
                "ntfy": _send_ntfy(
                    customer_id, summary, deep_link, title="速影批次已完成"
                ),
            }
        },
        escalation_step=1,
        next_escalation_at=None,
        resolved_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    from engine.ops.audit_log import write_log

    write_log(
        session,
        customer_id=customer_id,
        category="notification",
        event="publish_batch_completed",
        stage="completion",
        message=summary,
        source_type="publish_run",
        source_id=run_id,
        correlation_id=f"publish:{run_id}",
        details={
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "channels": row.channels_json,
        },
        commit=True,
    )
    return row
