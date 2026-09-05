"""Unit tests for GContent SEO/GEO foundation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from engine.catalog.db import init_db, reset_engine
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.content.compliance import check_article_compliance
from engine.content.generate import build_master_article, build_platform_variant
from engine.content.platforms import get_platform, list_platforms
from engine.content.reply_drafts import suggest_replies
from engine.reach.article_publishers import publisher_for


def test_platform_registry_core():
    rows = list_platforms(include_experimental=False)
    ids = {r["id"] for r in rows}
    assert "website" in ids
    assert "baijiahao" in ids
    assert get_platform("wechat_mp")["transport"] == "browser"


def test_compliance_requires_facts_and_ai_label():
    bad = check_article_compliance(
        title="测试标题足够长了吧",
        body_md="短",
        fact_ids=[],
        citations=[],
        ai_labeled=True,
    )
    assert not bad["passed"]
    good_body = ("可核验事实说明正文，用于满足最短字数要求。" * 12) + "\n\n（含 AI 生成声明：本稿经人工审校。）"
    ok = check_article_compliance(
        title="品牌服务说明文档标题",
        body_md=good_body,
        fact_ids=[1, 2, 3],
        citations=[{"url": "https://example.com"}],
        ai_labeled=True,
        platform="baijiahao",
    )
    assert ok["passed"], ok


def test_build_master_and_variant(tmp_path: Path):
    class F:
        id = 1
        title = "资质"
        body = "公司已取得相关经营许可"
        source_url = "https://example.com/license"

    draft = build_master_article(
        brand="演示品牌",
        topic="交付时效",
        facts=[F()],  # type: ignore[list-item]
        questions=["交付要多久？"],
        regions=["华东"],
        author="编辑",
    )
    assert "AI" in draft["body_md"] or "人工智能" in draft["body_md"]
    assert draft["fact_ids"] == [1]
    v = build_platform_variant(
        platform="baijiahao",
        master_title=draft["title"],
        master_body=draft["body_md"],
        brand="演示品牌",
    )
    assert v["title"]
    assert "AI" in v["body_md"] or "人工智能" in v["body_md"] or "创作声明" in v["body_md"]


def test_reply_drafts_mark_incomplete_context():
    drafts = suggest_replies(sender="用户A", summary="想了解报价", brand="演示")
    assert len(drafts) == 3
    assert all("脱敏摘要" in d or "上下文可能不完整" in d for d in drafts)


def test_publisher_dry_run():
    pub = publisher_for("baijiahao")
    result = pub.publish(
        title="足够长度的百家号标题测试",
        body_md="正文" * 40,
        payload={},
        dry_run=True,
    )
    assert result["outcome"] == "published"
    assert result["phase"] == "dry_run"


def test_content_api_flow(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    boot = tmp_path / "boot"
    for p in (data, lib, out, boot):
        p.mkdir()
    # Isolate bootstrap so this test never writes ~/Suying/data/settings.json
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_CHROME_PROFILES", str(tmp_path / "chrome-profiles"))
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=lib,
            library_roots=[lib],
            output_root=out,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer="演示客户",
        onboarded=True,
    )
    save_settings(settings)
    # Ephemeral data_root must not sync into real ~/Suying/data
    real_boot = Path.home() / "Suying" / "data" / "settings.json"
    if real_boot.exists():
        assert "pytest" not in real_boot.read_text(encoding="utf-8")
        assert "/var/folders/" not in real_boot.read_text(encoding="utf-8")
    reset_engine()
    init_db(settings)

    from engine.api.app import app

    client = TestClient(app)
    # brand name for completeness
    cust = client.get("/customers")
    assert cust.status_code == 200
    # patch profile via settings or customer update if available
    listed = client.get("/customers").json()
    assert isinstance(listed, (dict, list))

    # Ensure brand in profile
    from engine.catalog.db import Customer, get_session
    from sqlalchemy import select

    session = get_session()
    try:
        row = session.scalar(select(Customer).where(Customer.name == "演示客户"))
        assert row is not None
        profile = dict(row.profile_json or {})
        brand = dict(profile.get("brand") or {})
        brand["display_name"] = "演示品牌"
        profile["brand"] = brand
        row.profile_json = profile
        session.commit()
    finally:
        session.close()

    s1 = client.post(
        "/content/sources",
        json={
            "title": "事实1",
            "body": "公司提供本地配送服务，工作日48小时达。",
            "source_url": "https://example.com/a",
            "verified": True,
            "verified_by": "tester",
        },
    )
    assert s1.status_code == 200, s1.text
    sid = s1.json()["source"]["id"]
    assert client.post(f"/content/sources/{sid}/verify").status_code == 200
    for i in range(2, 4):
        r = client.post(
            "/content/sources",
            json={
                "title": f"事实{i}",
                "body": f"已确认事实条目{i}：支持开票与售后。",
                "verified": True,
                "verified_by": "tester",
            },
        )
        assert r.status_code == 200
        client.post(f"/content/sources/{r.json()['source']['id']}/verify")

    site = client.post(
        "/content/sites",
        json={"domain": "example.com", "sitemap_url": "https://example.com/sitemap.xml"},
    )
    assert site.status_code == 200, site.text

    comp = client.get("/content/completeness")
    assert comp.status_code == 200
    assert comp.json()["can_publish"] is True

    gen = client.post(
        "/content/articles/generate",
        json={"topic": "配送时效说明", "platforms": ["website", "baijiahao"]},
    )
    assert gen.status_code == 200, gen.text
    article = gen.json()["article"]
    variants = gen.json()["variants"]
    assert article["id"]
    assert len(variants) >= 1

    # Force compliance pass if needed by re-check — generation should already pass
    appr = client.post(f"/content/articles/{article['id']}/approve", json={"approved_by": "tester"})
    assert appr.status_code == 200, appr.text

    vid = variants[0]["id"]
    job = client.post("/content/jobs", json={"variant_id": vid})
    assert job.status_code == 200, job.text
    jid = job.json()["job"]["id"]
    run = client.post(f"/content/jobs/{jid}/run", json={"dry_run": True})
    assert run.status_code == 200, run.text
    assert run.json().get("ok") is True
    assert run.json()["job"]["status"] == "validated"
    assert run.json()["job"]["published_url"] == ""

    live = client.post(f"/content/jobs/{jid}/run", json={"dry_run": False})
    assert live.status_code == 409, live.text
    assert "实时确认已登录" in str(live.json().get("detail") or "")

    no_evidence = client.post(f"/content/jobs/{jid}/status?status=published")
    assert no_evidence.status_code == 400
    with_evidence = client.post(
        f"/content/jobs/{jid}/status?status=published&review_id=review-123"
    )
    assert with_evidence.status_code == 200, with_evidence.text
    assert with_evidence.json()["job"]["status"] == "published"

    pending_vid = variants[1]["id"]
    article_account = client.post(
        "/content/chrome-profiles/create",
        json={
            "platform": "baijiahao",
            "count": 1,
            "name_prefix": f"百家号-测试账号-{tmp_path.name}",
        },
    )
    assert article_account.status_code == 200, article_account.text
    pending = client.post(
        "/content/jobs",
        json={
            "variant_id": pending_vid,
            "profile_name": article_account.json()["selected"],
        },
    )
    assert pending.status_code == 200, pending.text
    pending_jid = pending.json()["job"]["id"]

    edited = client.patch(
        f"/content/articles/{article['id']}",
        json={"title": f"{article['title']}（修订）"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["article"]["status"] == "needs_review"
    detail = client.get(f"/content/articles/{article['id']}").json()
    assert all(v["status"] == "stale" for v in detail["variants"])
    stale_enqueue = client.post("/content/jobs", json={"variant_id": vid})
    assert stale_enqueue.status_code == 400
    stale_run = client.post(f"/content/jobs/{pending_jid}/run", json={"dry_run": True})
    assert stale_run.status_code == 200
    assert stale_run.json()["blocked"] is True
    assert stale_run.json()["job"]["status"] == "blocked"


def test_site_secret_ref_is_reference_only_and_never_echoed(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    boot = tmp_path / "boot"
    for p in (data, lib, out, boot):
        p.mkdir()
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_CHROME_PROFILES", str(tmp_path / "chrome-profiles"))
    save_settings(
        AppSettings(
            paths=PathConfig(
                data_root=data,
                library_root=lib,
                library_roots=[lib],
                output_root=out,
                cache_root=data / "cache",
                render_root=data / "render",
            ),
            active_customer="密钥客户",
            onboarded=True,
        )
    )
    reset_engine()
    init_db()
    from engine.api.app import app

    client = TestClient(app)
    bad = client.post(
        "/content/sites",
        json={"domain": "bad.example", "secret_ref": "plaintext-password"},
    )
    assert bad.status_code == 400
    good = client.post(
        "/content/sites",
        json={"domain": "good.example", "secret_ref": "keychain://suying/site/good"},
    )
    assert good.status_code == 200, good.text
    site = good.json()["site"]
    assert site["secret_ref_configured"] is True
    assert "secret_ref" not in site
