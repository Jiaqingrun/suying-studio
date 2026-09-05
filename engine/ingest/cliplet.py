from __future__ import annotations

import re
import subprocess
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, Customer, SemanticBackfillState
from engine.catalog.semantic_tags import annotate_cliplet, apply_structured_semantics
from engine.catalog.industry_pack import pack_id_for_customer
from engine.ingest.semantic_gate import (
    COARSE_SEMANTIC_SCHEMA_VERSION,
    MAX_SEMANTIC_ATTEMPTS,
    SEMANTIC_SCHEMA_VERSION,
    evaluate_semantic_gate,
)
from engine.ingest.vision_caption import analyze_cliplet_semantics
from engine.config.settings import load_settings
from engine.ingest.proxy import analysis_video_path


def detect_scenes(video_path: Path, threshold: float = 0.35) -> list[float]:
    """Return scene-change timestamps (seconds) using ffmpeg scene filter."""
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-filter:v",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    times: list[float] = []
    for line in (result.stderr or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def build_clip_ranges(duration: float, cuts: list[float], min_len: float = 2.0, max_len: float = 8.0) -> list[tuple[float, float]]:
    points = [0.0, *[t for t in cuts if 0 < t < duration], duration]
    points = sorted(set(round(p, 3) for p in points))
    ranges: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        start, end = points[i], points[i + 1]
        if end - start < min_len:
            continue
        # split long scenes into max_len chunks
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + max_len, end)
            if chunk_end - cursor >= min_len:
                ranges.append((round(cursor, 3), round(chunk_end, 3)))
            cursor = chunk_end
    if not ranges and duration > 0.05:
        # Keep ultra-short clips usable (otherwise reconcile loops forever on them)
        ranges.append((0.0, min(duration, max_len)))
    return ranges


def describe_cliplet(asset: Asset, start: float, end: float) -> str:
    """Heuristic text description (kept for callers; vision path preferred)."""
    from engine.ingest.vision_caption import heuristic_describe

    return heuristic_describe(asset, start, end)


def _pack_id_for_asset(session: Session, asset: Asset | None) -> str:
    if asset is None or not asset.customer_id:
        return "_blank"
    cust = session.get(Customer, asset.customer_id)
    if cust is None:
        return "_blank"
    return pack_id_for_customer(cust.name, cust.profile_json)


def _analyze_with_gate(
    asset: Asset,
    start: float,
    end: float,
    *,
    cache_dir: Path,
    use_vision: bool,
    max_attempts: int = MAX_SEMANTIC_ATTEMPTS,
    allow_cascade: bool = True,
    force_model: str | None = None,
    pack_id: str | None = None,
) -> tuple[dict | None, dict]:
    """Retry with distinct frame offsets, then fail closed with full audit.

    Cascade policy (pro/max): attempt on primary (9b); on gate reject /
    vision_timeout / schema_not_object switch remaining attempts to escalate
    (27b). Total attempts never exceed MAX_SEMANTIC_ATTEMPTS (3) — cascade
    consumes the same budget, it does not add extra tries.
    """
    from engine.catalog.host_profile import resolve_vision_policy
    from engine.ingest.vision_caption import should_escalate_vision

    policy = resolve_vision_policy()
    stage = "primary"
    attempts: list[dict] = []
    final_gate = {"passed": False, "reasons": ["not_attempted"]}
    attempt_limit = max(1, min(int(max_attempts), MAX_SEMANTIC_ATTEMPTS))
    for attempt in range(1, attempt_limit + 1):
        try:
            data, extraction_audit = analyze_cliplet_semantics(
                asset,
                start,
                end,
                cache_dir=cache_dir,
                attempt=attempt,
                use_vision=use_vision,
                model=force_model,
                cascade_stage=stage,
                pack_id=pack_id,
            )
        except Exception as exc:  # fail closed, but keep the retry budget bounded
            data = None
            extraction_audit = {
                "attempt": attempt,
                "backend": "vision",
                "error": "semantic_analysis_exception",
                "error_type": type(exc).__name__,
                "cascade_stage": stage,
                "vision_model": force_model or policy.model_for_stage(stage),
                "vision_tier": policy.tier,
            }
        if data is None:
            err = str((extraction_audit or {}).get("error") or "schema_not_object")
            gate = {"passed": False, "reasons": [err]}
        else:
            gate = evaluate_semantic_gate(data)
        attempts.append(
            {
                "attempt": attempt,
                "extraction": extraction_audit,
                "analysis": data,
                "gate": gate,
                "cascade_stage": stage,
                "vision_model": (extraction_audit or {}).get("vision_model")
                or policy.model_for_stage(stage),
            }
        )
        final_gate = gate
        if gate["passed"] and data is not None:
            return data, {
                "passed": True,
                "attempts": attempt,
                "max_attempts": attempt_limit,
                "history": attempts,
                "reasons": [],
                "vision_tier": policy.tier,
                "cascade": bool(policy.cascade and allow_cascade and not force_model),
                "final_cascade_stage": stage,
                "final_vision_model": (extraction_audit or {}).get("vision_model")
                or policy.model_for_stage(stage),
            }
        # Escalate for subsequent attempts only (does not burn an extra attempt).
        if (
            allow_cascade
            and not force_model
            and policy.cascade
            and stage == "primary"
            and should_escalate_vision(gate=gate, extraction_audit=extraction_audit)
        ):
            stage = "escalate"
    return None, {
        "passed": False,
        "attempts": attempt_limit,
        "max_attempts": attempt_limit,
        "history": attempts,
        "reasons": final_gate.get("reasons") or ["semantic_analysis_failed"],
        "disposition": "quarantined_no_embedding",
        "vision_tier": policy.tier,
        "cascade": bool(policy.cascade and allow_cascade and not force_model),
        "final_cascade_stage": stage,
        "final_vision_model": force_model or policy.model_for_stage(stage),
    }


