"""Item skip-on-timeout: Edge killable wall + skip_current_item seed advance."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_seed_for_output_advances_with_item_retry() -> None:
    from engine.jobs.worker import seed_for_output

    base = 42
    a = seed_for_output(base, produced_count=5, retry_count=0)
    b = seed_for_output(base, produced_count=5, retry_count=1)
    c = seed_for_output(base, produced_count=6, retry_count=0)
    assert a != b
    assert a != c
    assert seed_for_output(base, 5, 0) == a


def test_skip_current_item_does_not_raise_produced_or_circuit() -> None:
    from engine.jobs.worker import skip_current_item

    session = MagicMock()
    job = SimpleNamespace(
        id=812,
        customer_id=1,
        produced_count=5,
        target_count=10,
        config_snapshot_json={},
        updated_at=None,
        status="running",
    )
    with (
        patch("engine.catalog.paper_slip.release_paper_slip"),
        patch("engine.runtime.resource_gate.gate") as gate,
        patch("engine.jobs.worker.log_event") as log_event,
    ):
        gate.release = MagicMock()
        gate.release_all = MagicMock()
        skip_current_item(
            session,
            job,
            reason="edge_tts",
            stage="tts",
            error="edge_tts_timeout (45s)",
            infra=True,
            elapsed_sec=50.0,
            reservation_key="job:812:seed:1",
        )
    assert job.produced_count == 5
    assert job.status == "running"
    snap = job.config_snapshot_json
    assert int(snap["item_skip_count"]) == 1
    assert int(snap["item_retry_count"]) == 1
    assert int(snap["consecutive_ollama_infra"]) == 1
    assert "next_attempt_at" in snap
    assert log_event.called
    msg = log_event.call_args[0][3]
    assert msg == "跳过本条，继续生产"


def test_edge_save_mp3_killable_times_out(tmp_path: Path) -> None:
    """Hung child is killed within wall-clock; no permanent block."""
    from engine.pack import tts as tts_mod

    hang_snippet = r"""
import time, sys
# Never write audio; sleep longer than test timeout.
time.sleep(120)
"""
    mp3 = tmp_path / "oneshot_zh.mp3"
    with patch.object(tts_mod, "_EDGE_WORKER_SNIPPET", hang_snippet):
        with patch.object(tts_mod, "EDGE_TTS_ATTEMPT_SEC", 2.0):
            with patch.object(tts_mod, "EDGE_TTS_ZERO_BYTE_SEC", 0.5):
                t0 = time.monotonic()
                with pytest.raises((TimeoutError, RuntimeError)) as excinfo:
                    tts_mod._edge_save_mp3_killable(
                        "测试一句旁白",
                        mp3,
                        voice_id="zh-CN-XiaoxiaoNeural",
                        rate="-8%",
                        pitch="+20Hz",
                        volume="+0%",
                        timeout_sec=2.0,
                    )
                elapsed = time.monotonic() - t0
    assert elapsed < 15.0
    err = str(excinfo.value).lower()
    assert "timeout" in err or "edge_tts" in err
    assert not mp3.is_file() or mp3.stat().st_size == 0


def test_edge_to_wav_clamps_retries(tmp_path: Path) -> None:
    from engine.pack import tts as tts_mod

    calls: list[int] = []

    def _boom(*_a, **_k):
        calls.append(1)
        raise TimeoutError("edge_tts_timeout (45s)")

    wav = tmp_path / "out.wav"
    with patch.object(tts_mod, "_edge_save_mp3_killable", side_effect=_boom):
        with pytest.raises(RuntimeError) as excinfo:
            tts_mod._edge_to_wav(
                "测试",
                wav,
                retries=9,  # request many; impl must clamp to EDGE_TTS_MAX_ATTEMPTS
            )
    assert len(calls) == tts_mod.EDGE_TTS_MAX_ATTEMPTS
    assert "edge-tts failed after" in str(excinfo.value)


def test_error_looks_infra() -> None:
    from engine.jobs.worker import _error_looks_infra

    assert _error_looks_infra("edge_tts_timeout (45s)")
    assert _error_looks_infra("edge_tts_empty")
    assert not _error_looks_infra("compliance_blocked_foo")
