"""Vague unidentified-object speak must never enter VO / burned subtitles."""

from __future__ import annotations

import unittest


class TestVagueVisionSpeakBan(unittest.TestCase):
    def test_markers_catch_688_phrases(self):
        from engine.pack.narration_script import (
            _clause_from_hint,
            script_has_vague_vision_speak,
            script_has_video_meta_speak,
            scrub_video_meta_sentences,
            strip_vague_vision_spans,
            to_spoken_line,
        )

        bads = [
            "人物手持黑色物体",
            "能看到堆叠的黄色物品和货架",
            "左侧堆叠着多层带有孔洞的银灰色金属板材",
            "表面包裹透明塑料膜",
            "右侧白色板材堆叠高度较高边缘整齐",
            "前景有一个空的木质托盘",
            "左侧地面有绿色和灰色的桶状物体",
            "人物正弯腰在货架旁进行操作",
            "右侧依然是堆叠的白色板材和橙色卡车",
            "货物用绿色打包带捆扎",
        ]
        for b in bads:
            self.assertTrue(script_has_video_meta_speak(b), b)
            self.assertTrue(script_has_vague_vision_speak(b), b)
            self.assertEqual(_clause_from_hint(b), "")
            self.assertIsNone(to_spoken_line(b))

        # strip_punctuation style (no periods) — mid-script spans must be cut
        raw = (
            "仓库又忙了试机的时候从货架到车斗顺一遍流程更短几趟配齐"
            "报单我们帮着配人物手持黑色物体能看到堆叠的黄色物品和货架"
            "左侧堆叠着多层带有孔洞的银灰色金属板材表面包裹透明塑料膜"
            "右侧白色板材堆叠高度较高边缘整齐缺哪样到店跟我们配"
        )
        cleaned = strip_vague_vision_spans(raw)
        for needle in (
            "人物手持",
            "能看到",
            "黑色物体",
            "黄色物品",
            "堆叠着",
            "透明塑料膜",
            "边缘整齐",
        ):
            self.assertNotIn(needle, cleaned, needle)
        self.assertIn("仓库又忙了", cleaned)
        self.assertIn("缺哪样到店跟我们配", cleaned)

        # 707 / 708 leak — spatial inventory dump must not eat the CTA
        raw707 = (
            "到货先卸一车试机的时候从货架到车斗顺一遍流程更短几趟配齐"
            "报单我们帮着配前景有一个空的木质托盘左侧地面有绿色和灰色的桶状物体"
            "人物正弯腰在货架旁进行操作右侧依然是堆叠的白色板材和橙色卡车"
            "货物用绿色打包带捆扎缺哪样到店跟我们配"
        )
        cleaned707 = strip_vague_vision_spans(raw707)
        for needle in (
            "前景有",
            "木质托盘",
            "桶状物体",
            "人物正",
            "弯腰",
            "右侧依然",
            "打包带",
            "橙色卡车",
        ):
            self.assertNotIn(needle, cleaned707, needle)
        self.assertIn("到货先卸一车", cleaned707)
        self.assertIn("报单我们帮着配", cleaned707)
        self.assertIn("缺哪样到店跟我们配", cleaned707)
        self.assertNotIn("是跟我们配", cleaned707)

        raw708 = (
            "今天配了几车货机器加的是工程专用油现场怎么干就怎么拍当面看货再定"
            "背景货架结构清晰可见蓝色立柱和橙色横梁背景货架上可见黄色和绿色包装物"
            "箱体正面印有黑色汉字安全帽左侧箱体可见部分汉字安全帽缺哪样到店跟我们配"
        )
        cleaned708 = strip_vague_vision_spans(raw708)
        for needle in (
            "背景货架",
            "结构清晰",
            "可见",
            "立柱",
            "横梁",
            "包装物",
            "印有",
            "箱体",
        ):
            self.assertNotIn(needle, cleaned708, needle)
        self.assertIn("今天配了几车货", cleaned708)
        self.assertIn("当面看货再定", cleaned708)
        self.assertIn("缺哪样到店跟我们配", cleaned708)
        self.assertTrue(script_has_vague_vision_speak(raw708))
        self.assertFalse(script_has_vague_vision_speak(cleaned708))

        raw709 = (
            "货架又满了冷切锯切方管备货装车连着做干货不说多实在不啰嗦需要哪类跟我们说"
            "上方货架上有黄色和灰色的袋装物品板材堆叠整齐侧面有金属框架支撑"
            "红色卡车车厢内整齐码放着多摞板材侧面有黑色图案缺哪样到店跟我们配"
        )
        cleaned709 = strip_vague_vision_spans(raw709)
        for needle in (
            "上方货架",
            "袋装物品",
            "堆叠整齐",
            "侧面有",
            "金属框架",
            "整齐码放",
            "黑色图案",
        ):
            self.assertNotIn(needle, cleaned709, needle)
        self.assertIn("货架又满了", cleaned709)
        self.assertIn("需要哪类跟我们说", cleaned709)
        self.assertIn("缺哪样到店跟我们配", cleaned709)
        self.assertTrue(script_has_vague_vision_speak(raw709))
        self.assertFalse(script_has_vague_vision_speak(cleaned709))

        crumb = "到货先卸一车试机的时候从货架到车斗顺一遍到店看样再定左侧背景处"
        cleaned_crumb = strip_vague_vision_spans(crumb)
        self.assertNotIn("左侧背景", cleaned_crumb)
        self.assertNotIn("背景处", cleaned_crumb)
        self.assertIn("到店看样再定", cleaned_crumb)

        raw711 = (
            "三轮车场内转运够用生料带管卡胶水配送时间好商量省的是来回跑的工夫"
            "背景有蓝色金属结构塑料包装膜有反光袋子上有红色条纹和文字"
            "白色袋子堆叠在一起袋子上印有红色文字32克尼龙袋子表面有塑料包装"
            "货以当面看的为准"
        )
        cleaned711 = strip_vague_vision_spans(raw711)
        for needle in (
            "背景有",
            "包装膜",
            "反光",
            "袋子上",
            "堆叠在一起",
            "印有",
            "塑料包装",
            "条纹",
        ):
            self.assertNotIn(needle, cleaned711, needle)
        self.assertIn("三轮车场内转运够用", cleaned711)
        self.assertIn("货以当面看的为准", cleaned711)
        self.assertTrue(script_has_vague_vision_speak(raw711))
        self.assertFalse(script_has_vague_vision_speak(cleaned711))

        raw712 = (
            "仓库又忙了机器加的是工程专用油现场怎么干就怎么拍流程更短几趟配齐报单我们帮着配"
            "物体表面缠绕有红白相间的方格反光带特写显示米色圆柱体上的红白方格反光带细节"
            "圆柱体表面呈现塑料质感物体堆叠紧密缺哪样到店跟我们配"
        )
        cleaned712 = strip_vague_vision_spans(raw712)
        for needle in (
            "物体表面",
            "特写",
            "圆柱体",
            "反光带",
            "塑料质感",
            "堆叠紧密",
            "缠绕有",
        ):
            self.assertNotIn(needle, cleaned712, needle)
        self.assertIn("仓库又忙了", cleaned712)
        self.assertIn("报单我们帮着配", cleaned712)
        self.assertIn("缺哪样到店跟我们配", cleaned712)
        self.assertTrue(script_has_vague_vision_speak(raw712))
        self.assertFalse(script_has_vague_vision_speak(cleaned712))

        raw713 = (
            "到货先卸一车试机的时候从货架到车斗顺一遍当面看货再定"
            "一名穿黑色短袖上衣的人站在画面左侧手扶着一堆白色长条状货物"
            "穿黑色短袖的人伸出左臂手放在白色货物堆的顶部缺哪样到店跟我们配"
        )
        cleaned713 = strip_vague_vision_spans(raw713)
        for needle in (
            "短袖",
            "画面左侧",
            "手扶着",
            "长条状",
            "伸出左臂",
            "手放在",
            "货物堆",
        ):
            self.assertNotIn(needle, cleaned713, needle)
        self.assertIn("到货先卸一车", cleaned713)
        self.assertIn("当面看货再定", cleaned713)
        self.assertIn("缺哪样到店跟我们配", cleaned713)
        self.assertTrue(script_has_vague_vision_speak(raw713))
        self.assertFalse(script_has_vague_vision_speak(cleaned713))

        raw_heiyi = (
            "当面看货再定一名穿黑衣的男子站在画面左侧"
            "缺哪样到店跟我们配"
        )
        cleaned_heiyi = strip_vague_vision_spans(raw_heiyi)
        for needle in ("穿黑衣", "男子", "画面左侧", "站在画面"):
            self.assertNotIn(needle, cleaned_heiyi, needle)
        self.assertIn("当面看货再定", cleaned_heiyi)
        self.assertIn("缺哪样到店跟我们配", cleaned_heiyi)
        self.assertTrue(script_has_vague_vision_speak(raw_heiyi))
        self.assertFalse(script_has_vague_vision_speak(cleaned_heiyi))

        raw715 = (
            "切割片先看转速纸箱怕水省的是来回跑的工夫"
            "一块巨大的绿色防水布覆盖在物体上防水布下方货以当面看的为准"
            "先到始峰五金现场把过程听清楚工作人员正忙碌地装载货物每一件配件都仔细核对"
        )
        cleaned715 = strip_vague_vision_spans(raw715)
        for needle in ("防水布", "物体上", "工作人员正", "巨大", "覆盖在"):
            self.assertNotIn(needle, cleaned715, needle)
        self.assertIn("切割片先看转速", cleaned715)
        self.assertIn("货以当面看的为准", cleaned715)
        self.assertTrue(script_has_vague_vision_speak(raw715))
        self.assertFalse(script_has_vague_vision_speak(cleaned715))

        with_periods = (
            "仓库又忙了。试机的时候从货架到车斗顺一遍。"
            "人物手持黑色物体。能看到堆叠的黄色物品和货架。"
            "缺哪样到店跟我们配。"
        )
        scrubbed = scrub_video_meta_sentences(with_periods)
        self.assertNotIn("人物手持", scrubbed)
        self.assertNotIn("能看到", scrubbed)
        self.assertIn("仓库又忙了", scrubbed)

    def test_ready_gate_rejects_vague_script(self):
        from engine.qc.ready_gate import _check_voice

        fails = _check_voice(
            {
                "meta": {
                    "tts_provider": "clone",
                    "clone_pack": {"id": "aunt_slow", "ref_wav": "/tmp/x.wav"},
                    "narration_script": "仓库又忙了人物手持黑色物体能看到黄色物品",
                    "voice_lang": "zh",
                    "narration_path": "/tmp/v.wav",
                }
            }
        )
        self.assertTrue(any("vague vision" in f for f in fails), fails)


if __name__ == "__main__":
    unittest.main()
