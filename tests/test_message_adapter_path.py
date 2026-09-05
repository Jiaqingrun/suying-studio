"""Message adapter path matching / timeout classification."""

from __future__ import annotations

import pytest

from engine.reach.messages_adapters import (
    GenericDomMessageAdapter,
    PageLoadTimeout,
    adapter_for,
    canonical_message_url,
    message_external_key,
    normalize_identity_url,
    parse_event_time,
    platform_capabilities,
    validate_official_url,
)


def test_content_path_soft_match_accepts_sibling_inbox_routes():
    adapter = GenericDomMessageAdapter("baijiahao")
    assert adapter._path_matches(
        "https://baijiahao.baidu.com/builder/rc/commentmanage/comment/all"
    )
    assert adapter._path_matches(
        "https://baijiahao.baidu.com/builder/rc/commentmanage/comment"
    )
    # Sibling inbox route under same host should soft-match for content platforms.
    assert adapter._path_matches(
        "https://baijiahao.baidu.com/builder/rc/noticecenter/message"
    )


def test_toutiao_and_zhihu_soft_match_message_keywords():
    assert GenericDomMessageAdapter("toutiao")._path_matches(
        "https://mp.toutiao.com/profile_v4/personal/message"
    )
    assert GenericDomMessageAdapter("zhihu")._path_matches(
        "https://www.zhihu.com/messages"
    )
    assert GenericDomMessageAdapter("zhihu")._path_matches(
        "https://www.zhihu.com/notifications"
    )


def test_video_platforms_soft_match_sibling_inbox_routes():
    xhs = GenericDomMessageAdapter("xhs")
    assert xhs._path_matches("https://www.xiaohongshu.com/chat")
    assert not xhs._path_matches("https://www.xiaohongshu.com/explore")
    assert xhs._page_settled(
        "https://www.xiaohongshu.com/explore",
        {"inbox_hints": True},
    )

    douyin = GenericDomMessageAdapter("douyin")
    assert douyin._path_matches(
        "https://creator.douyin.com/creator-micro/data/following/chat"
    )
    assert douyin._path_matches(
        "https://creator.douyin.com/creator-micro/message"
    )

    kuaishou = GenericDomMessageAdapter("kuaishou")
    assert kuaishou._path_matches("https://cp.kuaishou.com/notification/list")
    assert kuaishou._path_matches("https://cp.kuaishou.com/notification/123/detail")


def test_canonical_message_urls_updated_for_video_platforms():
    assert canonical_message_url("xhs") == "https://www.xiaohongshu.com/chat"
    assert (
        canonical_message_url("kuaishou")
        == "https://cp.kuaishou.com/notification/list"
    )
    assert (
        canonical_message_url("douyin")
        == "https://creator.douyin.com/creator-micro/data/following/chat"
    )


def test_page_load_timeout_code_is_transient():
    err = PageLoadTimeout("slow")
    assert err.code == "page_load_timeout"
    assert adapter_for("zhihu").spec.host == "www.zhihu.com"


def test_video_platforms_do_not_accept_unrelated_home_paths_without_hints():
    adapter = GenericDomMessageAdapter("douyin")
    assert not adapter._path_matches(
        "https://creator.douyin.com/creator-micro/home"
    )
    assert not adapter._page_settled(
        "https://creator.douyin.com/creator-micro/home",
        {"inbox_hints": False},
    )


def test_capability_registry_covers_eight_versioned_platforms():
    rows = platform_capabilities()
    assert len(rows) == 8
    assert {row["platform"] for row in rows} == {
        "douyin",
        "channels",
        "xhs",
        "kuaishou",
        "baijiahao",
        "toutiao",
        "zhihu",
        "wechat_mp",
    }
    assert all(row["adapter_version"] for row in rows)
    xhs = next(row for row in rows if row["platform"] == "xhs")
    assert xhs["kinds"] == ["dm"]
    assert xhs["readonly_verified"] is True
    kuaishou = next(row for row in rows if row["platform"] == "kuaishou")
    assert kuaishou["kinds"] == ["notice"]
    assert kuaishou["readonly_verified"] is True
    assert kuaishou["reply_supported"] is False


def test_identity_url_strips_tokens_and_tracking_but_keeps_resource_id():
    normalized = normalize_identity_url(
        "toutiao",
        "https://mp.toutiao.com/profile_v4/personal/message?id=42&token=secret&utm_source=x#tab",
    )
    assert normalized == "https://mp.toutiao.com/profile_v4/personal/message?id=42"
    assert (
        validate_official_url("douyin", "https://www.douyin.com/chat?isPopup=1")
        == "https://www.douyin.com/chat?isPopup=1"
    )


def test_relative_event_time_is_normalized():
    value = parse_event_time("5分钟前")
    assert value is not None and value.endswith("+00:00")


def test_stable_message_key_depends_only_on_remote_identity():
    key = message_external_key("kuaishou", "history.v2", "native_id", "-111", "notice")
    assert key == message_external_key(
        "kuaishou", "history.v2", "native_id", "-111", "notice"
    )
    assert key != message_external_key(
        "kuaishou", "history.v2", "native_id", "-112", "notice"
    )
