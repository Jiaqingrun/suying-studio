"""G4 TTS adapter — script → segment audio + real durations (length drives cut).

Providers are pluggable; default prefers Edge 晓晓 (``edge``), then macOS ``say``,
then ``mock`` (ffmpeg tone). No customer-name branches.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderName = Literal["mock", "say", "edge"]

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


def _edge_to_wav(
    text: str,
    path: Path,
    *,
    voice: str | None = None,
    lang: str = "zh",
    rate: str = DEFAULT_EDGE_RATE,
    pitch: str = DEFAULT_EDGE_PITCH,
    volume: str = "+0%",
    retries: int = 3,
) -> None:
    """Edge TTS (晓晓 default) → MP3 → WAV. Retries on transient network errors."""
    try:
        import edge_tts
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
    attempts = max(1, int(retries))
    vol = volume or "+0%"

    for attempt in range(attempts):
        async def _run() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice_id,
                rate=rate or DEFAULT_EDGE_RATE,
                pitch=pitch or DEFAULT_EDGE_PITCH,
                volume=vol,
            )
            await communicate.save(str(mp3))

        try:
            try:
                asyncio.run(_run())
            except RuntimeError:
                # Nested event loop (rare): use a fresh loop
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()

            if not mp3.is_file() or mp3.stat().st_size < 64:
                raise RuntimeError("edge-tts produced empty audio")
            last_err = None
            break
        except Exception as e:  # noqa: BLE001 — retry transient network
            last_err = e
            mp3.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                import time

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
    cproc = subprocess.run(conv, capture_output=True, text=True, check=False)
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
                retries=6 if not allow_fallback else 3,
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

    # --- Path B: per-sentence Edge (resume-friendly) ---
    segments = []
    used_provider = provider
    edge_errors: list[str] = []
    if oneshot_err:
        edge_errors.append(f"oneshot_failed: {oneshot_err}")
    for i, text in enumerate(chunks):
        est = estimate_duration_sec(text, lang=lang, cps=cps)
        wav = out_dir / f"seg_{i:03d}_{lang}.wav"
        # Resume: keep already-good segment WAVs across retries / network flaps
        if (
            provider == "edge"
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


def narration_bed_from_result(
    result: NarrationResult,
    out_path: Path,
    *,
    gap_sec: float | None = None,
) -> Path:
    """Build a single WAV bed from synthesize_script output."""
    oneshot = (result.extras or {}).get("oneshot_path")
    mode = (result.extras or {}).get("mode")
    if oneshot and Path(oneshot).is_file() and mode in ("oneshot_punctuated", "oneshot_edge"):
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
