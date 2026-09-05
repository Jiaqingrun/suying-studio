"""Mandatory, idempotent decisions for rendered outputs.

READY_GATE success (ready_gate.ok) is always approved — including outputs that
landed in state=review for soft consistency signals. Hard failures stay
rejected; only missing/failed gate evidence is uncertain and needs a person.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from engine.catalog.db import Customer, Job, RenderOutput, ReviewItem, log_event

logger = logging.getLogger(__name__)

AUTO_APPROVE_NOTE = "自动通过：成片已通过出片门禁"
AUTO_REJECT_NOTE = "自动拒绝：硬失败不可发布"
AUTO_UNCERTAIN_NOTE = "待人工：审片状态或证据冲突"
BATCH_MANUAL_APPROVE_NOTE = "一键通过：人工批量审核"
RECONCILE_APPROVE_NOTE = "自动通过：出片门禁重验通过"
RECONCILE_UNCERTAIN_NOTE = "待人工：出片门禁重验未通过"
ARCHIVE_MISSING_NOTE = "归档出队：成片文件缺失，无法核验出片门禁"
ARCHIVE_GATE_FAIL_NOTE = "归档出队：出片门禁重验未通过，退出待审"
ARCHIVE_MANUAL_NOTE = "归档出队：人工确认不再审发该旧片"
# Legacy English notes kept for idempotent matching of older rows.
LEGACY_AUTO_APPROVE_NOTE = "[auto_approve] ready_gate"
LEGACY_AUTO_REJECT_NOTE = "[auto_reject] hard_failure"
LEGACY_AUTO_UNCERTAIN_NOTE = "[auto_uncertain] review_or_evidence_conflict"
KNOWN_AUTO_NOTES = {
    "approved": {
        AUTO_APPROVE_NOTE,
        LEGACY_AUTO_APPROVE_NOTE,
        RECONCILE_APPROVE_NOTE,
        BATCH_MANUAL_APPROVE_NOTE,
    },
    "rejected": {
        AUTO_REJECT_NOTE,
        LEGACY_AUTO_REJECT_NOTE,
        ARCHIVE_MISSING_NOTE,
        ARCHIVE_GATE_FAIL_NOTE,
        ARCHIVE_MANUAL_NOTE,
    },
    "uncertain": {
        AUTO_UNCERTAIN_NOTE,
        LEGACY_AUTO_UNCERTAIN_NOTE,
        RECONCILE_UNCERTAIN_NOTE,
    },
}
# Mandatory auto-approve still writes OperationLog (category=quality) so the
# 日志 Tab can show “审片自动通过”. It must NOT also write JobEvent, or the
# amalgamated legacy dump floods 全部 after backfill.
AUTO_APPROVE_SOURCES = frozenset(
    {"worker", "backfill", "migration", "test", "smoke", "reconcile"}
)
BACKFILL_LIMIT_DEFAULT = 50
BATCH_APPROVE_LIMIT_DEFAULT = 50
RECONCILE_LIMIT_DEFAULT = 50


def _emit_review_ops_log(
    session: Session,
    customer: Customer | None,
    output: RenderOutput,
    *,
    status: str,
    source: str,
    review_id: int,
    pack_result: dict[str, Any] | None = None,
) -> None:
    """Write structured ops logs for review outcomes (visible under 日志 → READY/质检)."""
    pack = pack_result or {}
    auto = source in AUTO_APPROVE_SOURCES
    if status == "approved":
        if source in {"manual", "batch"}:
            message = "审片人工通过"
        elif auto:
            message = "审片自动通过"
        else:
            message = "审片通过"
    elif status == "rejected":
        message = "审片自动拒绝" if auto else "审片拒绝"
    elif status == "uncertain":
        message = "审片进入人工队列"
    else:
        message = f"审片决定：{status}"
    level = "info" if status == "approved" else "warning"
    try:
        from engine.ops.audit_log import write_log

        write_log(
            session,
            customer_id=customer.id if customer else None,
            category="quality",
            event=f"review_{status}",
            message=message,
            level=level,
            stage="review",
            source_type="render_output",
            source_id=output.id,
            correlation_id=f"output:{output.id}",
            details={
                "output_id": output.id,
                "job_id": output.job_id,
                "review_id": review_id,
                "decision": status,
                "source": source,
                "pack_ok": bool(pack.get("ok")) if status == "approved" else None,
                "pack_error": pack.get("error"),
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("review ops log skipped", exc_info=True)
    # JobEvent only for non-approve anomalies on the job timeline (not 日志 flood).
    if status != "approved" and output.job_id:
        log_event(
            session,
            output.job_id,
            level,
            message,
            {
                "output_id": output.id,
                "review_id": review_id,
                "decision": status,
                "source": source,
            },
        )


def review_prefs(profile: dict[str, Any] | None) -> dict[str, Any]:
    # Kept in the public profile shape for compatibility with older Apps.
    return {"auto_approve_on_ready": True, "policy": "mandatory_ready_gate_v2"}

def merge_review_prefs(
    profile: dict[str, Any] | None, patch: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge review prefs into a copy of profile; returns full profile dict."""
    out = dict(profile) if isinstance(profile, dict) else {}
    out["review"] = review_prefs(out)
    return out


