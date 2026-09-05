"""Unified append-only, redacted operational audit log."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import OperationLog, RenderOutput
from engine.catalog.serial import format_display_no

_SECRET_KEYS = re.compile(
    r"(cookie|token|secret|password|authorization|验证码|短信码|正文|body)",
    re.IGNORECASE,
)


def _redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(child_key): _redact(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child, key=key) for child in value[:100]]
    if isinstance(value, str):
        text = re.sub(
            r"(验证码|短信码|安全码)\s*[:：]?\s*[A-Za-z0-9-]{4,12}",
            r"\1[已隐藏]",
            value,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"([?&](?:token|api_key|key|secret|authorization)=)[^&\s]+",
            r"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(?:sessionid|cookie|authorization)\s*[:=]\s*[^\s,;]+",
            "[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
        return text[:2000]
    return value


def write_log(
    session: Session,
    *,
    customer_id: int | None,
    category: str,
    event: str,
    message: str,
    level: str = "info",
    scope: str = "customer",
    stage: str = "",
    source_type: str = "",
    source_id: str | int = "",
    correlation_id: str = "",
    account: str = "",
    platform: str = "",
    retry_no: int = 0,
    duration_ms: int | None = None,
    details: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    commit: bool = False,
) -> OperationLog:
    row = OperationLog(
        customer_id=customer_id,
        scope=scope,
        category=category,
        level=level,
        event=event,
        stage=stage,
        message=str(_redact(message))[:1000],
        source_type=source_type,
        source_id=str(source_id),
        correlation_id=correlation_id,
        account=account,
        platform=platform,
        retry_no=retry_no,
        duration_ms=duration_ms,
        details_json=_redact(details or {}),
        evidence_json=_redact(evidence or {}),
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
    return row


def log_to_dict(row: OperationLog, *, session: Session | None = None) -> dict[str, Any]:
    details = row.details_json if isinstance(row.details_json, dict) else {}
    account = str(row.account or details.get("account") or details.get("chrome_profile") or "").strip()
    platform = str(row.platform or details.get("platform") or "").strip()
    result = {
        "id": row.id,
        "customer_id": row.customer_id,
        "scope": row.scope,
        "category": row.category,
        "level": row.level,
        "event": row.event,
        "stage": row.stage,
        "message": row.message,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "correlation_id": row.correlation_id,
        "account": account,
        "platform": platform,
        "retry_no": row.retry_no,
        "duration_ms": row.duration_ms,
        "details": details,
        "evidence": row.evidence_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    job_id: int | None = None
    raw_job = details.get("job_id")
    if raw_job in (None, "") and str(row.correlation_id or "").startswith("job:"):
        raw_job = str(row.correlation_id).split(":", 1)[-1]
    if raw_job in (None, "") and row.source_type == "job":
        raw_job = row.source_id
    try:
        job_id = int(raw_job) if raw_job not in (None, "") else None
    except (TypeError, ValueError):
        job_id = None
    if job_id is not None:
        result["job_id"] = job_id

    if session is None:
        return result
    output_id: int | None = None
    raw_output_id = (
        row.source_id
        if row.source_type == "render_output"
        else details.get("output_id")
    )
    try:
        output_id = int(raw_output_id) if raw_output_id not in (None, "") else None
    except (TypeError, ValueError):
        output_id = None
    output = session.get(RenderOutput, output_id) if output_id is not None else None
    if output is None and job_id is not None:
        from sqlalchemy import select

        output = session.scalar(
            select(RenderOutput)
            .where(RenderOutput.job_id == job_id)
            .order_by(RenderOutput.id.desc())
            .limit(1)
        )
    if output is not None:
        result["display_no"] = output.display_no
        result["display_label"] = (
            format_display_no(output.display_no) if output.display_no is not None else ""
        )
        result["output_path"] = output.output_path or ""
        result["output_id"] = output.id
        if result.get("job_id") is None and output.job_id is not None:
            result["job_id"] = int(output.job_id)
    elif details.get("display_no") is not None:
        try:
            result["display_no"] = int(details["display_no"])
            result["display_label"] = format_display_no(int(details["display_no"]))
        except (TypeError, ValueError):
            pass
    return result