def create_cliplets_for_asset(
    session: Session,
    asset: Asset,
    *,
    force: bool = False,
    use_vision: bool = False,
) -> list[Cliplet]:
    existing = list(session.scalars(select(Cliplet).where(Cliplet.asset_uuid == asset.uuid)).all())
    if existing and not force:
        return existing
    if existing and force:
        for row in existing:
            session.delete(row)
        session.commit()

    duration = float(asset.duration_sec or 0)
    if duration <= 0:
        return []
    video = analysis_video_path(asset)
    if not video.exists():
        return []
    cuts = detect_scenes(video)
    ranges = build_clip_ranges(duration, cuts)
    settings = load_settings()
    cache_dir = settings.paths.frames_root()
    from engine.ingest.quality import (
        CLIPLET_STATUS_REJECTED_BLUR,
        CLIPLET_STATUS_USABLE,
        is_usable_quality,
        score_clip_window,
    )

    pack_id = "_blank"
    if asset.customer_id:
        cust = session.get(Customer, asset.customer_id)
        if cust:
            pack_id = pack_id_for_customer(cust.name, cust.profile_json)

    rows: list[Cliplet] = []
    rejected_blur = 0
    for start, end in ranges:
        q = score_clip_window(video, start, end)
        if not is_usable_quality(q):
            # HARD: do not create usable blurry cliplets (optional audit row with rejected status)
            row = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=start,
                end_sec=end,
                duration_sec=round(end - start, 3),
                description="[rejected_blur] 虚焦/模糊切片，禁止向量化",
                category=asset.category,
                score=q,
                status=CLIPLET_STATUS_REJECTED_BLUR,
                embedding_json=None,
            )
            session.add(row)
            rejected_blur += 1
            continue
        if not use_vision:
            description = describe_cliplet(asset, start, end)
            row = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=start,
                end_sec=end,
                duration_sec=round(end - start, 3),
                description=description,
                category=asset.category,
                score=q,
                status=CLIPLET_STATUS_USABLE,
                semantic_schema_version=COARSE_SEMANTIC_SCHEMA_VERSION,
                semantic_gate_json={
                    "passed": True,
                    "mode": "coarse",
                    "source": "metadata_heuristic",
                    "attempts": 0,
                    "max_attempts": 0,
                    "reasons": [],
                },
                semantic_attempts=0,
            )
            annotate_cliplet(row, asset, pack_id=pack_id)
            session.add(row)
            rows.append(row)
            continue
        semantic, gate_audit = _analyze_with_gate(
            asset, start, end, cache_dir=cache_dir, use_vision=True, pack_id=pack_id
        )
        if semantic is None:
            # Keep one audit-only row; it is neither returned as usable nor vectorized.
            rejected = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=start,
                end_sec=end,
                duration_sec=round(end - start, 3),
                description="[rejected_semantic] 语义分析未通过严格门禁",
                category=asset.category,
                score=q,
                status="rejected_semantic",
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                semantic_gate_json=gate_audit,
                semantic_attempts=int(gate_audit["attempts"]),
                embedding_json=None,
            )
            session.add(rejected)
            continue
        row = Cliplet(
            asset_id=asset.id,
            asset_uuid=asset.uuid,
            start_sec=start,
            end_sec=end,
            duration_sec=round(end - start, 3),
            description=str(semantic["description"]),
            category=asset.category,
            score=q,
            status="semantic_pending",
            semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
            semantic_gate_json=gate_audit,
            semantic_attempts=int(gate_audit["attempts"]),
        )
        annotate_cliplet(row, asset, pack_id=pack_id)
        apply_structured_semantics(row, semantic)
        row.status = CLIPLET_STATUS_USABLE
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def verify_cliplets_on_demand(
    session: Session,
    *,
    customer_id: int,
    cliplet_ids: list[int],
    force: bool = False,
) -> dict[str, object]:
    """Verify selected production candidates with one 9B pass.

    A failed verification never destroys a usable coarse/legacy vector. It
    merely leaves the row outside strict semantic v1. This keeps ordinary
    production fast while allowing strict jobs to opt into verified material.
    """
    from engine.catalog.host_profile import VISION_FAST
    from engine.ingest.quality import CLIPLET_STATUS_REJECTED_BLUR
    from engine.ingest.semantic_gate import semantic_analysis_passed, semantic_gate_passed

    ids = list(dict.fromkeys(int(v) for v in cliplet_ids if int(v) > 0))[:10]
    settings = load_settings()
    cache_dir = settings.paths.frames_root()
    verified: list[int] = []
    not_verified: list[int] = []
    skipped: list[dict[str, object]] = []

    for cliplet_id in ids:
        row = session.scalar(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(Cliplet.id == cliplet_id, Asset.customer_id == customer_id)
        )
        if row is None:
            skipped.append({"id": cliplet_id, "reason": "not_found"})
            continue
        if row.status == CLIPLET_STATUS_REJECTED_BLUR:
            skipped.append({"id": cliplet_id, "reason": "rejected_blur"})
            continue
        # Fast return is allowed only when the persisted strict embedding
        # provenance is valid as well as the visual semantic record.
        if semantic_gate_passed(row) and not force:
            verified.append(cliplet_id)
            continue
        claimed = _claim_semantic_cliplet_by_id(session, cliplet_id=cliplet_id)
        if claimed is None:
            skipped.append({"id": cliplet_id, "reason": "already_claimed"})
            continue
        row, token = claimed
        asset = session.get(Asset, row.asset_id)
        if asset is None:
            skipped.append({"id": cliplet_id, "reason": "asset_missing"})
            _release_semantic_claim(session, row, token)
            continue

        semantic = None if force else (row.semantic_json if semantic_analysis_passed(row) else None)
        if semantic is None:
            semantic, audit = _analyze_with_gate(
                asset,
                row.start_sec,
                row.end_sec,
                cache_dir=cache_dir,
                use_vision=True,
                max_attempts=1,
                allow_cascade=False,
                force_model=VISION_FAST,
                pack_id=_pack_id_for_asset(session, asset),
            )
            audit = {**audit, "mode": "on_demand_9b"}
        else:
            audit = row.semantic_gate_json if isinstance(row.semantic_gate_json, dict) else {}
        if semantic is None:
            existing = row.semantic_gate_json if isinstance(row.semantic_gate_json, dict) else {}
            result = session.execute(
                update(Cliplet)
                .where(
                    Cliplet.id == cliplet_id,
                    Cliplet.semantic_claim_token == token,
                )
                .values(
                    semantic_gate_json={**existing, "last_verification": audit},
                    semantic_claim_token=None,
                    semantic_claimed_at=None,
                )
            )
            session.commit()
            if result.rowcount == 1:
                not_verified.append(cliplet_id)
            else:
                skipped.append({"id": cliplet_id, "reason": "claim_lost"})
            continue

        try:
            payload = _strict_candidate_payload(row, asset, semantic, audit)
            committed = _commit_strict_candidate(session, row.id, token, payload)
        except Exception:
            session.rollback()
            current = session.get(Cliplet, row.id)
            if current is not None:
                _release_semantic_claim(session, current, token)
            committed = False
        if not committed:
            not_verified.append(cliplet_id)
        else:
            verified.append(cliplet_id)

    return {
        "mode": "on_demand_9b",
        "requested": len(ids),
        "verified": verified,
        "not_verified": not_verified,
        "skipped": skipped,
        "model": "qwen3.5:9b",
        "attempts_per_cliplet": 1,
        "cascade": False,
    }


