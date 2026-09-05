"""G4 TTS adapter — script → segment audio + real durations (length drives cut).

Providers are pluggable; default prefers Edge 晓晓 (``edge``), then macOS ``say``,
then ``mock`` (ffmpeg tone). Optional local ``clone`` (F5-TTS voice pack).
No customer-name branches.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderName = Literal["mock", "say", "edge", "clone"]

# Wall-clock + progress guards for Edge TTS (Microsoft WS can stall with 0B mp3).
EDGE_TTS_ATTEMPT_SEC = 45.0
EDGE_TTS_MAX_ATTEMPTS = 2
EDGE_TTS_ZERO_BYTE_SEC = 8.0
EDGE_TTS_MIN_AUDIO_BYTES = 64

# Subprocess body: isolated so hung edge-tts sockets can be SIGKILL'd.
_EDGE_WORKER_SNIPPET = r"""
import asyncio, json, sys
from pathlib import Path
cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
try:
    import edge_tts
    async def _run():
        communicate = edge_tts.Communicate(
            cfg["text"],
            cfg["voice"],
            rate=cfg.get("rate") or "-8%",
            pitch=cfg.get("pitch") or "+35Hz",
            volume=cfg.get("volume") or "+0%",
        )
        await communicate.save(cfg["mp3"])
    asyncio.run(_run())
    print(json.dumps({"ok": True}))
except Exception as e:
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
    raise SystemExit(1)
"""

# Rough spoken pace (chars / sec). Overridden via opts / settings.
DEFAULT_CPS: dict[str, float] = {
    "zh": 3.8,
    "zh-TW": 3.8,
    "en": 13.0,
    "ja": 7.0,
    "ko": 7.0,
    "es": 12.0,
    "pt": 12.0,
    "fr": 12.0,
    "de": 11.0,
    "it": 12.0,
    "ru": 11.0,
    "ar": 10.0,
    "hi": 10.0,
    "th": 8.0,
    "vi": 10.0,
    "id": 11.0,
    "ms": 11.0,
    "tr": 11.0,
    "pl": 11.0,
    "nl": 12.0,
}

DEFAULT_EDGE_VOICE = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "es": "es-ES-ElviraNeural",
    "pt": "pt-BR-FranciscaNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "id": "id-ID-GadisNeural",
    "ms": "ms-MY-YasminNeural",
    "tr": "tr-TR-EmelNeural",
    "pl": "pl-PL-ZofiaNeural",
    "nl": "nl-NL-FennaNeural",
}
DEFAULT_EDGE_RATE = "-8%"
DEFAULT_EDGE_PITCH = "+35Hz"
DEFAULT_EDGE_VOLUME = "+12%"


@dataclass
class NarrationSegment:
    index: int
    text: str
    lang: str
    audio_path: str
    duration_sec: float
    estimated_sec: float
    # Absolute cue window on the final voiceover bed (oneshot). When set, SRT
    # MUST use these — duration_sec is speech-only and must NOT include silence.
    start_sec: float | None = None
    end_sec: float | None = None


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
_SENTENCE_SPLIT_ZH = re.compile(r"(?<=[。！？\n])\s*")


def split_script(text: str, *, max_chars: int = 80, lang: str = "zh") -> list[str]:
    """Split narration into speakable segments (sentence-ish, then length clamp)."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    # CJK: restore short breaths before split (Ollama often returns run-ons)
    if str(lang).startswith("zh"):
        from engine.pack.text_sanitize import ensure_zh_speech_breaks

        # Default breath ≤18 chars so Edge pauses; caller may pass larger max_chars
        breath_cap = min(int(max_chars), 18) if max_chars else 18
        raw = ensure_zh_speech_breaks(raw, max_chars=breath_cap)
    # CJK sentence ends for zh/ja/ko; Thai/others use .!? as well (foreign templates)
    splitter = _SENTENCE_SPLIT_ZH if lang.startswith("zh") or lang in ("ja", "ko") else _SENTENCE_SPLIT
    parts = [p.strip() for p in splitter.split(raw) if p and p.strip()]
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


