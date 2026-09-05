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
    LOCKED_TITLE_TOP_PX,
    LOCKED_TITLE_STROKE_COLOR,
    LOCKED_TITLE_STROKE_WIDTH,
)
from engine.render.subtitles_burn import (
    _render_cue_png,
    cue_png_twemoji_chroma_count,
    parse_srt_cues,
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def _frozen_rules(meta: dict[str, Any]) -> dict[str, Any]:
    production = (
        meta.get("production_rules")
        if isinstance(meta.get("production_rules"), dict)
        else {}
    )
    return (
        production.get("effective_rules")
        if isinstance(production.get("effective_rules"), dict)
        else {}
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
    "duration",
)

# HARD: 成片时长必须严格大于完整旁白时长（至少 +0.05s）；不可放松
DURATION_MIN_EXCEED_SEC = 0.05


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
    # Prefer the production work SRT (already tightened to VO) over sibling copies.
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    sp = meta.get("subtitle_path")
    if sp and Path(sp).is_file():
        return Path(sp)
    for p in (
        mp4.with_suffix(".zh.srt"),
        Path(str(mp4).replace(".mp4", ".zh.srt")),
    ):
        if p.is_file():
            return p
    return None


def _find_voice(mp4: Path, sidecar: dict[str, Any]) -> Path | None:
    # Prefer the same VO bed used for tighten / mux (meta.narration_path).
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    np = meta.get("narration_path")
    if np and Path(np).is_file():
        return Path(np)
    for p in (mp4.with_name(mp4.stem + ".voice.wav"),):
        if p.is_file():
            return p
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


_CJK_CHAR_RE = re.compile(r"[\u4E00-\u9FFF]")


def _is_mostly_cjk_line(text: str) -> bool:
    """CJK-heavy line → 晓晓 breath floor applies; foreign dual primary is not."""
    body = strip_all_punctuation(text or "")
    if not body:
        return False
    cjk = len(_CJK_CHAR_RE.findall(body))
    return cjk >= max(2, (len(body) + 2) // 3)


def _breath_lines_for_cue(text: str, *, zh_voice: bool) -> list[str]:
    """Pick which subtitle lines to measure for G.BREATH.

    burn_dual keeps foreign ``subtitle_lang`` on line 1 and VO-aligned Chinese
    on line 2. Counting only line 1 mis-applies the 22-char 晓晓 floor to Arabic
    etc. and false-fails nearly all dual packs (``max=48 limit 22``).
    """
    lines = [ln.strip() for ln in (text or "").split("\n") if ln and ln.strip()]
    if not lines:
        return []
    cjk_lines = [ln for ln in lines if _is_mostly_cjk_line(ln)]
    if zh_voice:
        # Chinese VO: enforce short breaths on CJK captions only.
        return cjk_lines
    # Foreign VO: primary line only (foreign templates may be longer).
    return [lines[0]]


def _check_breath(sidecar: dict[str, Any], srt: str) -> list[str]:
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    cues = _cues_from_srt(srt)
    if not cues:
        return ["breath: no cues"]

    zh_voice = _voice_lang_is_zh(meta)
    # Per-line limits: CJK 18→22; non-CJK dual/foreign templates use 48.
    cjk_lengths: list[int] = []
    foreign_lengths: list[int] = []
    for _, _, body in cues:
        lines = _breath_lines_for_cue(body, zh_voice=zh_voice)
        for ln in lines:
            n = len(strip_all_punctuation(ln))
            if _is_mostly_cjk_line(ln):
                cjk_lengths.append(n)
            else:
                foreign_lengths.append(n)
        if not lines and not zh_voice:
            primary = (body or "").split("\n", 1)[0]
            foreign_lengths.append(len(strip_all_punctuation(primary)))

    if cjk_lengths and max(cjk_lengths) > 22:
        fails.append(f"breath: cue too long max={max(cjk_lengths)} (limit 22)")
    if foreign_lengths and max(foreign_lengths) > 48:
        fails.append(f"breath: foreign cue too long max={max(foreign_lengths)} (limit 48)")
    lengths = cjk_lengths or foreign_lengths
    if len(cues) < 3 and lengths and sum(lengths) > 40:
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
    # 晓晓情感语速锁仅约束中文 Edge 旁白；clone / 外文跳过
    if zh_voice and str(meta.get("tts_provider") or "") != "clone":
        if rate and str(rate) != "-8%":
            fails.append(f"breath: rate={rate!r} want -8%")
        elif not rate:
            narr = meta.get("narration_path")
            man = Path(narr).parent / "narration" / "narration_manifest.json" if narr else None
            if meta.get("tts_mode") == "oneshot_punctuated" and (not man or not man.is_file()):
                fails.append("breath: missing rate evidence (no sidecar rate / no manifest)")
    return fails


def _check_voice(sidecar: dict[str, Any]) -> list[str]:
    """旁白情感锁：Edge+pitch/volume 或 clone 声色包；旁白正文无 emoji。Fail-closed on missing provider."""
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    rules = _frozen_rules(meta)
    provider = meta.get("tts_provider")
    expected_provider = str(rules.get("tts_provider") or provider or "edge")
    has_voice = bool(meta.get("narration_path") or meta.get("narration_script"))
    voice_lang = str(meta.get("voice_lang") or "").strip().lower()
    expects_voice = has_voice or voice_lang not in ("", "none")
    if provider == "mock":
        fails.append("voice: mock TTS forbidden for ready")
    elif not provider:
        # HARD: missing provider must not silently pass (READY_GATE fail-closed)
        if expects_voice:
            fails.append("voice: missing tts_provider want edge|clone")
    elif provider == "clone":
        pack = meta.get("clone_pack") if isinstance(meta.get("clone_pack"), dict) else {}
        if not pack.get("ref_wav") and not pack.get("id"):
            fails.append("voice: clone missing clone_pack id/ref_wav")
    elif provider != "edge":
        fails.append(f"voice: provider={provider!r} want edge|clone")
    if (
        provider in {"edge", "clone"}
        and expected_provider in {"edge", "clone"}
        and provider != expected_provider
    ):
        fails.append(
            f"voice: provider={provider!r} want frozen {expected_provider!r}"
        )
    # 中文旁白锁 pitch/volume；外文 Edge catalog 音色用中性参数；clone 跳过 Edge 调参
    if provider != "clone" and _voice_lang_is_zh(meta) and expects_voice:
        pitch = meta.get("tts_pitch")
        expected_pitch = str(rules.get("narration_pitch") or "+35Hz")
        if pitch is None:
            fails.append(f"voice: missing tts_pitch want {expected_pitch}")
        elif str(pitch) != expected_pitch:
            fails.append(f"voice: pitch={pitch!r} want {expected_pitch}")
        volume = meta.get("tts_volume")
        expected_volume = str(rules.get("narration_volume") or "+12%")
        if volume is None:
            fails.append(f"voice: missing tts_volume want {expected_volume}")
        elif str(volume) != expected_volume:
            fails.append(f"voice: volume={volume!r} want {expected_volume}")
        rate = meta.get("tts_rate")
        expected_rate = str(rules.get("narration_rate") or "-8%")
        if rate is None:
            fails.append(f"voice: missing tts_rate want {expected_rate}")
        elif str(rate) != expected_rate:
            fails.append(f"voice: rate={rate!r} want {expected_rate}")
    script = str(meta.get("narration_script") or "")
    if script and (script != strip_emoji_for_speech(script) or _EMOJI_RE.search(script)):
        fails.append("voice: narration_script contains emoji (must not be spoken)")
    return fails


def _check_title(sidecar: dict[str, Any]) -> list[str]:
    """Title must match the frozen orientation-specific rule."""
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    rules = _frozen_rules(meta)
    style = meta.get("title_style") if isinstance(meta.get("title_style"), dict) else {}
    color = _norm_hex(style.get("color"))
    stroke = _norm_hex(style.get("stroke_color"))
    want_c = _norm_hex(rules.get("title_color") or LOCKED_TITLE_COLOR)
    want_s = _norm_hex(
        rules.get("title_stroke_color") or LOCKED_TITLE_STROKE_COLOR
    )
    want_sw = int(
        rules.get("title_stroke_width")
        if rules.get("title_stroke_width") is not None
        else LOCKED_TITLE_STROKE_WIDTH
    )
    orientation = str(rules.get("orientation") or "portrait")
    want_top = int(
        rules.get("title_glyph_top_px")
        or (120 if orientation == "landscape" else LOCKED_TITLE_TOP_PX)
    )
    low, high = (60, 260) if orientation == "landscape" else (120, 420)
    if not style:
        fails.append("title: missing title_style in sidecar meta")
    else:
        if color != want_c:
            fails.append(f"title: color={color or None!r} want frozen {want_c}")
        if stroke != want_s:
            fails.append(
                f"title: stroke_color={stroke or None!r} want frozen {want_s}"
            )
        sw = int(style.get("stroke_width") or 0)
        if sw != want_sw:
            fails.append(f"title: stroke_width={sw} want frozen {want_sw}")
        bbox = meta.get("title_bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            fails.append("title: missing final title_bbox")
        elif int(bbox[1]) != want_top:
            fails.append(f"title: glyph_top={bbox[1]} want frozen {want_top}")
        if not low <= want_top <= high:
            fails.append(f"title: frozen glyph_top={want_top} outside safe {low}-{high}")
    return fails


def _check_subtitle_style(sidecar: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
    rules = _frozen_rules(meta)
    style = (
        meta.get("subtitle_style_effective")
        if isinstance(meta.get("subtitle_style_effective"), dict)
        else {}
    )
    if not style:
        return ["subtitle: missing effective style"]
    orientation = str(rules.get("orientation") or "portrait")
    expected = {
        "color": _norm_hex(rules.get("subtitle_color") or "#FFFFFF"),
        "stroke_color": _norm_hex(
            rules.get("subtitle_stroke_color") or "#000000"
        ),
        "stroke_width": int(
            rules.get("subtitle_stroke_width")
            if rules.get("subtitle_stroke_width") is not None
            else 4
        ),
        "glyph_bottom_px": int(
            rules.get("subtitle_glyph_bottom_px")
            or (180 if orientation == "landscape" else 420)
        ),
    }
    if _norm_hex(style.get("color")) != expected["color"]:
        fails.append("subtitle: color does not match frozen rule")
    if _norm_hex(style.get("stroke_color")) != expected["stroke_color"]:
        fails.append("subtitle: stroke color does not match frozen rule")
    if int(style.get("stroke_width") or 0) != expected["stroke_width"]:
        fails.append("subtitle: stroke width does not match frozen rule")
    if int(style.get("glyph_bottom_px") or 0) != expected["glyph_bottom_px"]:
        fails.append("subtitle: bottom position does not match frozen rule")
    low, high = (100, 360) if orientation == "landscape" else (240, 620)
    if not low <= expected["glyph_bottom_px"] <= high:
        fails.append("subtitle: frozen bottom position is outside safe range")
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
    width, height = _probe_video_size(mp4)
    width = width or 1080
    height = height or 1920
    rules = _frozen_rules(meta)
    subtitle_bottom = int(
        rules.get("subtitle_glyph_bottom_px")
        or (180 if width > height else 420)
    )
    # Pixel proof: re-render first SRT cue that still has emoji with frozen subtitle
    # style (stroke_width often 4). Colorful count proves Twemoji, not mono font/tofu.
    sample_text = ""
    for cue in parse_srt_cues(srt_text or ""):
        t = str(cue.get("text") or "")
        if _EMOJI_RE.search(t):
            sample_text = t
            break
    if sample_text:
        with tempfile.TemporaryDirectory(prefix="qa-emoji-cue-") as td:
            png = Path(td) / "cue.png"
            try:
                color = str(rules.get("subtitle_color") or "#FFFFFF")
                stroke_color = str(rules.get("subtitle_stroke_color") or "#000000")
                stroke_w = int(
                    rules.get("subtitle_stroke_width")
                    if rules.get("subtitle_stroke_width") is not None
                    else 4
                )
                _render_cue_png(
                    sample_text,
                    width,
                    font_size=int(rules.get("subtitle_font_size") or 64),
                    out=png,
                    bottom_padding_px=subtitle_bottom,
                    side_margin_px=48,
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_w,
                )
                chromas = cue_png_twemoji_chroma_count(
                    png, text_color=color, stroke_color=stroke_color
                )
                if chromas < 60:
                    fails.append(
                        f"emoji: subtitle cue PNG has no color Twemoji chromas={chromas}"
                    )
            except Exception as e:  # noqa: BLE001
                fails.append(f"emoji: subtitle Twemoji re-render failed: {e}")
    corner_size = min(240, width, height)
    corner_x = max(0, width - corner_size - 20)
    corner_y = 40
    band_width = max(200, width - 180)
    band_height = min(240, height)
    band_x = max(0, (width - band_width) // 2)
    band_y = max(0, min(height - band_height, height - subtitle_bottom - band_height // 2))
    with tempfile.TemporaryDirectory(prefix="qa-emoji-") as td:
        corner = Path(td) / "title_corner.jpg"
        bottom = Path(td) / "subtitle_band.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.2, at):.2f}", "-i", str(mp4),
                "-vf", f"crop={corner_size}:{corner_size}:{corner_x}:{corner_y}", "-frames:v", "1", str(corner),
            ],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.2, at):.2f}", "-i", str(mp4),
                "-vf", f"crop={band_width}:{band_height}:{band_x}:{band_y}", "-frames:v", "1", str(bottom),
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
                # Warehouse/indoors footage often has dark corners (false ~0.22–0.45).
                # Only flag dense near-black plates typical of banned title stickers.
                if dark > 0.55:
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
    elif mp4.is_file():
        width, height = _probe_video_size(mp4)
        meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
        orientation = str(_frozen_rules(meta).get("orientation") or "portrait")
        expected = (1920, 1080) if orientation == "landscape" else (1080, 1920)
        if (width, height) != expected:
            fails.append(
                f"basic: canvas={width}x{height} want frozen {expected[0]}x{expected[1]}"
            )
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


def _probe_video_duration(mp4: Path) -> float:
    """Return container duration seconds, or 0 on probe failure."""
    if not mp4.is_file():
        return 0.0
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return 0.0
    try:
        data = json.loads(proc.stdout or "{}")
        return float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def _probe_video_size(mp4: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(mp4),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        stream = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError, IndexError, json.JSONDecodeError):
        return 0, 0


def _check_duration(mp4: Path, wav: Path | None) -> list[str]:
    """有旁白 wav 时：成片 duration 必须严格大于旁白（至少 +0.05s）。"""
    if wav is None or not wav.is_file():
        return []
    fails: list[str] = []
    try:
        ndur = float(probe_audio_duration(wav))
    except Exception as e:  # noqa: BLE001
        return [f"duration: narration probe failed ({e})"]
    vdur = _probe_video_duration(mp4)
    if vdur <= 0:
        fails.append("duration: video probe failed (fail-closed)")
        return fails
    if ndur <= 0:
        fails.append("duration: narration duration invalid (fail-closed)")
        return fails
    # HARD LOCK: vdur must exceed ndur by ≥ DURATION_MIN_EXCEED_SEC
    if vdur < ndur + DURATION_MIN_EXCEED_SEC - 1e-9:
        fails.append(
            "duration: video_must_exceed_narration "
            f"vdur={vdur:.3f}s ndur={ndur:.3f}s "
            f"need>={ndur + DURATION_MIN_EXCEED_SEC:.3f}s "
            "（成片时长必须严格大于完整旁白时长）"
        )
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
    video_width, _video_height = _probe_video_size(mp4)
    srt_path = _find_srt(mp4, sidecar)
    wav_path = _find_voice(mp4, sidecar)
    checks: dict[str, list[str]] = {k: [] for k in GATE_CHECKS}
    checks["basic"] = _check_basic(mp4, sidecar)
    checks["title"] = _check_title(sidecar)
    checks["voice"] = _check_voice(sidecar)
    checks["margin"].extend(_check_subtitle_style(sidecar))
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
        checks["margin"].extend(_check_margin(srt, width=video_width or 1080))
        checks["breath"] = _check_breath(sidecar, srt)
    checks["emoji"] = _check_emoji(sidecar, mp4, srt, require_ollama=require_ollama)
    checks["duration"] = _check_duration(mp4, wav_path if wav_path and wav_path.is_file() else None)

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