def _strict_candidate_payload(
    row: Cliplet,
    asset: Asset,
    semantic: dict,
    audit: dict,
) -> dict[str, object]:
    """Build strict semantic + embedding values without mutating the DB row."""
    from engine.catalog.semantic_tags import compose_embed_text
    from engine.catalog.vector_index import embed_text
    from engine.ingest.semantic_gate import (
        STRICT_EMBEDDING_MODEL,
        STRICT_EMBEDDING_SCHEMA_VERSION,
    )

    candidate = SimpleNamespace(
        category=row.category,
        description=str(semantic["description"]),
        theme=row.theme,
        theme_score=row.theme_score,
        scene=row.scene,
        objects_json=list(row.objects_json or []),
        actions_json=list(row.actions_json or []),
        semantic_json=None,
        semantic_schema_version=None,
    )
    annotate_cliplet(candidate, asset)
    apply_structured_semantics(candidate, semantic)
    embed_input = compose_embed_text(candidate)
    if not embed_input.strip():
        raise RuntimeError("strict_embedding_input_empty")
    embedding, backend = embed_text(
        embed_input,
        model=STRICT_EMBEDDING_MODEL,
        strict=True,
    )
    if backend != "ollama":
        raise RuntimeError("strict_embedding_backend_invalid")
    return {
        "description": candidate.description,
        "theme": candidate.theme,
        "theme_score": candidate.theme_score,
        "scene": candidate.scene,
        "objects_json": candidate.objects_json,
        "actions_json": candidate.actions_json,
        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        "semantic_json": semantic,
        "semantic_gate_json": audit,
        "semantic_attempts": int(audit.get("attempts") or row.semantic_attempts or 0),
        "status": "usable",
        "embedding_json": embedding,
        "embedding_backend": backend,
        "embedding_model": STRICT_EMBEDDING_MODEL,
        "embedding_schema_version": STRICT_EMBEDDING_SCHEMA_VERSION,
        "indexed_at": datetime.now(timezone.utc),
        "semantic_claim_token": None,
        "semantic_claimed_at": None,
    }


