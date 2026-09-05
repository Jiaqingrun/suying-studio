from pathlib import Path
import random

import pytest
from PIL import Image

from engine.catalog.keyword_pack import normalize_title_layout, pick_title
from engine.render.ffmpeg import render_title_png, render_vertical_item_label_png
from engine.render.subtitles_burn import _render_cue_png
from engine.jobs.worker import build_item_label_cues


def test_title_seed_layout_preserves_complete_sentences_and_pixels(tmp_path: Path):
    assert normalize_title_layout("第一句完整表达") == "第一句完整表达"
    assert normalize_title_layout("第一句完整表达\n第二句也完整") == "第一句完整表达\n第二句也完整"
    out = tmp_path / "title.png"
    render_title_png("第一句完整表达\n第二句也完整", 1080, 1920, 92, out)
    image = Image.open(out).convert("RGBA")
    bbox = image.getbbox()
    assert bbox and bbox[1] == 220
    assert abs((bbox[0] + bbox[2]) / 2 - 540) <= 1
    pixels = list(image.crop(bbox).getdata())
    assert any(r >= 250 and g >= 220 and b <= 20 and a for r, g, b, a in pixels)
    assert any(r <= 8 and g <= 8 and b <= 8 and a for r, g, b, a in pixels)


def test_title_overwide_auto_wraps_within_24_and_centers(tmp_path: Path):
    # Within 24 chars: over-wide single line must auto-wrap + stay centered (not fail-closed).
    out = tmp_path / "wrap.png"
    text = "始峰五金装车搬运过程清晰可见全程"  # 16 chars
    assert len(text) <= 24
    render_title_png(text, 1080, 1920, 92, out, max_chars=24, max_lines=2)
    image = Image.open(out).convert("RGBA")
    bbox = image.getbbox()
    assert bbox and bbox[1] == 220
    assert abs((bbox[0] + bbox[2]) / 2 - 540) <= 2
    # Two visual rows → taller than a single-line glyph block
    assert bbox[3] - bbox[1] > 90


def test_title_over_budget_clamps_then_wraps(tmp_path: Path):
    # >24 chars are clamped to 24, then wrap/center — not a hard block for length alone.
    out = tmp_path / "clamp.png"
    render_title_png("超" * 80, 1080, 1920, 92, out, max_chars=24, max_lines=2)
    image = Image.open(out).convert("RGBA")
    bbox = image.getbbox()
    assert bbox and bbox[1] == 220
    assert abs((bbox[0] + bbox[2]) / 2 - 540) <= 2


def test_title_seed_reproducibly_selects_single_and_dual_complete_sentences():
    titles = ["单句完整标题", "第一句完整表达\n第二句也完整"]
    outputs = [pick_title(random.Random(seed), titles) for seed in range(1, 30)]
    assert "单句完整标题" in outputs
    assert "第一句完整表达\n第二句也完整" in outputs
    assert pick_title(random.Random(7), titles) == pick_title(random.Random(7), titles)


@pytest.mark.parametrize("side", ["left", "right"])
def test_vertical_item_label_exact_side_and_safe_zone(tmp_path: Path, side: str):
    out = tmp_path / f"{side}.png"
    result = render_vertical_item_label_png(
        "官方物品", 1080, 1920, out, side=side, font_size=56, safe_top=360, safe_bottom=1320
    )
    bbox = result["bbox"]
    assert bbox[0] == 80 if side == "left" else 1080 - bbox[2] == 80
    assert bbox[1] >= 360 and bbox[3] <= 1320
    assert bbox[1] > 220 and bbox[3] < 1500


def test_subtitle_png_is_alpha_cropped_for_exact_420_overlay(tmp_path: Path):
    out = tmp_path / "subtitle.png"
    _render_cue_png("字幕白字黑描边", 1080, font_size=64, out=out, bottom_padding_px=420)
    image = Image.open(out).convert("RGBA")
    bbox = image.getbbox()
    assert bbox and bbox[1] == 0 and bbox[3] == image.height
    assert bbox[0] >= 4 and image.width - bbox[2] >= 4


def test_item_label_requires_official_and_current_strict_evidence():
    from types import SimpleNamespace

    from engine.ingest.semantic_gate import (
        SEMANTIC_SCHEMA_VERSION,
        STRICT_EMBEDDING_SCHEMA_VERSION,
    )
    from tests.test_semantic_ingest import valid_analysis

    clip = SimpleNamespace(cliplet_id=7, duration_sec=3.0)
    plan = SimpleNamespace(clips=[clip])
    semantic = valid_analysis()
    row = SimpleNamespace(
        semantic_json=semantic,
        semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
        semantic_gate_json={"passed": True, "reasons": []},
        embedding_json=[1.0],
        embedding_backend="ollama",
        embedding_model="nomic-embed-text",
        embedding_schema_version=STRICT_EMBEDDING_SCHEMA_VERSION,
    )
    rules = {"item_label_enabled": True, "item_label_side": "right"}
    assert build_item_label_cues(plan, {7: row}, rules, {}) == []
    topic = {
        "strict_semantic_v1": True,
        "cliplet_ids": [7],
        "official_evidence": [{
            "id": 9,
            "canonical_name": "蓝色箱装产品",
            "url_hash": "url-sha",
            "content_sha256": "content-sha",
        }],
    }
    cues = build_item_label_cues(plan, {7: row}, rules, topic)
    assert cues[0]["text"] == "蓝色箱装产品"
    assert cues[0]["start"] == 0 and cues[0]["end"] == 3
    assert cues[0]["evidence"]["official_evidence_id"] == 9
    assert cues[0]["evidence"]["visual_evidence_frame_ids"] == ["f1", "f3"]
    assert build_item_label_cues(
        plan, {7: row}, rules, {**topic, "cliplet_ids": [8]}
    ) == []
    assert build_item_label_cues(plan, {7: SimpleNamespace(semantic_json={})}, rules, topic) == []
