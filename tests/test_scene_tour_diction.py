"""跟镜精品 · 人话导览合同。"""

from __future__ import annotations

import unittest

from engine.pack.scene_tour_diction import (
    cross_shot_promo_repeat_reasons,
    diction_fail_reasons,
    is_flat_diction,
    is_usable_anchor,
    line_reuses_promo_tail,
    normalize_diction_text,
    polish_anchor_join,
    promo_second_beat,
    purple_marker_count,
    replace_promo_second_beat,
    shorten_ornate_line,
)


class SceneTourDictionTests(unittest.TestCase):
    def test_rejects_flat_inventory(self) -> None:
        flat = "男子身穿绿T恤黑裤，手持黑色圆柱体走向深色车辆后备箱。"
        self.assertTrue(is_flat_diction(flat))
        self.assertTrue(diction_fail_reasons(flat))

    def test_rejects_purple_stack(self) -> None:
        bad = "始峰五金，招牌黄色映入眼帘，疏朗悬挂的电动工具端然入画。"
        self.assertTrue(diction_fail_reasons(bad))
        self.assertGreaterEqual(purple_marker_count(bad), 3)

    def test_rejects_latin(self) -> None:
        bad = "跟至装卸处，outdoors与货件错落码稳。"
        reasons = diction_fail_reasons(bad)
        self.assertTrue(any("英文" in r for r in reasons))

    def test_rejects_kejian_patch(self) -> None:
        self.assertTrue(diction_fail_reasons("货架整齐，可见工具。"))

    def test_accepts_human_guide(self) -> None:
        good = "仓里线材码得整齐，在架当场能看清。"
        self.assertEqual(diction_fail_reasons(good), [])

    def test_rejects_caption_inventory(self) -> None:
        bad = "臻享丽人，女士操作笔记本旁摆放着拖鞋和文件的白色推车。"
        reasons = diction_fail_reasons(bad)
        self.assertTrue(any("清点" in r or "复述" in r for r in reasons), reasons)

    def test_rejects_scene_only_without_promo(self) -> None:
        bad = "走廊里光线挺亮，玻璃墙面映着人影。"
        reasons = diction_fail_reasons(bad)
        self.assertTrue(any("宣传" in r for r in reasons), reasons)

    def test_accepts_promo_guide(self) -> None:
        good = "换上拖鞋，把外面的匆忙留在门外。"
        self.assertEqual(diction_fail_reasons(good), [])

    def test_rejects_absurd_path_collocation(self) -> None:
        bad = "顺着浴袍往里走，温和护理服务细致。"
        reasons = diction_fail_reasons(bad)
        self.assertTrue(any("清点" in r or "复述" in r for r in reasons), reasons)

    def test_accepts_real_corridor_line(self) -> None:
        good = "再往里走，空间敞亮，护理间好找。"
        self.assertEqual(diction_fail_reasons(good), [])

    def test_rejects_ornate_ai_voice(self) -> None:
        bad = "仓内线材层层铺陈，通道疏朗留白入画。"
        self.assertTrue(diction_fail_reasons(bad))

    def test_requires_breath_break(self) -> None:
        bad = "仓里线材码得整齐走道留得开。"
        self.assertTrue(any("断句" in r for r in diction_fail_reasons(bad)))

    def test_polish_anchor_no_kejian(self) -> None:
        out = polish_anchor_join("门口招牌清楚", "招牌")
        self.assertNotIn("可见", out)
        self.assertFalse(out.endswith("，。"))

    def test_normalize_strips_dirty_tail(self) -> None:
        self.assertEqual(normalize_diction_text("看清楚，。"), "看清楚。")

    def test_anchor_rejects_english_and_machine(self) -> None:
        self.assertFalse(is_usable_anchor("outdoors"))
        self.assertFalse(is_usable_anchor("管状物"))
        self.assertTrue(is_usable_anchor("货架"))

    def test_shorten_respects_budget(self) -> None:
        long = "先看门口招牌，店里在架一眼能看清，细节也清楚。"
        short = shorten_ornate_line(long, 18)
        pure = "".join(ch for ch in short if "\u4e00" <= ch <= "\u9fff")
        self.assertLessEqual(len(pure), 22)
        self.assertIn("，", short)

    def test_rejects_repeated_promo_tail_across_shots(self) -> None:
        texts = [
            "躺在护理床上，我们从细致沟通开始您的舒适体验。",
            "穿过门框，我们以温和护理迎接您的舒适体验。",
            "在白色衣物旁坐下，我们更注重您的舒适体验与清晰流程沟通。",
        ]
        reasons = cross_shot_promo_repeat_reasons(texts)
        self.assertTrue(reasons)
        self.assertTrue(any("舒适体验" in r for r in reasons))
        self.assertTrue(
            line_reuses_promo_tail(texts[1], [promo_second_beat(texts[0])])
        )
        fixed = replace_promo_second_beat(texts[1], "流程说清楚再开始")
        self.assertIn("流程说清楚再开始", fixed)
        self.assertNotIn("舒适体验", fixed)


if __name__ == "__main__":
    unittest.main()
