#!/usr/bin/env python3
"""Sprint A smoke: title clamp + audio render + QC require_audio."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config.settings import AppSettings, PathConfig  # noqa: E402
from engine.qc.gates import run_qc  # noqa: E402
from engine.render.ffmpeg import clamp_title_text, render_plan, resolve_bgm_path  # noqa: E402
from engine.template.engine import ClipPlan, MontagePlan  # noqa: E402


def _make_clip(path: Path, with_audio: bool) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=1280x720:d=2",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=f=440:d=2", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "2", str(path)]
    assert subprocess.run(cmd, capture_output=True, check=False).returncode == 0, path


def main() -> None:
    long = "一二三四五六七八九十一二三四五六七八九十一二三四五"
    assert len(long) > 24
    clamped = clamp_title_text(long, max_chars=12, max_lines=2)
    assert clamped.endswith("…"), repr(clamped)
    assert len(clamped) <= 24
    assert len(clamp_title_text("短标题", max_chars=12, max_lines=2)) <= 24

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        settings = AppSettings(
            paths=PathConfig(
                library_root=base / "library",
                output_root=base / "output",
                cache_root=base / "cache",
                data_root=base / "data",
                render_root=base / "render",
                music_root=base / "music",
            ),
            require_audio=True,
            ambient_gain=0.0,
            bgm_gain=0.45,
            keep_source_audio=False,
        )
        for p in (
            settings.paths.library_root,
            settings.paths.output_root,
            settings.paths.cache_root,
            settings.paths.data_root,
            settings.paths.render_root,
            settings.paths.music_root,
        ):
            p.mkdir(parents=True, exist_ok=True)

        src_a = base / "clip_a.mp4"
        src_b = base / "clip_b.mp4"
        _make_clip(src_a, with_audio=True)
        _make_clip(src_b, with_audio=False)

        bgm = resolve_bgm_path(settings, seed=1)
        assert bgm is not None and bgm.exists(), "placeholder BGM should be created"

        plan = MontagePlan(
            seed=42,
            template_name="default-vertical",
            theme="test",
            category="default",
            title="发货快｜工地五金批发促销特价清仓大甩卖",
            clips=[
                ClipPlan("hook", "a", str(src_a), 0.0, 2.0),
                ClipPlan("body1", "b", str(src_b), 0.0, 2.0),
            ],
            warnings=[],
            blocked=False,
            block_reasons=[],
            title_style={
                "position": "top",
                "font_size": 96,
                "color": "#FFFFFF",
                "bar_color": "#111827",
                "bar_opacity": 0.65,
                "max_chars": 12,
                "max_lines": 2,
            },
        )
        out = base / "out.mp4"
        ok = render_plan(settings, plan, out, {"output_width": 720, "output_height": 1280, "reframe_mode": "center"})
        assert ok and out.exists(), "render_plan failed"

        qc = run_qc(out, plan, require_audio=True, min_width=720, min_height=1280, min_duration=3.0)
        assert qc.metrics.get("has_audio") is True, qc.metrics
        assert qc.passed, {"reasons": qc.reasons, "metrics": qc.metrics}

        # G4.32: narration lead + BGM bed
        from engine.pack.tts import narration_bed_from_result, synthesize_script

        narr = synthesize_script("仓配一体。本地发货。", base / "tts", lang="zh", provider="mock")
        bed = narration_bed_from_result(narr, base / "narration.wav")
        assert bed.is_file()
        out2 = base / "out_narr.mp4"
        meta: dict = {}
        ok2 = render_plan(
            settings,
            plan,
            out2,
            {"output_width": 720, "output_height": 1280, "reframe_mode": "center"},
            render_meta=meta,
            narration_path=bed,
        )
        assert ok2 and out2.exists(), "narration+bgm render failed"
        assert meta.get("narration_path"), meta
        assert float(meta.get("bgm_gain_effective") or 1) <= float(settings.bgm_gain)
        qc2 = run_qc(out2, plan, require_audio=True, min_width=720, min_height=1280, min_duration=3.0)
        assert qc2.passed and qc2.metrics.get("has_audio") is True, qc2.reasons

        print(
            json.dumps(
                {
                    "ok": True,
                    "bgm": str(bgm.name),
                    "qc": qc.metrics,
                    "title_clamped_ok": True,
                    "g4_narration_bgm": True,
                    "bgm_bed_gain": meta.get("bgm_gain_effective"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
