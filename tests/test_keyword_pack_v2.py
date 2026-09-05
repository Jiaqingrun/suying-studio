from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Base, Customer
from engine.catalog.keyword_pack import (
    get_active_pack,
    install_keyword_pack,
    validate_keyword_pack,
)
from engine.content.compliance import check_output_fields
from engine.content.content_fingerprint import (
    build_content_fingerprint,
    select_recipe_components,
)
from engine.ingest.semantic_gate import (
    SEMANTIC_SCHEMA_VERSION,
    STRICT_EMBEDDING_MODEL,
    STRICT_EMBEDDING_SCHEMA_VERSION,
)
from engine.jobs.queue import CreateJobRequest, create_job
from tests.test_semantic_ingest import valid_analysis


def _pack(revision: int, title: str = "仓内实拍") -> dict:
    return {
        "meta": {"schema": "suying.customer.content-pack.v2", "revision": revision},
        "identity": {"display_name": "测试客户", "positioning_fact_id": "company.positioning"},
        "facts": {
            "company.positioning": {
                "value": "现场记录",
                "visibility": "public",
                "evidence_status": "client_statement",
            }
        },
        "taxonomy": {
            "content_types": {
                "warehouse_display": {
                    "label": "仓内陈列",
                    "match_any": ["warehouse", "stacking"],
                }
            }
        },
        "copy_components": {
            "default": {"hooks": ["先看现场"], "titles": [title]},
            "themes": {
                "default": {
                    "keywords": [{"text": "现场实拍", "weight": 1}],
                    "titles": [title],
                    "hooks": ["先看现场"],
                }
            },
            "library": {
                "hook": [{"id": "hook.01", "text": "先看现场", "requires_evidence": []}],
                "visible_facts": [
                    {
                        "id": "visible.01",
                        "text": "画面可见仓内陈列",
                        "requires_evidence": ["semantic.scene"],
                    }
                ],
            },
        },
        "recipes": {
            "warehouse_display": {
                "id": "recipe.warehouse_display",
                "sequence": ["hook", "visible_facts"],
            }
        },
        "compliance": {"hard_deny": ["全网最低"], "blocked_terms": []},
    }


def test_canonical_install_is_atomic_idempotent_and_preserves_profile(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        canonical = tmp_path / "keyword-pack.json"
        canonical.write_text(json.dumps(_pack(1), ensure_ascii=False), encoding="utf-8")
        customer = Customer(
            name="甲",
            keyword_pack_path=str(canonical),
            profile_json={"brand": {"logo_enabled": False}, "reach": {"enabled": True}},
        )
        session.add(customer)
        session.commit()

        first = install_keyword_pack(session, customer, canonical)
        again = install_keyword_pack(session, customer, canonical)
        assert again["idempotent"] is True
        assert again["pack"].id == first["pack"].id
        assert customer.profile_json["brand"]["logo_enabled"] is False
        assert customer.profile_json["reach"]["enabled"] is True

        update = tmp_path / "candidate.json"
        update.write_text(json.dumps(_pack(2, "装车实拍"), ensure_ascii=False), encoding="utf-8")
        second = install_keyword_pack(session, customer, update)
        assert second["pack"].revision == 2
        assert get_active_pack(session, "甲").id == second["pack"].id
        assert list((tmp_path / "archive").glob("keyword-pack.r1.*.json"))

        same_revision = tmp_path / "bad.json"
        same_revision.write_text(json.dumps(_pack(2, "另一内容"), ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="同 revision"):
            install_keyword_pack(session, customer, same_revision)


def test_v2_validation_rejects_missing_layers():
    broken = _pack(1)
    broken.pop("recipes")
    report = validate_keyword_pack(broken)
    assert report["ok"] is False
    assert "recipes_missing" in report["errors"]


def test_job_freezes_keyword_revision_across_later_update(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        canonical = tmp_path / "keyword-pack.json"
        canonical.write_text(json.dumps(_pack(1), ensure_ascii=False), encoding="utf-8")
        customer = Customer(name="乙", keyword_pack_path=str(canonical), profile_json={})
        session.add(customer)
        session.commit()
        first = install_keyword_pack(session, customer, canonical)["pack"]
        from engine.template.rule_store import ensure_default_rule

        ensure_default_rule(session, customer=customer, orientation="portrait")
        job = create_job(
            session,
            CreateJobRequest(customer_name="乙", target_count=1, use_active_rule=True),
            customer_id=customer.id,
        )
        frozen = dict(job.config_snapshot_json["keyword_pack"])
        update = tmp_path / "candidate.json"
        update.write_text(json.dumps(_pack(2, "新版标题"), ensure_ascii=False), encoding="utf-8")
        second = install_keyword_pack(session, customer, update)["pack"]
        session.refresh(job)
        assert second.id != first.id
        assert job.config_snapshot_json["keyword_pack"] == frozen
        assert frozen["id"] == first.id
        assert frozen["revision"] == 1


def test_strict_semantics_route_recipe_without_filename_inference():
    semantic = valid_analysis()
    semantic["frames"][0]["visible_facts"][0] = "纸箱整齐码放"
    clip = SimpleNamespace(
        id=7,
        semantic_json=semantic,
        semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
        semantic_gate_json={"passed": True},
        embedding_json=[0.1, 0.2],
        embedding_backend="ollama",
        embedding_model=STRICT_EMBEDDING_MODEL,
        embedding_schema_version=STRICT_EMBEDDING_SCHEMA_VERSION,
    )
    pack = _pack(1)
    fingerprint = build_content_fingerprint([clip], pack)
    routed = select_recipe_components(fingerprint, pack, seed=9)
    assert fingerprint["primary_type"] == "warehouse_display"
    assert "纸箱整齐码放" in fingerprint["visible_facts"]
    assert routed["recipe_id"] == "recipe.warehouse_display"
    assert routed["component_ids"] == ["hook.01", "visible.01"]


def test_unified_compliance_blocks_claims_and_private_identifiers():
    report = check_output_fields(
        {
            "title": "全网最低现货",
            "narration": "保证送达，联系18911195331",
            "platforms": {"douyin": {"body": "仓库面积两千平方米"}},
        },
        policy=_pack(1)["compliance"],
        evidence_ids=set(),
    )
    assert report["passed"] is False
    rule_ids = {issue["rule_id"] for issue in report["issues"]}
    assert "claim.hard_deny" in rule_ids
    assert "claim.evidence_required" in rule_ids
    assert "privacy.internal_identifier" in rule_ids


def test_safe_confirmation_fallback_does_not_require_dynamic_evidence():
    report = check_output_fields(
        {"narration": "具体型号规格与库存以实际确认结果为准"},
        policy=_pack(1)["compliance"],
        evidence_ids=set(),
    )
    assert report["passed"] is True
