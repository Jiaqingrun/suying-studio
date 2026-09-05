"""Budget and timings contracts for CDP publish speed work (no live browser)."""

from __future__ import annotations

import time

from engine.reach.cdp_publish import (
    CHANNELS_COVER_MAX_VISION_ATTEMPTS,
    COVER_SOFT_TIMEOUT_SEC,
    WAIT_UPLOAD_POLL_SEC,
    WAIT_UPLOAD_TIMEOUT_BY_PLATFORM,
    _budget_ok,
    _cover_timeout_result,
    _ms_since,
)
from engine.reach.publish_runner import CHROME_STOP_TIMEOUT_SEC


TIMING_KEYS = frozenset(
    {
        "login_ms",
        "chrome_ready_ms",
        "upload_inject_ms",
        "wait_upload_ready_ms",
        "fill_ms",
        "cover_ms",
        "submit_ms",
        "verify_ms",
        "total_ms",
    }
)


def test_cover_soft_timeout_is_twenty_seconds() -> None:
    assert COVER_SOFT_TIMEOUT_SEC == 20.0
    assert CHANNELS_COVER_MAX_VISION_ATTEMPTS == 2
    assert WAIT_UPLOAD_POLL_SEC < 1.0
    assert CHROME_STOP_TIMEOUT_SEC <= 12.0
    assert WAIT_UPLOAD_TIMEOUT_BY_PLATFORM["channels"] >= 45.0


def test_budget_ok_respects_deadline() -> None:
    assert _budget_ok(None) is True
    assert _budget_ok(time.monotonic() + 5.0) is True
    assert _budget_ok(time.monotonic() - 0.01) is False


def test_cover_timeout_result_schema() -> None:
    r = _cover_timeout_result(snapshots=[{"tag": "x"}])
    assert r["ok"] is False
    assert r["timed_out"] is True
    assert r["skipped"] is True
    assert r["reason"] == "cover_timeout_soft_pass"
    assert r["snapshots"]


def test_ms_since_non_negative() -> None:
    t0 = time.monotonic()
    time.sleep(0.02)
    assert _ms_since(t0) >= 15


def test_evidence_timings_key_set_documented() -> None:
    # Runner + publish_via_cdp merge into evidence.timings; document contract.
    assert "cover_ms" in TIMING_KEYS
    assert "wait_upload_ready_ms" in TIMING_KEYS
    sample = {k: 0 for k in TIMING_KEYS}
    assert set(sample) == TIMING_KEYS