def auto_approve_enabled(customer: Customer | None) -> bool:
    """Compatibility helper: the policy is mandatory for every real customer."""
    return customer is not None


def has_verified_ready_gate(qc_json: dict[str, Any] | None) -> bool:
    gate = (qc_json or {}).get("ready_gate") if isinstance(qc_json, dict) else None
    if not isinstance(gate, dict):
        return False
    return gate.get("ok") is True


def ready_gate_snapshot(qc_json: dict[str, Any] | None) -> dict[str, Any]:
    """Compact gate fields for list APIs and UI badges."""
    gate = (qc_json or {}).get("ready_gate") if isinstance(qc_json, dict) else None
    if not isinstance(gate, dict):
        return {"ready_gate_ok": False, "ready_gate_known": False, "ready_gate_fails": []}
    fails = [str(x) for x in (gate.get("fails") or []) if x][:20]
    return {
        "ready_gate_ok": gate.get("ok") is True,
        "ready_gate_known": "ok" in gate,
        "ready_gate_fails": fails,
        "ready_gate_fail_count": int(gate.get("fail_count") or len(fails) or 0),
    }


def latest_review(session: Session, output_id: int) -> ReviewItem | None:
    return session.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.render_output_id == output_id,
            ReviewItem.is_current.is_(True),
        )
        .order_by(ReviewItem.id.desc())
        .limit(1)
    ).first()


