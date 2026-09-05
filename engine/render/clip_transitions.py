"""Join clip segments with FFmpeg xfade (GRuleVisualLab). Default path remains concat hard-cut."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return 1.0
    try:
        data = json.loads(proc.stdout or "{}")
        return max(0.1, float((data.get("format") or {}).get("duration") or 1.0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 1.0


def join_segments(
    segment_paths: list[Path],
    *,
    out_path: Path,
    transition: str = "none",
    duration_sec: float = 0.4,
    ffmpeg_bin: str = "ffmpeg",
    crf: str = "18",
) -> bool:
    """Return True on success. transition=none → classic concat demuxer."""
    if not segment_paths:
        return False
    if len(segment_paths) == 1 or transition in {"", "none", "hard"}:
        list_file = out_path.parent / "concat_join.txt"
        lines = [f"file '{p.resolve().as_posix()}'" for p in segment_paths]
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "fast",
            "-crf",
            crf,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out_path),
        ]
        return subprocess.run(cmd, capture_output=True, check=False).returncode == 0

    dur = max(0.05, min(0.8, float(duration_sec)))
    durs = [_ffprobe_duration(p) for p in segment_paths]
    inputs: list[str] = []
    for p in segment_paths:
        inputs.extend(["-i", str(p)])

    filters: list[str] = []
    offset = max(0.0, durs[0] - dur)
    prev = "[0:v]"
    prev_a = "[0:a]"
    for i in range(1, len(segment_paths)):
        vlabel = f"[vx{i}]"
        alabel = f"[ax{i}]"
        filters.append(
            f"{prev}[{i}:v]xfade=transition={transition}:duration={dur:.3f}:offset={offset:.3f}{vlabel}"
        )
        filters.append(f"{prev_a}[{i}:a]acrossfade=d={dur:.3f}{alabel}")
        prev = vlabel
        prev_a = alabel
        if i < len(segment_paths) - 1:
            offset = max(0.0, offset + durs[i] - dur)

    fc = ";".join(filters)
    cmd = [
        ffmpeg_bin,
        "-y",
        *inputs,
        "-filter_complex",
        fc,
        "-map",
        prev,
        "-map",
        prev_a,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "fast",
        "-crf",
        crf,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0
