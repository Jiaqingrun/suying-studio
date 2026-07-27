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
    assert str(lock["voice"].get("rate") or "") == "-8%" or str(lock["voice"].get("rate")).endswith("%")
    assert int(lock["voice"]["max_chars_per_breath"]) <= 18
    assert lock["narration"]["require_sentence_breaks"] is True

    # Title HARD: yellow fill + black stroke (never red+yellow)
    assert str(lock["title"]["color"]).upper() == "#FFE600"
    assert str(lock["title"]["stroke_color"]).upper() == "#000000"
    assert int(lock["title"]["offset_y_px"]) == 120
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
    assert str(hijack["title"]["color"]).upper() == "#FFE600"
    assert str(hijack["title"]["stroke_color"]).upper() == "#000000"
    assert int(hijack["title"]["offset_y_px"]) == 120

    assert float(DEFAULT_TEMPLATE.min_cliplet_quality) >= MIN_QUALITY_SCORE
    assert DEFAULT_EDGE_RATE == "-8%"
    assert str(DEFAULT_TEMPLATE.title_color).upper() == "#FFE600"
    assert str(DEFAULT_TEMPLATE.title_stroke_color).upper() == "#000000"

    # Docs present
    for name in (
        "HARD_LOCKS.md",
        "QUALITY_LOCK.md",
        "NARRATION_SUBTITLE_LOCK.md",
        "EMOJI_STICKER_LOCK.md",
        "READY_GATE.md",
        "PAPER_SLIP_LOCK.md",
        "DEV_LOCK.md",
    ):
        p = ROOT / "docs" / name
        assert p.is_file(), f"missing {p}"

    from engine.catalog.paper_slip import MAX_DAILY_USES, assert_paper_slip_integrity

    assert_paper_slip_integrity()
    assert MAX_DAILY_USES == 2
    assert int(lock["paper_slip"]["max_daily_uses"]) == 2

    print("SMOKE_HARD_LOCKS OK", floors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
