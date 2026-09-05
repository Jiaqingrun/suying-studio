"""跟镜精品 · 单元测试（简报 / 门禁 / 文案 / 热点占位）。"""

from __future__ import annotations

import unittest


class SceneTourCopyTests(unittest.TestCase):
    def test_category_labels_zh(self) -> None:
        from engine.pack.scene_tour_copy import CATEGORY_LABELS, UI, category_label, reason_zh

        self.assertEqual(category_label("scene_tour"), "跟镜精品")
        self.assertEqual(category_label("premium"), "精品样式")
        self.assertEqual(category_label("default"), "日常日更")
        self.assertEqual(UI["generate"], "生成跟镜精品")
        self.assertIn("scene_tour", CATEGORY_LABELS)
        msg = reason_zh("coverage_low", bucket="护理床位", need=1)
        self.assertIn("护理床位", msg)
        self.assertNotIn("required bucket", msg.lower())
        self.assertNotIn("fail-closed", msg.lower())


class SceneTourBriefTests(unittest.TestCase):
    def test_missing_industry(self) -> None:
        from engine.pack.scene_tour_brief import compile_scene_tour_brief

        brief = compile_scene_tour_brief(
            customer_id=1,
            customer_name="测客户",
            profile={},
        )
        self.assertFalse(brief["ok"])
        self.assertTrue(any("行业" in r for r in brief["reasons"]))

    def test_industry_mismatch(self) -> None:
        from engine.pack.scene_tour_brief import compile_scene_tour_brief

        brief = compile_scene_tour_brief(
            customer_id=2,
            customer_name="测客户",
            profile={"industry_pack": "life-service"},
            rule_industry_key="building-supply",
        )
        self.assertFalse(brief["ok"])
        self.assertTrue(any("不一致" in r for r in brief["reasons"]))

    def test_life_service_ok(self) -> None:
        from engine.pack.scene_tour_brief import compile_scene_tour_brief

        brief = compile_scene_tour_brief(
            customer_id=3,
            customer_name="臻享丽人",
            profile={
                "industry_pack": "life-service",
                "brand": {"display_name": "臻享丽人"},
                "compliance": {"banned_terms": ["包治"]},
            },
        )
        self.assertTrue(brief["ok"])
        self.assertEqual(brief["customer_id"], 3)
        self.assertEqual(brief["industry_key"], "life-service")
        self.assertIn("包治", brief["banned_terms"])
        self.assertTrue(brief["title_seeds"])
        self.assertTrue(brief["stages"])
        self.assertTrue(brief["fingerprint"])


class SceneTourValidateTests(unittest.TestCase):
    def _brief(self) -> dict:
        from engine.pack.scene_tour_brief import compile_scene_tour_brief

        return compile_scene_tour_brief(
            customer_id=9,
            customer_name="测",
            profile={"industry_pack": "life-service", "brand": {"display_name": "测店"}},
        )

    def test_cross_industry_ban(self) -> None:
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = self._brief()
        shots = [
            {
                "bucket": "treatment_bed",
                "role": "open",
                "text": "护理床旁堆着水泥和钢筋，装车很忙。",
                "anchors": ["护理床"],
            },
            {
                "bucket": "tea",
                "role": "close",
                "text": "茶点小桌旁，鲜花静静摆放。",
                "anchors": ["茶点"],
            },
        ]
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertFalse(report["ok"])
        blob = " ".join(report["reasons"])
        self.assertTrue("串味" in blob or "其他行业" in blob)

    def test_rejects_repeated_comfort_tail(self) -> None:
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = self._brief()
        shots = [
            {
                "bucket": "treatment_room",
                "role": "open",
                "text": "仪器轻轻滑过肌肤，臻享丽人邀您亲身体验细致护理流程。",
                "anchors": ["仪器"],
            },
            {
                "bucket": "treatment_bed",
                "role": "body",
                "text": "躺在护理床上，我们从细致沟通开始您的舒适体验。",
                "anchors": ["护理床"],
            },
            {
                "bucket": "treatment_action",
                "role": "body",
                "text": "穿过门框，我们以温和护理迎接您的舒适体验。",
                "anchors": ["门框"],
            },
            {
                "bucket": "tea",
                "role": "close",
                "text": "在白色衣物旁坐下，我们更注重您的舒适体验与清晰流程沟通。",
                "anchors": ["白色衣物"],
            },
        ]
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertFalse(report["ok"])
        blob = " ".join(report["reasons"])
        self.assertIn("宣传收束重复", blob)
        self.assertIn("舒适体验", blob)

    def test_no_keyword_pool_english(self) -> None:
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = self._brief()
        shots = [
            {
                "bucket": "honor_wall",
                "role": "open",
                "text": "墙上奖牌整齐陈列，本地门店把口碑摆在明处。",
                "anchors": ["奖牌"],
            },
            {
                "bucket": "corridor",
                "role": "body",
                "text": "顺着走廊往里走，门店空间敞亮好找。",
                "anchors": ["走廊"],
            },
            {
                "bucket": "treatment_bed",
                "role": "body",
                "text": "护理床铺好巾单，到店沟通后再按节奏护理。",
                "anchors": ["护理床"],
            },
            {
                "bucket": "tea",
                "role": "close",
                "text": "小憩处有茶点，到店体验更从容。",
                "anchors": ["茶点"],
            },
        ]
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertTrue(report["ok"], report["reasons"])
        self.assertEqual(report["label_zh"], "成片检查报告")


