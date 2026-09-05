"""Business-scope isolation: video Reach vs content articles/messages/Chrome."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.catalog.db import (
    ReachMessage,
    ReachMessageAccount,
    get_session,
    init_db,
    reset_engine,
)
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.reach.browser import (
    chrome_profiles_root,
    create_chrome_profiles,
    customer_scope_root,
    list_chrome_profiles,
    migrate_legacy_chrome_profiles,
    rename_chrome_profile,
    resolve_chrome_user_data_dir,
    resolve_profile_platform,
)
from engine.reach.business_scope import SCOPE_CONTENT, SCOPE_VIDEO, assert_platform_in_scope
from engine.content.reply_drafts import create_reply_drafts_for_message


def _boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "隔离客户") -> AppSettings:
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    boot = tmp_path / "boot"
    chrome = tmp_path / "chrome-profiles"
    for p in (data, lib, out, boot, chrome):
        p.mkdir()
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_CHROME_PROFILES", str(chrome))
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=lib,
            library_roots=[lib],
            output_root=out,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer=name,
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    return settings


def test_platform_scope_matrix():
    assert assert_platform_in_scope("douyin", SCOPE_VIDEO) == "douyin"
    assert assert_platform_in_scope("baijiahao", SCOPE_CONTENT) == "baijiahao"
    with pytest.raises(ValueError):
        assert_platform_in_scope("baijiahao", SCOPE_VIDEO)
    with pytest.raises(ValueError):
        assert_platform_in_scope("douyin", SCOPE_CONTENT)


def test_chrome_dirs_isolated_per_customer_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _boot(tmp_path, monkeypatch)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        cust = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert cust is not None
        cid = cust.id
    finally:
        session.close()

    video = create_chrome_profiles(
        customer_id=cid, business_scope=SCOPE_VIDEO, platform="douyin", count=1
    )
    content = create_chrome_profiles(
        customer_id=cid, business_scope=SCOPE_CONTENT, platform="baijiahao", count=1
    )
    vname = video["selected"]
    cname = content["selected"]
    assert vname and cname
    vpath = resolve_chrome_user_data_dir(vname, customer_id=cid, business_scope=SCOPE_VIDEO)
    cpath = resolve_chrome_user_data_dir(cname, customer_id=cid, business_scope=SCOPE_CONTENT)
    assert "video" in str(vpath)
    assert "content" in str(cpath)
    assert vpath != cpath
    assert customer_scope_root(cid, SCOPE_VIDEO) in vpath.parents or vpath.parent == customer_scope_root(
        cid, SCOPE_VIDEO
    )

    listed_v = list_chrome_profiles(customer_id=cid, business_scope=SCOPE_VIDEO)
    listed_c = list_chrome_profiles(customer_id=cid, business_scope=SCOPE_CONTENT)
    assert all(p["name"] != cname for p in listed_v["profiles"])
    assert all(p["name"] != vname for p in listed_c["profiles"])

    with pytest.raises(ValueError):
        resolve_chrome_user_data_dir(cname, customer_id=cid, business_scope=SCOPE_VIDEO)
    with pytest.raises(ValueError, match="customer_id"):
        resolve_chrome_user_data_dir(vname)
    with pytest.raises(ValueError, match="已绑定平台"):
        resolve_profile_platform(
            vname,
            "kuaishou",
            customer_id=cid,
            business_scope=SCOPE_VIDEO,
        )


def test_profile_listing_reports_platform_login_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        customer = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert customer is not None
        customer_id = customer.id
    finally:
        session.close()

    created = create_chrome_profiles(
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
        platform="kuaishou",
        count=1,
    )
    profile_name = created["selected"]
    profile_dir = resolve_chrome_user_data_dir(
        profile_name,
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
    )
    listed = list_chrome_profiles(customer_id=customer_id, business_scope=SCOPE_VIDEO)
    initial = next(item for item in listed["profiles"] if item["name"] == profile_name)
    assert initial["login_data_state"] == "not_initialized"

    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True)
    (default_dir / "Preferences").write_text(
        '{"profile":{"exit_type":"Normal"}}',
        encoding="utf-8",
    )
    connection = sqlite3.connect(default_dir / "Cookies")
    try:
        connection.execute("CREATE TABLE cookies (host_key TEXT NOT NULL)")
        connection.execute("INSERT INTO cookies(host_key) VALUES (?)", (".kuaishou.com",))
        connection.commit()
    finally:
        connection.close()

    listed = list_chrome_profiles(customer_id=customer_id, business_scope=SCOPE_VIDEO)
    saved = next(item for item in listed["profiles"] if item["name"] == profile_name)
    assert saved["profile_initialized"] is True
    assert saved["platform_cookie_count"] == 1
    assert saved["login_data_state"] == "present"
    assert saved["login_status"] == "stale_unknown"
    assert saved["login_status_source"] == "no_live_managed_session"
    assert saved["last_profile_exit_type"] == "Normal"

    xhs = create_chrome_profiles(
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
        platform="xhs",
        count=1,
    )
    xhs_dir = resolve_chrome_user_data_dir(
        xhs["selected"],
        customer_id=customer_id,
        business_scope=SCOPE_VIDEO,
    )
    xhs_default = xhs_dir / "Default"
    xhs_default.mkdir(parents=True)
    (xhs_default / "Preferences").write_text("{}", encoding="utf-8")
    xhs_connection = sqlite3.connect(xhs_default / "Cookies")
    try:
        xhs_connection.execute("CREATE TABLE cookies (host_key TEXT NOT NULL)")
        xhs_connection.commit()
    finally:
        xhs_connection.close()
    listed = list_chrome_profiles(customer_id=customer_id, business_scope=SCOPE_VIDEO)
    xhs_saved = next(item for item in listed["profiles"] if item["name"] == xhs["selected"])
    assert xhs_saved["platform_cookie_count"] == 0
    assert xhs_saved["login_data_state"] == "unknown"


def test_content_csdn_custom_platform_and_profile_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        cust = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert cust is not None
        cid = cust.id
    finally:
        session.close()

    csdn = create_chrome_profiles(
        customer_id=cid,
        business_scope=SCOPE_CONTENT,
        platform="csdn",
        count=1,
        name_prefix="CSDN-账号名",
    )
    assert csdn["selected"] == "CSDN-账号名"
    custom = create_chrome_profiles(
        customer_id=cid,
        business_scope=SCOPE_CONTENT,
        platform="",
        count=1,
        custom_platform_name="企业博客",
        custom_platform_url="https://blog.example.com/admin",
    )
    assert str(custom["platform"]).startswith("custom_")

    listed = list_chrome_profiles(customer_id=cid, business_scope=SCOPE_CONTENT)
    assert any(p["id"] == "csdn" for p in listed["platforms"])
    assert any(p["label"] == "企业博客" for p in listed["platforms"])
    assert all(p["business_scope"] == SCOPE_CONTENT for p in listed["profiles"])

    renamed = rename_chrome_profile(
        csdn["selected"],
        "CSDN-123123",
        customer_id=cid,
        business_scope=SCOPE_CONTENT,
    )
    assert renamed["name"] == "CSDN-123123"
    assert (customer_scope_root(cid, SCOPE_CONTENT) / "CSDN-123123").is_dir()
    assert not (customer_scope_root(cid, SCOPE_CONTENT) / csdn["selected"]).exists()
    assert all(
        p["name"] != "CSDN-123123"
        for p in list_chrome_profiles(customer_id=cid, business_scope=SCOPE_VIDEO)["profiles"]
    )


def test_legacy_chrome_migration_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = _boot(tmp_path, monkeypatch)
    chrome = tmp_path / "chrome-profiles"
    assert chrome.is_dir()
    legacy = chrome / "抖音-99"
    legacy.mkdir()
    (legacy / "Local State").write_text("{}", encoding="utf-8")
    (chrome / "accounts.json").write_text(
        '{"抖音-99": {"platform": "douyin", "label": "抖音"}}',
        encoding="utf-8",
    )
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        cust = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert cust is not None
        row = ReachMessageAccount(
            customer_id=cust.id,
            business_scope=SCOPE_VIDEO,
            platform="douyin",
            profile_name="抖音-99",
            display_name="抖音-99",
            enabled=True,
            cooldown_sec=1800,
            message_url="https://creator.douyin.com/creator-micro/message",
        )
        session.add(row)
        session.commit()
        cid = cust.id
    finally:
        session.close()

    r1 = migrate_legacy_chrome_profiles()
    assert any(m["name"] == "抖音-99" for m in r1["moved"])
    dest = customer_scope_root(cid, SCOPE_VIDEO) / "抖音-99"
    assert dest.is_dir()
    assert not legacy.exists()
    r2 = migrate_legacy_chrome_profiles()
    assert r2["ok"]
    # second run should not invent duplicates
    assert dest.is_dir()
    _ = settings


def test_scoped_workspace_profiles_merge_into_precreated_local_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    boot = tmp_path / "boot"
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    for path in (home, boot, data, workspace / "cache", workspace / "render", lib, out):
        path.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.delenv("SUYING_CHROME_PROFILES", raising=False)
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=lib,
            library_roots=[lib],
            output_root=out,
            cache_root=workspace / "cache",
            render_root=workspace / "render",
        ),
        active_customer="迁移客户",
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    local_root = chrome_profiles_root()
    local_root.mkdir(parents=True, exist_ok=True)

    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        customer = session.scalar(select(Customer).where(Customer.name == "迁移客户"))
        assert customer is not None
        customer_id = int(customer.id)
    finally:
        session.close()

    legacy_scope = workspace / "chrome-profiles" / f"customer-{customer_id}" / SCOPE_VIDEO
    legacy_profile = legacy_scope / "快手-01"
    legacy_profile.mkdir(parents=True)
    (legacy_profile / "Local State").write_text("{}", encoding="utf-8")
    (legacy_scope / "accounts.json").write_text(
        '{"快手-01": {"platform": "kuaishou", "label": "快手"}}',
        encoding="utf-8",
    )

    result = migrate_legacy_chrome_profiles()
    destination = customer_scope_root(customer_id, SCOPE_VIDEO) / "快手-01"
    assert result["workspace_moved"]
    assert destination.is_dir()
    assert not legacy_profile.exists()
    listed = list_chrome_profiles(customer_id=customer_id, business_scope=SCOPE_VIDEO)
    assert [profile["name"] for profile in listed["profiles"]] == ["快手-01"]


def test_reply_draft_cross_scope_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _boot(tmp_path, monkeypatch)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        cust = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert cust is not None
        acct = ReachMessageAccount(
            customer_id=cust.id,
            business_scope=SCOPE_VIDEO,
            platform="douyin",
            profile_name="抖音-draft",
            display_name="d",
            enabled=True,
            cooldown_sec=1800,
            message_url="https://creator.douyin.com/creator-micro/message",
        )
        session.add(acct)
        session.flush()
        msg = ReachMessage(
            customer_id=cust.id,
            account_id=acct.id,
            business_scope=SCOPE_VIDEO,
            platform="douyin",
            external_key="k1",
            sender="用户",
            summary="想了解报价",
            reply_url="https://creator.douyin.com/creator-micro/message",
            unread=True,
        )
        session.add(msg)
        session.commit()
        mid = msg.id
        cid = cust.id
    finally:
        session.close()

    session = get_session()
    try:
        with pytest.raises(ValueError, match="业务域"):
            create_reply_drafts_for_message(
                session, customer_id=cid, message_id=mid, business_scope=SCOPE_CONTENT
            )
        drafts = create_reply_drafts_for_message(
            session, customer_id=cid, message_id=mid, business_scope=SCOPE_VIDEO
        )
        assert len(drafts) >= 1
        assert drafts[0]["business_scope"] == SCOPE_VIDEO
    finally:
        session.close()


def test_content_preview_read_and_api_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _boot(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        cust = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert cust is not None
        create_chrome_profiles(
            customer_id=cust.id, business_scope=SCOPE_CONTENT, platform="baijiahao", count=1
        )
        listed = list_chrome_profiles(customer_id=cust.id, business_scope=SCOPE_CONTENT)
        pname = listed["profiles"][0]["name"]
        acct = ReachMessageAccount(
            customer_id=cust.id,
            business_scope=SCOPE_CONTENT,
            platform="baijiahao",
            profile_name=pname,
            display_name=pname,
            purpose="article",
            provisioning_status="explicit",
            enabled=True,
            cooldown_sec=1800,
            message_url="https://baijiahao.baidu.com/builder/rc/commentmanage/comment/all",
        )
        session.add(acct)
        session.flush()
        msg = ReachMessage(
            customer_id=cust.id,
            account_id=acct.id,
            business_scope=SCOPE_CONTENT,
            platform="baijiahao",
            external_key="c1",
            sender="访客",
            summary="想看报价明细",
            reply_url="https://baijiahao.baidu.com/builder/rc/commentmanage/comment/all",
            unread=True,
        )
        session.add(msg)
        session.commit()
        mid = msg.id
    finally:
        session.close()

    reach_msgs = client.get("/reach/messages")
    assert reach_msgs.status_code == 200
    assert all(m.get("business_scope") != "content" for m in reach_msgs.json().get("messages") or [])

    content_msgs = client.get("/content/messages")
    assert content_msgs.status_code == 200
    body = content_msgs.json()
    assert body["unread_count"] >= 1
    assert any(m["id"] == mid and m["unread"] for m in body["messages"])

    read = client.post(f"/content/messages/{mid}/read")
    assert read.status_code == 200
    assert read.json()["message"]["unread"] is False

    after = client.get("/content/messages").json()
    assert after["unread_count"] == 0

    # video chrome create must reject content platform
    bad = client.post("/reach/chrome-profiles/create", json={"platform": "baijiahao", "count": 1})
    assert bad.status_code == 400


def test_content_chrome_create_does_not_bind_message_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Publishing profiles and GET requests must not create message accounts."""
    _boot(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    created = client.post(
        "/content/chrome-profiles/create",
        json={"platform": "zhihu", "count": 1, "name_prefix": "知乎-自动绑"},
    )
    assert created.status_code == 200, created.text
    assert "message_accounts" not in created.json()
    before = client.get("/content/message-accounts").json()["accounts"]
    accounts = client.get("/content/message-accounts").json()["accounts"]
    assert before == accounts == []
    message = client.post(
        "/content/message-accounts",
        json={
            "platform": "zhihu",
            "profile_name": "知乎-消息",
            "display_name": "知乎消息",
        },
    )
    assert message.status_code == 200, message.text
    accounts = client.get("/content/message-accounts").json()["accounts"]
    assert any(
        a["profile_name"] == "知乎-消息"
        and a["platform"] == "zhihu"
        and a["purpose"] == "message"
        and a["profile_role"] == "message"
        and a["enabled"]
        for a in accounts
    )
    publishing = client.get("/content/chrome-profiles").json()["profiles"]
    assert any(p["name"] == "知乎-自动绑" for p in publishing)
    assert all(p["name"] != "知乎-消息" for p in publishing)


def test_profile_rename_does_not_touch_independent_message_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    created = client.post(
        "/content/chrome-profiles/create",
        json={"platform": "baijiahao", "count": 1, "name_prefix": "百家号-旧名"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["selected"] == "百家号-旧名"
    bound = client.post(
        "/content/message-accounts",
        json={
            "platform": "baijiahao",
            "profile_name": "百家号-消息",
            "display_name": "百家号-消息",
            "enabled": True,
        },
    )
    assert bound.status_code == 200, bound.text
    assert bound.json().get("created") is True

    renamed = client.post(
        "/content/chrome-profiles/rename",
        json={"old_name": "百家号-旧名", "new_name": "百家号-123123"},
    )
    assert renamed.status_code == 200, renamed.text
    profiles = client.get("/content/chrome-profiles").json()
    assert profiles["selected"] == "百家号-123123"
    assert any(p["name"] == "百家号-123123" for p in profiles["profiles"])
    accounts = client.get("/content/message-accounts").json()["accounts"]
    assert any(
        a["profile_name"] == "百家号-消息" and a["display_name"] == "百家号-消息"
        for a in accounts
    )
    assert all(a["profile_name"] != "百家号-123123" for a in accounts)

    video = client.post(
        "/reach/chrome-profiles/create",
        json={"platform": "douyin", "count": 1, "name_prefix": "抖音-01"},
    )
    assert video.status_code == 200, video.text
    renamed_video = client.post(
        "/reach/chrome-profiles/rename",
        json={"old_name": "抖音-01", "new_name": "抖音-账号名"},
    )
    assert renamed_video.status_code == 200, renamed_video.text
    video_profiles = client.get("/reach/chrome-profiles").json()
    assert video_profiles["selected"] == "抖音-账号名"
    assert all(p["name"] != "百家号-123123" for p in video_profiles["profiles"])


def test_video_account_delete_rejects_active_plan_then_cleans_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    from datetime import datetime, timezone

    from engine.api.app import app
    from engine.catalog.db import Customer, ReachPublishSchedule
    from sqlalchemy import select

    client = TestClient(app)
    created = client.post(
        "/reach/chrome-profiles/create",
        json={"platform": "douyin", "count": 1, "name_prefix": "抖音-待删"},
    )
    assert created.status_code == 200, created.text

    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert customer is not None
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="有效计划",
            enabled=True,
            timezone="Asia/Shanghai",
            chrome_profile="抖音-待删",
            platform="douyin",
            content_source="eligible_ready_random",
            source_config_json={},
            times_json=[{"hour": 9, "minute": 0}],
            windows_json=[],
            items_per_trigger=1,
            repeat_count=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(schedule)
        session.commit()
        schedule_id = schedule.id
        customer_id = customer.id
    finally:
        session.close()

    blocked = client.request(
        "DELETE",
        "/reach/chrome-profiles",
        json={"names": ["抖音-待删"]},
    )
    assert blocked.status_code == 409
    assert "有效发布计划" in blocked.text
    assert (customer_scope_root(customer_id, SCOPE_VIDEO) / "抖音-待删").is_dir()

    session = get_session()
    try:
        schedule = session.get(ReachPublishSchedule, schedule_id)
        assert schedule is not None
        schedule.enabled = False
        session.commit()
    finally:
        session.close()
    deleted = client.request(
        "DELETE",
        "/reach/chrome-profiles",
        json={"names": ["抖音-待删"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == ["抖音-待删"]
    assert not (customer_scope_root(customer_id, SCOPE_VIDEO) / "抖音-待删").exists()
    profiles = client.get("/reach/chrome-profiles").json()
    assert profiles["selected"] in (None, "")


def test_content_publish_account_delete_keeps_independent_message_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    from engine.api.app import app
    from engine.catalog.db import Customer
    from sqlalchemy import select

    client = TestClient(app)
    created = client.post(
        "/content/chrome-profiles/create",
        json={"platform": "baijiahao", "count": 1, "name_prefix": "百家号-删除"},
    )
    assert created.status_code == 200, created.text
    bound = client.post(
        "/content/message-accounts",
        json={
            "platform": "baijiahao",
            "profile_name": "百家号-消息保留",
            "display_name": "百家号-消息保留",
            "enabled": True,
        },
    )
    assert bound.status_code == 200, bound.text
    assert bound.json().get("created") is True
    account_id = int(bound.json()["account"]["id"])
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert customer is not None
        message = ReachMessage(
            customer_id=customer.id,
            account_id=account_id,
            business_scope=SCOPE_CONTENT,
            platform="baijiahao",
            external_key="history-kept",
            sender="访客",
            summary="历史消息",
            reply_url="https://baijiahao.baidu.com/",
            unread=False,
        )
        session.add(message)
        session.commit()
        message_id = message.id
        customer_id = customer.id
    finally:
        session.close()

    deleted = client.request(
        "DELETE",
        "/content/chrome-profiles",
        json={"names": ["百家号-删除"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["message_bindings_disabled"] == 0
    assert not (customer_scope_root(customer_id, SCOPE_CONTENT) / "百家号-删除").exists()

    session = get_session()
    try:
        account = session.get(ReachMessageAccount, account_id)
        history = session.get(ReachMessage, message_id)
        assert account is not None
        assert account.enabled is True
        assert account.profile_name == "百家号-消息保留"
        assert account.profile_role == "message"
        assert history is not None
        assert history.summary == "历史消息"
    finally:
        session.close()


def test_content_account_full_edit_and_cookie_does_not_enable_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _boot(tmp_path, monkeypatch)
    from engine.api.app import app

    client = TestClient(app)
    content = client.post(
        "/content/chrome-profiles/create",
        json={"platform": "csdn", "count": 1, "name_prefix": "CSDN-编辑前"},
    )
    assert content.status_code == 200, content.text
    edited = client.patch(
        "/content/chrome-profiles",
        json={
            "name": "CSDN-编辑前",
            "new_name": "企业博客-账号",
            "custom_platform_name": "企业博客",
            "custom_platform_url": "https://blog.example.com/admin",
        },
    )
    assert edited.status_code == 200, edited.text
    profiles = client.get("/content/chrome-profiles").json()
    row = next(profile for profile in profiles["profiles"] if profile["name"] == "企业博客-账号")
    assert row["custom_platform"] is True
    assert row["official_url"] == "https://blog.example.com/admin"
    assert profiles["selected"] == "企业博客-账号"

    video = client.post(
        "/reach/chrome-profiles/create",
        json={"platform": "douyin", "count": 1, "name_prefix": "抖音-Cookie"},
    )
    assert video.status_code == 200, video.text
    video_row = client.get("/reach/chrome-profiles").json()["profiles"][0]
    profile_dir = Path(video_row["path"])
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    (default_dir / "Preferences").write_text("{}", encoding="utf-8")
    connection = sqlite3.connect(default_dir / "Cookies")
    try:
        connection.execute("CREATE TABLE cookies (host_key TEXT NOT NULL)")
        connection.execute("INSERT INTO cookies(host_key) VALUES ('.douyin.com')")
        connection.commit()
    finally:
        connection.close()
    refreshed = client.get("/reach/chrome-profiles").json()["profiles"][0]
    assert refreshed["login_data_state"] == "present"
    assert refreshed["login_status"] == "stale_unknown"
    preview = client.post(
        "/reach/publish/immediate/preview",
        json={
            "accounts": [{"platform": "douyin", "chrome_profile": "抖音-Cookie"}],
            "total_count": 1,
            "allocation_mode": "auto_even",
            "manual_counts": {},
            "content_mode": "random_unique",
            "output_ids": [],
        },
    )
    assert preview.status_code == 400
    assert "实时确认登录" not in preview.text
    assert "发布物料" in preview.text


def test_message_history_tombstones_and_read_state_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from datetime import datetime, timedelta, timezone

    from engine.reach.message_sync import MessageSync

    _boot(tmp_path, monkeypatch)
    session = get_session()
    try:
        from engine.catalog.db import Customer
        from sqlalchemy import select

        customer = session.scalar(select(Customer).where(Customer.name == "隔离客户"))
        assert customer is not None
        created = create_chrome_profiles(
            customer_id=customer.id,
            business_scope=SCOPE_VIDEO,
            platform="douyin",
            count=1,
            name_prefix="抖音-消息历史",
            purpose="message",
        )
        account = ReachMessageAccount(
            customer_id=customer.id,
            business_scope=SCOPE_VIDEO,
            platform="douyin",
            profile_name=created["selected"],
            display_name="消息历史",
            purpose="message",
            profile_role="message",
            provisioning_status="explicit",
            enabled=True,
            cooldown_sec=1800,
            message_url="https://creator.douyin.com/creator-micro/message",
        )
        session.add(account)
        session.flush()
        now = datetime.now(timezone.utc)
        items = [
            {
                "external_key": f"event-{index:03d}",
                "identity_source": "native_id",
                "sender": f"用户{index}",
                "summary": f"摘要{index}",
                "reply_url": "https://creator.douyin.com/creator-micro/message",
                "platform_event_at": (now - timedelta(minutes=index)).isoformat(),
            }
            for index in range(201)
        ]
        items[0]["sender"] = "用户 13800138000"
        items[0]["summary"] = "请联系 a@example.com"
        new_ids = MessageSync._upsert_messages(session, account, items)
        assert len(new_ids) == 201
        assert MessageSync._apply_history_retention(session, account) == 1
        tombstone = session.scalar(
            select(ReachMessage).where(ReachMessage.external_key == "event-200")
        )
        assert tombstone is not None
        assert tombstone.purged_at is not None
        assert tombstone.summary == ""
        stable = session.scalar(
            select(ReachMessage).where(ReachMessage.external_key == "event-000")
        )
        assert stable is not None
        assert "13800138000" not in stable.sender
        assert "a@example.com" not in stable.summary
        stable.unread = False
        stable.read_at = now
        session.flush()
        assert MessageSync._upsert_messages(session, account, [items[0]]) == []
        assert stable.unread is False
        assert stable.read_at == now
    finally:
        session.close()


def test_message_migration_is_idempotent_and_bulk_read_is_server_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = _boot(tmp_path, monkeypatch)
    init_db(settings)
    init_db(settings)
    from engine.api.app import app

    client = TestClient(app)
    created = client.post(
        "/reach/message-accounts",
        json={"platform": "douyin", "profile_name": "抖音-独立消息"},
    )
    assert created.status_code == 200, created.text
    account = created.json()["account"]
    assert account["purpose"] == "message"
    assert account["profile_role"] == "message"
    assert all(
        row["name"] != "抖音-独立消息"
        for row in client.get("/reach/chrome-profiles").json()["profiles"]
    )
    opened = client.post(
        f"/reach/message-accounts/{account['id']}/open",
        json={"dry_run": True},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["message_url"].startswith("https://creator.douyin.com/")

    session = get_session()
    try:
        for index in range(3):
            session.add(
                ReachMessage(
                    customer_id=account["customer_id"],
                    account_id=account["id"],
                    business_scope=SCOPE_VIDEO,
                    platform="douyin",
                    external_key=f"bulk-{index}",
                    kind="dm",
                    sender="用户",
                    summary=f"消息{index}",
                    reply_url="https://creator.douyin.com/creator-micro/message",
                    unread=True,
                )
            )
        session.commit()
    finally:
        session.close()
    listed = client.get("/reach/messages", params={"kind": "dm"}).json()
    assert listed["unread_count"] == 3
    filtered = client.get("/reach/messages", params={"platform": "xhs"}).json()
    assert filtered["messages"] == []
    assert filtered["unread_count"] == 0
    bulk = client.post(
        "/reach/messages/bulk-read",
        json={"account_id": account["id"], "kind": "dm"},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json() == {"ok": True, "updated": 3, "unread_count": 0}
    history = client.get("/reach/messages", params={"history": True}).json()
    assert len(history["messages"]) == 3
    read_all = client.post("/reach/messages/read-all", json={})
    assert read_all.status_code == 200, read_all.text
    assert read_all.json()["updated"] == 0
    assert client.get("/reach/messages").json()["messages"] == []
