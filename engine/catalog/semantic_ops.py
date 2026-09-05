"""Customer-scoped semantic health, clustering, and official-catalog evidence."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Asset,
    Cliplet,
    OfficialObjectEvidence,
    PlatformRuleVersion,
    SemanticCluster,
    SemanticClusterMember,
    SemanticHealthCandidate,
    SemanticHealthDecision,
    SemanticHealthRun,
)
from engine.catalog.vector_index import cosine
from engine.ingest.semantic_gate import (
    COARSE_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    semantic_gate_passed,
)

HEALTH_VERSION = "semantic-health-v1"
CLUSTER_VERSION = "labels-embed-v1"
_VAGUE = {"实拍业务素材", "现场画面", "视频片段", "素材画面", "暂无描述"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _customer_cliplets(session: Session, customer_id: int):
    return (
        select(Cliplet)
        .join(Asset, Asset.id == Cliplet.asset_id)
        .where(Asset.customer_id == customer_id)
    )


def _level(row: Cliplet) -> str:
    if semantic_gate_passed(row):
        return "semantic.v1"
    if row.semantic_schema_version == COARSE_SEMANTIC_SCHEMA_VERSION:
        return "coarse.v1"
    return "unknown"


def _frame_unknowns(data: dict[str, Any]) -> list[str]:
    values = [str(x).strip() for x in data.get("unknowns") or [] if str(x).strip()]
    for frame in data.get("frames") or []:
        if isinstance(frame, dict):
            values.extend(str(x).strip() for x in frame.get("unknowns") or [] if str(x).strip())
    return list(dict.fromkeys(values))


def _findings(row: Cliplet, rules: Iterable[PlatformRuleVersion], now: datetime) -> list[tuple[str, str, dict[str, Any]]]:
    data = row.semantic_json if isinstance(row.semantic_json, dict) else {}
    findings: list[tuple[str, str, dict[str, Any]]] = []
    description = str(row.description or "").strip()
    if not description or len(description) < 12 or description in _VAGUE:
        findings.append(("vague_or_empty", "error", {"description": description}))
    consistency = data.get("consistency") if isinstance(data.get("consistency"), dict) else {}
    if consistency and (
        consistency.get("consistent") is not True or bool(consistency.get("issues"))
    ):
        findings.append(("semantic_conflict", "error", {"consistency": consistency}))
    unknowns = _frame_unknowns(data)
    frame_count = len([x for x in data.get("frames") or [] if isinstance(x, dict)])
    if unknowns:
        findings.append(
            (
                "unknown_evidence",
                "warning" if len(unknowns) <= max(1, frame_count) else "error",
                {"unknowns": unknowns, "frame_count": frame_count},
            )
        )
    gate = row.semantic_gate_json if isinstance(row.semantic_gate_json, dict) else {}
    verified_raw = gate.get("verified_at") or gate.get("evaluated_at")
    verified_at = row.indexed_at
    if verified_raw:
        try:
            verified_at = datetime.fromisoformat(str(verified_raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    if verified_at and verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    if not verified_at or verified_at < now - timedelta(days=90):
        findings.append(
            (
                "evidence_stale",
                "warning",
                {"verified_at": verified_at.isoformat() if verified_at else None, "max_age_days": 90},
            )
        )
    searchable = " ".join(
        [
            description,
            *[str(x) for x in data.get("visible_facts") or []],
            *[
                str(x)
                for frame in data.get("frames") or []
                if isinstance(frame, dict)
                for x in frame.get("visible_facts") or []
            ],
        ]
    ).lower()
    for rule in rules:
        terms = [
            str(x).strip().lower()
            for x in (rule.rules_json or {}).get("forbidden_terms", [])
            if str(x).strip()
        ]
        hits = sorted({term for term in terms if term in searchable})
        if hits:
            findings.append(
                (
                    "platform_rule_risk",
                    "error",
                    {"platform": rule.platform, "version": rule.version, "hits": hits},
                )
            )
    if row.semantic_schema_version == SEMANTIC_SCHEMA_VERSION and not semantic_gate_passed(row):
        findings.append(("strict_provenance_invalid", "error", {"gate": gate}))
    return findings


def run_health_check(
    session: Session,
    *,
    customer_id: int,
    mode: str = "manual",
    idempotency_key: str | None = None,
    requested_by: str = "operator",
    limit: int = 500,
) -> SemanticHealthRun:
    if mode not in {"manual", "daily"}:
        raise ValueError("mode must be manual or daily")
    now = _now()
    previous_cursor = session.scalar(
        select(func.max(SemanticHealthRun.cursor_end)).where(
            SemanticHealthRun.customer_id == customer_id,
            SemanticHealthRun.status == "completed",
        )
    ) or 0
    key = idempotency_key or (
        f"daily:{now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()}:"
        f"after:{int(previous_cursor)}:{HEALTH_VERSION}"
        if mode == "daily"
        else f"manual:{uuid4().hex}"
    )
    existing = session.scalar(
        select(SemanticHealthRun).where(
            SemanticHealthRun.customer_id == customer_id,
            SemanticHealthRun.idempotency_key == key,
        )
    )
    if existing:
        return existing
    run = SemanticHealthRun(
        customer_id=customer_id,
        idempotency_key=key,
        mode=mode,
        status="running",
        cursor_after=int(previous_cursor),
        requested_by=requested_by,
    )
    session.add(run)
    session.flush()
    rows = list(
        session.scalars(
            _customer_cliplets(session, customer_id)
            .where(Cliplet.id > int(previous_cursor))
            .order_by(Cliplet.id.asc())
            .limit(max(1, min(limit, 5000)))
        ).all()
    )
    rules = list(
        session.scalars(
            select(PlatformRuleVersion).where(
                PlatformRuleVersion.customer_id == customer_id,
                PlatformRuleVersion.status == "active",
            )
        ).all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        for kind, severity, evidence in _findings(row, rules, now):
            candidate_key = f"{HEALTH_VERSION}:cliplet:{row.id}:{kind}"
            candidate = session.scalar(
                select(SemanticHealthCandidate).where(
                    SemanticHealthCandidate.customer_id == customer_id,
                    SemanticHealthCandidate.candidate_key == candidate_key,
                )
            )
            if candidate is None:
                candidate = SemanticHealthCandidate(
                    customer_id=customer_id,
                    run_id=run.id,
                    candidate_key=candidate_key,
                    cliplet_id=row.id,
                    kind=kind,
                    severity=severity,
                    evidence_level=_level(row),
                    evidence_json=evidence,
                )
                session.add(candidate)
                run.candidate_count += 1
            else:
                candidate.run_id = run.id
                candidate.last_seen_at = now
                candidate.updated_at = now
                candidate.severity = severity
                candidate.evidence_level = _level(row)
                candidate.evidence_json = evidence
            counts[kind] = counts.get(kind, 0) + 1
    expired_sources: list[tuple[str, int, dict[str, Any]]] = []
    for evidence_row in session.scalars(
        select(OfficialObjectEvidence).where(
            OfficialObjectEvidence.customer_id == customer_id,
            OfficialObjectEvidence.expires_at.is_not(None),
            OfficialObjectEvidence.expires_at <= now,
        )
    ).all():
        expired_sources.append(
            (
                "official",
                int(evidence_row.id),
                {"url_hash": evidence_row.url_hash, "expires_at": evidence_row.expires_at},
            )
        )
    for rule in rules:
        rule_expires = _aware(rule.expires_at)
        if rule_expires and rule_expires <= now:
            expired_sources.append(
                (
                    "platform_rule",
                    int(rule.id),
                    {"platform": rule.platform, "version": rule.version, "expires_at": rule.expires_at},
                )
            )
    for source_type, source_id, evidence in expired_sources:
        candidate_key = f"{HEALTH_VERSION}:{source_type}:{source_id}:evidence_stale"
        candidate = session.scalar(
            select(SemanticHealthCandidate).where(
                SemanticHealthCandidate.customer_id == customer_id,
                SemanticHealthCandidate.candidate_key == candidate_key,
            )
        )
        if candidate is None:
            session.add(
                SemanticHealthCandidate(
                    customer_id=customer_id,
                    run_id=run.id,
                    candidate_key=candidate_key,
                    kind="evidence_stale",
                    severity="error",
                    evidence_level="official_catalog" if source_type == "official" else "platform_rule",
                    evidence_json=evidence,
                )
            )
            run.candidate_count += 1
        else:
            candidate.run_id = run.id
            candidate.last_seen_at = now
            candidate.updated_at = now
            candidate.evidence_json = evidence
        counts["evidence_stale"] = counts.get("evidence_stale", 0) + 1
    run.checked_count = len(rows)
    run.cursor_end = int(rows[-1].id if rows else previous_cursor)
    run.summary_json = {"findings": counts, "incremental": True, "vlm_requests": 0}
    run.status = "completed"
    run.finished_at = now
    run.updated_at = now
    session.commit()
    session.refresh(run)
    return run


def review_candidate(
    session: Session,
    *,
    customer_id: int,
    candidate_id: int,
    action: str,
    decision_key: str,
    actor: str = "operator",
    note: str = "",
) -> SemanticHealthDecision:
    if action not in {"accept", "dismiss", "ignore", "reopen"}:
        raise ValueError("unsupported candidate action")
    candidate = session.scalar(
        select(SemanticHealthCandidate).where(
            SemanticHealthCandidate.id == candidate_id,
            SemanticHealthCandidate.customer_id == customer_id,
        )
    )
    if candidate is None:
        raise LookupError("candidate not found")
    existing = session.scalar(
        select(SemanticHealthDecision).where(
            SemanticHealthDecision.customer_id == customer_id,
            SemanticHealthDecision.decision_key == decision_key,
        )
    )
    if existing:
        return existing
    decision = SemanticHealthDecision(
        customer_id=customer_id,
        candidate_id=candidate.id,
        decision_key=decision_key,
        action=action,
        note=note,
        actor=actor,
        evidence_json={"candidate_key": candidate.candidate_key},
    )
    candidate.status = "pending" if action == "reopen" else action
    candidate.updated_at = _now()
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return decision


def _tag_key(row: Cliplet) -> tuple[str, ...]:
    data = row.semantic_json if isinstance(row.semantic_json, dict) else {}
    scenes = sorted(
        str(x.get("label")).strip()
        for x in data.get("scenes") or []
        if isinstance(x, dict) and x.get("label")
    )
    objects = sorted(str(x).strip() for x in row.objects_json or [] if str(x).strip())
    values = tuple([str(row.theme or ""), str(row.scene or ""), *scenes, *objects])
    return values if any(values) else ()


def suggest_clusters(
    session: Session, *, customer_id: int, threshold: float = 0.82
) -> list[SemanticCluster]:
    rows = list(
        session.scalars(
            _customer_cliplets(session, customer_id)
            .where(Cliplet.embedding_json.is_not(None))
            .order_by(Cliplet.id.asc())
        ).all()
    )
    buckets: dict[tuple[str, ...], list[Cliplet]] = {}
    for row in rows:
        key = _tag_key(row)
        if key:
            buckets.setdefault(key, []).append(row)
    groups: list[tuple[tuple[str, ...], list[Cliplet]]] = []
    for tags, bucket in sorted(buckets.items()):
        pending = list(bucket)
        while pending:
            anchor = pending.pop(0)
            group = [anchor]
            rest: list[Cliplet] = []
            for row in pending:
                if cosine(anchor.embedding_json or [], row.embedding_json or []) >= threshold:
                    group.append(row)
                else:
                    rest.append(row)
            pending = rest
            groups.append((tags, group))
    result: list[SemanticCluster] = []
    for tags, members in groups:
        ids = sorted(int(row.id) for row in members)
        cluster_key = _hash({"version": CLUSTER_VERSION, "tags": tags, "members": ids})[:40]
        cluster = session.scalar(
            select(SemanticCluster).where(
                SemanticCluster.customer_id == customer_id,
                SemanticCluster.cluster_key == cluster_key,
            )
        )
        if cluster is None:
            product_categories = {
                str(item.get("category") or "").strip()
                for member in members
                for item in ((member.semantic_json or {}).get("products") or [])
                if isinstance(item, dict) and item.get("category")
            }
            cluster = SemanticCluster(
                customer_id=customer_id,
                cluster_key=cluster_key,
                name=" / ".join(x for x in tags if x)[:256],
                labels_json={
                    "hard_filter": list(tags),
                    "member_ids": ids,
                    "taxonomy_category": (
                        next(iter(product_categories)) if len(product_categories) == 1 else ""
                    ),
                },
            )
            session.add(cluster)
            session.flush()
        for row in members:
            exists = session.scalar(
                select(SemanticClusterMember).where(
                    SemanticClusterMember.customer_id == customer_id,
                    SemanticClusterMember.cluster_id == cluster.id,
                    SemanticClusterMember.cliplet_id == row.id,
                )
            )
            if exists is None:
                session.add(
                    SemanticClusterMember(
                        customer_id=customer_id,
                        cluster_id=cluster.id,
                        cliplet_id=row.id,
                        similarity=cosine(members[0].embedding_json or [], row.embedding_json or []),
                        evidence_json={"hard_filter": list(tags)},
                    )
                )
        result.append(cluster)
    session.commit()
    return result


def update_cluster(
    session: Session,
    *,
    customer_id: int,
    cluster_id: int,
    action: str,
    actor: str = "operator",
    name: str = "",
    other_cluster_ids: list[int] | None = None,
    member_ids: list[int] | None = None,
) -> SemanticCluster:
    cluster = session.scalar(
        select(SemanticCluster).where(
            SemanticCluster.id == cluster_id, SemanticCluster.customer_id == customer_id
        )
    )
    if cluster is None:
        raise LookupError("cluster not found")
    if action == "rename":
        cluster.name = name.strip()
        cluster.status = "named"
    elif action == "ignore":
        cluster.status = "ignored"
    elif action == "merge":
        ids = sorted(set([cluster_id, *(other_cluster_ids or [])]))
        clusters = list(
            session.scalars(
                select(SemanticCluster).where(
                    SemanticCluster.customer_id == customer_id,
                    SemanticCluster.id.in_(ids),
                )
            ).all()
        )
        if len(clusters) != len(ids):
            raise LookupError("merge cluster not found")
        members = sorted(
            set(
                session.scalars(
                    select(SemanticClusterMember.cliplet_id).where(
                        SemanticClusterMember.customer_id == customer_id,
                        SemanticClusterMember.cluster_id.in_(ids),
                    )
                ).all()
            )
        )
        key = _hash({"manual_merge": ids, "members": members})[:40]
        merged = session.scalar(
            select(SemanticCluster).where(
                SemanticCluster.customer_id == customer_id,
                SemanticCluster.cluster_key == key,
            )
        )
        if merged is None:
            merged = SemanticCluster(
                customer_id=customer_id,
                cluster_key=key,
                name=name.strip() or cluster.name,
                status="merged",
                labels_json={"source_cluster_ids": ids, "member_ids": members},
                created_by=actor,
                updated_by=actor,
            )
            session.add(merged)
            session.flush()
            for cliplet_id in members:
                session.add(
                    SemanticClusterMember(
                        customer_id=customer_id,
                        cluster_id=merged.id,
                        cliplet_id=cliplet_id,
                        similarity=1.0,
                        evidence_json={"manual_merge": ids},
                    )
                )
        for source in clusters:
            source.status = "merged_source"
            source.updated_by = actor
        cluster = merged
    elif action == "split":
        split_ids = sorted(set(member_ids or []))
        owned = set(
            session.scalars(
                select(SemanticClusterMember.cliplet_id).where(
                    SemanticClusterMember.customer_id == customer_id,
                    SemanticClusterMember.cluster_id == cluster.id,
                    SemanticClusterMember.cliplet_id.in_(split_ids or [-1]),
                )
            ).all()
        )
        if owned != set(split_ids) or not split_ids:
            raise ValueError("split members must belong to cluster")
        key = _hash({"manual_split": cluster.id, "members": split_ids})[:40]
        split = session.scalar(
            select(SemanticCluster).where(
                SemanticCluster.customer_id == customer_id,
                SemanticCluster.cluster_key == key,
            )
        )
        if split is None:
            split = SemanticCluster(
                customer_id=customer_id,
                cluster_key=key,
                name=name.strip(),
                status="split",
                labels_json={"source_cluster_id": cluster.id, "member_ids": split_ids},
                created_by=actor,
                updated_by=actor,
            )
            session.add(split)
            session.flush()
            for cliplet_id in split_ids:
                session.add(
                    SemanticClusterMember(
                        customer_id=customer_id,
                        cluster_id=split.id,
                        cliplet_id=cliplet_id,
                        similarity=1.0,
                        evidence_json={"manual_split": cluster.id},
                    )
                )
            source_members = list(
                session.scalars(
                    select(SemanticClusterMember).where(
                        SemanticClusterMember.customer_id == customer_id,
                        SemanticClusterMember.cluster_id == cluster.id,
                        SemanticClusterMember.cliplet_id.in_(split_ids),
                    )
                ).all()
            )
            for source_member in source_members:
                session.delete(source_member)
            cluster.status = "split_source"
            cluster.updated_by = actor
        cluster = split
    else:
        raise ValueError("unsupported cluster action")
    cluster.updated_by = actor
    cluster.updated_at = _now()
    session.commit()
    session.refresh(cluster)
    return cluster


def freeze_topic_intent(
    session: Session,
    *,
    customer_id: int,
    mode: str,
    cluster_ids: list[int],
    official_evidence_ids: list[int],
    similarity_threshold: float = 0.82,
    requested_uses: list[str] | None = None,
) -> dict[str, Any]:
    if mode not in {"single_product", "same_category_products"}:
        raise ValueError("unsupported topic mode")
    ids = sorted(set(cluster_ids))
    if mode == "single_product" and len(ids) != 1:
        raise ValueError("single_product requires exactly one cluster")
    if mode == "same_category_products" and len(ids) < 2:
        raise ValueError("same_category_products requires at least two clusters")
    clusters = list(
        session.scalars(
            select(SemanticCluster).where(
                SemanticCluster.customer_id == customer_id,
                SemanticCluster.id.in_(ids or [-1]),
                SemanticCluster.status.not_in(("ignored", "merged_source")),
            )
        ).all()
    )
    if len(clusters) != len(ids):
        raise LookupError("topic cluster not found")
    categories = {
        str((row.labels_json or {}).get("taxonomy_category") or "").strip()
        for row in clusters
    }
    if not categories or "" in categories or len(categories) != 1:
        raise ValueError("topic clusters must share one explicit taxonomy category")
    members = list(
        session.scalars(
            select(SemanticClusterMember).where(
                SemanticClusterMember.customer_id == customer_id,
                SemanticClusterMember.cluster_id.in_(ids),
            )
        ).all()
    )
    if not members or any(float(row.similarity or 0.0) < similarity_threshold for row in members):
        raise ValueError("cluster similarity threshold not met")
    cliplet_ids = sorted({int(row.cliplet_id) for row in members})
    cliplets = list(
        session.scalars(
            _customer_cliplets(session, customer_id).where(Cliplet.id.in_(cliplet_ids))
        ).all()
    )
    if len(cliplets) != len(cliplet_ids) or not all(semantic_gate_passed(row) for row in cliplets):
        raise ValueError("topic requires strict semantic.v1 cliplets")
    evidence_ids = sorted(set(official_evidence_ids))
    evidence = list(
        session.scalars(
            select(OfficialObjectEvidence).where(
                OfficialObjectEvidence.customer_id == customer_id,
                OfficialObjectEvidence.id.in_(evidence_ids or [-1]),
                OfficialObjectEvidence.status == "verified",
            )
        ).all()
    )
    now = _now()
    if (
        not evidence
        or len(evidence) != len(evidence_ids)
        or any(row.conflict_json for row in evidence)
        or any(_aware(row.expires_at) is not None and _aware(row.expires_at) <= now for row in evidence)
    ):
        raise ValueError("verified conflict-free official evidence required")
    names = {row.canonical_name.strip() for row in evidence if row.canonical_name.strip()}
    visual_names = {
        str(item.get("name") or "").strip()
        for row in cliplets
        for item in ((row.semantic_json or {}).get("products") or [])
        if isinstance(item, dict) and item.get("name")
    }
    if mode == "single_product" and (
        len(names) != 1 or not names.intersection(visual_names)
    ):
        raise ValueError("single product official and visual names conflict")
    allowed_uses: list[str] = []
    for use in requested_uses or []:
        use_text = str(use).strip()
        if not use_text:
            continue
        official_support = any(use_text in (row.summary or "") for row in evidence)
        visual_support = any(
            use_text in " ".join(
                [
                    str((product or {}).get("use") or "")
                    for product in ((row.semantic_json or {}).get("products") or [])
                    if isinstance(product, dict)
                ]
                + [
                    str(fact)
                    for frame in ((row.semantic_json or {}).get("frames") or [])
                    if isinstance(frame, dict)
                    for fact in frame.get("visible_facts") or []
                ]
            )
            for row in cliplets
        )
        if official_support and visual_support:
            allowed_uses.append(use_text)
    return {
        "schema": "suying.topic-intent.v1",
        "mode": mode,
        "cluster_ids": ids,
        "cluster_keys": [row.cluster_key for row in sorted(clusters, key=lambda item: item.id)],
        "taxonomy_category": next(iter(categories)),
        "similarity_threshold": similarity_threshold,
        "cliplet_ids": cliplet_ids,
        "official_evidence": [
            {
                "id": row.id,
                "url_hash": row.url_hash,
                "content_sha256": row.content_sha256,
                "canonical_name": row.canonical_name,
            }
            for row in sorted(evidence, key=lambda item: item.id)
        ],
        "requested_uses": list(requested_uses or []),
        "allowed_uses": allowed_uses,
        "rejected_uses": sorted(set(requested_uses or []) - set(allowed_uses)),
        "strict_semantic_v1": True,
        "allow_whole_asset_fallback": False,
    }


def official_domains(profile: dict[str, Any]) -> set[str]:
    config = profile.get("semantic_ops") if isinstance(profile.get("semantic_ops"), dict) else {}
    domains = config.get("official_catalog_domains") or []
    return {str(x).strip().lower().rstrip(".") for x in domains if str(x).strip()}


def validate_official_url(url: str, allowed_domains: set[str]) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("invalid official catalog URL")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        raise ValueError("URL host is not in customer official-domain whitelist")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except OSError as exc:
        raise ConnectionError("official host DNS unavailable") from exc
    if not addresses:
        raise ConnectionError("official host DNS unavailable")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("SSRF-protected address")
    return parsed.geturl()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def query_official_evidence(
    session: Session, *, customer_id: int, url: str
) -> OfficialObjectEvidence | None:
    return session.scalar(
        select(OfficialObjectEvidence).where(
            OfficialObjectEvidence.customer_id == customer_id,
            OfficialObjectEvidence.url_hash == hashlib.sha256(url.encode()).hexdigest(),
        )
    )


def verify_official_catalog(
    session: Session,
    *,
    customer_id: int,
    profile: dict[str, Any],
    url: str,
    candidate_name: str = "",
    visual_facts: list[str] | None = None,
    timeout_sec: float = 5.0,
    force: bool = False,
) -> OfficialObjectEvidence:
    safe_url = validate_official_url(url, official_domains(profile))
    url_hash = hashlib.sha256(safe_url.encode()).hexdigest()
    row = query_official_evidence(session, customer_id=customer_id, url=safe_url)
    now = _now()
    cached_until = _aware(row.expires_at) if row else None
    if row and not force and cached_until and cached_until > now:
        return row
    row = row or OfficialObjectEvidence(
        customer_id=customer_id, url=safe_url, url_hash=url_hash
    )
    if row.id is None:
        session.add(row)
    conflicts: list[str] = []
    try:
        request = urllib.request.Request(
            safe_url, headers={"User-Agent": "Suying-OfficialCatalogVerifier/1.0"}
        )
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=max(0.5, min(timeout_sec, 15.0))
        ) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "text/" not in content_type and "json" not in content_type:
                raise ValueError("unsupported official catalog content type")
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("official catalog response too large")
        text = raw.decode("utf-8", errors="replace")
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text))).strip()[:2000]
        if candidate_name and candidate_name.lower() not in summary.lower():
            conflicts.append("candidate_name_not_found")
        visible = [str(x).strip() for x in visual_facts or [] if str(x).strip()]
        if visible and candidate_name and not any(candidate_name in fact for fact in visible):
            conflicts.append("visual_fact_name_conflict")
        row.summary = summary
        row.content_sha256 = hashlib.sha256(raw).hexdigest()
        row.canonical_name = candidate_name if candidate_name and not conflicts else ""
        row.status = "verified" if not conflicts else "unknown"
        row.visual_fact_supported = False
        row.conflict_json = conflicts
        row.fetched_at = now
        row.expires_at = now + timedelta(days=7)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        row.status = "unknown"
        row.summary = ""
        row.content_sha256 = ""
        row.visual_fact_supported = False
        row.conflict_json = [f"network_or_validation:{type(exc).__name__}"]
        row.fetched_at = now
        row.expires_at = now + timedelta(minutes=15)
    row.updated_at = now
    session.commit()
    session.refresh(row)
    return row
