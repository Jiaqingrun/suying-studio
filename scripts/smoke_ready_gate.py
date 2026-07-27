#!/usr/bin/env python3
"""Assert READY_GATE + 黄字黑描边 title lock are intact."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from engine.pack.video_lock import (
        DEFAULT_LOCK,
        LOCKED_TITLE_COLOR,
        LOCKED_TITLE_OFFSET_Y_PX,
        LOCKED_TITLE_STROKE_COLOR,
        apply_lock_to_title_style,
        load_video_lock,
    )
    from engine.qc.ready_gate import GATE_CHECKS, evaluate_ready_gate
    from engine.template.engine import DEFAULT_TEMPLATE

    assert LOCKED_TITLE_COLOR == "#FFE600"
    assert LOCKED_TITLE_STROKE_COLOR == "#000000"
    assert LOCKED_TITLE_OFFSET_Y_PX == 120
    assert (DEFAULT_LOCK.get("title") or {}).get("color") == "#FFE600"
    assert (DEFAULT_LOCK.get("title") or {}).get("stroke_color") == "#000000"
    assert int((DEFAULT_LOCK.get("title") or {}).get("offset_y_px") or 0) == 120

    lock = load_video_lock("北京始峰伟业")
    assert lock["title"]["color"] == "#FFE600"
    assert lock["title"]["stroke_color"] == "#000000"
    assert int(lock["title"]["offset_y_px"]) == 120

    # Attempt to restore forbidden red+yellow / zero offset via profile — must clamp
    soft = load_video_lock(
        "北京始峰伟业",
        profile={
            "video_lock": {
                "title": {"color": "#E10600", "stroke_color": "#FFE600", "offset_y_px": 0},
            }
        },
    )
    assert soft["title"]["color"] == "#FFE600", soft["title"]
    assert soft["title"]["stroke_color"] == "#000000", soft["title"]
    assert int(soft["title"]["offset_y_px"]) == 120

    styled = apply_lock_to_title_style({"color": "#E10600", "stroke_color": "#FFE600", "offset_y_px": 0}, soft)
    assert styled["color"] == "#FFE600"
    assert styled["stroke_color"] == "#000000"
    assert int(styled["offset_y_px"]) == 120

    assert DEFAULT_TEMPLATE.title_color == "#FFE600"
    assert DEFAULT_TEMPLATE.title_stroke_color == "#000000"

    for name in GATE_CHECKS:
        assert name  # non-empty ids
    assert "title" in GATE_CHECKS and "blur" in GATE_CHECKS

    # Missing file → not ok
    r = evaluate_ready_gate(Path("/tmp/__no_such_montage__.mp4"), require_ollama=False)
    assert r["ok"] is False
    assert r.get("action_on_fail") == "reject_ready"
    assert any("basic" in f or "title" in f for f in r["fails"])

    # Fail-closed: missing clips / missing tts_provider must not silently pass
    from engine.qc.ready_gate import _check_blur, _check_voice

    assert any("clips missing" in f for f in _check_blur({}))
    assert any("clips empty" in f for f in _check_blur({"clips": []}))
    assert _check_blur({"clips": [{"quality_score": 0.9}]}) == []
    voice_miss = _check_voice(
        {"meta": {"narration_path": "/x.wav", "voice_lang": "zh"}}
    )
    assert any("missing tts_provider" in f for f in voice_miss), voice_miss
    voice_ok = _check_voice(
        {
            "meta": {
                "narration_path": "/x.wav",
                "voice_lang": "zh",
                "tts_provider": "edge",
                "tts_pitch": "+35Hz",
                "tts_volume": "+12%",
            }
        }
    )
    assert voice_ok == [], voice_ok

    # Product DEFAULT_LOCK must not embed customer-disk paths
    blob = str(DEFAULT_LOCK.get("reference_outputs") or [])
    for marker in ("始峰", "QR-Volume", "/Users/qr"):
        assert marker not in blob, f"DEFAULT_LOCK leaked {marker!r}"

    doc = ROOT / "docs" / "READY_GATE.md"
    assert doc.is_file(), f"missing {doc}"
    text = doc.read_text(encoding="utf-8")
    assert "黄字" in text and "黑描边" in text
    assert "evaluate_ready_gate" in text

    print(
        "SMOKE_READY_GATE OK",
        LOCKED_TITLE_COLOR,
        LOCKED_TITLE_STROKE_COLOR,
        "checks=",
        GATE_CHECKS,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
