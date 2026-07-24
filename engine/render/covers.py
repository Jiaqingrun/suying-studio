from __future__ import annotations

import subprocess
from pathlib import Path


def extract_cover_candidates(video_path: Path, out_dir: Path, count: int = 3) -> list[Path]:
    """Extract evenly spaced JPEG covers from a finished montage."""
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = float((probe.stdout or "0").strip() or "0")
    except ValueError:
        duration = 0.0
    if duration <= 0:
        duration = 10.0

    # Prefer early / mid / late beats (avoid very first black frame)
    fractions = [0.12, 0.45, 0.78][:count]
    paths: list[Path] = []
    for i, frac in enumerate(fractions):
        t = max(0.05, min(duration - 0.05, duration * frac))
        out = out_dir / f"cover_{i + 1}.jpg"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out),
        ]
        if subprocess.run(cmd, capture_output=True, check=False).returncode == 0 and out.exists():
            paths.append(out)
    return paths
