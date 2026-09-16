"""Channels create SPA wait + file-input hunt budget (no live browser)."""

from __future__ import annotations


def test_channels_file_input_hunt_budget_longer_than_default() -> None:
    from engine.reach.cdp_publish import (
        FILE_INPUT_HUNT_SEC_BY_PLATFORM,
        FILE_INPUT_HUNT_SEC_DEFAULT,
    )

    assert FILE_INPUT_HUNT_SEC_BY_PLATFORM["channels"] > FILE_INPUT_HUNT_SEC_DEFAULT
    assert FILE_INPUT_HUNT_SEC_BY_PLATFORM["channels"] >= 40.0


def test_channels_spa_probe_helpers_exist() -> None:
    from engine.reach import cdp_publish as mod

    assert callable(mod._channels_create_spa_probe)
    assert callable(mod._wait_channels_create_spa_ready)
