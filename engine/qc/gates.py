from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.render.ffmpeg import probe_output
from engine.template.engine import MontagePlan


@dataclass
class QCResult:
    passed: bool
    state: str
    reasons: list[str]
    metrics: dict[str, Any]


def _run_ffmpeg_stderr(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (result.stderr or "") + "\n" + (result.stdout or "")


def detect_black_ratio(path: Path, duration: float) -> float:
    """Return fraction of timeline flagged as black (0–1)."""
    if duration <= 0:
        return 0.0
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        "blackdetect=d=0.4:pix_th=0.10",
        "-an",
        "-f",
        "null",
        "-",
    ]
    text = _run_ffmpeg_stderr(cmd)
    total = 0.0
    for m in re.finditer(r"black_duration:([0-9.]+)", text):
        total += float(m.group(1))
    return min(1.0, total / duration)


def detect_silence_ratio(path: Path, duration: float) -> float:
    """Return fraction of timeline flagged as silence (0–1)."""
    if duration <= 0:
        return 0.0
    probe = probe_output(path)
    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    if not has_audio:
        return 1.0
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "silencedetect=noise=-35dB:d=0.5",
        "-f",
        "null",
        "-",
    ]
    text = _run_ffmpeg_stderr(cmd)
    total = 0.0
    for m in re.finditer(r"silence_duration: ([0-9.]+)", text):
        total += float(m.group(1))
    return min(1.0, total / duration)


def detect_mean_volume_db(path: Path) -> float | None:
    probe = probe_output(path)
    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    if not has_audio:
        return None
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    text = _run_ffmpeg_stderr(cmd)
    m = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", text)
    if not m:
        return None
    return float(m.group(1))


def run_qc(
    output_path: Path,
    plan: MontagePlan,
    *,
    min_duration: float = 8.0,
    max_duration: float = 90.0,
    min_width: int = 720,
    min_height: int = 1280,
    max_black_ratio: float = 0.25,
    max_silence_ratio: float = 0.95,
    min_mean_volume_db: float = -45.0,
    require_audio: bool = True,
) -> QCResult:
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    if not output_path.exists():
        return QCResult(passed=False, state="failed", reasons=["输出文件不存在"], metrics=metrics)

    probe = probe_output(output_path)
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    width = int(video.get("width", 0)) if video else 0
    height = int(video.get("height", 0)) if video else 0
    has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))

    metrics = {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
    }

    if duration < min_duration:
        reasons.append(f"时长过短: {duration:.1f}s")
    if duration > max_duration:
        reasons.append(f"时长过长: {duration:.1f}s")
    if width < min_width or height < min_height:
        reasons.append(f"分辨率不足: {width}x{height}")

    black_ratio = detect_black_ratio(output_path, duration)
    metrics["black_ratio"] = round(black_ratio, 4)
    if black_ratio > max_black_ratio:
        reasons.append(f"黑场过多: {black_ratio:.0%} > {max_black_ratio:.0%}")

    if require_audio and not has_audio:
        reasons.append("缺少音轨（Sprint A 强制有声成片）")
        metrics["silence_ratio"] = 1.0
        metrics["audio_note"] = "missing_audio_stream"
    elif has_audio:
        silence_ratio = detect_silence_ratio(output_path, duration)
        metrics["silence_ratio"] = round(silence_ratio, 4)
        if silence_ratio > max_silence_ratio:
            reasons.append(f"静音过多: {silence_ratio:.0%}")
        mean_db = detect_mean_volume_db(output_path)
        if mean_db is not None:
            metrics["mean_volume_db"] = round(mean_db, 2)
            if mean_db < min_mean_volume_db:
                reasons.append(f"响度过低: {mean_db:.1f}dB < {min_mean_volume_db}dB")
    else:
        metrics["silence_ratio"] = 1.0
        metrics["audio_note"] = "no_audio_stream_allowed"

    if plan.blocked:
        reasons.extend(plan.block_reasons)

    if not plan.clips:
        reasons.append("无有效片段")

    # Soft title length check (does not fail alone — render already clamps)
    title = plan.title or ""
    metrics["title_len"] = len(title)
    if len(title) > 28:
        metrics["title_note"] = "title_long_clamped_at_render"

    passed = len(reasons) == 0
    state = "ready" if passed else "failed"
    return QCResult(passed=passed, state=state, reasons=reasons, metrics=metrics)
