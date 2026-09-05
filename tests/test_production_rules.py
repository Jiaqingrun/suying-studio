"""Unit tests for GVideoRules production rule schema / store / clamp."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from engine.catalog.db import Customer, Job, init_db, reset_engine
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.template.rule_parser import parse_production_rules_with_ollama
from engine.template.rule_schema import (
    SCHEMA_VERSION,
    apply_rules_to_template,
    resolve_job_inputs,
    rules_semantic_hints,
    validate_and_clamp,
)
from engine.template.engine import DEFAULT_TEMPLATE
from engine.template.rule_store import (
    activate_rule,
    approve_rule,
    create_draft,
    freeze_for_job,
    get_active_rule,
    list_rules,
    delete_draft,
)


def test_output_seed_and_narration_vary_while_remaining_reproducible():
    from engine.jobs.worker import seed_for_output
    from engine.pack.narration_script import narration_script_zh

    first_seed = seed_for_output(42, 0)
    second_seed = seed_for_output(42, 1)
    assert first_seed == 42
    assert second_seed != first_seed
    assert seed_for_output(42, 1) == second_seed

    kwargs = {
        "title": "屏幕标题不要念",
        "brand": "演示品牌",
        "theme": "产品",
        "clip_hints": ["画面展示仓库中整齐排列的产品细节", "工作人员正在认真核对现场货物"],
        "target_duration_sec": 22,
        "tone": "professional",
    }
    first = narration_script_zh(**kwargs, variation_seed=first_seed)
    second = narration_script_zh(**kwargs, variation_seed=second_seed)
    assert first != second
    assert "仓库中整齐排列" in first
    assert narration_script_zh(**kwargs, variation_seed=first_seed) == first


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


def test_validate_rejects_forbidden_and_clamps_quality():
    report = validate_and_clamp(
        {
            "skip_ready_gate": True,
            "title_color": "#FF0000",
            "min_cliplet_quality": 0.1,
            "pace": "fast",
            "theme": "产品细节",
            "bogus_field": 1,
        }
    )
    assert report["schema_version"] == SCHEMA_VERSION
    assert any("skip_ready_gate" in r for r in report["rejected"])
    assert report["effective_rules"]["title_color"] == "#FF0000"
    assert any("bogus_field" in r for r in report["rejected"])
    assert report["effective_rules"]["pace"] == "fast"
    assert report["effective_rules"]["theme"] == "产品细节"
    # quality below floor rejected in normalize (not applied)
    assert report["effective_rules"]["min_cliplet_quality"] is None
    assert any("min_cliplet_quality" in r for r in report["rejected"])


def test_resolve_job_inputs_and_template_pace():
    resolved = resolve_job_inputs(
        theme="default",
        category="default",
        template_name="default-vertical",
        rules={"theme": "仓配", "pace": "fast", "target_duration_sec": 25},
    )
    assert resolved["theme"] == "仓配"
    assert resolved["template_name"] == "fast-ship"
    assert resolved["sources"]["theme"] == "rule"
    tpl = apply_rules_to_template(DEFAULT_TEMPLATE, resolved["effective_rules"])
    assert abs(float(tpl.target_duration) - 25.0) < 0.01


def test_strict_semantic_cannot_be_turned_off_by_rule():
    report = validate_and_clamp(
        {"strict_semantic_v1": False},
        job_strict_semantic=True,
    )
    assert report["effective_rules"]["strict_semantic_v1"] is True
    assert any(c.get("field") == "strict_semantic_v1" for c in report["clamped"])


def test_burn_mono_clamps_subtitle_lang_to_voice_lang():
    report = validate_and_clamp(
        {
            "voice_lang": "zh",
            "subtitle_lang": "zh-TW",
            "subtitle_burn": "burn_mono",
        }
    )
    assert report["effective_rules"]["subtitle_lang"] == "zh"
    assert report["effective_rules"]["subtitle_burn"] == "burn_mono"
    assert any(
        c.get("field") == "subtitle_lang" and c.get("action") == "aligned_to_voice_lang"
        for c in report["clamped"]
    )


def test_burn_dual_keeps_mismatched_subtitle_lang():
    report = validate_and_clamp(
        {
            "voice_lang": "zh",
            "subtitle_lang": "en",
            "subtitle_burn": "burn_dual",
            "dual_secondary_lang": "en",
        }
    )
    assert report["effective_rules"]["subtitle_lang"] == "en"
    assert report["effective_rules"]["subtitle_burn"] == "burn_dual"
    assert not any(c.get("field") == "subtitle_lang" for c in report["clamped"])


def test_exclude_people_is_consumed_as_scene_filter_and_query():
    report = validate_and_clamp({"exclude_people_faces": True})
    hints = rules_semantic_hints(report["effective_rules"])
    assert "people_activity" in hints["exclude_scenes"]
    assert "无人" in hints["semantic_query_extra"]


def test_parser_fail_closed_on_short_and_mock_malformed(monkeypatch):
    bad = parse_production_rules_with_ollama(source_text="短", model="x")
    assert bad["ok"] is False

    import engine.template.rule_parser as rp

    def fake_heavy(**kwargs):
        return {
            "ok": True,
            "body": {"message": {"content": "不是json"}},
        }

    monkeypatch.setattr(rp, "heavy_request", fake_heavy)
    out = parse_production_rules_with_ollama(source_text="希望节奏快点多拍细节", model="mock")
    assert out["ok"] is False
    assert "合法 JSON" in out["error"]


def test_parser_requires_ai_to_complete_every_structured_field(monkeypatch):
    import json

    payload = {
        "theme": "default",
        "category": "default",
        "template_preference": "default-vertical",
        "target_duration_sec": 25,
        "pace": "normal",
        "narration_tone": "warm",
        "prefer_semantic_labels": [],
        "exclude_semantic_labels": [],
        "exclude_people_faces": False,
        "strict_semantic_v1": False,
        "min_cliplet_quality": 0.35,
        "notes": "按日常暖色表达补齐默认主题、分类和时长。",
        "confidence": 0.8,
        "warnings": [],
    }

    import engine.template.rule_parser as rp

    def fake_heavy(**kwargs):
        schema = kwargs["payload"]["format"]
        assert schema["properties"]["theme"]["type"] == "string"
        assert schema["properties"]["target_duration_sec"]["type"] == "number"
        return {
            "ok": True,
            "body": {"message": {"content": json.dumps(payload, ensure_ascii=False)}},
        }

    monkeypatch.setattr(rp, "heavy_request", fake_heavy)
    out = parse_production_rules_with_ollama(
        source_text="日常视频语气温暖，其余请按合理默认补齐",
        model="mock",
    )
    assert out["ok"] is True
    assert out["effective_rules"]["theme"] == "default"
    assert out["effective_rules"]["target_duration_sec"] == 25.0
    assert out["effective_rules"]["min_cliplet_quality"] == 0.35
    assert out["effective_rules"]["notes"]


def test_customer_isolation_and_job_freeze(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from sqlalchemy import select

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        a = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert a is not None
        b = Customer(name="规则客户B", library_root=str(tmp_path / "b"), output_root=str(tmp_path / "bo"))
        session.add(b)
        session.commit()
        session.refresh(a)
        session.refresh(b)

        draft = create_draft(
            session,
            customer_id=a.id,
            name="快节奏",
            source_text="节奏快一点",
            rules={"pace": "fast", "theme": "细节"},
        )
        approve_rule(session, draft)
        activate_rule(session, draft, a)
        frozen = freeze_for_job(draft)
        assert frozen and frozen["revision"] == draft.revision
        assert frozen["effective_rules"]["pace"] == "fast"

        from engine.template.rule_store import update_draft

        import pytest

        with pytest.raises(ValueError, match="不可编辑"):
            update_draft(session, draft, rules={"pace": "slow"})
        assert draft.status == "approved"
        # Frozen snapshot immutable
        assert frozen["effective_rules"]["pace"] == "fast"

        assert list_rules(session, customer_id=b.id) == []
        assert get_active_rule(session, customer_id=b.id) is None
        assert get_active_rule(session, customer_id=a.id) is not None

        next_draft = create_draft(
            session,
            customer_id=a.id,
            name="快节奏",
            source_text="下一版改慢",
            rules={"pace": "slow"},
        )
        assert next_draft.revision == draft.revision + 1
        assert next_draft.id != draft.id
    finally:
        session.close()


def test_api_production_rules_flow(tmp_path: Path, monkeypatch):
    _tmp_settings(tmp_path, monkeypatch)

    from engine.api.app import app

    client = TestClient(app)
    schema = client.get("/production-rules/schema")
    assert schema.status_code == 200
    assert schema.json()["ok"]
    assert schema.json()["metadata"]["active_scope"] == ["customer_id", "content_category"]
    assert schema.json()["metadata"]["job_snapshot"] == "immutable"

    created = client.post(
        "/production-rules",
        json={
            "name": "测试规则",
            "source_text": "多拍产品细节，旁白朴实",
            "rules": {
                "pace": "normal",
                "narration_tone": "plain",
                "prefer_semantic_labels": ["product_closeup"],
                "skip_ready_gate": True,
            },
        },
    )
    assert created.status_code == 200, created.text
    rule = created.json()["rule"]
    assert rule["status"] == "draft"
    assert any("skip_ready_gate" in str(x) for x in rule["rejected"])

    rid = rule["id"]
    approved = client.post(f"/production-rules/{rid}/approve", json={"approved_by": "tester"})
    assert approved.status_code == 200
    activated = client.post(f"/production-rules/{rid}/activate")
    assert activated.status_code == 200
    active = client.get("/production-rules/active")
    assert active.json()["rule"]["id"] == rid

    listed = client.get("/production-rules")
    assert listed.status_code == 200
    assert listed.json()["active_id"] == rid

    dry = client.post(
        "/dry-run",
        json={"theme": "default", "category": "default", "use_active_rule": True},
    )
    assert dry.status_code == 200, dry.text
    assert "production_rules" in dry.json()


def test_v1_category_migration_and_independent_active_revisions(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_engine, get_session
    from sqlalchemy import text

    _tmp_settings(tmp_path, monkeypatch)
    with get_engine().begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(production_rule_profiles)"))}
        assert "content_category" in cols
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        product = create_draft(
            session, customer_id=customer.id, content_category="产品", name="产品规则",
            source_text="产品", rules={"pace": "fast"}
        )
        scene = create_draft(
            session, customer_id=customer.id, content_category="场景", name="场景规则",
            source_text="场景", rules={"pace": "slow"}
        )
        approve_rule(session, product)
        approve_rule(session, scene)
        activate_rule(session, product, customer)
        activate_rule(session, scene, customer)
        assert get_active_rule(session, customer_id=customer.id, content_category="产品").id == product.id
        assert get_active_rule(session, customer_id=customer.id, content_category="场景").id == scene.id
    finally:
        session.close()


def test_draft_physical_delete_restrictions(tmp_path: Path, monkeypatch):
    import pytest
    from engine.catalog.db import get_session

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        draft = create_draft(
            session, customer_id=customer.id, content_category="产品", name="可删",
            source_text="", rules={}
        )
        draft_id = draft.id
        delete_draft(session, draft)
        assert session.get(type(draft), draft_id) is None
        approved = create_draft(
            session, customer_id=customer.id, content_category="产品", name="不可删",
            source_text="", rules={}
        )
        approve_rule(session, approved)
        with pytest.raises(ValueError, match="只能归档"):
            delete_draft(session, approved)
        referenced = create_draft(
            session, customer_id=customer.id, content_category="场景", name="被引用草稿",
            source_text="", rules={}
        )
        session.add(
            Job(
                customer_id=customer.id,
                status="queued",
                    template_name="default-vertical",
                config_snapshot_json={"production_rules": {"rule_profile_id": referenced.id}},
            )
        )
        session.commit()
        with pytest.raises(ValueError, match="只能归档"):
            delete_draft(session, referenced)
    finally:
        session.close()


def test_archiving_active_category_does_not_resurrect_older_revision(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from engine.template.rule_store import archive_rule

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        first = create_draft(
            session, customer_id=customer.id, content_category="产品", name="第一版",
            source_text="", rules={"pace": "slow"}
        )
        second = create_draft(
            session, customer_id=customer.id, content_category="产品", name="第二版",
            source_text="", rules={"pace": "fast"}
        )
        approve_rule(session, first)
        approve_rule(session, second)
        activate_rule(session, first, customer)
        activate_rule(session, second, customer)
        archive_rule(session, second, customer)
        assert get_active_rule(session, customer_id=customer.id, content_category="产品") is None
    finally:
        session.close()


def test_active_rule_required_and_v1_freezes_as_v2(tmp_path: Path, monkeypatch):
    import pytest
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        legacy = create_draft(
            session, customer_id=customer.id, content_category="产品", name="旧规则",
            source_text="", rules={"pace": "fast"}
        )
        approve_rule(session, legacy)
        legacy.schema_version = "suying.production_rules.v1"
        legacy.effective_json = {"pace": "fast"}
        session.commit()
        activate_rule(session, legacy, customer)
        with pytest.raises(ValueError, match="use_active_rule"):
            create_job(
                session,
                CreateJobRequest(category="产品", use_active_rule=False),
                customer_id=customer.id,
            )
        with pytest.raises(ValueError, match="禁止指定"):
            create_job(
                session,
                CreateJobRequest(category="产品", rule_profile_id=legacy.id),
                customer_id=customer.id,
            )
        with pytest.raises(ValueError, match="未找到已启用"):
            create_job(
                session,
                CreateJobRequest(category="场景", rule_rotation=False),
                customer_id=customer.id,
            )
        job = create_job(
            session,
            CreateJobRequest(category="产品"),
            customer_id=customer.id,
        )
        frozen = job.config_snapshot_json["production_rules"]
        assert frozen["schema_version"] == SCHEMA_VERSION
        assert frozen["source_schema_version"] == "suying.production_rules.v1"
        assert frozen["effective_rules"]["item_label_enabled"] is False
        assert frozen.get("contract_hash")
        assert frozen["picked_by"] == "rotation"
        assert frozen["rotation_pool_size"] == 1
        assert frozen["rule_profile_id"] == legacy.id
    finally:
        session.close()


def test_activate_customer_rebinds_paths_and_rules_scope(tmp_path: Path, monkeypatch):
    """Switching tenants must rewrite settings.paths and only surface that tenant's rules."""
    _tmp_settings(tmp_path, monkeypatch)
    from engine.catalog.db import get_session
    from engine.config.settings import load_settings
    from engine.api.app import app

    session = get_session()
    try:
        a = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert a is not None
        a.library_root = str(tmp_path / "a-lib")
        a.output_root = str(tmp_path / "a-root" / "02-成片")
        b = Customer(
            name="规则客户B",
            library_root=str(tmp_path / "b-lib"),
            output_root=str(tmp_path / "b-root" / "02-成片"),
        )
        session.add(b)
        session.commit()
        session.refresh(a)
        session.refresh(b)
        a_id, b_id = a.id, b.id
        Path(a.library_root).mkdir(parents=True, exist_ok=True)
        Path(a.output_root).mkdir(parents=True, exist_ok=True)
        Path(b.library_root).mkdir(parents=True, exist_ok=True)
        Path(b.output_root).mkdir(parents=True, exist_ok=True)

        a_rule = create_draft(
            session,
            customer_id=a_id,
            name="A规则",
            source_text="A客户专用",
            rules={"pace": "fast"},
        )
        approve_rule(session, a_rule)
        activate_rule(session, a_rule, a)
        b_rule = create_draft(
            session,
            customer_id=b_id,
            name="B规则",
            source_text="B客户专用",
            rules={"pace": "slow"},
        )
        approve_rule(session, b_rule)
        activate_rule(session, b_rule, b)
    finally:
        session.close()

    client = TestClient(app)
    switch_b = client.post("/customers/activate", json={"name": "规则客户B"})
    assert switch_b.status_code == 200, switch_b.text
    body = switch_b.json()
    assert body["active_customer"] == "规则客户B"
    assert body["active_customer_id"] == b_id
    assert "b-root" in str(body["paths"]["output_root"])
    settings = load_settings()
    assert settings.active_customer == "规则客户B"
    assert "b-root" in str(settings.paths.output_root)
    assert "b-lib" in str(settings.paths.library_root)
    assert str(settings.paths.music_root).endswith("04-音乐")

    listed = client.get("/production-rules?orientation=portrait&content_category=default")
    assert listed.status_code == 200
    data = listed.json()
    assert data["customer_id"] == b_id
    assert data["customer_name"] == "规则客户B"
    names = {r["name"] for r in data["rules"]}
    assert names == {"B规则"}
    assert data["active_id"] == b_rule.id

    switch_a = client.post("/customers/activate", json={"name": "规则客户A"})
    assert switch_a.status_code == 200
    settings = load_settings()
    assert settings.active_customer == "规则客户A"
    assert "a-root" in str(settings.paths.output_root)
    listed_a = client.get("/production-rules?orientation=portrait&content_category=default")
    assert listed_a.json()["customer_id"] == a_id
    assert {r["name"] for r in listed_a.json()["rules"]} == {"A规则"}


