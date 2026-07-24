"""G4 TTS adapter — script → segment audio + real durations (length drives cut).

Providers are pluggable; default ``mock`` needs only ffmpeg (smoke / CI).
Real voices can use macOS ``say`` when available. No customer-name branches.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderName = Literal["mock", "say"]

# Rough spoken pace (chars / sec). Overridden via opts / settings.
DEFAULT_CPS: dict[str, float] = {
    "zh": 4.2,
    "en": 13.0,
}


@dataclass
class NarrationSegment:
    index: int
    text: str
    lang: str
    audio_path: str
    duration_sec: float
    estimated_sec: float


@dataclass
class NarrationResult:
    segments: list[NarrationSegment]
    total_duration_sec: float
    provider: str
    lang: str
    out_dir: str
    script_text: str
    manifest_path: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "lang": self.lang,
            "total_duration_sec": round(self.total_duration_sec, 3),
            "segment_count": len(self.segments),
            "segments": [asdict(s) for s in self.segments],
            "out_dir": self.out_dir,
            **self.extras,
        }


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?\n])\s*")


def split_script(text: str, *, max_chars: int = 80) -> list[str]:
    """Split narration into speakable segments (sentence-ish, then length clamp)."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(raw) if p and p.strip()]
    if not parts:
        parts = [raw]
    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
            continue
        # Hard wrap long lines without breaking mid-ASCII word when possible
        buf = part
        while buf:
            if len(buf) <= max_chars:
                out.append(buf)
                break
            cut = max_chars
            for i in range(max_chars, max(max_chars // 2, 8), -1):
                if buf[i - 1] in " ，,、；; ":
                    cut = i
                    break
            out.append(buf[:cut].strip())
            buf = buf[cut:].strip()
    return [s for s in out if s]


def estimate_duration_sec(text: str, *, lang: str = "zh", cps: float | None = None) -> float:
    """Heuristic duration before synthesis; real duration comes from probing audio."""
    t = (text or "").strip()
    if not t:
        return 0.4
    rate = cps if cps is not None else DEFAULT_CPS.get(lang, DEFAULT_CPS["zh"])
    # Count CJK as 1, latin words roughly via char/rate
    return max(0.5, round(len(t) / max(rate, 0.5) + 0.15, 3))


def probe_audio_duration(path: Path) -> float:
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
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    if dur <= 0:
        raise RuntimeError(f"invalid audio duration for {path}")
    return round(dur, 3)


def _ffmpeg_tone(path: Path, duration_sec: float, *, freq: float = 220.0) -> None:
    """Generate a soft placeholder WAV of exact length (mock TTS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.4, float(duration_sec))
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=44100:duration={dur}",
        "-af",
        "volume=0.15",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(f"mock TTS ffmpeg failed: {proc.stderr[-400:]}")


def _say_to_wav(text: str, path: Path, *, voice: str | None = None) -> None:
    """macOS ``say`` → AIFF → WAV. Raises if ``say`` missing."""
    if not shutil.which("say"):
        raise RuntimeError("say binary not found (macOS only)")
    path.parent.mkdir(parents=True, exist_ok=True)
    aiff = path.with_suffix(".aiff")
    cmd = ["say", "-o", str(aiff)]
    if voice:
        cmd.extend(["-v", voice])
    cmd.append(text)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not aiff.is_file():
        raise RuntimeError(f"say failed: {proc.stderr.strip() or proc.stdout.strip()}")
    conv = [
        "ffmpeg",
        "-y",
        "-i",
        str(aiff),
        "-ar",
        "44100",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    cproc = subprocess.run(conv, capture_output=True, text=True, check=False)
    aiff.unlink(missing_ok=True)
    if cproc.returncode != 0 or not path.is_file():
        raise RuntimeError(f"say→wav ffmpeg failed: {cproc.stderr[-400:]}")


def synthesize_script(
    script: str,
    out_dir: Path,
    *,
    lang: str = "zh",
    provider: ProviderName = "mock",
    voice: str | None = None,
    cps: float | None = None,
    write_manifest: bool = True,
) -> NarrationResult:
    """Turn a full script into segment WAVs + real durations.

    ``provider=mock``: ffmpeg tone sized by estimate (deterministic, offline).
    ``provider=say``: macOS speech (falls back to mock if say fails and allow_fallback).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = split_script(script)
    if not chunks:
        chunks = ["（无旁白）"]

    segments: list[NarrationSegment] = []
    used_provider = provider
    for i, text in enumerate(chunks):
        est = estimate_duration_sec(text, lang=lang, cps=cps)
        wav = out_dir / f"seg_{i:03d}_{lang}.wav"
        if provider == "say":
            try:
                _say_to_wav(text, wav, voice=voice)
            except RuntimeError:
                used_provider = "mock"
                _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        else:
            _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        real = probe_audio_duration(wav)
        segments.append(
            NarrationSegment(
                index=i,
                text=text,
                lang=lang,
                audio_path=str(wav.resolve()),
                duration_sec=real,
                estimated_sec=est,
            )
        )

    total = round(sum(s.duration_sec for s in segments), 3)
    result = NarrationResult(
        segments=segments,
        total_duration_sec=total,
        provider=used_provider,
        lang=lang,
        out_dir=str(out_dir.resolve()),
        script_text=script,
        extras={"requested_provider": provider},
    )
    if write_manifest:
        man_path = out_dir / "narration_manifest.json"
        man_path.write_text(
            json.dumps(result.to_manifest(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result.manifest_path = str(man_path.resolve())
    return result


def concat_narration_audio(segment_paths: list[Path], out_path: Path) -> Path:
    """Concatenate segment WAVs into one continuous narration bed."""
    paths = [Path(p) for p in segment_paths if Path(p).is_file()]
    if not paths:
        raise ValueError("no narration segments to concat")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        if paths[0].resolve() != out_path.resolve():
            shutil.copy2(paths[0], out_path)
        return out_path
    list_file = out_path.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in paths),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "1",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"narration concat failed: {proc.stderr[-400:]}")
    return out_path


def narration_bed_from_result(result: NarrationResult, out_path: Path) -> Path:
    """Build a single WAV bed from synthesize_script output."""
    segs = [Path(s.audio_path) for s in result.segments]
    return concat_narration_audio(segs, out_path)