def _commit_strict_candidate(
    session: Session,
    cliplet_id: int,
    token: str,
    payload: dict[str, object],
) -> bool:
    """CAS commit all strict fields at once; never expose a partial strict row."""
    result = session.execute(
        update(Cliplet)
        .where(
            Cliplet.id == cliplet_id,
            Cliplet.semantic_claim_token == token,
        )
        .values(**payload)
    )
    session.commit()
    return result.rowcount == 1


def recaption_existing_cliplets(
    session: Session,
    *,
    customer_id: int,
    limit: int = 500,
    use_vision: bool = False,
) -> dict[str, object]:
    """Safely backfill old semantic rows using one-row atomic SQLite leases."""
    limit = max(1, min(int(limit), 500))
    settings = load_settings()
    cache_dir = settings.paths.frames_root()
    vision_n = 0
    rejected_n = 0
    claimed_ids: list[int] = []
    errors: list[str] = []
    failure_reasons: Counter[str] = Counter()

    for _ in range(limit):
        claimed = _claim_next_semantic_cliplet(session, customer_id=customer_id)
        if claimed is None:
            break
        row, token = claimed
        claimed_ids.append(int(row.id))
        asset = session.get(Asset, row.asset_id)
        if not asset:
            _release_semantic_claim(session, row, token)
            errors.append(f"cliplet {row.id}: asset_missing")
            continue
        try:
            # Full-library backfill must stay on the 9B primary path.
            # Cascade/27B is lab-only for on-demand candidates, never for sweep.
            from engine.catalog.host_profile import VISION_FAST

            semantic, gate_audit = _analyze_with_gate(
                asset,
                row.start_sec,
                row.end_sec,
                cache_dir=cache_dir,
                use_vision=use_vision,
                allow_cascade=False,
                force_model=VISION_FAST if use_vision else None,
                pack_id=_pack_id_for_asset(session, asset),
            )
            if semantic is None:
                # A strict verification failure is audit-only: preserve the
                # usable coarse/legacy row and its vector.
                existing = (
                    row.semantic_gate_json
                    if isinstance(row.semantic_gate_json, dict)
                    else {}
                )
                result = session.execute(
                    update(Cliplet)
                    .where(
                        Cliplet.id == row.id,
                        Cliplet.semantic_claim_token == token,
                    )
                    .values(
                        semantic_gate_json={
                            **existing,
                            "last_verification": gate_audit,
                            "strict_verification_terminal": True,
                        },
                        semantic_claim_token=None,
                        semantic_claimed_at=None,
                    )
                )
                if result.rowcount != 1:
                    session.rollback()
                    raise RuntimeError("semantic claim lost")
                rejected_n += 1
                failure_reasons.update(str(v) for v in (gate_audit.get("reasons") or []))
                _record_semantic_progress(session, customer_id, row.id, passed=False)
                session.commit()
                continue
            payload = _strict_candidate_payload(row, asset, semantic, gate_audit)
            if not _commit_strict_candidate(session, row.id, token, payload):
                raise RuntimeError("semantic claim lost")
            _record_semantic_progress(session, customer_id, row.id, passed=True)
            session.commit()
            vision_n += 1
        except Exception as exc:
            session.rollback()
            errors.append(f"cliplet {row.id}: {type(exc).__name__}: {exc}")
        finally:
            # Engine kill / client abort must not leave a 30min lease blocking the queue.
            current = session.get(Cliplet, row.id)
            if current is not None and current.semantic_claim_token == token:
                _release_semantic_claim(session, current, token)

    progress = semantic_backfill_progress(session, customer_id=customer_id)
    return {
        "queued": len(claimed_ids),
        "processed": vision_n + rejected_n,
        "passed": vision_n,
        "rejected": rejected_n,
        "remaining": progress["remaining"],
        "eligible": progress["eligible"],
        "last_cursor": progress["last_cursor"],
        "claimed_ids": claimed_ids,
        "failure_reasons": dict(failure_reasons.most_common()),
        "errors": errors,
        # Backward-compatible response keys.
        "updated": vision_n + rejected_n,
        "vision": vision_n,
        "heuristic": 0,
        "semantic_rejected": rejected_n,
        "quality_rejected": 0,
    }


