"""GRL.8: production rules pin a keyword-pack content facet."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from engine.catalog.db import Customer, init_db, reset_engine
from engine.catalog.keyword_pack import (
    compile_keyword_pack,
    import_keyword_pack,
    list_content_facets,
    list_title_pool,
    resolve_facet_slice,
    resolve_rule_facet,
    validate_keyword_pack,
)
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.template.rule_schema import content_facet_of, empty_rules, validate_and_clamp
from engine.template.rule_store import (
    activate_rule,
    approve_rule,
    create_draft,
    drafts_from_pack,
)


def _tmp_settings(tmp_path: Path, monkeypatch) -> AppSettings:
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    boot = tmp_path / "boot"
    for p in (data, lib, out, boot):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=lib,
            library_roots=[str(lib)],
            output_root=out,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer="规则客户A",
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    return settings


def _facet_pack() -> dict:
    return {
        "meta": {"schema": "suying.customer.content-pack.v2", "revision": 1},
        "identity": {"display_name": "规则客户A", "positioning_fact_id": "company.positioning"},
        "facts": {
            "company.positioning": {
                "value": "本地护理",
                "visibility": "public",
                "evidence_status": "client_statement",
            }
        },
        "taxonomy": {"content_types": {}},
        "copy_components": {
            "default": {
                "hooks": ["今天来看看这家店"],
                "titles": ["这家门店好温柔", "到店护理先沟通"],
            },
            "themes": {
                "default": {
                    "keywords": [{"text": "本地护理", "weight": 1}],
                    "titles": ["这家门店好温柔"],
                    "hooks": ["今天来看看这家店"],
                },
                "门店形象": {
                    "label": "门店形象",
                    "keywords": [{"text": "门店门头", "weight": 3}],
                    "titles": ["进门先问好再请坐", "拖鞋换好再慢慢坐"],
                    "hooks": ["进门先问好"],
                },
                "品牌故事": {
                    "label": "品牌故事",
                    "keywords": [{"text": "回乡开店", "weight": 3}],
                    "titles": ["农村出来的开店人", "十六岁去广州学手艺"],
                    "hooks": ["农村出来的开店人"],
                },
                "空面": {
                    "label": "空面",
                    "keywords": [{"text": "空", "weight": 1}],
                    "titles": [],
                    "hooks": [],
                },
            },
            "library": {"hook": [{"id": "hook.01", "text": "今天来看看这家店", "requires_evidence": []}]},
        },
        "recipes": {},
        "compliance": {"hard_deny": [], "blocked_terms": []},
    }


def test_schema_content_facet_default_and_normalize():
    base = empty_rules()
    assert base["content_facet"] is None
    report = validate_and_clamp({"content_facet": "门店形象", "pace": "normal"})
    assert report["effective_rules"]["content_facet"] == "门店形象"
    cleared = validate_and_clamp({"content_facet": "default"})
    assert cleared["effective_rules"]["content_facet"] is None
    assert content_facet_of({"content_facet": " 品牌故事 "}) == "品牌故事"


def test_strict_theme_title_pool_does_not_mix_default():
    from engine.catalog.db import KeywordPack

    compiled = compile_keyword_pack(_facet_pack())
    pack = KeywordPack(id=1, customer_id=1, name="default", data_json=compiled, status="active")
    store = list_title_pool(pack, theme="门店形象", strict_theme=True)
    assert "进门先问好再请坐" in store
    assert "这家门店好温柔" not in store
    mixed = list_title_pool(pack, theme="门店形象", strict_theme=False)
    assert "进门先问好再请坐" in mixed
    assert "这家门店好温柔" in mixed
    empty = resolve_facet_slice(pack, "空面")
    assert empty["ok"] is False
    assert "没有可用标题" in empty["error"]


def test_job_freeze_pins_facet_and_fail_closed(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        import_keyword_pack(session, customer.name, _facet_pack(), source_kind="test")
        store = create_draft(
            session,
            customer_id=customer.id,
            name="门店形象规则",
            source_text="",
            rules={"content_facet": "门店形象", "pace": "normal"},
        )
        story = create_draft(
            session,
            customer_id=customer.id,
            content_category="premium",
            name="品牌故事规则",
            source_text="",
            rules={"content_facet": "品牌故事", "pace": "slow"},
        )
        approve_rule(session, store)
        approve_rule(session, story)
        activate_rule(session, store, customer)

        job = create_job(
            session,
            CreateJobRequest(category="default", rule_rotation=False, target_count=1),
            customer_id=customer.id,
        )
        frozen = job.config_snapshot_json["production_rules"]
        assert frozen["content_facet"] == "门店形象"
        assert frozen["facet_resolved_from"] == "rule_binding"
        assert "门店形象标题" in (frozen.get("title_categories_resolved") or [])

        empty = create_draft(
            session,
            customer_id=customer.id,
            name="空面规则",
            source_text="",
            rules={"content_facet": "空面"},
        )
        try:
            approve_rule(session, empty)
            raise AssertionError("empty facet must fail closed")
        except ValueError as exc:
            assert "没有可用标题" in str(exc)

        missing = create_draft(
            session,
            customer_id=customer.id,
            name="缺失面",
            source_text="",
            rules={"content_facet": "不存在的面"},
        )
        try:
            approve_rule(session, missing)
            raise AssertionError("missing facet must fail closed")
        except ValueError as exc:
            assert "没有内容面" in str(exc)
    finally:
        session.close()


def test_legacy_rule_without_facet_stays_customer_default(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        import_keyword_pack(session, customer.name, _facet_pack(), source_kind="test")
        row = create_draft(
            session,
            customer_id=customer.id,
            name="旧规则",
            source_text="",
            rules={"pace": "normal"},
        )
        approve_rule(session, row)
        activate_rule(session, row, customer)
        job = create_job(
            session,
            CreateJobRequest(category="default", rule_rotation=False, target_count=1),
            customer_id=customer.id,
        )
        frozen = job.config_snapshot_json["production_rules"]
        assert frozen.get("content_facet") in (None, "")
        assert frozen.get("facet_resolved_from") == "customer_default"
        meta = resolve_rule_facet(
            None,
            row.effective_json,
        )
        assert meta["ok"] is True
        assert meta["facet"] is None
    finally:
        session.close()


def test_drafts_from_pack_and_api(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        import_keyword_pack(session, customer.name, _facet_pack(), source_kind="test")
        created = drafts_from_pack(session, customer=customer, orientation="portrait")
        names = {content_facet_of(r.effective_json) for r in created}
        assert "门店形象" in names
        assert "品牌故事" in names
        assert "空面" not in names
        assert "default" not in names
        again = drafts_from_pack(session, customer=customer, orientation="portrait")
        assert again == []
    finally:
        session.close()

    from engine.api.app import app

    client = TestClient(app)
    schema = client.get("/production-rules/schema")
    assert schema.status_code == 200
    keys = [f["key"] for f in schema.json().get("fields") or []]
    assert "content_facet" in keys
    facets = client.get("/production-rules/content-facets")
    assert facets.status_code == 200
    body = facets.json()
    assert body["ok"]
    usable = {f["name"] for f in body["facets"] if f.get("usable")}
    assert "门店形象" in usable


def test_zhenxiang_seed_pack_facets_and_no_slang():
    path = Path("configs/customers/臻享丽人/keyword-pack.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    report = validate_keyword_pack(data)
    assert report["ok"], report
    compiled = compile_keyword_pack(data)
    from engine.catalog.db import KeywordPack

    pack = KeywordPack(id=1, customer_id=1, name="default", data_json=compiled, status="active")
    facets = {f["name"]: f for f in list_content_facets(pack)}
    assert facets["门店形象"]["usable"]
    assert facets["品牌故事"]["usable"]
    store = list_title_pool(pack, theme="门店形象", strict_theme=True)
    story = list_title_pool(pack, theme="品牌故事", strict_theme=True)
    service = list_title_pool(pack, theme="服务沟通", strict_theme=True)
    assert any("安静" in t or "光线" in t or "进门" in t for t in store)
    assert any("本地" in t or "好多年" in t or "石家庄" in t for t in story)
    assert any("沟通" in t or "倾听" in t or "听完" in t for t in service)
    bio_or_sop = ("农村出来的", "十六岁", "有我呢", "托盘端过来", "门口说完慢走", "自己看自己练")
    blob = " ".join(store + story + service)
    assert not any(tok in blob for tok in bio_or_sop)
    slang = ("打卡", "拉满", "上头", "妥妥", "松弛感", "超舒服")
    assert not any(tok in blob for tok in slang)
    assert data["meta"]["vo_style_lock"]["l20_frozen"] is True
    assert int(data["meta"]["revision"]) >= 21
    copy = json.dumps(data.get("copy_components") or {}, ensure_ascii=False)
    assert not any(tok in copy for tok in slang)


def test_rotation_each_rule_keeps_own_facet(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job
    from engine.template.rule_store import set_customer_rotation_enabled, set_rule_rotation

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        import_keyword_pack(session, customer.name, _facet_pack(), source_kind="test")
        store = create_draft(
            session,
            customer_id=customer.id,
            name="门店形象规则",
            source_text="",
            rules={"content_facet": "门店形象", "pace": "normal"},
        )
        story = create_draft(
            session,
            customer_id=customer.id,
            name="品牌故事规则",
            source_text="",
            rules={"content_facet": "品牌故事", "pace": "slow"},
        )
        approve_rule(session, store)
        approve_rule(session, story)
        activate_rule(session, store, customer)
        set_rule_rotation(session, store, enabled=True)
        set_rule_rotation(session, story, enabled=True)
        set_customer_rotation_enabled(session, customer, enabled=True)

        monkeypatch.setattr(
            "engine.template.rule_store.secrets.choice",
            lambda pool: store,
        )
        job_store = create_job(
            session,
            CreateJobRequest(category="default", rule_rotation=True, target_count=1),
            customer_id=customer.id,
        )
        frozen_store = job_store.config_snapshot_json["production_rules"]
        assert frozen_store["content_facet"] == "门店形象"
        assert frozen_store["picked_by"] == "rotation"

        monkeypatch.setattr(
            "engine.template.rule_store.secrets.choice",
            lambda pool: story,
        )
        job_story = create_job(
            session,
            CreateJobRequest(category="default", rule_rotation=True, target_count=1),
            customer_id=customer.id,
        )
        frozen_story = job_story.config_snapshot_json["production_rules"]
        assert frozen_story["content_facet"] == "品牌故事"
        assert frozen_story["picked_by"] == "rotation"
        assert frozen_store["content_facet"] != frozen_story["content_facet"]
    finally:
        session.close()
