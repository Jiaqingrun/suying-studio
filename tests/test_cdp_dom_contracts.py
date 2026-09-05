"""DOM/text contracts for upload-state classification (no live browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.reach.cdp_platforms import get_platform_adapter, list_platform_ids
from engine.reach.upload_state_classify import (
    bare_uploading_help_is_not_progress,
    classify_upload_page_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cdp_dom"


def _text_of(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_douyin_help_uploading_copy_is_not_progress() -> None:
    html = _text_of("douyin_help_uploading.html")
    assert bare_uploading_help_is_not_progress(html)
    got = classify_upload_page_text(html, has_video=True, has_delete=False)
    assert got["state"] == "form"
    assert got.get("reason") in {"generic_preview", "help_upload_copy_ignored"}


def test_real_upload_progress_stays_uploading() -> None:
    html = _text_of("douyin_real_progress.html")
    got = classify_upload_page_text(html, has_video=False)
    assert got["state"] == "uploading"


def test_channels_preview_is_form() -> None:
    html = _text_of("channels_form.html")
    got = classify_upload_page_text(html, has_video=True, has_delete=True)
    assert got["state"] == "form"


def test_help_verification_word_is_not_need_human() -> None:
    text = "如遇问题请查看帮助中心关于验证的说明。封面 标题 发布"
    got = classify_upload_page_text(text, has_video=True)
    assert got["state"] == "form"
    assert got["state"] != "need_human"


def test_sms_captcha_is_need_human() -> None:
    text = "请完成安全验证 请拖动滑块"
    got = classify_upload_page_text(text, has_video=True)
    assert got["state"] == "need_human"


def test_platform_adapter_registry() -> None:
    assert list_platform_ids() == ("douyin", "channels", "xhs", "kuaishou")
    for pid in list_platform_ids():
        assert get_platform_adapter(pid).platform == pid
    with pytest.raises(KeyError):
        get_platform_adapter("not-a-platform")
