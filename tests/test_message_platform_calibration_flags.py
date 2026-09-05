"""Uncalibrated message platforms must not look verified."""

from __future__ import annotations

from engine.reach.messages_adapters import SPECS


def test_wechat_mp_and_douyin_not_readonly_verified_by_default() -> None:
    assert SPECS["wechat_mp"].readonly_verified is False
    assert SPECS["douyin"].readonly_verified is False


def test_calibrated_platforms_keep_flag() -> None:
    # Platforms already true-machine calibrated may stay verified.
    assert SPECS["zhihu"].readonly_verified is True
    assert SPECS["kuaishou"].readonly_verified is True
    assert SPECS["kuaishou"].reply_supported is False
