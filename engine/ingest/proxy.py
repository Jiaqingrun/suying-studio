from __future__ import annotations

import subprocess
from pathlib import Path

from engine.catalog.db import Asset


def make_proxy(source: Path, dest: Path, *, max_height: int = 720, fps: int = 15) -> bool:
    """Create a low-bitrate proxy for analysis/preview. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # scale to max_height keeping aspect; even dims for x264
    vf = (
        f"scale=-2:'min({max_height},ih)':force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        vf,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-an",
        str(dest),
    ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0 and dest.exists()


def analysis_video_path(asset: Asset) -> Path:
    """Prefer proxy for analysis; fall back to normalized then source."""
    if asset.proxy_path:
        p = Path(asset.proxy_path)
        if p.exists():
            return p
    storage = Path(asset.storage_path)
    if storage.exists():
        return storage
    return Path(asset.source_path)


def proxy_dest_for_asset(cache_library_dir: Path) -> Path:
    return cache_library_dir / "proxy.mp4"
