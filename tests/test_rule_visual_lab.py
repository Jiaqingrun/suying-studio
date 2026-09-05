"""GRuleVisualLab · schema / fx asset smoke."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from engine.pack.fx_assets import schema_public_bundle, xfade_names
from engine.render.mask_layers import prepare_mask_pngs, render_geom_mask_png
from engine.template.rule_schema import empty_rules, validate_and_clamp


class RuleVisualLabTests(unittest.TestCase):
    def test_empty_defaults_off(self) -> None:
        r = empty_rules()
        self.assertEqual(r["clip_transition"], "none")
        self.assertEqual(r["mask_layers"], [])
        self.assertEqual(r["narration_text_effect"], "none")
        self.assertEqual(r["subtitle_layout"], "horizontal")
        self.assertTrue(r["title_enabled"])
        self.assertTrue(r["subtitle_enabled"])
        self.assertTrue(r["title_stroke_enabled"])
        self.assertTrue(r["subtitle_stroke_enabled"])
        self.assertEqual(r["subtitle_y_pct"], 50.0)

    def test_user_overlay_sources(self) -> None:
        rep = validate_and_clamp(
            {
                "mask_layers": [
                    {
                        "id": "m1",
                        "source": "user:/tmp/demo-mask.png",
                        "x_pct": 40,
                        "y_pct": 50,
                        "scale": 1,
                        "opacity": 0.8,
                        "z": 0,
                    }
                ],
                "sticker_layers": [
                    {
                        "id": "s1",
                        "source": "twemoji:✨",
                        "x_pct": 70,
                        "y_pct": 10,
                        "scale": 1,
                        "opacity": 1,
                        "z": 0,
                    }
                ],
            }
        )
        eff = rep["effective_rules"]
        self.assertEqual(eff["mask_layers"][0]["source"], "user:/tmp/demo-mask.png")
        self.assertEqual(eff["sticker_layers"][0]["source"], "twemoji:✨")
        self.assertEqual(eff["sticker_layers"][0]["y_pct"], 10.0)
        rep = validate_and_clamp(
            {
                "subtitle_layout": "vertical",
                "subtitle_x_pct": 22,
                "subtitle_y_pct": 40,
                "title_enabled": False,
                "subtitle_enabled": False,
                "title_stroke_enabled": False,
                "subtitle_stroke_enabled": False,
                "title_stroke_width": 0,
                "title_color": "#FFE600",
                "title_stroke_color": "#FFE600",
            }
        )
        eff = rep["effective_rules"]
        self.assertEqual(eff["subtitle_layout"], "vertical")
        self.assertEqual(eff["subtitle_vertical_side"], "left")
        self.assertEqual(eff["subtitle_x_pct"], 22.0)
        self.assertEqual(eff["subtitle_y_pct"], 40.0)
        self.assertFalse(eff["title_enabled"])
        self.assertFalse(eff["subtitle_enabled"])
        self.assertFalse(eff["title_stroke_enabled"])
        self.assertFalse(eff["subtitle_stroke_enabled"])
        # Disabled stroke must not be auto-bumped for readability.
        self.assertEqual(eff["title_stroke_width"], 0)

    def test_clamp_layers_and_transition(self) -> None:
        rep = validate_and_clamp(
            {
                "clip_transition": "wipeleft",
                "clip_transition_duration_sec": 2.0,
                "mask_layers": [
                    {
                        "id": "a",
                        "source": "geom_bottom_gradient",
                        "x_pct": 50,
                        "y_pct": 80,
                        "scale": 1,
                        "opacity": 0.7,
                        "z": 1,
                    }
                ]
                * 10,
                "sticker_layers": [
                    {"id": "s", "source": "twemoji", "x_pct": 50, "y_pct": 5, "scale": 1, "opacity": 1, "z": 0}
                ],
            }
        )
        eff = rep["effective_rules"]
        self.assertEqual(eff["clip_transition"], "wipeleft")
        self.assertLessEqual(eff["clip_transition_duration_sec"], 0.8)
        self.assertLessEqual(len(eff["mask_layers"]), 8)
        self.assertEqual(eff["sticker_layers"][0]["y_pct"], 5.0)

    def test_xfade_whitelist(self) -> None:
        names = xfade_names()
        self.assertIn("fade", names)
        self.assertIn("revealdown", names)
        bundle = schema_public_bundle()
        self.assertTrue(bundle["xfade_transitions"])

    def test_geom_mask_render(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "m.png"
            render_geom_mask_png(out, kind="geom_vignette", width=120, height=200)
            self.assertTrue(out.is_file())
            prepared = prepare_mask_pngs(
                [{"id": "1", "source": "geom_top_gradient", "visible": True, "scale": 1, "opacity": 1}],
                temp_dir=Path(td),
                width=120,
                height=200,
            )
            self.assertEqual(len(prepared), 1)

    def test_twemoji_sticker_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prepared = prepare_mask_pngs(
                [
                    {
                        "id": "s1",
                        "source": "twemoji:✨",
                        "visible": True,
                        "scale": 1,
                        "opacity": 1,
                        "x_pct": 70,
                        "y_pct": 40,
                    }
                ],
                temp_dir=Path(td),
                width=360,
                height=640,
            )
            self.assertEqual(len(prepared), 1)
            self.assertTrue(prepared[0][0].is_file())

    def test_font_catalog_capped(self) -> None:
        bundle = schema_public_bundle()
        self.assertLessEqual(len(bundle["fonts"]), 80)
        from engine.pack.fx_assets import resolve_bundled_font_path

        # At least one bundled face should resolve when fetch/copy ran
        noto = resolve_bundled_font_path("noto_sans_sc")
        inter = resolve_bundled_font_path("inter")
        self.assertTrue(noto is not None or inter is not None)


if __name__ == "__main__":
    unittest.main()
