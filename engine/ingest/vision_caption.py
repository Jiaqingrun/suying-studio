from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import httpx

from engine.catalog.db import Asset
from engine.ingest.proxy import analysis_video_path

OLLAMA_URL = "http://127.0.0.1:11434"
VISION_MODEL = "gemma4:latest"


def heuristic_describe(asset: Asset, start: float, end: float) -> str:
    name = Path(asset.source_path).stem
    cat = asset.category or "未分类"
    dur = round(end - start, 1)
    orientation = "竖屏" if (asset.height or 0) > (asset.width or 0) else "横屏"
    bits = [
        f"分类:{cat}",
        f"素材:{name}",
        f"片段{start:.1f}-{end:.1f}秒共{dur}秒",
        orientation,
        "工地五金门店仓库实拍素材" if cat in {"Camera", "WeiXin", "Pictures"} else "业务素材",
    ]
    for token in ("仓库", "门店", "送货", "产品", "工具", "装车", "五金"):
        if token in name:
            bits.append(token)
    return "；".join(bits)


def extract_frame(video: Path, at_sec: float, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, at_sec):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(out_path),
    ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0 and out_path.exists()


def vision_caption(image_path: Path, *, model: str = VISION_MODEL, timeout: float = 90.0) -> str | None:
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            "你是五金建材批发短视频素材标注员。"
            "用一句中文描述画面（15–40字），点明：场景（仓库/门店/工地/装车/产品特写等）、"
            "可见物体或动作。不要开场白，不要引号。"
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                },
            )
        if resp.status_code != 200:
            return None
        content = (resp.json().get("message") or {}).get("content") or ""
        # strip possible thinking leakage — keep last non-empty line-ish
        text = content.strip().split("\n")[0].strip()
        if len(text) < 4:
            return None
        return text[:120]
    except Exception:
        return None


def describe_cliplet_vision(
    asset: Asset,
    start: float,
    end: float,
    *,
    cache_dir: Path,
    use_vision: bool = True,
) -> tuple[str, str]:
    """Return (description, backend) where backend is vision|heuristic."""
    fallback = heuristic_describe(asset, start, end)
    if not use_vision:
        return fallback, "heuristic"

    video = analysis_video_path(asset)
    if not video.exists():
        return fallback, "heuristic"

    mid = start + max(0.1, (end - start) * 0.4)
    frame = cache_dir / f"frame_{asset.uuid}_{int(start*10)}_{int(end*10)}.jpg"
    if not extract_frame(video, mid, frame):
        return fallback, "heuristic"

    caption = vision_caption(frame)
    if not caption:
        return fallback, "heuristic"

    cat = asset.category or "未分类"
    # Keep category/time as retrieval anchors + vision sentence
    merged = f"分类:{cat}；{caption}；时段{start:.1f}-{end:.1f}秒"
    return merged, "vision"
