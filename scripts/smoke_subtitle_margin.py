#!/usr/bin/env python3
"""Smoke: subtitle cue PNGs must not clip against frame edges (Job87)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from engine.render.subtitles_burn import _render_cue_png  # noqa: E402


def _edge_opaque_ratio(path: Path, *, edge: int = 3, thr: int = 200) -> tuple[float, float]:
    im = Image.open(path).convert("RGBA")
    px = im.load()
    w, h = im.size
    left = right = 0
    total = h * edge
    for y in range(h):
        for x in range(edge):
            if px[x, y][3] > thr:
                left += 1
            if px[w - 1 - x, y][3] > thr:
                right += 1
    return left / max(1, total), right / max(1, total)


def main() -> None:
    samples = [
        "这里是始峰五金我们精心打理仓库里的每一份货物确保快速准确地分拣和装车发货",
        "不论是现货还是急件我们都能够及时送到您的工地效率高服务贴心",
        "始峰五金本地实拍配货发货省心放心",
    ]
    out_dir = ROOT / "_smoke_sub_margin"
    out_dir.mkdir(exist_ok=True)
    for i, text in enumerate(samples):
        out = out_dir / f"cue_{i}.png"
        _render_cue_png(text, 1080, font_size=64, out=out, side_margin_px=48)
        im = Image.open(out)
        assert im.size[0] <= 1080 - 48, f"box too wide: {im.size} text={text[:20]}"
        lo, ro = _edge_opaque_ratio(out)
        assert lo < 0.02 and ro < 0.02, f"edge clip L={lo:.3f} R={ro:.3f} text={text[:20]}"
        print("OK", i, im.size, f"L={lo:.3f}", f"R={ro:.3f}")
    print("SMOKE_SUBTITLE_MARGIN OK")


if __name__ == "__main__":
    main()
