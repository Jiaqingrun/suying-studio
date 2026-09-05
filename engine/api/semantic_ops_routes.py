"""GSemanticOps APIs: persisted health, clustering, and official evidence."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import (
    KeywordPackRevisionCandidate,
    OfficialObjectEvidence,
    SemanticCluster,
    SemanticClusterMember,
    SemanticHealthCandidate,
    SemanticHealthRun,
    get_session,
)
from engine.catalog.keyword_pack import (
    approve_revision_draft,
    create_revision_draft,
    promote_revision_draft,
    rollback_keyword_pack,
)
from engine.catalog.semantic_ops import (
    query_official_evidence,
    review_candidate,
    run_health_check,
    suggest_clusters,
    update_cluster,
    verify_official_catalog,
)
from engine.config.settings import load_settings

router = APIRouter(prefix="/semantic-ops", tags=["semantic-ops"])


def _scope():
    session = get_session()
    customer = require_active_customer(session, load_settings())
    return session, customer


def _run(row: SemanticHealthRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "mode": row.mode,
        "status": row.status,
        "cursor_after": row.cursor_after,
        "cursor_end": row.cursor_end,
        "checked_count": row.checked_count,
        "candidate_count": row.candidate_count,
        "summary": row.summary_json or {},
        "created_at": row.created_at,
        "finished_at": row.finished_at,
    }


def _candidate(row: SemanticHealthCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "cliplet_id": row.cliplet_id,
        "kind": row.kind,
        "severity": row.severity,
        "status": row.status,
        "evidence_level": row.evidence_level,
        "evidence": row.evidence_json or {},
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
    }


def _cluster(session, row: SemanticCluster) -> dict[str, Any]:
    members = list(
        session.scalars(
            select(SemanticClusterMember).where(
                SemanticClusterMember.customer_id == row.customer_id,
                SemanticClusterMember.cluster_id == row.id,
            )
        ).all()
    )
    return {
        "id": row.id,
        "cluster_key": row.cluster_key,
        "name": row.name,
        "status": row.status,
        "labels": row.labels_json or {},
        "algorithm_version": row.algorithm_version,
        "members": [
            {"cliplet_id": item.cliplet_id, "similarity": item.similarity}
            for item in members
        ],
    }


def _official(row: OfficialObjectEvidence) -> dict[str, Any]:
    return {
        "id": row.id,
        "url": row.url,
        "source_type": row.source_type,
        "status": row.status,
        "canonical_name": row.canonical_name,
        "category": row.category,
        "model": row.model,
        "summary": row.summary,
        "content_sha256": row.content_sha256,
        "visual_fact_supported": row.visual_fact_supported,
        "conflicts": row.conflict_json or [],
        "fetched_at": row.fetched_at,
        "expires_at": row.expires_at,
    }


class RunIn(BaseModel):
    mode: str = "manual"
    idempotency_key: str | None = None
    requested_by: str = "operator"
    limit: int = Field(default=500, ge=1, le=5000)


class ReviewIn(BaseModel):
    action: str
    decision_key: str = Field(min_length=4, max_length=160)
    actor: str = "operator"
    note: str = ""


class ClusterIn(BaseModel):
    threshold: float = Field(default=0.82, ge=0.0, le=1.0)


class ClusterActionIn(BaseModel):
    action: str
    actor: str = "operator"
    name: str = ""
    other_cluster_ids: list[int] = Field(default_factory=list)
    member_ids: list[int] = Field(default_factory=list)


class OfficialVerifyIn(BaseModel):
    url: str
    candidate_name: str = ""
    visual_facts: list[str] = Field(default_factory=list)
    timeout_sec: float = Field(default=5.0, ge=0.5, le=15.0)
    force: bool = False


class RevisionPreviewIn(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    semantic_candidate_ids: list[int] = Field(min_length=1, max_length=200)
    official_evidence_ids: list[int] = Field(default_factory=list, max_length=100)
    draft_key: str = Field(min_length=4, max_length=160)
    created_by: str = "operator"
    auto_promote: bool = False


class RevisionApproveIn(BaseModel):
    approved_by: str = "operator"


class RevisionPromoteIn(BaseModel):
    actor: str = "operator"
    auto: bool = False


class RevisionRollbackIn(BaseModel):
    source_revision: int = Field(ge=1)
    actor: str = "operator"


def _revision(row: KeywordPackRevisionCandidate) -> dict[str, Any]:
    return {
        "id": row.id,
        "base_pack_id": row.base_pack_id,
        "target_revision": row.target_revision,
        "status": row.status,
        "risk_level": row.risk_level,
        "diff": row.diff_json or [],
        "draft": row.draft_json or {},
        "evidence": row.evidence_json or {},
        "validation": row.validation_json or {},
        "approved_by": row.approved_by,
        "promoted_pack_id": row.promoted_pack_id,
    }


@router.post("/health/runs")
def semantic_health_run(body: RunIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = run_health_check(
                session,
                customer_id=customer.id,
                mode=body.mode,
                idempotency_key=body.idempotency_key,
                requested_by=body.requested_by,
                limit=body.limit,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "run": _run(row)}
    finally:
        session.close()


@router.get("/health/status")
def semantic_health_status() -> dict[str, Any]:
    session, customer = _scope()
    try:
        row = session.scalar(
            select(SemanticHealthRun)
            .where(SemanticHealthRun.customer_id == customer.id)
            .order_by(SemanticHealthRun.id.desc())
        )
        return {"ok": True, "run": _run(row) if row else None}
    finally:
        session.close()


@router.get("/health/runs")
def semantic_health_runs(limit: int = 50) -> dict[str, Any]:
    session, customer = _scope()
    try:
        rows = list(
            session.scalars(
                select(SemanticHealthRun)
                .where(SemanticHealthRun.customer_id == customer.id)
                .order_by(SemanticHealthRun.id.desc())
                .limit(max(1, min(limit, 200)))
            ).all()
        )
        return {"ok": True, "runs": [_run(row) for row in rows]}
    finally:
        session.close()


@router.get("/health/candidates")
def semantic_health_candidates(status: str = "", limit: int = 200) -> dict[str, Any]:
    session, customer = _scope()
    try:
        query = select(SemanticHealthCandidate).where(
            SemanticHealthCandidate.customer_id == customer.id
        )
        if status:
            query = query.where(SemanticHealthCandidate.status == status)
        rows = list(
            session.scalars(
                query.order_by(SemanticHealthCandidate.id.desc()).limit(max(1, min(limit, 500)))
            ).all()
        )
        return {"ok": True, "candidates": [_candidate(row) for row in rows]}
    finally:
        session.close()


@router.post("/health/candidates/{candidate_id}/review")
def semantic_health_review(candidate_id: int, body: ReviewIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            decision = review_candidate(
                session,
                customer_id=customer.id,
                candidate_id=candidate_id,
                action=body.action,
                decision_key=body.decision_key,
                actor=body.actor,
                note=body.note,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "decision_id": decision.id, "action": decision.action}
    finally:
        session.close()


@router.post("/clusters/suggest")
def semantic_clusters_suggest(body: ClusterIn | None = None) -> dict[str, Any]:
    body = body or ClusterIn()
    session, customer = _scope()
    try:
        rows = suggest_clusters(session, customer_id=customer.id, threshold=body.threshold)
        return {"ok": True, "clusters": [_cluster(session, row) for row in rows]}
    finally:
        session.close()


@router.get("/topic-intents/schema")
def topic_intent_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "suying.topic-intent.request.v1",
        "modes": ["single_product", "same_category_products"],
        "fields": {
            "cluster_ids": "active-customer semantic cluster IDs",
            "official_evidence_ids": "verified active-customer official evidence IDs",
            "similarity_threshold": {"default": 0.82, "minimum": 0.0, "maximum": 1.0},
            "requested_uses": "用途仅在官方证据与当前画面语义共同支持时保留",
        },
        "hard_locks": {
            "strict_semantic_v1": True,
            "whole_asset_fallback": False,
            "single_product_cluster_count": 1,
            "same_category_min_cluster_count": 2,
        },
    }


@router.get("/clusters")
def semantic_clusters_list() -> dict[str, Any]:
    session, customer = _scope()
    try:
        rows = list(
            session.scalars(
                select(SemanticCluster)
                .where(SemanticCluster.customer_id == customer.id)
                .order_by(SemanticCluster.id.asc())
            ).all()
        )
        return {"ok": True, "clusters": [_cluster(session, row) for row in rows]}
    finally:
        session.close()


@router.post("/clusters/{cluster_id}/actions")
def semantic_cluster_action(cluster_id: int, body: ClusterActionIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = update_cluster(
                session,
                customer_id=customer.id,
                cluster_id=cluster_id,
                action=body.action,
                actor=body.actor,
                name=body.name,
                other_cluster_ids=body.other_cluster_ids,
                member_ids=body.member_ids,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "cluster": _cluster(session, row)}
    finally:
        session.close()


@router.get("/official-evidence")
def official_evidence_query(url: str = "") -> dict[str, Any]:
    session, customer = _scope()
    try:
        if url:
            row = query_official_evidence(session, customer_id=customer.id, url=url)
            return {"ok": True, "evidence": _official(row) if row else None}
        rows = list(
            session.scalars(
                select(OfficialObjectEvidence)
                .where(OfficialObjectEvidence.customer_id == customer.id)
                .order_by(OfficialObjectEvidence.id.desc())
                .limit(200)
            ).all()
        )
        return {"ok": True, "evidence": [_official(row) for row in rows]}
    finally:
        session.close()


@router.post("/official-evidence/verify")
def official_evidence_verify(body: OfficialVerifyIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = verify_official_catalog(
                session,
                customer_id=customer.id,
                profile=customer.profile_json or {},
                url=body.url,
                candidate_name=body.candidate_name,
                visual_facts=body.visual_facts,
                timeout_sec=body.timeout_sec,
                force=body.force,
            )
        except (ValueError, ConnectionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "evidence": _official(row)}
    finally:
        session.close()


@router.post("/keyword-revisions/preview")
def keyword_revision_preview(body: RevisionPreviewIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = create_revision_draft(
                session,
                customer,
                patch=body.patch,
                semantic_candidate_ids=body.semantic_candidate_ids,
                official_evidence_ids=body.official_evidence_ids,
                draft_key=body.draft_key,
                created_by=body.created_by,
            )
            if body.auto_promote:
                row = promote_revision_draft(
                    session, customer, draft_id=row.id, actor=body.created_by, auto=True
                )
        except (ValueError, LookupError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "revision": _revision(row)}
    finally:
        session.close()


@router.get("/keyword-revisions")
def keyword_revision_list() -> dict[str, Any]:
    session, customer = _scope()
    try:
        rows = list(
            session.scalars(
                select(KeywordPackRevisionCandidate)
                .where(KeywordPackRevisionCandidate.customer_id == customer.id)
                .order_by(KeywordPackRevisionCandidate.id.desc())
                .limit(200)
            ).all()
        )
        return {"ok": True, "revisions": [_revision(row) for row in rows]}
    finally:
        session.close()


@router.post("/keyword-revisions/{draft_id}/approve")
def keyword_revision_approve(draft_id: int, body: RevisionApproveIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = approve_revision_draft(
                session, customer, draft_id=draft_id, approved_by=body.approved_by
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "revision": _revision(row)}
    finally:
        session.close()


@router.post("/keyword-revisions/{draft_id}/promote")
def keyword_revision_promote(draft_id: int, body: RevisionPromoteIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            row = promote_revision_draft(
                session,
                customer,
                draft_id=draft_id,
                actor=body.actor,
                auto=body.auto,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "revision": _revision(row)}
    finally:
        session.close()


@router.post("/keyword-revisions/rollback")
def keyword_revision_rollback(body: RevisionRollbackIn) -> dict[str, Any]:
    session, customer = _scope()
    try:
        try:
            pack = rollback_keyword_pack(
                session,
                customer,
                source_revision=body.source_revision,
                actor=body.actor,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "ok": True,
            "pack": {
                "id": pack.id,
                "revision": pack.revision,
                "sha256": pack.content_sha256,
                "source_kind": pack.source_kind,
            },
        }
    finally:
        session.close()