class SceneTourWriteTests(unittest.TestCase):
    def test_template_line_has_anchor(self) -> None:
        from engine.pack.scene_tour import write_lines_for_shots
        from engine.pack.scene_tour_brief import compile_scene_tour_brief

        brief = compile_scene_tour_brief(
            customer_id=1,
            customer_name="测",
            profile={"industry_pack": "life-service", "brand": {"display_name": "测店"}},
        )
        draft = [
            {"bucket": "treatment_bed", "role": "body", "cliplet_id": 1, "available_sec": 8.0},
            {"bucket": "tea", "role": "close", "cliplet_id": 2, "available_sec": 8.0},
        ]
        shots = write_lines_for_shots(draft, brief, use_ollama=False)
        self.assertEqual(len(shots), 2)
        for s in shots:
            self.assertTrue(str(s.get("text") or "").strip())
            anchors = list(s.get("anchors") or [])
            self.assertTrue(anchors)
            self.assertTrue(any(a in s["text"] for a in anchors))

    def test_brand_at_most_once_building_supply(self) -> None:
        from engine.pack.scene_tour import write_lines_for_shots
        from engine.pack.scene_tour_brief import compile_scene_tour_brief
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = compile_scene_tour_brief(
            customer_id=1,
            customer_name="北京始峰伟业",
            profile={
                "industry_pack": "building-supply",
                "brand": {"display_name": "始峰五金"},
            },
        )
        draft = [
            {
                "bucket": "storefront",
                "role": "open",
                "cliplet_id": 1,
                "available_sec": 10.0,
                "visual_tokens": ["招牌"],
            },
            {
                "bucket": "warehouse",
                "role": "body",
                "cliplet_id": 2,
                "available_sec": 10.0,
                "visual_tokens": ["货架"],
            },
            {
                "bucket": "loading",
                "role": "body",
                "cliplet_id": 3,
                "available_sec": 10.0,
                "visual_tokens": ["装车"],
            },
            {
                "bucket": "other",
                "role": "close",
                "cliplet_id": 4,
                "available_sec": 10.0,
                "visual_tokens": ["场地"],
            },
        ]
        shots = write_lines_for_shots(draft, brief, use_ollama=False)
        joined = "".join(str(s.get("text") or "") for s in shots)
        self.assertLessEqual(joined.count("始峰五金"), 1, joined)
        for s in shots:
            s["available_sec"] = 10.0
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertTrue(report["ok"], report["reasons"])


class SceneTourHotInboxTests(unittest.TestCase):
    def test_stub_disabled(self) -> None:
        from engine.pack.scene_tour_hot_inbox import hot_inbox_status, ingest_hot_stub

        st = hot_inbox_status()
        self.assertTrue(st["ok"])
        self.assertFalse(st["enabled"])
        self.assertIn("第二版", st["message"])
        self.assertEqual(ingest_hot_stub()["ok"], False)


