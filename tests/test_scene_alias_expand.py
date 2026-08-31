"""Scene alias expansion for life-service cliplet tags."""

from __future__ import annotations

import unittest

from engine.catalog.industry_pack import clear_pack_cache, expand_prefer_scenes


class SceneAliasExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_pack_cache()

    def test_life_service_maps_canonical_to_cliplet_scenes(self) -> None:
        expanded = expand_prefer_scenes(
            "life-service",
            ["company_image", "office", "product_closeup"],
        )
        self.assertIn("honor_wall", expanded)
        self.assertIn("culture_wall", expanded)
        self.assertIn("corridor", expanded)
        self.assertIn("supply", expanded)

    def test_unknown_pack_passthrough(self) -> None:
        self.assertEqual(expand_prefer_scenes("_blank", ["foo", "bar"]), ["foo", "bar"])


if __name__ == "__main__":
    unittest.main()
