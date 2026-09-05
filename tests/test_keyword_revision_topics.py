from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Asset,
    Base,
    Cliplet,
    Customer,
    KeywordPack,
    OfficialObjectEvidence,
    SemanticCluster,
    SemanticClusterMember,
    SemanticHealthCandidate,
)
from engine.catalog.keyword_pack import (
    approve_revision_draft,
    create_revision_draft,
    import_keyword_pack,
    promote_revision_draft,
    rollback_keyword_pack,
)
from engine.catalog.semantic_ops import freeze_topic_intent
from engine.ingest.semantic_gate import SEMANTIC_SCHEMA_VERSION, STRICT_EMBEDDING_SCHEMA_VERSION
from engine.jobs.queue import CreateJobRequest, TopicIntentRequest, create_job
from tests.test_semantic_ingest import valid_analysis


def _pack(revision: int = 1) -> dict:
    return {
        "meta": {"schema": "suying.customer.content-pack.v2", "revision": revision},
        "identity": {"display_name": "测试品牌"},
        "facts": {},
        "taxonomy": {
            "categories": {
                "box": {"label": "箱装货物", "synonyms": ["箱体"]},
            },
            "content_types": {},
        },
        "copy_components": {"themes": {}, "default": {}, "hashtags": []},
        "recipes": {},
        "compliance": {"blocked_terms": ["绝对功效"]},
    }


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        customer = Customer(
            name="客户A",
            keyword_pack_path=str(root / "keyword-pack.json"),
            profile_json={},
        )
        other = Customer(name="客户B", profile_json={})
        session.add_all([customer, other])
        session.flush()
        (root / "keyword-pack.json").write_text(
            __import__("json").dumps(_pack(), ensure_ascii=False), encoding="utf-8"
        )
        import_keyword_pack(
            session,
            customer.name,
            _pack(),
            source_path=str(root / "keyword-pack.json"),
        )
        yield session, customer, other, root
    session.close()
    engine.dispose()


def _accepted_candidate(session: Session, customer_id: int) -> SemanticHealthCandidate:
    row = SemanticHealthCandidate(
        customer_id=customer_id,
        run_id=1,
        candidate_key=f"accepted:{customer_id}",
        kind="taxonomy_suggestion",
        severity="warning",
        status="accept",
        evidence_level="semantic.v1",
        evidence_json={"strict": True},
    )
    session.add(row)
    session.commit()
    return row


def test_low_risk_auto_promotes_and_archives_then_rolls_back(ctx) -> None:
    session, customer, _, root = ctx
    candidate = _accepted_candidate(session, customer.id)
    draft = create_revision_draft(
        session,
        customer,
        patch={"taxonomy": {"categories": {"box": {"synonyms": ["箱体", "纸箱"]}}}},
        semantic_candidate_ids=[candidate.id],
        draft_key="low-risk-r2",
    )
    assert draft.risk_level == "low"
    assert draft.validation_json["auto_promotable"] is True
    promoted = promote_revision_draft(
        session, customer, draft_id=draft.id, auto=True, actor="system"
    )
    assert promoted.status == "promoted"
    active = session.get(KeywordPack, promoted.promoted_pack_id)
    assert active.revision == 2
    assert session.scalar(
        select(KeywordPack).where(
            KeywordPack.customer_id == customer.id,
            KeywordPack.revision == 1,
            KeywordPack.status == "archived",
        )
    )
    assert list((root / "archive").glob("keyword-pack.r1.*.json"))
    rolled = rollback_keyword_pack(session, customer, source_revision=1)
    assert rolled.revision == 3
    assert rolled.data_json["meta"]["rollback_of_revision"] == 1