class SceneTourSchemaTests(unittest.TestCase):
    def test_recommended_includes_scene_tour(self) -> None:
        from engine.template.rule_schema import SCHEMA_METADATA

        cats = SCHEMA_METADATA.get("recommended_content_categories") or []
        self.assertIn("scene_tour", cats)


class SceneTourRecipeTests(unittest.TestCase):
    def test_no_storefront_skips_gate_recipe(self) -> None:
        from engine.pack.scene_tour_skeleton import expand_route_stages, skeleton_for

        skel = skeleton_for("building-supply")
        assert skel is not None
        inv = {"warehouse": 10, "loading": 5, "product_closeup": 3, "other": 2, "storefront": 0}
        stages, meta = expand_route_stages(skel, inventory=inv, seed=42)
        keys = [str(s.get("key")) for s in stages]
        self.assertNotIn("storefront", keys)
        self.assertNotEqual(meta.get("recipe_id"), "gate_if_any")
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertGreaterEqual(len(keys), 3)

    def test_validate_uses_route_order_not_skeleton(self) -> None:
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = {
            "industry_key": "building-supply",
            "min_shots": 4,
            "banned_terms": [],
            "brand_display": "始峰",
            "stages": [
                {"key": "loading", "role": "open"},
                {"key": "warehouse", "role": "body"},
                {"key": "product_closeup", "role": "body"},
                {"key": "other", "role": "close"},
            ],
        }
        shots = [
            {
                "bucket": "loading",
                "role": "open",
                "text": "跟到装卸处看货件，配货有序方便跟拍。",
                "anchors": ["货"],
                "visual_tokens": ["货"],
            },
            {
                "bucket": "warehouse",
                "role": "body",
                "text": "仓里货架码得整齐，在架当场能看清。",
                "anchors": ["货架"],
                "visual_tokens": ["货架"],
            },
            {
                "bucket": "product_closeup",
                "role": "body",
                "text": "近看软管成色，规格细节方便您选。",
                "anchors": ["软管"],
                "visual_tokens": ["软管"],
            },
            {
                "bucket": "other",
                "role": "close",
                "text": "现场扫过圆桶，仓配能力一眼能体会。",
                "anchors": ["圆桶"],
                "visual_tokens": ["圆桶"],
            },
        ]
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertTrue(report["ok"], report["reasons"])


class SceneTourBanTermTests(unittest.TestCase):
    def test_building_supply_copy_avoids_现货(self) -> None:
        from engine.pack.scene_tour import write_lines_for_shots
        from engine.pack.scene_tour_brief import compile_scene_tour_brief
        from engine.pack.scene_tour_diction import _PROMO_CUES
        from engine.pack.scene_tour_skeleton import skeleton_for

        self.assertNotIn("现货", _PROMO_CUES)
        self.assertIn("在架", _PROMO_CUES)
        sk = skeleton_for("building-supply")
        seeds = " ".join(sk.get("title_seeds") or [])
        self.assertNotIn("现货", seeds)
        brief = compile_scene_tour_brief(
            customer_id=1,
            customer_name="测客户",
            profile={"industry_pack": "building-supply"},
            rule_industry_key="building-supply",
        )
        self.assertTrue(brief.get("ok"), brief.get("reasons"))
        pitch = str((brief.get("enterprise_pitch") or {}).get("positioning") or "")
        lines = " ".join((brief.get("enterprise_pitch") or {}).get("lines") or [])
        self.assertNotIn("现货", pitch + lines)
        shots = [
            {
                "bucket": "warehouse",
                "role": "body",
                "duration_sec": 5.0,
                "available_sec": 8.0,
                "visual_tokens": ["货架"],
                "visual_description": "仓库货架陈列",
            }
        ]
        lined = write_lines_for_shots(shots, brief=brief, use_ollama=False)
        joined = " ".join(str(s.get("text") or "") for s in lined)
        self.assertNotIn("现货", joined)


if __name__ == "__main__":
    unittest.main()