def test_gvisualpack_defaults_off():
    report = validate_and_clamp({})
    eff = report["effective_rules"]
    assert eff.get("intro_punch") == "none"
    assert eff.get("item_label_motion") == "none"
    assert eff.get("end_card") == "none"
    assert eff.get("color_lut") == "off"
    assert eff.get("plan_lang_reuse") == "off"


def test_gvisualpack_enums_clamp():
    report = validate_and_clamp(
        {
            "intro_punch": "soft",
            "item_label_motion": "fade",
            "end_card": "simple",
            "color_lut": "light",
            "plan_lang_reuse": "on",
        }
    )
    eff = report["effective_rules"]
    assert eff["intro_punch"] == "soft"
    assert eff["item_label_motion"] == "fade"
    assert eff["end_card"] == "simple"
    assert eff["color_lut"] == "light"
    assert eff["plan_lang_reuse"] == "on"
    bad = validate_and_clamp(
        {
            "intro_punch": "kenburns",
            "end_card": "flash",
            "color_lut": "heavy",
            "plan_lang_reuse": "maybe",
        }
    )
    assert bad["effective_rules"]["intro_punch"] == "none"
    assert bad["effective_rules"]["end_card"] == "none"
    assert bad["effective_rules"]["color_lut"] == "off"
    assert bad["effective_rules"]["plan_lang_reuse"] == "off"


