"""G.BREATH must not false-fail burn_dual (foreign primary + CJK secondary)."""

from __future__ import annotations

from engine.qc.ready_gate import _check_breath, _is_mostly_cjk_line


def test_mostly_cjk_vs_arabic() -> None:
    assert _is_mostly_cjk_line("真实现场认真记录每一个细节都看得见")
    assert not _is_mostly_cjk_line(
        "نعرض العمل الحقيقي وكل التفاصيل، مع خدمة يمكنك الوثوق بها."
    )


def test_dual_ar_primary_zh_secondary_passes_under_zh_voice() -> None:
    srt = """1
00:00:00,194 --> 00:00:00,371
هذه Brand.
这里是始峰五金

2
00:00:01,226 --> 00:00:04,321
نعرض العمل الحقيقي وكل التفاصيل، مع خدمة يمكنك الوثوق بها.
真实现场认真记录每一个细节都看得见

"""
    sidecar = {
        "meta": {
            "voice_lang": "zh",
            "subtitle_lang": "ar",
            "tts_provider": "edge",
            "tts_rate": "-8%",
        }
    }
    fails = _check_breath(sidecar, srt)
    assert not any("cue too long" in f for f in fails), fails


def test_long_zh_primary_still_fails() -> None:
    long = "来跟着镜头把始峰五金现场过一遍从外观包装再看到现场操作信息以当前实拍为准"
    assert len(long) > 22
    srt = f"""1
00:00:00,000 --> 00:00:03,000
{long}

"""
    sidecar = {"meta": {"voice_lang": "zh", "tts_provider": "edge", "tts_rate": "-8%"}}
    fails = _check_breath(sidecar, srt)
    assert any("cue too long" in f for f in fails), fails
