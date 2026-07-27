"""READY_GATE — 成片进 ready 前的全套审核门禁（强制参考 docs/READY_GATE.md）。

Fail-closed: 任一项失败 → 不得标 ready（failed / review + 明确 reasons）。
Agent 脚本与引擎 worker 必须走同一套 evaluate_ready_gate。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from engine.pack.emoji_stickers import strip_emoji_for_speech
from engine.pack.text_sanitize import strip_all_punctuation
from engine.pack.tts import _speech_spans_by_silence, probe_audio_duration
from engine.pack.video_lock import (
    LOCKED_TITLE_COLOR,
    LOCKED_TITLE_OFFSET_Y_PX,
    LOCKED_TITLE_STROKE_COLOR,
    LOCKED_TITLE_STROKE_WIDTH,
)
from engine.render.subtitles_burn import _render_cue_png, parse_srt_cues

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)

# Gate check ids — keep in sync with docs/READY_GATE.md
GATE_CHECKS = (
    "basic",
    "title",
    "align",
    "margin",
    "breath",
    "voice",
    "emoji",
    "blur",
    "semantic_source",
)


def _norm_hex(value: Any) -> str:
    s = str(value or "").strip().upper()
    if not s:
        return ""
    if not s.startswith("#"):
        s = "#" + s
    if len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s


def _parse_ts(ts: str) -> float:
    hh, mm, rest = ts.strip().split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _cues_from_srt(srt: str) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for block in [b.strip() for b in (srt or "").strip().split("\n\n") if b.strip()]:
        lines = block.splitlines()
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = [p.strip() for p in lines[1].split("-->")]
        # Keep newlines so burn_dual primary/secondary stay separable
        text = "\n".join(lines[2:]).strip()
        out.append((_parse_ts(a), _parse_ts(b), text))
    return out


def _load_sidecar(mp4: Path) -> dict[str, Any]:
    side = mp4.with_suffix(".json")
    if not side.is_file():
        return {}
    data = json.loads(side.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _find_srt(mp4: Path, sidecar: dict[str, Any]) -> Path | None:
    for p in (
        mp4.with_suffix(".zh.srt"),
        Path(str(mp4).replace(".mp4", ".zh.srt")),
    ):
        if p.is_file():
            return p
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    sp = meta.get("subtitle_path")
    if sp and Path(sp).is_file():
        return Path(sp)
    return None


def _find_voice(mp4: Path, sidecar: dict[str, Any]) -> Path | None:
    for p in (mp4.with_name(mp4.stem + ".voice.wav"),):
        if p.is_file():
            return p
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    np = meta.get("narration_path")
    if np and Path(np).is_file():
        return Path(np)
    return None


def _check_align(srt: str, wav: Path, *, max_overhang: float = 0.08) -> list[str]:
    fails: list[str] = []
    spans = _speech_spans_by_silence(wav, min_silence=0.06)
    vo = probe_audio_duration(wav)
    cues = _cues_from_srt(srt)
    if not cues:
        return ["align: empty SRT"]
    for i, (start, end, text) in enumerate(cues):
        if end <= start:
            fails.append(f"align: cue{i} end<=start")
        if end > vo + 0.05:
            fails.append(f"align: cue{i} past VO end={end:.3f} vo={vo:.3f}")
        if i + 1 < len(cues) and end > cues[i + 1][0] + 0.001:
            fails.append(f"align: cue{i} overlaps next")
        overlapping = [(s, e) for s, e in spans if e > start + 0.02 and s < end + 0.05]
        if not overlapping:
            fails.append(f"align: cue{i} has no overlapping speech span text={text[:18]}")
            continue
        speech_end = max(e for _, e in overlapping)
        overhang = end - speech_end
        if overhang > max_overhang:
            fails.append(
                f"align: cue{i} overhang={overhang:.3f}s text={text[:18]}"
            )
    return fails


def _check_margin(srt: str, *, width: int = 1080, font_size: int = 64) -> list[str]:
    fails: list[str] = []
    cues = parse_srt_cues(srt)
    if not cues:
        return ["margin: no cues"]
    with tempfile.TemporaryDirectory(prefix="qa-margin-") as td:
        work = Path(td)
        for i, cue in enumerate(cues[:8]):
            text = str(cue.get("text") or "").strip()
            if not text:
                continue
            out = work / f"c{i}.png"
            _render_cue_png(text, width, font_size=font_size, out=out, side_margin_px=48)
            from PIL import Image
            import numpy as np

            im = Image.open(out).convert("RGBA")
            a = np.array(im)
            if im.size[0] > width - 48:
                fails.append(f"margin: cue{i} box_w={im.size[0]} > {width - 48}")
            left = float((a[:, :3, 3] > 200).mean())
            right = float((a[:, -3:, 3] > 200).mean())
            if left > 0.02 or right > 0.02:
                fails.append(f"margin: cue{i} edge clip L={left:.3f} R={right:.3f}")
    return fails


def _voice_lang_is_zh(meta: dict[str, Any]) -> bool:
    vl = str(meta.get("voice_lang") or "zh").strip().lower()
    return vl in ("", "zh", "zh-tw", "none")


def _check_breath(sidecar: dict[str, Any], srt: str) -> list[str]:
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    cues = _cues_from_srt(srt)
    if not cues:
        return ["breath: no cues"]
    # Dual burn: length on primary line only (secondary is translation)
    def _cue_len(t: str) -> int:
        primary = (t or "").split("\n", 1)[0]
        return len(strip_all_punctuation(primary))

    lengths = [_cue_len(t) for _, _, t in cues]
    zh_voice = _voice_lang_is_zh(meta)
    # zh lock 18→22; foreign VO templates are longer — do not apply 晓晓 breath char floor
    max_chars = 22 if zh_voice else 48
    if max(lengths) > max_chars:
        fails.append(f"breath: cue too long max={max(lengths)} (limit {max_chars})")
    if len(cues) < 3 and sum(lengths) > 40:
        fails.append(f"breath: too few cues ({len(cues)}) for {sum(lengths)} chars")
    narr = meta.get("narration_path")
    rate = meta.get("tts_rate")
    if narr:
        man = Path(narr).parent / "narration" / "narration_manifest.json"
        if man.is_file():
            try:
                nm = json.loads(man.read_text(encoding="utf-8"))
                rate = nm.get("rate") or rate
                segs = nm.get("segments") or []
                if segs and zh_voice:
                    mx = max(len(str(s.get("text") or "")) for s in segs if isinstance(s, dict))
                    if mx > 22:
                        fails.append(f"breath: manifest segment max={mx}")
            except Exception as e:  # noqa: BLE001
                fails.append(f"breath: manifest read err {e}")
    # 晓晓情感语速锁仅约束中文旁白；外文 Edge 用中性 rate
    if zh_voice:
        if rate and str(rate) != "-8%":
            fails.append(f"breath: rate={rate!r} want -8%")
        elif not rate:
            narr = meta.get("narration_path")
            man = Path(narr).parent / "narration" / "narration_manifest.json" if narr else None
            if meta.get("tts_mode") == "oneshot_punctuated" and (not man or not man.is_file()):
                fails.append("breath: missing rate evidence (no sidecar rate / no manifest)")
    return fails


def _check_voice(sidecar: dict[str, Any]) -> list[str]:
    """旁白情感锁：Edge + pitch/volume；旁白正文无 emoji。Fail-closed on missing provider."""
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    provider = meta.get("tts_provider")
    has_voice = bool(meta.get("narration_path") or meta.get("narration_script"))
    voice_lang = str(meta.get("voice_lang") or "").strip().lower()
    expects_voice = has_voice or voice_lang not in ("", "none")
    if provider == "mock":
        fails.append("voice: mock TTS forbidden for ready")
    elif not provider:
        # HARD: missing provider must not silently pass (READY_GATE fail-closed)
        if expects_voice:
            fails.append("voice: missing tts_provider want edge")
    elif provider != "edge":
        fails.append(f"voice: provider={provider!r} want edge")
    # 中文旁白锁 pitch/volume；外文 Edge catalog 音色用中性参数
    if _voice_lang_is_zh(meta) and expects_voice:
        pitch = meta.get("tts_pitch")
        if pitch is None:
            fails.append("voice: missing tts_pitch want +35Hz")
        elif str(pitch) != "+35Hz":
            fails.append(f"voice: pitch={pitch!r} want +35Hz")
        volume = meta.get("tts_volume")
        if volume is None:
            fails.append("voice: missing tts_volume want +12%")
        elif str(volume) != "+12%":
            fails.append(f"voice: volume={volume!r} want +12%")
    script = str(meta.get("narration_script") or "")
    if script and (script != strip_emoji_for_speech(script) or _EMOJI_RE.search(script)):
        fails.append("voice: narration_script contains emoji (must not be spoken)")
    return fails


def _check_title(sidecar: dict[str, Any]) -> list[str]:
    """标题必须黄字黑描边 + offset_y_px=120（sidecar title_style / video_lock）。"""
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    style = meta.get("title_style") if isinstance(meta.get("title_style"), dict) else {}
    color = _norm_hex(style.get("color"))
    stroke = _norm_hex(style.get("stroke_color"))
    want_c = _norm_hex(LOCKED_TITLE_COLOR)
    want_s = _norm_hex(LOCKED_TITLE_STROKE_COLOR)
    if not style:
        fails.append("title: missing title_style in sidecar meta")
    else:
        if color != want_c:
            fails.append(f"title: color={color or None!r} want {want_c} (黄字)")
        if stroke != want_s:
            fails.append(f"title: stroke_color={stroke or None!r} want {want_s} (黑描边)")
        sw = int(style.get("stroke_width") or 0)
        if sw < LOCKED_TITLE_STROKE_WIDTH:
            fails.append(f"title: stroke_width={sw} want >={LOCKED_TITLE_STROKE_WIDTH}")
        oy = style.get("offset_y_px")
        if oy is None:
            fails.append(f"title: missing offset_y_px want {LOCKED_TITLE_OFFSET_Y_PX}")
        elif int(oy) != LOCKED_TITLE_OFFSET_Y_PX:
            fails.append(f"title: offset_y_px={oy} want {LOCKED_TITLE_OFFSET_Y_PX}")
    # Legacy red+yellow must never pass
    if color == "#E10600":
        fails.append("title: forbidden red-fill yellow-stroke palette")
    return fails


def _check_emoji(sidecar: dict[str, Any], mp4: Path, srt: str | None, *, require_ollama: bool = True) -> list[str]:
    """HARD: emoji in burned subtitles; never spoken; never title-area stickers."""
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    script = str(meta.get("narration_script") or "")
    if script != strip_emoji_for_speech(script) or _EMOJI_RE.search(script or ""):
        fails.append("emoji: narration_script still contains emoji (must not be spoken)")
    if require_ollama and not meta.get("ollama_narration"):
        fails.append("ollama: ollama_narration=false (batch expects Ollama rewrite)")
    cues = meta.get("emoji_cues") or []
    if not isinstance(cues, list) or len(cues) < 1:
        fails.append("emoji: no emoji_cues")
    if meta.get("emoji_burned"):
        fails.append("emoji: title stickers burned (forbidden — use subtitle inline only)")
    if meta.get("title_stickers_disabled") is False:
        fails.append("emoji: title_stickers_disabled=false")
    srt_text = srt or ""
    if not srt_text:
        fails.append("emoji: srt missing for subtitle-emoji check")
    elif not _EMOJI_RE.search(srt_text):
        fails.append("emoji: subtitle SRT has no emoji (must appear in cues)")
    if not meta.get("subtitle_burned"):
        fails.append("emoji: subtitle_burned=false (inline emoji not visible in video)")
    at = 1.0
    if isinstance(cues, list) and cues:
        try:
            at = float(cues[0].get("at_sec") or 1.0)
        except (TypeError, ValueError):
            at = 1.0
    with tempfile.TemporaryDirectory(prefix="qa-emoji-") as td:
        corner = Path(td) / "title_corner.jpg"
        bottom = Path(td) / "subtitle_band.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.2, at):.2f}", "-i", str(mp4),
                "-vf", "crop=240:240:820:80", "-frames:v", "1", str(corner),
            ],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.2, at):.2f}", "-i", str(mp4),
                "-vf", "crop=900:220:90:1550", "-frames:v", "1", str(bottom),
            ],
            capture_output=True,
            check=False,
        )
        if corner.is_file() and corner.stat().st_size > 2000:
            try:
                from PIL import Image
                import numpy as np

                a = np.array(Image.open(corner).convert("RGB"))
                dark = ((a[:, :, 0] < 40) & (a[:, :, 1] < 40) & (a[:, :, 2] < 40)).mean()
                if dark > 0.22:
                    fails.append(f"emoji: title-area dark sticker suspected dark_ratio={dark:.3f}")
            except Exception as e:  # noqa: BLE001
                fails.append(f"emoji: title crop inspect err {e}")
        if not bottom.is_file() or bottom.stat().st_size < 2500:
            fails.append(
                f"emoji: subtitle band crop empty/small bytes={bottom.stat().st_size if bottom.is_file() else 0}"
            )
    return fails


def _check_basic(mp4: Path, sidecar: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    if not mp4.is_file() or mp4.stat().st_size < 100_000:
        fails.append("basic: mp4 missing/too small")
    qc = sidecar.get("qc")
    if not isinstance(qc, dict):
        fails.append("basic: qc missing in sidecar (fail-closed)")
        qc = {}
    elif qc.get("passed") is not True:
        fails.append(f"basic: qc not passed value={qc.get('passed')!r}")
    if qc.get("passed") is False:
        fails.append(f"basic: qc failed reasons={qc.get('reasons')}")
    metrics = qc.get("metrics") if isinstance(qc.get("metrics"), dict) else {}
    if not metrics:
        fails.append("basic: qc metrics missing (fail-closed)")
    else:
        if float(metrics.get("silence_ratio") or 0) > 0.35:
            fails.append(f"basic: silence_ratio={metrics.get('silence_ratio')}")
        if float(metrics.get("black_ratio") or 0) > 0.08:
            fails.append(f"basic: black_ratio={metrics.get('black_ratio')}")
        if not metrics.get("has_audio"):
            fails.append("basic: no audio")
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    if meta.get("tts_provider") == "mock":
        fails.append("basic: mock TTS in ready")
    return fails


def _check_blur(sidecar: dict[str, Any]) -> list[str]:
    """画质：成片候选不得含 rejected_blur / 低分切片。Fail-closed when clips absent."""
    fails: list[str] = []
    clips = sidecar.get("clips")
    if not isinstance(clips, list):
        # HARD: missing clips must not silently pass READY_GATE
        fails.append("blur: clips missing in sidecar (fail-closed)")
        return fails
    if len(clips) < 1:
        fails.append("blur: clips empty in sidecar (fail-closed)")
        return fails
    for i, c in enumerate(clips):
        if not isinstance(c, dict):
            continue
        st = str(c.get("status") or "")
        if st == "rejected_blur":
            fails.append(f"blur: clip{i} status=rejected_blur")
        q = c.get("quality_score")
        if q is not None:
            try:
                if float(q) < 0.35:
                    fails.append(f"blur: clip{i} quality_score={q} < 0.35")
            except (TypeError, ValueError):
                fails.append(f"blur: clip{i} bad quality_score={q!r}")
    return fails


def _check_semantic_source(sidecar: dict[str, Any]) -> list[str]:
    """Strict jobs must prove every rendered source is an audited semantic-v1 cliplet."""
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    if not meta.get("strict_semantic_v1"):
        return []
    clips = sidecar.get("clips") if isinstance(sidecar.get("clips"), list) else []
    audit = meta.get("semantic_source_audit")
    if not isinstance(audit, list) or len(audit) != len(clips):
        return [
            "semantic_source: strict audit missing or count mismatch "
            f"clips={len(clips)} audit={len(audit) if isinstance(audit, list) else 0}"
        ]
    fails: list[str] = []
    for index, item in enumerate(audit, start=1):
        row = item if isinstance(item, dict) else {}
        cliplet_id = row.get("cliplet_id")
        if not cliplet_id:
            fails.append(f"semantic_source: clip{index} whole-asset fallback forbidden")
        if row.get("status") != "usable":
            fails.append(f"semantic_source: clip{index} status={row.get('status')!r}")
        if row.get("has_embedding") is not True:
            fails.append(f"semantic_source: clip{index} embedding missing")
        if row.get("semantic_schema_version") != "suying.cliplet.semantic.v1":
            fails.append(
                f"semantic_source: clip{index} schema={row.get('semantic_schema_version')!r}"
            )
        if row.get("semantic_v1_passed") is not True:
            fails.append(f"semantic_source: clip{index} semantic v1 gate not passed")
        try:
            if float(row.get("quality_score")) < 0.35:
                fails.append(
                    f"semantic_source: clip{index} quality_score={row.get('quality_score')} < 0.35"
                )
        except (TypeError, ValueError):
            fails.append(f"semantic_source: clip{index} quality score missing")
    return fails


def evaluate_ready_gate(
    mp4: Path,
    *,
    require_ollama: bool = True,
    sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run full READY_GATE. ok=True only when every check is empty.

    强制参考：docs/READY_GATE.md · HARD_LOCKS · VIDEO_LOCK
    """
    mp4 = Path(mp4)
    sidecar = sidecar if isinstance(sidecar, dict) else _load_sidecar(mp4)
    srt_path = _find_srt(mp4, sidecar)
    wav_path = _find_voice(mp4, sidecar)
    checks: dict[str, list[str]] = {k: [] for k in GATE_CHECKS}
    checks["basic"] = _check_basic(mp4, sidecar)
    checks["title"] = _check_title(sidecar)
    checks["voice"] = _check_voice(sidecar)
    checks["blur"] = _check_blur(sidecar)
    checks["semantic_source"] = _check_semantic_source(sidecar)
    srt: str | None
    if not srt_path:
        checks["align"].append("align: srt missing")
        checks["margin"].append("margin: srt missing")
        checks["breath"].append("breath: srt missing")
        srt = None
    else:
        srt = srt_path.read_text(encoding="utf-8", errors="replace")
        if wav_path and wav_path.is_file():
            checks["align"] = _check_align(srt, wav_path)
        else:
            checks["align"] = ["align: voice wav missing"]
        checks["margin"] = _check_margin(srt)
        checks["breath"] = _check_breath(sidecar, srt)
    checks["emoji"] = _check_emoji(sidecar, mp4, srt, require_ollama=require_ollama)

    fails = [f for xs in checks.values() for f in xs]
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    return {
        "ok": not fails,
        "gate": "READY_GATE",
        "file": mp4.name,
        "path": str(mp4),
        "job_id": sidecar.get("job_id"),
        "seed": sidecar.get("seed"),
        "title": sidecar.get("title"),
        "tts_mode": meta.get("tts_mode"),
        "tts_rate": meta.get("tts_rate"),
        "tts_pitch": meta.get("tts_pitch"),
        "tts_volume": meta.get("tts_volume"),
        "title_style": meta.get("title_style"),
        "ollama_narration": meta.get("ollama_narration"),
        "checks": checks,
        "fails": fails,
        "fail_count": len(fails),
        "action_on_fail": "reject_ready",  # → failed / review, never ready
    }


# Back-compat alias used by scripts/qa_ready_montage.py
qa_montage = evaluate_ready_gate