def test_grulelabopt_schema_orientation_defaults():
    from engine.template.rule_schema import ORIENTATION_DEFAULTS, empty_rules, orientation_defaults

    od = orientation_defaults()
    assert "portrait" in od and "landscape" in od
    assert od["portrait"]["title_glyph_top_px"] == 220
    assert od["landscape"]["title_glyph_top_px"] == 120
    assert od["portrait"]["title_glyph_top_px_min"] == 120
    assert od["portrait"]["title_glyph_top_px_max"] == 420
    assert od["landscape"]["title_glyph_top_px_min"] == 60
    base = empty_rules()
    assert base["intro_punch"] == "none"
    assert base["color_lut"] == "off"
    # clamp empty uses same packaging defaults
    report = validate_and_clamp({})
    assert report["effective_rules"]["pace"] == base["pace"]
    assert ORIENTATION_DEFAULTS["portrait"]["subtitle_glyph_bottom_px"] == 420


def test_grulelabopt_api_schema_and_cross_category_copy(tmp_path: Path, monkeypatch):
    _tmp_settings(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    schema = client.get("/production-rules/schema")
    assert schema.status_code == 200
    body = schema.json()
    assert body["ok"]
    assert "orientation_defaults" in body
    assert body["orientation_defaults"]["portrait"]["title_font_size"] == 92
    assert "default" in body.get("recommended_content_categories", [])
    assert "premium" in body.get("recommended_content_categories", [])

    created = client.post(
        "/production-rules",
        json={
            "name": "日更基线",
            "content_category": "default",
            "source_text": "节奏正常",
            "rules": {"pace": "normal", "narration_tone": "warm"},
        },
    )
    assert created.status_code == 200, created.text
    rid = created.json()["rule"]["id"]
    assert client.post(f"/production-rules/{rid}/approve", json={"approved_by": "t"}).status_code == 200
    assert client.post(f"/production-rules/{rid}/activate").status_code == 200

    copied = client.post(
        f"/production-rules/{rid}/copy",
        json={"content_category": "premium", "name": "日更基线·精品"},
    )
    assert copied.status_code == 200, copied.text
    draft = copied.json()["rule"]
    assert draft["status"] == "draft"
    assert draft["content_category"] == "premium"
    assert draft["name"] == "日更基线·精品"
    assert draft["effective_rules"]["narration_tone"] == "warm"

    # default active slot untouched
    active = client.get("/production-rules/active?content_category=default&orientation=portrait")
    assert active.json()["rule"]["id"] == rid
    premium_active = client.get(
        "/production-rules/active?content_category=premium&orientation=portrait"
    )
    assert premium_active.json().get("rule") is None


def test_rule_rotation_pool_excludes_drafts_and_falls_back(tmp_path: Path, monkeypatch):
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job
    from engine.template.rule_store import (
        list_rotation_pool,
        pick_rule_for_job,
        set_customer_rotation_enabled,
        set_rule_rotation,
    )

    _tmp_settings(tmp_path, monkeypatch)
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "规则客户A"))
        assert customer
        draft = create_draft(
            session,
            customer_id=customer.id,
            content_category="default",
            name="草稿不入池",
            source_text="",
            rules={"pace": "normal"},
        )
        saved = create_draft(
            session,
            customer_id=customer.id,
            content_category="default",
            name="日更A",
            source_text="",
            rules={"pace": "fast", "narration_tone": "warm"},
        )
        other_cat = create_draft(
            session,
            customer_id=customer.id,
            content_category="premium",
            name="精品B",
            source_text="",
            rules={"pace": "slow", "narration_tone": "calm"},
        )
        landscape = create_draft(
            session,
            customer_id=customer.id,
            content_category="default",
            orientation="landscape",
            name="横屏C",
            source_text="",
            rules={"pace": "normal"},
        )
        approve_rule(session, saved)
        approve_rule(session, other_cat)
        approve_rule(session, landscape)
        activate_rule(session, saved, customer)

        pool = list_rotation_pool(session, customer_id=customer.id, orientation="portrait")
        assert {row.id for row in pool} == {saved.id, other_cat.id}
        assert draft.id not in {row.id for row in pool}
        assert landscape.id not in {row.id for row in pool}

        set_rule_rotation(session, other_cat, enabled=False)
        pool = list_rotation_pool(session, customer_id=customer.id, orientation="portrait")
        assert [row.id for row in pool] == [saved.id]

        picked, by = pick_rule_for_job(
            session,
            customer=customer,
            content_category="default",
            orientation="portrait",
        )
        assert picked.id == saved.id
        assert by == "rotation"

        set_rule_rotation(session, saved, enabled=False)
        empty = list_rotation_pool(session, customer_id=customer.id, orientation="portrait")
        assert empty == []
        fallback, by = pick_rule_for_job(
            session,
            customer=customer,
            content_category="default",
            orientation="portrait",
        )
        assert fallback.id == saved.id
        assert by == "active_slot"

        set_rule_rotation(session, saved, enabled=True)
        set_rule_rotation(session, other_cat, enabled=True)
        set_customer_rotation_enabled(session, customer, enabled=False)
        session.refresh(customer)
        forced_slot, by = pick_rule_for_job(
            session,
            customer=customer,
            content_category="default",
            orientation="portrait",
            rotation=None,
        )
        assert forced_slot.id == saved.id
        assert by == "active_slot"

        set_customer_rotation_enabled(session, customer, enabled=True)
        session.refresh(customer)
        job = create_job(
            session,
            CreateJobRequest(category="default", target_count=3),
            customer_id=customer.id,
        )
        frozen = job.config_snapshot_json["production_rules"]
        assert frozen["picked_by"] == "rotation"
        assert frozen["rotation_pool_size"] == 2
        assert frozen["rule_profile_id"] in {saved.id, other_cat.id}

        import pytest

        with pytest.raises(ValueError, match="禁止指定"):
            create_job(
                session,
                CreateJobRequest(category="default", rule_profile_id=saved.id),
                customer_id=customer.id,
            )
    finally:
        session.close()