def _eligible_semantic_stmt(customer_id: int):
    from engine.ingest.quality import effective_min_quality_score

    floor = float(effective_min_quality_score())
    return (
        select(Cliplet)
        .join(Asset, Cliplet.asset_id == Asset.id)
        .where(
            Asset.customer_id == customer_id,
            Cliplet.status.notin_(("rejected_blur", "rejected_semantic")),
            Cliplet.score >= floor,
            Cliplet.score != 1.0,
            or_(
                Cliplet.semantic_schema_version.is_(None),
                Cliplet.semantic_schema_version != SEMANTIC_SCHEMA_VERSION,
                Cliplet.semantic_gate_json.is_(None),
            ),
            func.coalesce(
                func.json_extract(
                    Cliplet.semantic_gate_json,
                    "$.strict_verification_terminal",
                ),
                0,
            )
            != 1,
        )
    )


def _claim_next_semantic_cliplet(
    session: Session,
    *,
    customer_id: int,
    lease_minutes: int = 30,
) -> tuple[Cliplet, str] | None:
    """Claim the oldest eligible row under SQLite's write lock."""
    session.rollback()
    session.execute(text("BEGIN IMMEDIATE"))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=lease_minutes)
    row = session.scalar(
        _eligible_semantic_stmt(customer_id)
        .where(
            or_(
                Cliplet.semantic_claim_token.is_(None),
                Cliplet.semantic_claimed_at.is_(None),
                Cliplet.semantic_claimed_at < cutoff,
            )
        )
        .order_by(Cliplet.id.asc())
        .limit(1)
    )
    if row is None:
        session.commit()
        return None
    token = uuid.uuid4().hex
    row.semantic_claim_token = token
    row.semantic_claimed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row, token


