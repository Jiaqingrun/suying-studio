from engine.ingest.metadata import classify_orientation, parse_probe
from engine.ingest.orientation import (
    assert_orientation_lock_integrity,
    evaluate_orientation_audit,
    extract_rotation,
    rotation_from_displaymatrix_text,
)
from engine.template.rule_schema import validate_and_clamp


def test_rotation_aware_orientation_classification() -> None:
    assert classify_orientation(1920, 1080, 0) == "landscape"
    assert classify_orientation(1920, 1080, 90) == "portrait"
    assert classify_orientation(1920, 1080, -90) == "portrait"
    assert classify_orientation(1080, 1920, 0) == "portrait"
    assert classify_orientation(1080, 1080, 0) == "other"


def test_parse_probe_display_matrix_rotation() -> None:
    probe = {
        "format": {"duration": "1.0", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": 90.0},
                ],
            }
        ],
    }
    meta = parse_probe(probe)
    assert meta["rotation"] == 90
    assert classify_orientation(meta["width"], meta["height"], meta["rotation"]) == "portrait"


def test_displaymatrix_text_infer_rotation() -> None:
    dump = (
        "\n00000000:            0      -65536           0\n"
        "00000001:        65536           0           0\n"
        "00000002:            0           0  1073741824\n"
    )
    assert rotation_from_displaymatrix_text(dump) == 90
    assert extract_rotation(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "side_data_list": [{"displaymatrix": dump}],
                }
            ]
        }
    ) == 90


def test_orientation_hard_audit_dual_source() -> None:
    source = {"width": 1920, "height": 1080, "rotation": 90}
    baked_ok = {"width": 1080, "height": 1920, "rotation": 0}
    baked_bad = {"width": 1920, "height": 1080, "rotation": 0}
    ok = evaluate_orientation_audit(source_meta=source, baked_meta=baked_ok)
    assert ok["passed"] is True
    assert ok["orientation"] == "portrait"
    bad = evaluate_orientation_audit(source_meta=source, baked_meta=baked_bad)
    assert bad["passed"] is False
    assert "source_baked_mismatch" in bad["violations"]
    assert_orientation_lock_integrity()


def test_landscape_rules_have_independent_safe_geometry() -> None:
    report = validate_and_clamp(
        {
            "orientation": "landscape",
            "title_glyph_top_px": 999,
            "subtitle_glyph_bottom_px": 999,
            "title_color": "#123456",
            "subtitle_color": "#FEDCBA",
            "narration_rate": "+99%",
        }
    )
    effective = report["effective_rules"]
    assert effective["orientation"] == "landscape"
    assert effective["title_glyph_top_px"] == 260
    assert effective["subtitle_glyph_bottom_px"] == 360
    assert effective["title_color"] == "#123456"
    assert effective["subtitle_color"] == "#FEDCBA"
    assert effective["narration_rate"] == "+30%"