def test_rule_rotation_api_policy_and_switch(tmp_path: Path, monkeypatch):
    _tmp_settings(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    created = client.post(
        "/production-rules",
        json={
            "name": "轮换基线",
            "content_category": "default",
            "source_text": "",
            "rules": {"pace": "normal"},
        },
    )
    assert created.status_code == 200, created.text
    rid = created.json()["rule"]["id"]
    listed = client.get("/production-rules?content_category=default&orientation=portrait")
    body = listed.json()
    assert body["rotation_policy"] is True
    assert body["rotation_pool"] == []
    assert created.json()["rule"].get("rotation_enabled") is True

    assert client.post(f"/production-rules/{rid}/approve", json={"approved_by": "t"}).status_code == 200
    listed = client.get("/production-rules?content_category=default&orientation=portrait")
    assert any(item["id"] == rid for item in listed.json()["rotation_pool"])

    off = client.post(f"/production-rules/{rid}/rotation", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["rule"]["rotation_enabled"] is False
    listed = client.get("/production-rules?content_category=default&orientation=portrait")
    assert listed.json()["rotation_pool"] == []

    policy = client.post("/production-rules/rotation-policy", json={"enabled": False})
    assert policy.status_code == 200
    assert policy.json()["rotation_policy"] is False
    listed = client.get("/production-rules?content_category=default&orientation=portrait")
    assert listed.json()["rotation_policy"] is False