def _claim_semantic_cliplet_by_id(
    session: Session,
    *,
    cliplet_id: int,
    lease_minutes: int = 30,
) -> tuple[Cliplet, str] | None:
    """CAS-claim one requested row using the shared semantic lease columns."""
    session.rollback()
    session.execute(text("BEGIN IMMEDIATE"))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=lease_minutes)
    row = session.get(Cliplet, cliplet_id)
    if row is None:
        session.commit()
        return None
    if (
        row.semantic_claim_token
        and row.semantic_claimed_at is not None
        and row.semantic_claimed_at >= cutoff
    ):
        session.commit()
        return None
    token = uuid.uuid4().hex
    row.semantic_claim_token = token
    row.semantic_claimed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row, token


def _release_semantic_claim(session: Session, row: Cliplet, token: str) -> None:
    session.execute(
        update(Cliplet)
        .where(
            Cliplet.id == row.id,
            Cliplet.semantic_claim_token == token,
        )
        .values(semantic_claim_token=None, semantic_claimed_at=None)
    )
    session.commit()


def clear_semantic_claims(session: Session) -> int:
    """Drop all semantic leases (safe on engine boot: no in-process worker holds them)."""
    result = session.execute(
        update(Cliplet)
        .where(Cliplet.semantic_claim_token.is_not(None))
        .values(semantic_claim_token=None, semantic_claimed_at=None)
    )
    session.commit()
    return int(result.rowcount or 0)


def _record_semantic_progress(
    session: Session,
    customer_id: int,
    cursor: int,
    *,
    passed: bool,
) -> None:
    state = session.scalar(
        select(SemanticBackfillState).where(
            SemanticBackfillState.customer_id == customer_id,
            SemanticBackfillState.schema_version == SEMANTIC_SCHEMA_VERSION,
        )
    )
    if state is None:
        state = SemanticBackfillState(
            customer_id=customer_id,
            schema_version=SEMANTIC_SCHEMA_VERSION,
        )
        session.add(state)
    state.processed = int(state.processed or 0) + 1
    state.passed = int(state.passed or 0) + int(passed)
    state.rejected = int(state.rejected or 0) + int(not passed)
    state.last_cursor = cursor
    state.updated_at = datetime.now(timezone.utc)


def semantic_backfill_progress(session: Session, *, customer_id: int) -> dict[str, object]:
    """Return auditable v1 totals for the active customer."""
    scoped = (
        select(Cliplet.status, func.count(Cliplet.id))
        .join(Asset, Cliplet.asset_id == Asset.id)
        .where(
            Asset.customer_id == customer_id,
            Cliplet.semantic_schema_version == SEMANTIC_SCHEMA_VERSION,
        )
        .group_by(Cliplet.status)
    )
    by_status = {str(status): int(count) for status, count in session.execute(scoped).all()}
    passed = by_status.get("usable", 0)
    rejected = by_status.get("rejected_semantic", 0)
    remaining = int(
        session.scalar(select(func.count()).select_from(_eligible_semantic_stmt(customer_id).subquery()))
        or 0
    )
    state = session.scalar(
        select(SemanticBackfillState).where(
            SemanticBackfillState.customer_id == customer_id,
            SemanticBackfillState.schema_version == SEMANTIC_SCHEMA_VERSION,
        )
    )
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "eligible": passed + rejected + remaining,
        "processed": passed + rejected,
        "passed": passed,
        "rejected": rejected,
        "remaining": remaining,
        "last_cursor": state.last_cursor if state else None,
        "claimed": int(
            session.scalar(
                select(func.count(Cliplet.id))
                .join(Asset, Cliplet.asset_id == Asset.id)
                .where(
                    Asset.customer_id == customer_id,
                    Cliplet.semantic_claim_token.is_not(None),
                )
            )
            or 0
        ),
    }
