#!/usr/bin/env python3
"""Assert all HARD_LOCKS floors are intact (fail if softened)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from engine.ingest.quality import (
        ASSET_BLUR_FAIL_RATIO,
        MIN_LAPLACIAN_VAR,
        MIN_QUALITY_SCORE,
        assert_quality_lock_integrity,
        locked_quality_floors,
    )
    from engine.pack.tts import DEFAULT_EDGE_RATE
    from engine.pack.video_lock import DEFAULT_LOCK, load_video_lock
    from engine.template.engine import DEFAULT_TEMPLATE

    assert_quality_lock_integrity()
    floors = locked_quality_floors()
    assert floors["min_quality_score"] >= 0.35
    assert floors["min_laplacian_var"] >= 48.0
    assert floors["asset_blur_fail_ratio"] <= 0.6

    q = DEFAULT_LOCK.get("quality") or {}
    assert q.get("locked") is True
    assert q.get("reject_blur") is True
    assert float(q.get("min_quality_score") or 0) >= 0.35
    # Product defaults must stay customer-path free
    for marker in ("始峰", "QR-Volume", "/Users/qr"):
        assert marker not in str(DEFAULT_LOCK.get("reference_outputs") or [])
        assert marker not in str(DEFAULT_LOCK.get("reference_title") or "")

    lock = load_video_lock("北京始峰伟业")
    # Attempt soft merge should still clamp
    soft = load_video_lock(
        "北京始峰伟业",
        profile={"video_lock": {"quality": {"min_quality_score": 0.1, "min_laplacian_var": 1.0}}},
    )
    assert float(soft["quality"]["min_quality_score"]) >= 0.35
    assert float(soft["quality"]["min_laplacian_var"]) >= 48.0
    assert int(lock["subtitle"]["side_margin_px"]) >= 48
    assert lock["subtitle"]["forbid_edge_clip"] is True
    assert lock["subtitle"]["align_to_voice_mandatory"] is True
    assert float(lock["subtitle"]["tail_trim_seconds"]) >= 0.12
    rate = str(lock["voice"].get("rate") or "")
    assert rate == "-8%" or rate.endswith("%") or rate == "clone"
    assert int(lock["voice"]["max_chars_per_breath"]) <= 18
    assert lock["narration"]["require_sentence_breaks"] is True

    # Default portrait style remains yellow/black at 220/420.
    assert str(lock["title"]["color"]).upper() == "#FFE600"
    assert str(lock["title"]["stroke_color"]).upper() == "#000000"
    assert int(lock["title"]["offset_y_px"]) == 0
    assert int(lock["title"]["glyph_top_px"]) == 220
    assert int(lock["subtitle"]["glyph_bottom_px"]) == 420
    hijack = load_video_lock(
        "北京始峰伟业",
        profile={
            "video_lock": {
                "title": {
                    "color": "#E10600",
                    "stroke_color": "#FFE600",
                    "offset_y_px": 0,
                }
            }
        },
    )
    # 2026-08-01 user-approved: style is configurable; loader preserves it
    # while geometry/rule schema keeps values inside the safe range.
    assert str(hijack["title"]["color"]).upper() == "#E10600"
    assert str(hijack["title"]["stroke_color"]).upper() == "#FFE600"
    assert int(hijack["title"]["offset_y_px"]) == 0
    assert int(hijack["title"]["glyph_top_px"]) == 220

    assert float(DEFAULT_TEMPLATE.min_cliplet_quality) >= MIN_QUALITY_SCORE
    assert DEFAULT_EDGE_RATE == "-8%"
    assert str(DEFAULT_TEMPLATE.title_color).upper() == "#FFE600"
    assert str(DEFAULT_TEMPLATE.title_stroke_color).upper() == "#000000"
    assert int(DEFAULT_TEMPLATE.title_max_chars) == 24
    assert int((DEFAULT_LOCK.get("title") or {}).get("max_chars") or 0) == 24
    assert int(lock["title"]["max_chars"]) == 24

    # Docs present
    for name in (
        "HARD_LOCKS.md",
        "QUALITY_LOCK.md",
        "ORIENTATION_LOCK.md",
        "NARRATION_SUBTITLE_LOCK.md",
        "EMOJI_STICKER_LOCK.md",
        "READY_GATE.md",
        "PAPER_SLIP_LOCK.md",
        "DEV_LOCK.md",
    ):
        p = ROOT / "docs" / name
        assert p.is_file(), f"missing {p}"

    from engine.catalog.paper_slip import (
        ROLLING_CLIPLET_WINDOW,
        ROLLING_PHRASE_WINDOW,
        assert_paper_slip_integrity,
    )
    from engine.ingest.orientation import (
        ORIENTATION_LOCK_VERSION,
        assert_orientation_lock_integrity,
        evaluate_orientation_audit,
    )
    from engine.qc.ready_gate import DURATION_MIN_EXCEED_SEC, GATE_CHECKS, _check_duration
    from engine.template.rule_schema import FORBIDDEN_KEYS, validate_and_clamp

    assert_paper_slip_integrity()
    assert_orientation_lock_integrity()
    assert ORIENTATION_LOCK_VERSION >= 1
    # L16: DJI coded landscape + rotation 90 must not pass with sideways bake
    fail = evaluate_orientation_audit(
        source_meta={"width": 1920, "height": 1080, "rotation": 90},
        baked_meta={"width": 1920, "height": 1080, "rotation": 0},
    )
    assert fail["passed"] is False
    ok = evaluate_orientation_audit(
        source_meta={"width": 1920, "height": 1080, "rotation": 90},
        baked_meta={"width": 1080, "height": 1920, "rotation": 0},
    )
    assert ok["passed"] is True and ok["orientation"] == "portrait"
    assert ROLLING_CLIPLET_WINDOW == 20
    assert ROLLING_PHRASE_WINDOW == 15
    assert lock["paper_slip"]["mode"] == "rolling_diversity"
    assert int(lock["paper_slip"]["rolling_cliplet_window"]) == 20
    assert "max_daily_uses" not in lock["paper_slip"]

    # L15: video duration must exceed narration — gate + forbidden relax keys
    assert "duration" in GATE_CHECKS
    assert float(DURATION_MIN_EXCEED_SEC) >= 0.05
    assert "trim_to_narration" in FORBIDDEN_KEYS
    assert "allow_video_shorter_than_narration" in FORBIDDEN_KEYS
    assert "relax_duration_lock" in FORBIDDEN_KEYS
    landscape = validate_and_clamp(
        {
            "orientation": "landscape",
            "title_glyph_top_px": 999,
            "subtitle_glyph_bottom_px": 999,
        }
    )["effective_rules"]
    assert landscape["title_glyph_top_px"] == 260
    assert landscape["subtitle_glyph_bottom_px"] == 360
    # No wav → duration check skipped (empty)
    assert _check_duration(Path("/tmp/__no_such__.mp4"), None) == []
    hard = (ROOT / "docs" / "HARD_LOCKS.md").read_text(encoding="utf-8")
    assert "L15" in hard
    assert "video_must_exceed_narration" in hard or "成片×旁白时长" in hard
    assert "L16" in hard
    assert "ORIENTATION_LOCK" in hard

    print("SMOKE_HARD_LOCKS OK", floors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
