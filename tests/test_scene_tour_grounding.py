"""跟镜精品 · 画面锚定与错标自纠。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace


class SceneTourGroundingTests(unittest.TestCase):
    def test_stub_description(self) -> None:
        from engine.pack.scene_tour_grounding import is_stub_description

        self.assertTrue(is_stub_description("分类:Camera；素材:xxx；片段8.0-11.8秒共3.8秒；竖屏；实拍业务素材"))
        self.assertFalse(
            is_stub_description("画面展示了一个存放金属管材和配件的货架区域。货架为白色金属材质。")
        )

    def test_storefront_rejects_indoor_sign_only(self) -> None:
        from engine.pack.scene_tour_grounding import cliplet_is_grounded_for_bucket

        row = SimpleNamespace(
            description="室内店铺货架上方悬挂着红底和黄底的中文招牌，陈列电动工具。",
            objects_json=["货架", "招牌", "电动工具"],
            semantic_json={},
        )
        ok, why = cliplet_is_grounded_for_bucket(row, "storefront")
        self.assertFalse(ok)
        self.assertTrue("门头" in why or "招牌" in why)

    def test_narration_fluff_without_door_evidence(self) -> None:
        from engine.pack.scene_tour_grounding import narration_conflicts_evidence

        reasons = narration_conflicts_evidence(
            "北京始峰伟业的门头招牌在阳光下清晰可见，吸引着过往客户的目光。",
            bucket="storefront",
            tokens=["灰砖", "托盘", "码垛"],
            stub=False,
        )
        self.assertTrue(reasons)
        self.assertTrue(any("门头" in r or "串味" in r for r in reasons))

    def test_validate_rejects_ungrounded_open(self) -> None:
        from engine.pack.scene_tour_brief import compile_scene_tour_brief
        from engine.pack.scene_tour_validate import validate_scene_tour_shots

        brief = compile_scene_tour_brief(
            customer_id=1,
            customer_name="北京始峰伟业",
            profile={"industry_pack": "building-supply", "brand_display": "北京始峰伟业"},
        )
        self.assertTrue(brief.get("ok"))
        shots = [
            {
                "bucket": "storefront",
                "role": "open",
                "cliplet_id": 1,
                "text": "北京始峰伟业的门头招牌在阳光下清晰可见，吸引着过往客户。",
                "anchors": ["门头"],
                "visual_tokens": ["灰砖", "托盘"],
                "visual_stub": False,
            },
            {
                "bucket": "warehouse",
                "role": "body",
                "cliplet_id": 2,
                "text": "仓内货架分列清楚，通道留得开。",
                "anchors": ["货架"],
                "visual_tokens": ["货架", "管材"],
                "visual_stub": False,
            },
            {
                "bucket": "loading",
                "role": "body",
                "cliplet_id": 3,
                "text": "跟到装车现场，车斗与货一件件码稳。",
                "anchors": ["装车"],
                "visual_tokens": ["装车", "货车"],
                "visual_stub": False,
            },
            {
                "bucket": "product_closeup",
                "role": "body",
                "cliplet_id": 4,
                "text": "近镜停在五金工具，型号与细节看得见。",
                "anchors": ["五金"],
                "visual_tokens": ["五金", "工具"],
                "visual_stub": False,
            },
            {
                "bucket": "other",
                "role": "close",
                "cliplet_id": 5,
                "text": "镜头扫过场地，现场什么样就什么样。",
                "anchors": ["场地"],
                "visual_tokens": ["场地"],
                "visual_stub": False,
            },
        ]
        report = validate_scene_tour_shots(shots, brief=brief)
        self.assertFalse(report["ok"])
        self.assertIn(1, report.get("bad_cliplet_ids") or [])


if __name__ == "__main__":
    unittest.main()
