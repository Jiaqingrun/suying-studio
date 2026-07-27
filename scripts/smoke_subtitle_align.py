#!/usr/bin/env python3
"""Smoke: 旁白字幕对齐硬规则（NARRATION_SUBTITLE_LOCK）.

Fails if any cue outlives speech on the VO bed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pack.narration_script import srt_from_narration_segments, tighten_srt_to_voiceover
from engine.pack.tts import (
    NarrationSegment,
    _group_spans_for_chunks,
    _speech_spans_by_silence,
    probe_audio_duration,
    synthesize_script,
)


def _parse_ts(ts: str) -> float:
    hh, mm, rest = ts.strip().split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _cues(srt: str) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for block in [b.strip() for b in srt.strip().split("\n\n") if b.strip()]:
        lines = block.splitlines()
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = [p.strip() for p in lines[1].split("-->")]
        text = " ".join(lines[2:]).strip()
        out.append((_parse_ts(a), _parse_ts(b), text))
    return out


def _assert_aligned(srt: str, wav: Path, *, label: str, max_overhang: float = 0.08) -> None:
    spans = _speech_spans_by_silence(wav, min_silence=0.06)
    vo = probe_audio_duration(wav)
    cues = _cues(srt)
    assert cues, f"{label}: empty SRT"
    for i, (start, end, text) in enumerate(cues):
        assert end > start, f"{label}: cue{i} end<=start"
        assert end <= vo + 0.05, f"{label}: cue{i} past VO ({end:.3f}>{vo:.3f}) text={text[:16]}"
        if i + 1 < len(cues):
            assert end <= cues[i + 1][0] + 0.001, f"{label}: cue{i} overlaps next"
        # Overhang into silence: time after last overlapping speech
        overlapping = [(s, e) for s, e in spans if e > start + 0.02 and s < end + 0.05]
        if overlapping:
            speech_end = max(e for _, e in overlapping)
            overhang = end - speech_end
            assert overhang <= max_overhang, (
                f"{label}: cue{i} outlives speech by {overhang:.3f}s "
                f"(end={end:.3f} speech_end={speech_end:.3f}) text={text[:20]}"
            )


def test_group_spans_speech_only() -> None:
    spans = [(0.0, 1.0), (1.2, 2.0), (2.3, 2.8), (3.0, 4.5)]
    chunks = ["一二三四五六", "七八九十", "甲乙丙丁戊己"]
    grouped = _group_spans_for_chunks(spans, chunks)
    assert grouped is not None and len(grouped) == 3
    # Group ends must be speech ends, not next-span starts
    for (gs, ge), (rs, re) in zip(grouped, [(0.0, 2.0), (2.3, 2.8), (3.0, 4.5)]):
        assert abs(ge - re) < 1e-6 or ge <= re + 0.01


def test_srt_absolute_clears_silence() -> None:
    segs = [
        NarrationSegment(0, "第一句测试", "zh", "x", 1.0, 1.0, start_sec=0.1, end_sec=1.1),
        NarrationSegment(1, "第二句测试", "zh", "x", 1.0, 1.0, start_sec=1.5, end_sec=2.5),
    ]
    srt = srt_from_narration_segments(segs, tail_trim_seconds=0.12, inter_sentence_gap_seconds=0.0)
    cues = _cues(srt)
    assert len(cues) == 2
    assert abs(cues[0][1] - (1.1 - 0.12)) < 0.02
    assert cues[0][1] < cues[1][0]  # gap 1.1..1.5 is blank
    assert cues[1][0] >= 1.45


def test_live_edge_oneshot_if_available() -> None:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("SKIP live edge (no edge_tts)")
        return
    script = "仓库现货充足您可以直接来提。叉车分拣打包很快就好。产品线齐全配货更省心。"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        try:
            narr = synthesize_script(
                script,
                out / "narration",
                lang="zh",
                provider="edge",
                voice="zh-CN-XiaoxiaoNeural",
                rate="-8%",
                pitch="+20Hz",
                strip_punctuation=True,
                allow_fallback=False,
                inter_sentence_gap_sec=0.15,
            )
        except Exception as e:  # noqa: BLE001
            print(f"SKIP live edge ({e})")
            return
        from engine.pack.tts import narration_bed_from_result

        bed = narration_bed_from_result(narr, out / "voiceover.wav")
        srt = srt_from_narration_segments(
            narr.segments,
            tail_trim_seconds=0.12,
            inter_sentence_gap_seconds=float((narr.extras or {}).get("inter_sentence_gap_sec") or 0.0),
        )
        srt = tighten_srt_to_voiceover(srt, bed, tail_trim_seconds=0.12)
        _assert_aligned(srt, bed, label="live_oneshot")
        # Absolute timing present on oneshot
        if (narr.extras or {}).get("mode") == "oneshot_punctuated":
            assert all(s.start_sec is not None and s.end_sec is not None for s in narr.segments)
        print("live_oneshot OK", "cues", len(_cues(srt)), "vo", round(probe_audio_duration(bed), 2))


def main() -> int:
    test_group_spans_speech_only()
    print("group_spans OK")
    test_srt_absolute_clears_silence()
    print("srt_absolute OK")
    test_live_edge_oneshot_if_available()
    print("smoke_subtitle_align PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