def test_public_claim_requires_human_approval_and_customer_scope(ctx) -> None:
    session, customer, other, _ = ctx
    candidate = _accepted_candidate(session, customer.id)
    official = OfficialObjectEvidence(
        customer_id=customer.id,
        url="https://example.com/brand",
        url_hash="brand-evidence",
        status="verified",
        canonical_name="新公开品牌名",
        summary="官方目录中的新公开品牌名",
        content_sha256="b" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    session.add(official)
    session.commit()
    draft = create_revision_draft(
        session,
        customer,
        patch={"identity": {"display_name": "新公开品牌名"}},
        semantic_candidate_ids=[candidate.id],
        official_evidence_ids=[official.id],
        draft_key="high-risk-r2",
    )
    assert draft.risk_level == "high"
    with pytest.raises(ValueError):
        promote_revision_draft(session, customer, draft_id=draft.id, auto=True)
    with pytest.raises(ValueError):
        promote_revision_draft(session, customer, draft_id=draft.id, auto=False)
    with pytest.raises(LookupError):
        approve_revision_draft(session, other, draft_id=draft.id, approved_by="operator")
    approve_revision_draft(session, customer, draft_id=draft.id, approved_by="operator")
    promoted = promote_revision_draft(session, customer, draft_id=draft.id)
    assert promoted.status == "promoted"


def _topic_fixture(session: Session, customer: Customer):
    asset = Asset(
        customer_id=customer.id,
        uuid=f"asset-{customer.id}",
        source_path="/tmp/a.mp4",
        storage_path="/tmp/a.mp4",
        status="ready",
    )
    session.add(asset)
    session.flush()
    data = valid_analysis()
    data["products"][0]["use"] = "仓库码放"
    data["frames"][0]["visible_facts"].append("人物正在用蓝色箱装产品进行仓库码放")
    clip = Cliplet(
        asset_id=asset.id,
        asset_uuid=asset.uuid,
        start_sec=0,
        end_sec=4,
        duration_sec=4,
        description=data["description"],
        status="usable",
        semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
        semantic_json=data,
        semantic_gate_json={"passed": True},
        embedding_json=[1.0, 0.0],
        embedding_backend="ollama",
        embedding_model="nomic-embed-text",
        embedding_schema_version=STRICT_EMBEDDING_SCHEMA_VERSION,
    )
    session.add(clip)
    session.flush()
    cluster = SemanticCluster(
        customer_id=customer.id,
        cluster_key=f"cluster-{customer.id}",
        name="蓝色箱装产品",
        labels_json={"taxonomy_category": "箱装货物"},
    )
    session.add(cluster)
    session.flush()
    session.add(
        SemanticClusterMember(
            customer_id=customer.id,
            cluster_id=cluster.id,
            cliplet_id=clip.id,
            similarity=0.91,
        )
    )
    official = OfficialObjectEvidence(
        customer_id=customer.id,
        url="https://example.com/box",
        url_hash=f"hash-{customer.id}",
        status="verified",
        canonical_name="蓝色箱装产品",
        summary="官方目录：蓝色箱装产品，可用于仓库码放。",
        content_sha256="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    session.add(official)
    session.commit()
    return cluster, clip, official


def test_topic_dual_evidence_threshold_and_job_snapshot_freeze(ctx) -> None:
    session, customer, other, _ = ctx
    cluster, clip, official = _topic_fixture(session, customer)
    frozen = freeze_topic_intent(
        session,
        customer_id=customer.id,
        mode="single_product",
        cluster_ids=[cluster.id],
        official_evidence_ids=[official.id],
        requested_uses=["仓库码放", "治疗疾病"],
    )
    assert frozen["allowed_uses"] == ["仓库码放"]
    assert frozen["rejected_uses"] == ["治疗疾病"]
    assert frozen["allow_whole_asset_fallback"] is False
    with pytest.raises(LookupError):
        freeze_topic_intent(
            session,
            customer_id=other.id,
            mode="single_product",
            cluster_ids=[cluster.id],
            official_evidence_ids=[official.id],
        )
    member = session.scalar(
        select(SemanticClusterMember).where(SemanticClusterMember.cluster_id == cluster.id)
    )
    cluster_two = SemanticCluster(
        customer_id=customer.id,
        cluster_key="cluster-second",
        name="同类箱装产品",
        labels_json={"taxonomy_category": "箱装货物"},
    )
    session.add(cluster_two)
    session.flush()
    session.add(
        SemanticClusterMember(
            customer_id=customer.id,
            cluster_id=cluster_two.id,
            cliplet_id=clip.id,
            similarity=0.9,
        )
    )
    member.similarity = 0.5
    session.commit()
    with pytest.raises(ValueError):
        freeze_topic_intent(
            session,
            customer_id=customer.id,
            mode="same_category_products",
            cluster_ids=[cluster.id, cluster_two.id],
            official_evidence_ids=[official.id],
            similarity_threshold=0.82,
        )
    member.similarity = 0.91
    session.commit()
    from engine.template.rule_store import ensure_default_rule

    ensure_default_rule(session, customer=customer, orientation="portrait")
    job = create_job(
        session,
        CreateJobRequest(
            customer_name=customer.name,
            target_count=1,
            use_active_rule=True,
            topic_intent=TopicIntentRequest(
                mode="single_product",
                cluster_ids=[cluster.id],
                official_evidence_ids=[official.id],
                requested_uses=["仓库码放"],
            ),
        ),
        customer_id=customer.id,
    )
    assert job.config_snapshot_json["keyword_pack"]["revision"] == 1
    assert job.config_snapshot_json["topic_intent"]["cluster_keys"] == [cluster.cluster_key]
    assert job.config_snapshot_json["strict_semantic_v1"] is True
    import_keyword_pack(session, customer.name, _pack(2), source_kind="later-update")
    session.refresh(job)
    assert job.config_snapshot_json["keyword_pack"]["revision"] == 1