def _atempo_filter_chain(speed: float) -> str:
    """Build an ffmpeg atempo chain. Each atempo must stay in [0.5, 2.0]."""
    s = float(speed)
    if s <= 0:
        raise ValueError(f"invalid atempo speed: {speed}")
    parts: list[str] = []
    # Speed up (s > 1): peel 2.0 factors until remainder in range.
    while s > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        s /= 2.0
    # Slow down (s < 1): peel 0.5 factors if ever needed.
    while s < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def fit_narration_to_picture_duration(
    bed_path: Path,
    *,
    picture_duration_sec: float,
    segments: list[NarrationSegment] | None = None,
    total_duration_holder: list[float] | None = None,
    max_speed: float = 1.35,
    min_tail_sec: float = 0.15,
    min_apply_speed: float = 1.02,
) -> dict[str, Any]:
    """Time-compress a VO bed so speech finishes before picture ends.

    When narration is longer than the planned picture, prefer adaptive tempo
    over freeze-padding the last frame (still keeps L15: final > narration).

    Max speed default 1.35×; residual overage is left for a short freeze only
    as fail-closed fallback (caller/ffmpeg still may pad if needed).
    """
    bed = Path(bed_path)
    out: dict[str, Any] = {
        "applied": False,
        "path": str(bed.resolve()) if bed.is_file() else str(bed),
        "picture_duration_sec": float(picture_duration_sec or 0),
        "before_sec": 0.0,
        "after_sec": 0.0,
        "target_sec": 0.0,
        "speed": 1.0,
        "max_speed": float(max_speed),
        "min_tail_sec": float(min_tail_sec),
        "reason": "",
    }
    pic = float(picture_duration_sec or 0)
    if pic < 0.5 or not bed.is_file():
        out["reason"] = "skip_no_picture_or_bed"
        return out
    try:
        before = float(probe_audio_duration(bed))
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"probe_failed:{e}"
        return out
    out["before_sec"] = before
    # Leave a short silence window after speech so L15 can pass without freeze pad.
    target = max(0.5, pic - max(0.05, float(min_tail_sec)))
    out["target_sec"] = round(target, 3)
    if before <= target + 0.04:
        out["after_sec"] = before
        out["reason"] = "already_fits"
        return out
    speed = before / target
    cap = max(1.0, float(max_speed))
    if speed > cap:
        speed = cap
    if speed < float(min_apply_speed):
        out["after_sec"] = before
        out["reason"] = "speed_below_threshold"
        return out
    out["speed"] = round(speed, 4)
    af = _atempo_filter_chain(speed)
    tmp = bed.with_suffix(bed.suffix + ".fit_tmp.wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(bed),
        "-af",
        af,
        "-c:a",
        "pcm_s16le",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 64:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        out["after_sec"] = before
        out["reason"] = f"ffmpeg_failed:{(proc.stderr or '')[-400:]}"
        return out
    try:
        after = float(probe_audio_duration(tmp))
    except Exception as e:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        out["after_sec"] = before
        out["reason"] = f"probe_after_failed:{e}"
        return out
    # Atomically replace bed
    try:
        os.replace(tmp, bed)
    except OSError:
        shutil.move(str(tmp), str(bed))
    out["after_sec"] = after
    out["applied"] = True
    out["path"] = str(bed.resolve())
    out["reason"] = "tempo_fit"
    # Scale absolute cue times so SRT still tracks compressed speech.
    scale = after / before if before > 0.01 else (1.0 / speed)
    if segments:
        for seg in segments:
            if seg.start_sec is not None:
                seg.start_sec = round(float(seg.start_sec) * scale, 3)
            if seg.end_sec is not None:
                seg.end_sec = round(float(seg.end_sec) * scale, 3)
            if seg.duration_sec:
                seg.duration_sec = round(float(seg.duration_sec) * scale, 3)
    if total_duration_holder is not None:
        total_duration_holder.clear()
        total_duration_holder.append(float(after))
    return out


def force_narration_within_picture(
    bed_path: Path,
    *,
    picture_duration_sec: float,
    segments: list[NarrationSegment] | None = None,
    max_speed: float = 1.55,
    min_tail_sec: float = 0.06,
) -> dict[str, Any]:
    """跟镜硬对齐：先 tempo-fit，仍超片源则 atrim，保证旁白不跨镜。"""
    bed = Path(bed_path)
    meta = fit_narration_to_picture_duration(
        bed,
        picture_duration_sec=picture_duration_sec,
        segments=segments,
        max_speed=max_speed,
        min_tail_sec=min_tail_sec,
        min_apply_speed=1.01,
    )
    pic = float(picture_duration_sec or 0)
    target = max(0.45, pic - max(0.04, float(min_tail_sec)))
    try:
        after = float(probe_audio_duration(bed)) if bed.is_file() else 0.0
    except Exception:  # noqa: BLE001
        after = float(meta.get("after_sec") or 0)
    meta["after_sec"] = after
    if after <= target + 0.06 or not bed.is_file() or pic < 0.5:
        meta["forced_trim"] = False
        return meta
    tmp = bed.with_suffix(bed.suffix + ".atrim_tmp.wav")
    # atrim + 极短淡出，避免咔哒；时长硬锁在 target
    af = f"atrim=0:{target:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.05, target - 0.05):.3f}:d=0.05"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(bed),
        "-af",
        af,
        "-c:a",
        "pcm_s16le",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 64:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        meta["forced_trim"] = False
        meta["reason"] = f"{meta.get('reason')}|atrim_failed"
        return meta
    try:
        os.replace(tmp, bed)
    except OSError:
        shutil.move(str(tmp), str(bed))
    try:
        trimmed = float(probe_audio_duration(bed))
    except Exception:  # noqa: BLE001
        trimmed = target
    scale = trimmed / after if after > 0.01 else (trimmed / max(after, 0.01))
    if segments and 0.2 <= scale <= 1.0:
        for seg in segments:
            if seg.start_sec is not None:
                seg.start_sec = round(min(float(seg.start_sec) * scale, trimmed), 3)
            if seg.end_sec is not None:
                seg.end_sec = round(min(float(seg.end_sec) * scale, trimmed), 3)
            if seg.duration_sec:
                seg.duration_sec = round(max(0.05, float(seg.end_sec or 0) - float(seg.start_sec or 0)), 3)
            # drop cues that start past trim
    if segments:
        keep: list[NarrationSegment] = []
        for seg in list(segments):
            if float(seg.start_sec or 0) >= trimmed - 0.04:
                continue
            if seg.end_sec is not None and float(seg.end_sec) > trimmed:
                seg.end_sec = round(trimmed, 3)
                seg.duration_sec = round(max(0.05, trimmed - float(seg.start_sec or 0)), 3)
            keep.append(seg)
        segments[:] = keep
    meta["after_sec"] = trimmed
    meta["applied"] = True
    meta["forced_trim"] = True
    meta["reason"] = f"{meta.get('reason')}|atrim_lock"
    return meta
    if total_duration_holder is not None:
        total_duration_holder.clear()
        total_duration_holder.append(after)
    return out


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


def _kill_edge_proc(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        pass


def _edge_save_mp3_killable(
    text: str,
    mp3: Path,
    *,
    voice_id: str,
    rate: str,
    pitch: str,
    volume: str,
    timeout_sec: float = EDGE_TTS_ATTEMPT_SEC,
) -> None:
    """Run edge-tts in a child process so hung WebSockets can be killed on wall-clock timeout."""
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.unlink(missing_ok=True)
    cfg = {
        "text": text,
        "voice": voice_id,
        "rate": rate or DEFAULT_EDGE_RATE,
        "pitch": pitch or DEFAULT_EDGE_PITCH,
        "volume": volume or "+0%",
        "mp3": str(mp3),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(cfg, handle, ensure_ascii=False)
        payload_path = handle.name
    proc: subprocess.Popen[str] | None = None
    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_sec))
    zero_deadline = started + max(1.0, float(EDGE_TTS_ZERO_BYTE_SEC))
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _EDGE_WORKER_SNIPPET, payload_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            if now >= deadline:
                _kill_edge_proc(proc)
                mp3.unlink(missing_ok=True)
                raise TimeoutError(f"edge_tts_timeout ({timeout_sec:.0f}s)")
            # Stalled Microsoft WS: 0-byte file for several seconds → bail early.
            if now >= zero_deadline:
                size = mp3.stat().st_size if mp3.is_file() else 0
                if size < EDGE_TTS_MIN_AUDIO_BYTES:
                    _kill_edge_proc(proc)
                    mp3.unlink(missing_ok=True)
                    raise TimeoutError(
                        f"edge_tts_timeout (0B for {EDGE_TTS_ZERO_BYTE_SEC:.0f}s)"
                    )
            time.sleep(0.1)
        stdout, stderr = proc.communicate(timeout=2)
        if proc.returncode != 0:
            detail = (stderr or stdout or "").strip()[:300] or f"exit_{proc.returncode}"
            mp3.unlink(missing_ok=True)
            raise RuntimeError(f"edge_tts_failed: {detail}")
        if not mp3.is_file() or mp3.stat().st_size < EDGE_TTS_MIN_AUDIO_BYTES:
            mp3.unlink(missing_ok=True)
            raise RuntimeError("edge_tts_empty")
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass
        if proc is not None and proc.poll() is None:
            _kill_edge_proc(proc)


def _edge_to_wav(
    text: str,
    path: Path,
    *,
    voice: str | None = None,
    lang: str = "zh",
    rate: str = DEFAULT_EDGE_RATE,
    pitch: str = DEFAULT_EDGE_PITCH,
    volume: str = "+0%",
    retries: int = EDGE_TTS_MAX_ATTEMPTS,
) -> None:
    """Edge TTS (晓晓 default) → MP3 → WAV. Killable wall-clock + 0B stall guard."""
    try:
        import edge_tts  # noqa: F401
    except ImportError as e:
        raise RuntimeError("edge-tts not installed") from e

    path.parent.mkdir(parents=True, exist_ok=True)
    voice_id = (voice or "").strip() or DEFAULT_EDGE_VOICE.get(
        lang if lang in DEFAULT_EDGE_VOICE else ("zh" if str(lang).startswith("zh") else "en"),
        DEFAULT_EDGE_VOICE["zh"],
    )
    # If caller passed a macOS say voice name, fall back to Edge default
    known_prefixes = (
        "zh-", "en-", "ja-", "ko-", "es-", "pt-", "fr-", "de-", "it-", "ru-",
        "ar-", "hi-", "th-", "vi-", "id-", "ms-", "tr-", "pl-", "nl-",
    )
    if voice_id and not any(voice_id.startswith(p) for p in known_prefixes):
        voice_id = DEFAULT_EDGE_VOICE.get(
            lang if lang in DEFAULT_EDGE_VOICE else ("zh" if str(lang).startswith("zh") else "en"),
            DEFAULT_EDGE_VOICE["zh"],
        )

    mp3 = path.with_suffix(".mp3")
    last_err: Exception | None = None
    # Cap retries so stacked timeouts cannot drag a single item for minutes.
    attempts = max(1, min(int(retries), EDGE_TTS_MAX_ATTEMPTS))
    vol = volume or "+0%"

    for attempt in range(attempts):
        try:
            _edge_save_mp3_killable(
                text,
                mp3,
                voice_id=voice_id,
                rate=rate or DEFAULT_EDGE_RATE,
                pitch=pitch or DEFAULT_EDGE_PITCH,
                volume=vol,
                timeout_sec=EDGE_TTS_ATTEMPT_SEC,
            )
            last_err = None
            break
        except Exception as e:  # noqa: BLE001 — retry transient network
            last_err = e
            mp3.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(1.2 * (attempt + 1))
            continue

    if last_err is not None:
        raise RuntimeError(f"edge-tts failed after {attempts} attempts: {last_err}") from last_err

    conv = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3),
        "-ar",
        "44100",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    cproc = subprocess.run(conv, capture_output=True, text=True, check=False, timeout=60)
    mp3.unlink(missing_ok=True)
    if cproc.returncode != 0 or not path.is_file():
        raise RuntimeError(f"edge→wav ffmpeg failed: {cproc.stderr[-400:]}")


