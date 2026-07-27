#!/usr/bin/env python3
"""Smoke: zh narration must breathe in ≤18-char units (Job89)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pack.text_sanitize import (  # noqa: E402
    ensure_zh_speech_breaks,
    split_then_strip_sentences,
)
from engine.pack.tts import DEFAULT_EDGE_RATE, synthesize_script  # noqa: E402


def main() -> None:
    run_on = (
        "这里是始峰五金每一件货物仓库分拣快速又整齐装车发货也不耽误时间"
        "材料送到工地效率看得见服务更贴心始峰五金本地实拍配货发货省心放心"
    )
    punct = ensure_zh_speech_breaks(run_on, max_chars=18)
    assert "。" in punct, "must insert sentence ends"
    chunks = split_then_strip_sentences(punct)
    assert len(chunks) >= 4, chunks
    assert max(len(c) for c in chunks) <= 20, chunks
    print("chunks", chunks)

    # Live Edge if available — verify rate lands in manifest
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("SMOKE_NARRATION_BREATH OK (offline chunk-only)")
        return

    out = ROOT / "_smoke_breath_tts"
    out.mkdir(exist_ok=True)
    narr = synthesize_script(
        run_on,
        out,
        lang="zh",
        provider="edge",
        voice="zh-CN-XiaoxiaoNeural",
        rate=DEFAULT_EDGE_RATE,
        pitch="+20Hz",
        strip_punctuation=True,
        allow_fallback=False,
        write_manifest=True,
    )
    assert (narr.extras or {}).get("rate") == DEFAULT_EDGE_RATE, narr.extras
    assert len(narr.segments) >= 4, narr.segments
    assert max(len(s.text) for s in narr.segments) <= 20
    print(
        "live OK",
        "segs",
        len(narr.segments),
        "rate",
        narr.extras.get("rate"),
        "dur",
        narr.total_duration_sec,
    )
    print("SMOKE_NARRATION_BREATH OK")


if __name__ == "__main__":
    main()