def record_review_decision(
    session: Session,
    output: RenderOutput,
    *,
    status: str,
    note: str,
    source: str,
    decision_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> tuple[ReviewItem, bool]:
    """Append a decision while atomically retaining exactly one current row.

    The no-op output update obtains SQLite's writer lock before the idempotency
    read, so concurrent workers cannot both append the same automatic decision.
    """
    if status not in {"approved", "rejected", "uncertain"}:
        raise ValueError(f"invalid review status: {status}")
    session.execute(
        text("UPDATE render_outputs SET id=id WHERE id=:output_id"),
        {"output_id": output.id},
    )
    if decision_key:
        keyed = session.scalar(
            select(ReviewItem).where(ReviewItem.decision_key == decision_key)
        )
        if keyed:
            return keyed, False
    current = latest_review(session, output.id)
    known = KNOWN_AUTO_NOTES.get(status, set())
    cur_note = (current.note or "") if current else ""
    if current and current.status == status and (
        cur_note == note or (cur_note in known and note in known)
    ):
        return current, False
    session.execute(
        update(ReviewItem)
        .where(
            ReviewItem.render_output_id == output.id,
            ReviewItem.is_current.is_(True),
        )
        .values(is_current=False)
    )
    item = ReviewItem(
        render_output_id=output.id,
        status=status,
        note=note,
        is_current=True,
        decision_source=source,
        decision_key=decision_key,
        evidence_json=evidence or {},
        decided_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.flush()
    return item, True


def classify_output_decision(output: RenderOutput) -> tuple[str, str] | None:
    """Return the mandatory current decision implied by state and evidence."""
    if output.state in {"failed", "rejected"}:
        return "rejected", AUTO_REJECT_NOTE
    gate_ok = has_verified_ready_gate(
        output.qc_json if isinstance(output.qc_json, dict) else None
    )
    # Gate pass is authoritative: soft needs_review / migration review rows
    # must not stay in the human queue once ready_gate.ok is proven.
    if output.state in {"ready", "review"} and gate_ok:
        return "approved", AUTO_APPROVE_NOTE
    if output.state in {"ready", "review"}:
        return "uncertain", AUTO_UNCERTAIN_NOTE
    return None


def ensure_output_decision(
    session: Session,
    customer: Customer | None,
    output: RenderOutput,
    *,
    source: str,
    export_pack: bool = False,
) -> dict[str, Any]:
    if not customer:
        return {"ok": False, "skipped": "no_customer"}
    classified = classify_output_decision(output)
    if not classified:
        return {"ok": False, "skipped": "non_final_state", "state": output.state}
    status, note = classified
    if status == "approved":
        output.state = "ready"
    elif status == "rejected":
        output.state = "failed"
    elif status == "uncertain" and output.state == "ready":
        output.state = "review"
    item, created = record_review_decision(
        session,
        output,
        status=status,
        note=note,
        source=source,
        decision_key=f"mandatory-review-v2:{output.id}:{status}",
        evidence={
            "output_state": output.state,
            "ready_gate_ok": has_verified_ready_gate(
                output.qc_json if isinstance(output.qc_json, dict) else None
            ),
        },
    )
    pack_result: dict[str, Any] = {"ok": False, "skipped": "not_approved"}
    if status == "approved" and export_pack:
        # Idempotent reconciliation also retries a previous pack failure.
        pack_result = ensure_publish_pack(session, customer, output)
    if created:
        _emit_review_ops_log(
            session,
            customer,
            output,
            status=status,
            source=source,
            review_id=item.id,
            pack_result=pack_result,
        )
    return {
        "ok": True,
        "output_id": output.id,
        "review_id": item.id,
        "decision": status,
        "created": created,
        "skipped": None if created else f"already_{status}",
        "source": source,
        "pack": pack_result,
    }


def ensure_publish_pack(
    session: Session,
    customer: Customer,
    output: RenderOutput,
    *,
    force: bool = False,
    expression_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize and persist the mandatory four-video-platform pack contract."""
    from engine.catalog.keyword_pack import get_active_pack, get_pack_by_id
    from engine.pack.publish import PACK_VERSION, export_publish_pack
    from engine.reach.business_scope import VIDEO_PLATFORMS
    from engine.reach.publish_assets import validate_pack_contract

    # Serialize pack reconciliation per output across worker/API/backfill sessions.
    session.execute(
        text("UPDATE render_outputs SET id=id WHERE id=:output_id"),
        {"output_id": output.id},
    )
    session.refresh(output)
    if not output.output_path or not Path(output.output_path).is_file():
        error = "成片文件缺失"
        output.pack_status = "pack_failed"
        output.pack_error = error
        output.state = "asset_blocked"
        output.platform_asset_status = {
            platform: {"status": "blocked", "error": error}
            for platform in sorted(VIDEO_PLATFORMS)
        }
        return {"ok": False, "error": error, "status": output.pack_status}
    if not force and not expression_overrides and output.pack_status == "ready" and output.pack_dir:
        try:
            contract = validate_pack_contract(output.pack_dir)
            output.pack_version = str(contract["pack_version"])
            output.platform_asset_status = dict(contract["platform_asset_status"])
            output.pack_error = ""
            return {
                "ok": True,
                "pack_dir": output.pack_dir,
                "pack_version": output.pack_version,
                "platform_asset_status": output.platform_asset_status,
                "idempotent": True,
            }
        except Exception:
            # Rebuild stale or externally damaged material.
            pass
    output.pack_status = "packing"
    output.pack_error = ""
    session.flush()
    try:
        frozen_id = None
        if output.job_id:
            from engine.catalog.db import Job

            job = session.get(Job, output.job_id)
            snapshot = job.config_snapshot_json if job and isinstance(job.config_snapshot_json, dict) else {}
            frozen = snapshot.get("keyword_pack") if isinstance(snapshot.get("keyword_pack"), dict) else {}
            frozen_id = int(frozen["id"]) if frozen.get("id") else None
        pack_row = (
            get_pack_by_id(session, frozen_id, customer_id=customer.id)
            if frozen_id
            else get_active_pack(session, customer.name)
        )
        pack_data = pack_row.data_json if pack_row else None
        manifest = export_publish_pack(
            output_path=Path(output.output_path),
            sidecar_path=Path(output.sidecar_path) if output.sidecar_path else None,
            profile=customer.profile_json if isinstance(customer.profile_json, dict) else {},
            pack_data=pack_data,
            expression_overrides=expression_overrides,
        )
        contract = validate_pack_contract(str(manifest.get("pack_dir") or ""))
        output.pack_status = "ready"
        output.pack_dir = str(contract["pack_dir"])
        output.pack_error = ""
        output.pack_version = str(contract["pack_version"] or PACK_VERSION)
        output.platform_asset_status = dict(contract["platform_asset_status"])
        if output.state == "asset_blocked":
            output.state = "ready"
        return {
            "ok": True,
            "pack_dir": output.pack_dir,
            "pack_version": output.pack_version,
            "platform_asset_status": output.platform_asset_status,
            "idempotent": False,
        }
    except Exception as e:  # noqa: BLE001
        error = str(e)[:2000]
        logger.warning("mandatory publish pack failed output=%s: %s", output.id, error)
        output.pack_status = "pack_failed"
        output.pack_error = error
        output.pack_version = PACK_VERSION
        output.state = "asset_blocked"
        output.platform_asset_status = {
            platform: {"status": "blocked", "error": error}
            for platform in sorted(VIDEO_PLATFORMS)
        }
        return {"ok": False, "error": error, "status": output.pack_status}


def maybe_auto_approve_ready(
    session: Session,
    customer: Customer | None,
    output: RenderOutput,
    *,
    source: str = "worker",
    export_pack: bool = True,
) -> dict[str, Any]:
    """Approve a READY_GATE-passed ready output. Idempotent and mandatory.

    Returns a status dict; never raises for policy skips.
    """
    if not customer:
        return {"ok": False, "skipped": "no_customer"}
    if output.state != "ready":
        return {"ok": False, "skipped": "not_ready", "state": output.state}
    if not has_verified_ready_gate(output.qc_json if isinstance(output.qc_json, dict) else None):
        return {"ok": False, "skipped": "ready_gate_missing"}

    return ensure_output_decision(
        session, customer, output, source=source, export_pack=export_pack
    )


def backfill_auto_approve(
    session: Session,
    customer: Customer,
    *,
    limit: int = BACKFILL_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """Reconcile final outputs to mandatory current decisions (bounded)."""
    lim = max(1, min(int(limit or BACKFILL_LIMIT_DEFAULT), BACKFILL_LIMIT_DEFAULT))
    rows = session.scalars(
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .where(
            Job.customer_id == customer.id,
            RenderOutput.state.in_(("ready", "review", "failed", "asset_blocked")),
        )
        .order_by(RenderOutput.id.desc())
        .limit(lim * 3)  # oversample; filter already-approved below
    ).all()

    decisions: list[dict[str, Any]] = []
    counts = {"approved": 0, "uncertain": 0, "rejected": 0}
    errors: list[dict[str, Any]] = []
    skipped = 0
    for output in rows:
        if len(decisions) >= lim:
            break
        try:
            current = latest_review(session, output.id)
            if output.state == "asset_blocked" and current and current.status == "approved":
                pack = ensure_publish_pack(session, customer, output)
                decisions.append(
                    {
                        "output_id": output.id,
                        "review_id": current.id,
                        "decision": "approved",
                        "pack": pack,
                    }
                )
                if pack.get("ok"):
                    counts["approved"] += 1
                else:
                    errors.append({"output_id": output.id, "error": pack.get("error")})
                continue
            result = ensure_output_decision(
                session, customer, output, source="backfill", export_pack=True
            )
            if result.get("ok") and (
                result.get("created")
                or (
                    result.get("decision") == "approved"
                    and not (result.get("pack") or {}).get("ok")
                )
            ):
                decision = str(result.get("decision"))
                if result.get("created"):
                    counts[decision] += 1
                decisions.append(
                    {
                        "output_id": output.id,
                        "review_id": result.get("review_id"),
                        "decision": decision,
                        "pack": result.get("pack"),
                    }
                )
                pack = result.get("pack") or {}
                if decision == "approved" and not pack.get("ok") and not pack.get("skipped"):
                    errors.append(
                        {"output_id": output.id, "error": pack.get("error") or "pack_failed"}
                    )
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            errors.append({"output_id": output.id, "error": str(e)})

    session.commit()
    return {
        "ok": True,
        "approved": counts["approved"],
        "uncertain": counts["uncertain"],
        "rejected": counts["rejected"],
        "skipped": skipped,
        "items": decisions,
        "errors": errors,
        "limit": lim,
    }


def _sidecar_meta(output: RenderOutput) -> dict[str, Any]:
    if not output.sidecar_path:
        return {}
    path = Path(output.sidecar_path)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    meta = data.get("meta") if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


def known_issue_flags(
    output: RenderOutput,
    *,
    video_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Soft quality badges aligned with the Review page list API."""
    from engine.ops.publish_trail import (
        tts_lock_violation,
        voice_flags_from_meta,
    )

    media_ok = bool(output.output_path and Path(output.output_path).is_file())
    meta = _sidecar_meta(output)
    has_voice, subtitle_burned = voice_flags_from_meta(meta)
    violation = tts_lock_violation(meta, video_lock)
    flags = {
        "media_missing": not media_ok,
        "missing_voice": not has_voice,
        "missing_subtitle": not subtitle_burned,
        "tts_noncompliant": bool(violation),
    }
    flags["has_known_issue"] = any(flags.values())
    return flags


def batch_manual_approve(
    session: Session,
    customer: Customer,
    *,
    skip_known_issues: bool = True,
    limit: int = BATCH_APPROVE_LIMIT_DEFAULT,
    video_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Human bulk-approve open uncertain outputs that already pass READY_GATE."""
    lim = max(
        1,
        min(int(limit or BATCH_APPROVE_LIMIT_DEFAULT), BATCH_APPROVE_LIMIT_DEFAULT),
    )
    if video_lock is None:
        try:
            from engine.pack.video_lock import load_video_lock

            video_lock = load_video_lock(
                customer.name, output_root=getattr(customer, "output_root", None)
            )
        except Exception:  # noqa: BLE001
            video_lock = None

    rows = session.scalars(
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .join(
            ReviewItem,
            (ReviewItem.render_output_id == RenderOutput.id)
            & (ReviewItem.is_current.is_(True)),
        )
        .where(
            Job.customer_id == customer.id,
            ReviewItem.status == "uncertain",
            RenderOutput.state.in_(("ready", "review")),
        )
        .order_by(RenderOutput.id.desc())
        .limit(lim * 3)
    ).all()

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    approved = 0
    skipped_known = 0
    skipped_gate = 0
    skipped_media = 0

    for output in rows:
        if approved + len(errors) >= lim:
            break
        try:
            gate_ok = has_verified_ready_gate(
                output.qc_json if isinstance(output.qc_json, dict) else None
            )
            if not gate_ok:
                skipped_gate += 1
                continue
            flags = known_issue_flags(output, video_lock=video_lock)
            if flags["media_missing"]:
                # File missing is always a hard skip for batch approve.
                skipped_media += 1
                continue
            soft_issue = bool(
                flags["missing_voice"]
                or flags["missing_subtitle"]
                or flags["tts_noncompliant"]
            )
            if skip_known_issues and soft_issue:
                skipped_known += 1
                continue

            output.state = "ready"
            item, created = record_review_decision(
                session,
                output,
                status="approved",
                note=BATCH_MANUAL_APPROVE_NOTE,
                source="manual",
                evidence={
                    "batch": True,
                    "ready_gate_ok": True,
                    "skip_known_issues": skip_known_issues,
                    "known_issue_flags": flags,
                },
            )
            if not created:
                continue
            pack = ensure_publish_pack(session, customer, output)
            approved += 1
            items.append(
                {
                    "output_id": output.id,
                    "review_id": item.id,
                    "decision": "approved",
                    "pack_ok": bool(pack.get("ok")),
                    "pack_error": pack.get("error"),
                }
            )
            _emit_review_ops_log(
                session,
                customer,
                output,
                status="approved",
                source="batch",
                review_id=item.id,
                pack_result=pack,
            )
        except Exception as e:  # noqa: BLE001
            errors.append({"output_id": output.id, "error": str(e)[:500]})

    session.commit()
    return {
        "ok": True,
        "approved": approved,
        "skipped_known": skipped_known,
        "skipped_gate": skipped_gate,
        "skipped_media": skipped_media,
        "items": items,
        "errors": errors,
        "limit": lim,
        "skip_known_issues": skip_known_issues,
    }


def _load_sidecar_document(output: RenderOutput) -> dict[str, Any]:
    if not output.sidecar_path:
        return {}
    path = Path(output.sidecar_path)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _persist_ready_gate_evidence(
    output: RenderOutput,
    gate: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """Write gate result into qc_json (+ sidecar when present). Never invents ok=true."""
    compact = {
        "ok": bool(gate.get("ok")),
        "fail_count": int(gate.get("fail_count") or len(gate.get("fails") or []) or 0),
        "fails": list(gate.get("fails") or [])[:40],
        "checks": gate.get("checks") if isinstance(gate.get("checks"), dict) else {},
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "reconcile_source": source,
    }
    qc = dict(output.qc_json) if isinstance(output.qc_json, dict) else {}
    if compact["ok"]:
        qc["passed"] = True
    qc["ready_gate"] = compact
    reasons = list(qc.get("reasons") or [])
    if not compact["ok"]:
        for fail in compact["fails"]:
            if fail not in reasons:
                reasons.append(fail)
        qc["reasons"] = reasons[:80]
    output.qc_json = qc

    if output.sidecar_path:
        path = Path(output.sidecar_path)
        try:
            import json

            data = _load_sidecar_document(output)
            data["ready_gate"] = compact
            if path.parent.is_dir():
                path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except (OSError, TypeError, ValueError):
            logger.debug("sidecar ready_gate write skipped", exc_info=True)
    return compact


def _media_present(output: RenderOutput) -> bool:
    return bool(output.output_path and Path(output.output_path).is_file())


def _open_uncertain_outputs(
    session: Session,
    customer: Customer,
    *,
    limit: int,
    output_id: int | None = None,
) -> list[RenderOutput]:
    stmt = (
        select(RenderOutput)
        .join(Job, RenderOutput.job_id == Job.id)
        .join(
            ReviewItem,
            (ReviewItem.render_output_id == RenderOutput.id)
            & (ReviewItem.is_current.is_(True)),
        )
        .where(
            Job.customer_id == customer.id,
            ReviewItem.status == "uncertain",
            RenderOutput.state.in_(("ready", "review")),
        )
        .order_by(RenderOutput.id.desc())
    )
    if output_id is not None:
        stmt = stmt.where(RenderOutput.id == int(output_id))
    return list(session.scalars(stmt.limit(max(1, min(int(limit), RECONCILE_LIMIT_DEFAULT * 3)))).all())


def archive_unusable_output(
    session: Session,
    customer: Customer,
    output: RenderOutput,
    *,
    reason: str = "manual",
    note: str | None = None,
) -> dict[str, Any]:
    """Close open uncertain queue without ever minting approved (no READY_GATE bypass)."""
    note_map = {
        "missing": ARCHIVE_MISSING_NOTE,
        "gate_fail": ARCHIVE_GATE_FAIL_NOTE,
        "manual": ARCHIVE_MANUAL_NOTE,
    }
    final_note = (note or note_map.get(reason) or ARCHIVE_MANUAL_NOTE).strip()
    prior = latest_review(session, output.id)
    if prior and prior.status == "rejected" and prior.note == final_note:
        return {
            "ok": True,
            "output_id": output.id,
            "action": "already_archived",
            "decision": "rejected",
            "review_id": prior.id,
        }
    output.state = "failed"
    item, created = record_review_decision(
        session,
        output,
        status="rejected",
        note=final_note,
        source="archive",
        decision_key=f"archive-unusable-v1:{output.id}:{reason}",
        evidence={
            "archive_reason": reason,
            "ready_gate_ok": has_verified_ready_gate(
                output.qc_json if isinstance(output.qc_json, dict) else None
            ),
            "media_ok": _media_present(output),
            **ready_gate_snapshot(
                output.qc_json if isinstance(output.qc_json, dict) else None
            ),
        },
    )
    if created:
        _emit_review_ops_log(
            session,
            customer,
            output,
            status="rejected",
            source="archive",
            review_id=item.id,
        )
    purge: dict[str, Any] = {}
    try:
        from engine.catalog.output_purge import purge_output_media

        purge = purge_output_media(
            session,
            output,
            reason=f"archive_{reason}",
            actor="archive",
        )
    except Exception:  # noqa: BLE001
        logger.exception("archive media purge failed output=%s", output.id)
    return {
        "ok": True,
        "output_id": output.id,
        "action": "archived",
        "decision": "rejected",
        "created": created,
        "review_id": item.id,
        "note": final_note,
        "reason": reason,
        "purge": purge,
    }


def reconcile_ready_gate_output(
    session: Session,
    customer: Customer,
    output: RenderOutput,
    *,
    require_ollama: bool | None = None,
    archive_missing: bool = True,
    archive_gate_fail: bool = False,
    export_pack: bool = True,
) -> dict[str, Any]:
    """Re-evaluate READY_GATE on disk evidence and close the queue when possible.

    - Gate ok → write evidence + mandatory approve (+ pack)
    - File missing → optional archive out of open queue
    - Gate fail → write fails; optional archive, else remain uncertain with readable fails
    Never invents ready_gate.ok=true without evaluate_ready_gate.
    """
    from engine.qc.ready_gate import evaluate_ready_gate

    if output.state not in {"ready", "review"}:
        return {
            "ok": False,
            "output_id": output.id,
            "action": "skipped_state",
            "state": output.state,
        }

    if has_verified_ready_gate(
        output.qc_json if isinstance(output.qc_json, dict) else None
    ):
        decision = ensure_output_decision(
            session, customer, output, source="reconcile", export_pack=export_pack
        )
        return {
            "ok": True,
            "output_id": output.id,
            "action": "already_gate_ok",
            "decision": decision.get("decision"),
            "pack": decision.get("pack"),
            "ready_gate_ok": True,
        }

    if not _media_present(output):
        if archive_missing:
            archived = archive_unusable_output(
                session, customer, output, reason="missing"
            )
            return {
                "ok": True,
                "output_id": output.id,
                "action": "archived_missing",
                "decision": "rejected",
                "ready_gate_ok": False,
                **{k: archived[k] for k in ("review_id", "note") if k in archived},
            }
        return {
            "ok": False,
            "output_id": output.id,
            "action": "media_missing",
            "ready_gate_ok": False,
            "error": "成片文件缺失，无法重验出片门禁",
        }

    if require_ollama is None:
        try:
            from engine.config.settings import load_settings

            require_ollama = bool(
                getattr(load_settings(), "ollama_narration_enabled", False)
            )
        except Exception:  # noqa: BLE001
            require_ollama = False

    sidecar = _load_sidecar_document(output)
    gate = evaluate_ready_gate(
        Path(output.output_path),
        require_ollama=bool(require_ollama),
        sidecar=sidecar or None,
    )
    compact = _persist_ready_gate_evidence(output, gate, source="reconcile")
    session.flush()

    if compact["ok"]:
        if output.state != "ready":
            output.state = "ready"
        decision = ensure_output_decision(
            session,
            customer,
            output,
            source="reconcile",
            export_pack=export_pack,
        )
        # Prefer explicit reconcile note when a new approved row was created.
        current = latest_review(session, output.id)
        if (
            current
            and current.status == "approved"
            and current.decision_source == "reconcile"
            and current.note != RECONCILE_APPROVE_NOTE
        ):
            # ensure_output_decision used AUTO_APPROVE_NOTE; overwrite label once.
            item, _ = record_review_decision(
                session,
                output,
                status="approved",
                note=RECONCILE_APPROVE_NOTE,
                source="reconcile",
                evidence={
                    "ready_gate_ok": True,
                    "reconcile": True,
                    "fail_count": 0,
                },
            )
            current = item
        return {
            "ok": True,
            "output_id": output.id,
            "action": "approved",
            "decision": "approved",
            "ready_gate_ok": True,
            "ready_gate_fails": [],
            "review_id": current.id if current else decision.get("review_id"),
            "pack": decision.get("pack"),
        }

    fails = list(compact.get("fails") or [])
    if archive_gate_fail:
        archived = archive_unusable_output(
            session, customer, output, reason="gate_fail"
        )
        return {
            "ok": True,
            "output_id": output.id,
            "action": "archived_gate_fail",
            "decision": "rejected",
            "ready_gate_ok": False,
            "ready_gate_fails": fails[:20],
            "review_id": archived.get("review_id"),
            "note": archived.get("note"),
        }

    note = (
        f"{RECONCILE_UNCERTAIN_NOTE}（{int(compact.get('fail_count') or len(fails))}项）"
    )
    item, created = record_review_decision(
        session,
        output,
        status="uncertain",
        note=note,
        source="reconcile",
        evidence={
            "ready_gate_ok": False,
            "fails": fails[:20],
            "fail_count": compact.get("fail_count"),
            "reconcile": True,
        },
    )
    if output.state == "ready":
        output.state = "review"
    if created:
        _emit_review_ops_log(
            session,
            customer,
            output,
            status="uncertain",
            source="reconcile",
            review_id=item.id,
        )
    return {
        "ok": True,
        "output_id": output.id,
        "action": "gate_failed",
        "decision": "uncertain",
        "ready_gate_ok": False,
        "ready_gate_fails": fails[:20],
        "review_id": item.id,
        "created": created,
        "note": note,
    }


def batch_reconcile_ready_gate(
    session: Session,
    customer: Customer,
    *,
    limit: int = RECONCILE_LIMIT_DEFAULT,
    archive_missing: bool = True,
    archive_gate_fail: bool = False,
    export_pack: bool = True,
) -> dict[str, Any]:
    """Bounded reconcile for open uncertain outputs (fail-closed)."""
    lim = max(1, min(int(limit or RECONCILE_LIMIT_DEFAULT), RECONCILE_LIMIT_DEFAULT))
    rows = _open_uncertain_outputs(session, customer, limit=lim * 3)
    items: list[dict[str, Any]] = []
    counts = {
        "approved": 0,
        "archived_missing": 0,
        "archived_gate_fail": 0,
        "gate_failed": 0,
        "already_gate_ok": 0,
        "skipped": 0,
        "errors": 0,
    }
    errors: list[dict[str, Any]] = []

    for output in rows:
        if len(items) >= lim:
            break
        try:
            result = reconcile_ready_gate_output(
                session,
                customer,
                output,
                archive_missing=archive_missing,
                archive_gate_fail=archive_gate_fail,
                export_pack=export_pack,
            )
            action = str(result.get("action") or "skipped")
            if action in counts:
                counts[action] += 1
            elif action.startswith("skipped"):
                counts["skipped"] += 1
            items.append(result)
            if result.get("error") and not result.get("ok"):
                errors.append(
                    {
                        "output_id": output.id,
                        "error": str(result.get("error"))[:500],
                    }
                )
        except Exception as e:  # noqa: BLE001
            counts["errors"] += 1
            errors.append({"output_id": output.id, "error": str(e)[:500]})

    session.commit()
    return {
        "ok": True,
        "limit": lim,
        "processed": len(items),
        "approved": counts["approved"] + counts["already_gate_ok"],
        "archived_missing": counts["archived_missing"],
        "archived_gate_fail": counts["archived_gate_fail"],
        "gate_failed": counts["gate_failed"],
        "already_gate_ok": counts["already_gate_ok"],
        "skipped": counts["skipped"],
        "items": items,
        "errors": errors,
        "archive_missing": archive_missing,
        "archive_gate_fail": archive_gate_fail,
    }