def synthesize_script(
    script: str,
    out_dir: Path,
    *,
    lang: str = "zh",
    provider: ProviderName = "mock",
    voice: str | None = None,
    cps: float | None = None,
    write_manifest: bool = True,
    rate: str | None = None,
    pitch: str | None = None,
    volume: str | None = None,
    strip_punctuation: bool = False,
    allow_fallback: bool = True,
    inter_sentence_gap_sec: float = 0.15,
    clone_pack: Any | None = None,
    clone_pack_id: str | None = None,
    clone_ref_wav: str | Path | None = None,
    clone_ref_text: str | None = None,
    clone_speed: float | None = None,
) -> NarrationResult:
    """Turn a full script into segment WAVs + real durations.

    Prosody / 断句 for locked Edge zh:
    1) Prefer **one network call** with ``。`` between sentences (晓晓 keeps tone + pauses)
    2) Split the bed by silence so SRT tracks real pauses
    3) Fall back to per-sentence synthesis (with resume) if oneshot fails
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # zh lock: re-punctuate → split on 。 → strip marks for spoken chunk text.
    # Order matters: never strip before split (would destroy 断句).
    effective_rate = rate or DEFAULT_EDGE_RATE
    effective_pitch = pitch or DEFAULT_EDGE_PITCH
    effective_volume = volume or DEFAULT_EDGE_VOLUME
    if str(lang).startswith("zh") and strip_punctuation:
        from engine.pack.text_sanitize import ensure_zh_speech_breaks, split_then_strip_sentences

        punctuated = ensure_zh_speech_breaks(script, max_chars=18)
        chunks = split_then_strip_sentences(punctuated)
    else:
        chunks = split_script(script, lang=lang, max_chars=18 if str(lang).startswith("zh") else 80)
        if strip_punctuation:
            from engine.pack.text_sanitize import strip_all_punctuation

            chunks = [strip_all_punctuation(c) for c in chunks]
            chunks = [c for c in chunks if c]
    if not chunks:
        chunks = ["无旁白"] if strip_punctuation else ["（无旁白）"]

    def _edge_speak_text(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return t
        if str(lang).startswith("zh"):
            return t if t[-1] in "。！？!?" else t + "。"
        return t if t[-1] in ".!?" else t + "."

    gap = max(0.0, float(inter_sentence_gap_sec))
    oneshot_err: str | None = None

    # --- Path A: punctuated oneshot (best 语气/断句 + one Edge round-trip) ---
    if provider == "edge" and str(lang).startswith("zh") and len(chunks) > 1:
        speak_full = "。".join(chunks) + "。"
        oneshot = out_dir / f"oneshot_{lang}.wav"
        try:
            _edge_to_wav(
                speak_full,
                oneshot,
                voice=voice,
                lang=lang,
                rate=effective_rate,
                pitch=effective_pitch,
                volume=effective_volume,
                retries=EDGE_TTS_MAX_ATTEMPTS,
            )
            total_real = probe_audio_duration(oneshot)
            # Slice by silence, then merge spans → one group per sentence.
            # HARD: cue end = speech end (never absorb inter-sentence silence).
            raw_spans = _speech_spans_by_silence(oneshot, min_silence=0.08)
            grouped = _group_spans_for_chunks(raw_spans, chunks)
            segments: list[NarrationSegment] = []
            if grouped is not None:
                for i, (text, (st, en)) in enumerate(zip(chunks, grouped)):
                    speech_dur = max(0.12, float(en) - float(st))
                    segments.append(
                        NarrationSegment(
                            index=i,
                            text=text,
                            lang=lang,
                            audio_path=str(oneshot.resolve()),
                            duration_sec=round(speech_dur, 3),
                            estimated_sec=estimate_duration_sec(text, lang=lang, cps=cps),
                            start_sec=round(float(st), 3),
                            end_sec=round(float(en), 3),
                        )
                    )
            else:
                # Span count < sentences — proportional speech windows on bed.
                # Still leave measurable gaps so captions clear between cues.
                weights = [max(1, len(c)) for c in chunks]
                tw = float(sum(weights)) or 1.0
                gap_est = min(0.12, gap if gap > 0 else 0.08)
                usable = max(0.4, total_real - gap_est * max(0, len(chunks) - 1))
                cursor = 0.0
                for i, (text, w) in enumerate(zip(chunks, weights)):
                    speech_dur = max(0.12, usable * (w / tw))
                    st = cursor
                    en = min(total_real, st + speech_dur)
                    segments.append(
                        NarrationSegment(
                            index=i,
                            text=text,
                            lang=lang,
                            audio_path=str(oneshot.resolve()),
                            duration_sec=round(en - st, 3),
                            estimated_sec=estimate_duration_sec(text, lang=lang, cps=cps),
                            start_sec=round(st, 3),
                            end_sec=round(en, 3),
                        )
                    )
                    cursor = en + (gap_est if i + 1 < len(chunks) else 0.0)
            result = NarrationResult(
                segments=segments,
                total_duration_sec=round(total_real, 3),
                provider="edge",
                lang=lang,
                out_dir=str(out_dir.resolve()),
                script_text=script,
                extras={
                    "requested_provider": provider,
                    "voice": voice,
                    "rate": effective_rate,
                    "pitch": effective_pitch,
                    "volume": effective_volume,
                    "allow_fallback": allow_fallback,
                    "mode": "oneshot_punctuated",
                    "oneshot_path": str(oneshot.resolve()),
                    "inter_sentence_gap_sec": 0.0,  # pauses already inside oneshot
                    "silence_spans": len(raw_spans),
                    "grouped_spans": len(grouped or []),
                    "chunk_count": len(chunks),
                    "cue_timing": "speech_absolute",
                    "max_chars_per_breath": 18,
                },
            )
            if write_manifest:
                man_path = out_dir / "narration_manifest.json"
                man_path.write_text(
                    json.dumps(result.to_manifest(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result.manifest_path = str(man_path.resolve())
            return result
        except Exception as e:
            oneshot_err = str(e)

    # --- Path B: per-sentence (Edge / clone / say / mock) ---
    segments = []
    used_provider = provider
    edge_errors: list[str] = []
    clone_meta: dict[str, Any] = {}
    resolved_clone = None
    from engine.pack.voice_clone import is_clone_provider, resolve_voice_pack

    if is_clone_provider(provider):
        provider = "clone"  # type: ignore[assignment]
        used_provider = "clone"
        if clone_pack is not None and hasattr(clone_pack, "ref_wav"):
            resolved_clone = clone_pack
        else:
            resolved_clone = resolve_voice_pack(
                pack_id=clone_pack_id or voice,
                ref_wav=clone_ref_wav,
                ref_text=clone_ref_text,
            )
        if clone_speed is not None:
            from engine.pack.voice_clone import VoicePack as _VP

            resolved_clone = _VP(
                id=resolved_clone.id,
                label=resolved_clone.label,
                ref_wav=resolved_clone.ref_wav,
                ref_text=resolved_clone.ref_text,
                speed=float(clone_speed),
                chars_per_sec_zh=resolved_clone.chars_per_sec_zh,
                engine=resolved_clone.engine,
                root=resolved_clone.root,
            )
        clone_meta = resolved_clone.to_dict()
        if cps is None:
            cps = float(resolved_clone.chars_per_sec_zh or 3.3)

    if oneshot_err:
        edge_errors.append(f"oneshot_failed: {oneshot_err}")
    for i, text in enumerate(chunks):
        est = estimate_duration_sec(text, lang=lang, cps=cps)
        wav = out_dir / f"seg_{i:03d}_{lang}.wav"
        # Resume: keep already-good segment WAVs across retries / network flaps
        if (
            provider in ("edge", "clone")
            and wav.is_file()
            and wav.stat().st_size > 1000
            and probe_audio_duration(wav) > 0.2
        ):
            real = _trim_wav_trailing_silence(wav)
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
            continue
        if provider == "edge":
            try:
                _edge_to_wav(
                    _edge_speak_text(text),
                    wav,
                    voice=voice,
                    lang=lang,
                    rate=effective_rate,
                    pitch=effective_pitch,
                    volume=effective_volume,
                    retries=5 if not allow_fallback else 3,
                )
                if i + 1 < len(chunks):
                    import time

                    time.sleep(0.25)
            except Exception as e:
                edge_errors.append(str(e))
                if not allow_fallback:
                    raise RuntimeError(
                        f"Edge TTS required (no fallback). segment={i} err={e}"
                    ) from e
                try:
                    if shutil.which("say"):
                        _say_to_wav(text, wav, voice=None)
                        used_provider = "say"
                    else:
                        raise RuntimeError("no say")
                except Exception:
                    used_provider = "mock"
                    _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        elif provider == "clone":
            from engine.pack.voice_clone import clone_utterance_to_wav

            assert resolved_clone is not None
            try:
                speak = text if not str(lang).startswith("zh") else (
                    text if text[-1:] in "。！？!?" else text + "。"
                )
                clone_utterance_to_wav(speak, wav, resolved_clone, seed=1000 + i)
            except Exception as e:
                edge_errors.append(f"clone:{e}")
                if not allow_fallback:
                    raise RuntimeError(
                        f"Clone TTS required (no fallback). segment={i} err={e}"
                    ) from e
                used_provider = "mock"
                _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        elif provider == "say":
            try:
                _say_to_wav(text, wav, voice=voice)
            except RuntimeError:
                used_provider = "mock"
                _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        else:
            _ffmpeg_tone(wav, est, freq=200 + (i % 5) * 20)
        real = _trim_wav_trailing_silence(wav)
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

    spoken = sum(s.duration_sec for s in segments)
    total = round(spoken + (gap * max(0, len(segments) - 1)), 3)
    result = NarrationResult(
        segments=segments,
        total_duration_sec=total,
        provider=used_provider,
        lang=lang,
        out_dir=str(out_dir.resolve()),
        script_text=script,
        extras={
            "requested_provider": provider,
            "voice": voice,
            "allow_fallback": allow_fallback,
            "mode": "per_sentence",
            "inter_sentence_gap_sec": gap,
            "edge_errors": edge_errors[:5],
            **({"clone_pack": clone_meta} if clone_meta else {}),
        },
    )
    if write_manifest:
        man_path = out_dir / "narration_manifest.json"
        man_path.write_text(
            json.dumps(result.to_manifest(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result.manifest_path = str(man_path.resolve())
    return result


def _group_spans_for_chunks(
    spans: list[tuple[float, float]],
    chunks: list[str],
) -> list[tuple[float, float]] | None:
    """Merge consecutive speech spans into len(chunks) groups by char weight.

    Returns None when grouping is impossible (fewer spans than sentences).
    HARD RULE: each group end is the last speech sample — never includes the
    silence gap before the next sentence (字幕不得拖过话音).
    """
    n = len(chunks)
    if n <= 0 or not spans:
        return None
    if len(spans) == n:
        return [(float(s), float(e)) for s, e in spans]
    if len(spans) < n:
        return None
    weights = [max(1, len((c or "").strip())) for c in chunks]
    tw = float(sum(weights)) or 1.0
    speech_lens = [max(0.01, float(e) - float(s)) for s, e in spans]
    total_speech = float(sum(speech_lens))
    # Cumulative speech-duration targets at each group boundary
    boundaries: list[float] = []
    acc_w = 0.0
    for w in weights[:-1]:
        acc_w += w
        boundaries.append(total_speech * (acc_w / tw))
    groups: list[tuple[float, float]] = []
    span_i = 0
    spoken_before = 0.0
    for b_i, boundary in enumerate(boundaries):
        remaining_groups_after = n - len(groups) - 1
        if span_i >= len(spans):
            break
        g_start = float(spans[span_i][0])
        g_end = float(spans[span_i][1])
        spoken_in = speech_lens[span_i]
        span_i += 1
        while span_i < len(spans) - remaining_groups_after:
            # Stop absorbing once we reached/passed this group's speech budget
            if spoken_before + spoken_in >= boundary * 0.92 and spoken_in >= 0.2:
                break
            g_end = float(spans[span_i][1])
            spoken_in += speech_lens[span_i]
            span_i += 1
        groups.append((g_start, g_end))
        spoken_before += spoken_in
    # Last group takes all remaining spans
    if span_i < len(spans):
        groups.append((float(spans[span_i][0]), float(spans[-1][1])))
    elif len(groups) < n and spans:
        # Degenerate: pad with tiny tails from last span end
        last_end = float(spans[-1][1])
        while len(groups) < n:
            groups.append((max(0.0, last_end - 0.2), last_end))
    return groups if len(groups) == n else None


def _speech_end_on_wav(wav: Path, *, fallback: float) -> float:
    """Last audible speech end on a clip WAV (trim TTS trailing silence)."""
    spans = _speech_spans_by_silence(wav, min_silence=0.06)
    if not spans:
        return float(fallback)
    end = float(spans[-1][1])
    # Never exceed file length; keep a tiny floor so ultra-short cues still show
    return max(0.12, min(float(fallback), end))


def _trim_wav_trailing_silence(wav: Path, *, pad_sec: float = 0.04) -> float:
    """Rewrite WAV so trailing TTS silence is removed; return new duration."""
    wav = Path(wav)
    full = probe_audio_duration(wav)
    if full <= 0.2:
        return full
    speech_end = _speech_end_on_wav(wav, fallback=full)
    target = min(full, speech_end + max(0.0, pad_sec))
    if full - target < 0.08:
        return full
    tmp = wav.with_suffix(".trim.wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav),
        "-t",
        f"{target:.3f}",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "1",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return full
    tmp.replace(wav)
    return probe_audio_duration(wav)


def _speech_spans_by_silence(wav: Path, *, min_silence: float = 0.08) -> list[tuple[float, float]]:
    """Return [(start, end), ...] speech spans using ffmpeg silencedetect."""
    wav = Path(wav)
    dur = probe_audio_duration(wav)
    if dur <= 0.2:
        return [(0.0, max(dur, 0.2))]
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(wav),
        "-af",
        f"silencedetect=noise=-35dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log = (proc.stderr or "") + (proc.stdout or "")
    silence_starts: list[float] = []
    silence_ends: list[float] = []
    for line in log.splitlines():
        if "silence_start:" in line:
            try:
                silence_starts.append(float(line.split("silence_start:")[-1].strip().split()[0]))
            except ValueError:
                pass
        if "silence_end:" in line:
            try:
                # silence_end: 1.23 | silence_duration: 0.15
                silence_ends.append(float(line.split("silence_end:")[-1].strip().split()[0]))
            except ValueError:
                pass
    # Build speech spans between silences
    spans: list[tuple[float, float]] = []
    t = 0.0
    # Pair ends with starts
    ends = list(silence_ends)
    starts = list(silence_starts)
    # If first silence starts mid-file, speech is 0..start
    idx_s = 0
    idx_e = 0
    cursor = 0.0
    events: list[tuple[float, str]] = []
    for s in starts:
        events.append((s, "start"))
    for e in ends:
        events.append((e, "end"))
    events.sort(key=lambda x: x[0])
    in_silence = False
    speech_start = 0.0
    for ts, kind in events:
        if kind == "start" and not in_silence:
            if ts - speech_start > 0.12:
                spans.append((speech_start, ts))
            in_silence = True
        elif kind == "end" and in_silence:
            speech_start = ts
            in_silence = False
    if not in_silence and dur - speech_start > 0.12:
        spans.append((speech_start, dur))
    if not spans:
        spans = [(0.0, dur)]
    return spans


def _slice_wav(src: Path, dest: Path, *, start: float, duration: float) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.12, duration):.3f}",
        "-i",
        str(src),
        "-ar",
        "44100",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"slice wav failed: {proc.stderr[-300:]}")


def _make_silence_wav(path: Path, *, duration_sec: float, sample_rate: int = 44100) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.04, float(duration_sec))
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl=mono",
        "-t",
        f"{dur:.3f}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(f"silence wav failed: {proc.stderr[-300:]}")
    return path


def concat_narration_audio(
    segment_paths: list[Path],
    out_path: Path,
    *,
    gap_sec: float = 0.0,
) -> Path:
    """Concatenate segment WAVs; optional silence between cues for natural 断句."""
    paths = [Path(p) for p in segment_paths if Path(p).is_file()]
    if not paths:
        raise ValueError("no narration segments to concat")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1 and gap_sec <= 0:
        if paths[0].resolve() != out_path.resolve():
            shutil.copy2(paths[0], out_path)
        return out_path

    work = out_path.parent / f"{out_path.stem}_concat_parts"
    work.mkdir(parents=True, exist_ok=True)
    ordered: list[Path] = []
    silence: Path | None = None
    if gap_sec > 0.02 and len(paths) > 1:
        silence = _make_silence_wav(work / "gap.wav", duration_sec=gap_sec)
    for i, p in enumerate(paths):
        ordered.append(p)
        if silence is not None and i < len(paths) - 1:
            ordered.append(silence)

    list_file = out_path.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in ordered),
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
    if silence is not None:
        silence.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"narration concat failed: {proc.stderr[-400:]}")
    return out_path


def synthesize_script_clip_slots(
    slots: list[dict[str, Any]],
    out_dir: Path,
    *,
    lang: str = "zh",
    provider: ProviderName = "mock",
    voice: str | None = None,
    cps: float | None = None,
    rate: str | None = None,
    pitch: str | None = None,
    volume: str | None = None,
    strip_punctuation: bool = False,
    allow_fallback: bool = True,
    inter_sentence_gap_sec: float = 0.12,
    clone_pack: Any | None = None,
    clone_pack_id: str | None = None,
    clone_ref_wav: str | Path | None = None,
    clone_ref_text: str | None = None,
    clone_speed: float | None = None,
    pad_to_duration: bool = True,
) -> NarrationResult:
    """Synthesize one VO block per picture slot; pad silence so each block ends near the cut.

    ``slots`` items: {duration_sec, text}. Speech for slot i only describes slot i's goods.
    When ``pad_to_duration`` is False (跟镜精品), do not pad — caller sets mirror length
    to the measured speech so 镜切=句切.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bed_parts: list[Path] = []
    all_segments: list[NarrationSegment] = []
    cursor = 0.0
    script_parts: list[str] = []
    slot_speech_sec: list[float] = []
    gap = max(0.0, float(inter_sentence_gap_sec))
    provider_used = str(provider)
    for i, slot in enumerate(slots or []):
        if not isinstance(slot, dict):
            continue
        target = max(0.6, float(slot.get("duration_sec") or 0) or 4.0)
        text = str(slot.get("text") or "").strip()
        script_parts.append(text)
        slot_dir = out_dir / f"slot_{i:02d}"
        slot_dir.mkdir(parents=True, exist_ok=True)
        if not text:
            sil_dur = target if pad_to_duration else 0.05
            sil = _make_silence_wav(slot_dir / "empty.wav", duration_sec=sil_dur)
            bed_parts.append(sil)
            slot_speech_sec.append(0.0)
            cursor += sil_dur
            continue
        narr = synthesize_script(
            text,
            slot_dir / "narr",
            lang=lang,
            provider=provider,
            voice=voice,
            cps=cps,
            rate=rate,
            pitch=pitch,
            volume=volume,
            strip_punctuation=strip_punctuation,
            allow_fallback=allow_fallback,
            inter_sentence_gap_sec=gap,
            clone_pack=clone_pack,
            clone_pack_id=clone_pack_id,
            clone_ref_wav=clone_ref_wav,
            clone_ref_text=clone_ref_text,
            clone_speed=clone_speed,
        )
        provider_used = narr.provider or provider_used
        speech_bed = slot_dir / "speech.wav"
        narration_bed_from_result(narr, speech_bed, gap_sec=gap)
        speech_dur = float(narr.total_duration_sec or probe_audio_duration(speech_bed) or 0.0)
        # 跟镜：单镜旁白不得超过片源；先压速，仍超则硬裁，禁止跨镜
        max_d = float(slot.get("max_duration_sec") or 0) or 0.0
        if max_d > 0.8 and speech_dur > max_d - 0.05:
            try:
                # 过长：缩短文案再合成一次（优先保住雅述完整读完）
                if speech_dur > max_d * 1.25:
                    try:
                        from engine.pack.scene_tour_diction import (
                            max_chars_for_available,
                            shorten_ornate_line,
                        )

                        short = shorten_ornate_line(text, max_chars_for_available(max_d))
                        if short and short != text:
                            narr2 = synthesize_script(
                                short,
                                slot_dir / "narr_short",
                                lang=lang,
                                provider=provider,
                                voice=voice,
                                cps=cps,
                                rate=rate,
                                pitch=pitch,
                                volume=volume,
                                strip_punctuation=strip_punctuation,
                                allow_fallback=allow_fallback,
                                inter_sentence_gap_sec=gap,
                                clone_pack=clone_pack,
                                clone_pack_id=clone_pack_id,
                                clone_ref_wav=clone_ref_wav,
                                clone_ref_text=clone_ref_text,
                                clone_speed=clone_speed,
                            )
                            narration_bed_from_result(narr2, speech_bed, gap_sec=gap)
                            narr = narr2
                            text = short
                            script_parts[-1] = short
                            speech_dur = float(
                                narr.total_duration_sec or probe_audio_duration(speech_bed) or 0.0
                            )
                    except Exception:
                        pass
                if speech_dur > max_d - 0.05:
                    segs = list(narr.segments or [])
                    force_narration_within_picture(
                        speech_bed,
                        picture_duration_sec=max(0.6, max_d),
                        segments=segs,
                        max_speed=1.55,
                        min_tail_sec=0.06,
                    )
                    narr.segments = segs
                    speech_dur = float(probe_audio_duration(speech_bed) or speech_dur)
                    # 同步 segment 绝对本地时间到裁后时长
                    if narr.segments and speech_dur > 0.2:
                        last_end = max(float(s.end_sec or 0) for s in narr.segments) or speech_dur
                        if last_end > speech_dur + 0.05:
                            scale = speech_dur / last_end
                            for seg in narr.segments:
                                if seg.start_sec is not None:
                                    seg.start_sec = round(float(seg.start_sec) * scale, 3)
                                if seg.end_sec is not None:
                                    seg.end_sec = round(min(speech_dur, float(seg.end_sec) * scale), 3)
                                if seg.duration_sec is not None:
                                    seg.duration_sec = round(
                                        max(0.05, float(seg.end_sec or 0) - float(seg.start_sec or 0)),
                                        3,
                                    )
            except Exception:
                pass
        slot_speech_sec.append(round(speech_dur, 3))
        # Place relative segment times onto absolute timeline
        local_cursor = 0.0
        for seg in narr.segments:
            st = float(seg.start_sec) if seg.start_sec is not None else local_cursor
            en = float(seg.end_sec) if seg.end_sec is not None else st + float(seg.duration_sec or 0)
            all_segments.append(
                NarrationSegment(
                    index=len(all_segments),
                    text=seg.text,
                    lang=seg.lang,
                    audio_path=seg.audio_path,
                    duration_sec=float(seg.duration_sec or max(0.05, en - st)),
                    estimated_sec=float(seg.estimated_sec or 0),
                    start_sec=round(cursor + st, 3),
                    end_sec=round(cursor + en, 3),
                )
            )
            local_cursor = max(local_cursor, en)
        pad = max(0.0, target - speech_dur) if pad_to_duration else 0.0
        if pad > 0.05:
            sil = _make_silence_wav(slot_dir / "pad.wav", duration_sec=pad)
            filled = slot_dir / "filled.wav"
            concat_narration_audio([speech_bed, sil], filled, gap_sec=0.0)
            bed_parts.append(filled)
            cursor += speech_dur + pad
        else:
            bed_parts.append(speech_bed)
            cursor += speech_dur
    if not bed_parts:
        raise ValueError("no product slot audio")
    final = out_dir / "clip_aligned.wav"
    concat_narration_audio(bed_parts, final, gap_sec=0.0)
    total = probe_audio_duration(final) or cursor
    return NarrationResult(
        segments=all_segments,
        total_duration_sec=float(total),
        provider=provider_used,
        lang=lang,
        out_dir=str(out_dir),
        script_text="".join(script_parts),
        extras={
            "mode": "clip_aligned_slots",
            "oneshot_path": str(final),
            "inter_sentence_gap_sec": 0.0,  # silences already baked into bed
            "slot_count": len(bed_parts),
            "slot_speech_sec": slot_speech_sec,
            "pad_to_duration": bool(pad_to_duration),
        },
    )


def narration_bed_from_result(
    result: NarrationResult,
    out_path: Path,
    *,
    gap_sec: float | None = None,
) -> Path:
    """Build a single WAV bed from synthesize_script output."""
    oneshot = (result.extras or {}).get("oneshot_path")
    mode = (result.extras or {}).get("mode")
    # oneshot bed is already the final timeline (including clip-aligned pads)
    if oneshot and Path(oneshot).is_file() and mode in (
        "oneshot_punctuated",
        "oneshot_edge",
        "clip_aligned_slots",
    ):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if Path(oneshot).resolve() != out_path.resolve():
            shutil.copy2(oneshot, out_path)
        return out_path
    segs = [Path(s.audio_path) for s in result.segments]
    # Default ~150ms breath between sentences for 晓晓 断句
    gap = 0.15 if gap_sec is None else float(gap_sec)
    if (result.extras or {}).get("inter_sentence_gap_sec") is not None:
        gap = float(result.extras["inter_sentence_gap_sec"])
    return concat_narration_audio(segs, out_path, gap_sec=gap)
