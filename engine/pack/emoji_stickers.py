"""Theme emoji — visual only (never spoken in VO).

HARD RULES (2026-07-26 用户重申):
- Emoji appear **inside burned subtitles** (SRT display text).
- **Forbidden**: floating stickers near the title band.
- TTS / narration_script must stay emoji-free (``strip_emoji_for_speech``).
- Rasterize via Twemoji when fonts would tofu □.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any

# Strip emoji + variation selectors / ZWJ sequences from spoken text
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # misc symbols & pictographs / supplemental
    "\U0001FA00-\U0001FAFF"  # extended-A
    "\U00002700-\U000027BF"  # dingbats
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE0F"  # VS16
    "\U0000200D"  # ZWJ
    "]+",
    flags=re.UNICODE,
)

# Match emoji clusters for inline subtitle compositing (no greedy join across gaps)
_EMOJI_TOKEN_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]"
    "[\U0000FE0F\U0000200D\U0001F3FB-\U0001F3FF]*"
    "(?:\u200d["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]"
    "[\U0000FE0F\U0000200D\U0001F3FB-\U0001F3FF]*)*",
    flags=re.UNICODE,
)

# Spoken phrases that narrate the sticker (ban)
_SPEAK_STICKER_RE = re.compile(
    r"(表情包|表情符号|贴个表情|来个表情|emoji|Emoji|EMOJI)",
)

_TWEMOJI_CDN = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{code}.png"

# Offline fallback colors for common emoji (drawn if download fails)
_THEME_FALLBACK_COLORS: dict[str, tuple[int, int, int]] = {
    "📦": (234, 179, 8),
    "🚚": (59, 130, 246),
    "🚛": (37, 99, 235),
    "🏭": (100, 116, 139),
    "⏱️": (249, 115, 22),
    "🏪": (16, 185, 129),
    "✅": (34, 197, 94),
    "🛒": (244, 63, 94),
    "🔧": (148, 163, 184),
    "⚙️": (148, 163, 184),
    "🏗️": (245, 158, 11),
    "✨": (250, 204, 21),
    "🛠️": (161, 161, 170),
    "📌": (239, 68, 68),
    "👍": (52, 211, 153),
    "🏠": (96, 165, 250),
}


def strip_emoji_for_speech(text: str) -> str:
    """Remove emoji glyphs and sticker-speak phrases from VO text (not display SRT)."""
    t = _EMOJI_RE.sub("", text or "")
    t = _SPEAK_STICKER_RE.sub("", t)
    # Collapse leftover double spaces / empty punct runs
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    return t.strip()


def text_has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text or ""))


def iter_emoji_tokens(text: str) -> list[str]:
    return _EMOJI_TOKEN_RE.findall(text or "")


def inject_emojis_into_srt(
    srt: str,
    cues: list[dict[str, Any]] | None,
    *,
    max_per_cue: int = 1,
) -> str:
    """Append theme emoji into SRT cue bodies (display only; VO already stripped).

    Timing: attach each emoji_cue to the subtitle cue whose time window covers ``at_sec``,
    else the nearest cue. Never invent new timings (keeps align lock).
    """
    body = (srt or "").strip()
    if not body:
        return srt or ""
    emoji_cues = sanitize_emoji_cues(cues)
    if not emoji_cues:
        return srt

    chunks = [c.strip() for c in body.split("\n\n") if c.strip()]
    if not chunks:
        return srt

    parsed: list[tuple[str, str, str, float, float]] = []
    for ch in chunks:
        lines = ch.split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        timing = lines[1]
        text = "\n".join(lines[2:]).strip()
        try:
            a, b = [p.strip() for p in timing.split("-->")]
            start = _srt_ts_to_sec(a)
            end = _srt_ts_to_sec(b)
        except Exception:
            start, end = 0.0, 0.0
        parsed.append((lines[0], timing, text, start, end))

    if not parsed:
        return srt

    used: set[int] = set()
    for ec in emoji_cues:
        emoji = str(ec.get("emoji") or "").strip()
        if not emoji:
            continue
        at = float(ec.get("at_sec") or 0.0)
        # Prefer containing window
        best_i = None
        best_dist = 1e9
        for i, (_, _, text, st, en) in enumerate(parsed):
            if i in used and max_per_cue <= 1 and text_has_emoji(text):
                continue
            if st - 0.05 <= at <= en + 0.15:
                best_i = i
                break
            mid = (st + en) / 2.0
            dist = abs(mid - at)
            if dist < best_dist:
                best_dist = dist
                best_i = i
        if best_i is None:
            continue
        idx, timing, text, st, en = parsed[best_i]
        if max_per_cue <= 1 and text_has_emoji(text):
            # Already has emoji — skip duplicate
            used.add(best_i)
            continue
        # Append once at end of last line (keep dual-line structure)
        if "\n" in text:
            parts = text.split("\n")
            parts[-1] = f"{parts[-1].rstrip()} {emoji}".strip()
            text = "\n".join(parts)
        else:
            text = f"{text.rstrip()} {emoji}".strip()
        parsed[best_i] = (idx, timing, text, st, en)
        used.add(best_i)

    out_blocks = [f"{idx}\n{timing}\n{text}\n" for idx, timing, text, _, _ in parsed]
    return "\n".join(out_blocks).strip() + "\n"


def _srt_ts_to_sec(ts: str) -> float:
    hh, mm, rest = ts.strip().replace(".", ",").split(":")
    ss, ms = (rest.split(",") + ["0"])[:2]
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def twemoji_codepoint_candidates(emoji: str) -> list[str]:
    """Candidate Twemoji filenames (ZWJ sequences often need trailing fe0f)."""
    raw = (emoji or "").strip()
    if not raw:
        return []
    # Full sequence as-is (keep FE0F)
    full = "-".join(f"{ord(c):x}" for c in raw)
    # Drop all FE0F
    no_vs = "-".join(f"{ord(c):x}" for c in raw if ord(c) != 0xFE0F)
    # Base emoji only (first non-ZWJ/VS char) — safest fallback
    base_chars = []
    for c in raw:
        o = ord(c)
        if o in (0xFE0F, 0x200D):
            break
        base_chars.append(c)
        break
    base = "-".join(f"{ord(c):x}" for c in base_chars) if base_chars else ""
    # Prefer: full with fe0f → no_vs → base
    out: list[str] = []
    for c in (full, f"{no_vs}-fe0f" if no_vs and not no_vs.endswith("fe0f") else "", no_vs, base):
        if c and c not in out:
            out.append(c)
    return out


def twemoji_codepoint(emoji: str) -> str:
    cands = twemoji_codepoint_candidates(emoji)
    return cands[0] if cands else ""


def resolve_emoji_png(
    emoji: str,
    *,
    cache_dir: Path,
    size: int = 160,
    plate: bool = True,
) -> Path | None:
    """Return a local PNG for ``emoji`` (Twemoji cache or drawn fallback).

    HARD: glyph must stay **inside** the circular plate (no square-corner overflow)
    when ``plate=True`` (legacy floating stickers). For **inline subtitle**, use
    ``plate=False`` (transparent Twemoji only — no dark disc near title).
    """
    emoji = (emoji or "").strip()
    if not emoji:
        return None
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    size = max(28 if not plate else 64, int(size))
    tag = "plate_v3" if plate else "inline_v1"
    dest = cache_dir / f"resolved_{tag}_{twemoji_codepoint(emoji) or 'x'}_{size}.png"
    if dest.is_file() and dest.stat().st_size > 800:
        return dest

    raw_path: Path | None = None
    for code in twemoji_codepoint_candidates(emoji):
        trial = cache_dir / f"{code}_72.png"
        if trial.is_file() and trial.stat().st_size > 400:
            head = trial.read_bytes()[:8]
            if head.startswith(b"\x89PNG"):
                raw_path = trial
                break
        url = _TWEMOJI_CDN.format(code=code)
        try:
            urllib.request.urlretrieve(url, trial)  # noqa: S310 — fixed CDN path
            if trial.is_file() and trial.stat().st_size > 400:
                head = trial.read_bytes()[:16]
                if head.startswith(b"\x89PNG"):
                    raw_path = trial
                    break
            trial.unlink(missing_ok=True)
        except Exception:
            trial.unlink(missing_ok=True)
            continue

    if raw_path is None:
        simple = emoji[0] if emoji else "📦"
        return _draw_fallback_sticker(simple, dest, size=size)

    try:
        from PIL import Image, ImageDraw

        img = Image.open(raw_path).convert("RGBA")
        if not plate:
            # Inline subtitle: transparent Twemoji only
            sticker = img.resize((size, size), Image.Resampling.LANCZOS)
            sticker.save(dest)
            return dest

        plate_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(plate_img)
        # Disc fill first
        ring = max(3, size // 40)
        draw.ellipse((2, 2, size - 3, size - 3), fill=(0, 0, 0, 185))
        # Keep emoji well inside the circle (square asset otherwise clips corners out)
        # ~22% margin → diagonal of inner square stays inside the disc + ring
        margin = max(28, int(size * 0.22))
        inner = size - margin * 2
        sticker = img.resize((inner, inner), Image.Resampling.LANCZOS)
        plate_img.paste(sticker, (margin, margin), sticker)
        # Circular alpha mask — kill any pixels outside the disc (inside the ring)
        clip_inset = 2 + ring + 1
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse(
            (clip_inset, clip_inset, size - clip_inset - 1, size - clip_inset - 1),
            fill=255,
        )
        r, g, b, a = plate_img.split()
        from PIL import ImageChops

        a = ImageChops.multiply(a, mask)
        plate_img = Image.merge("RGBA", (r, g, b, a))
        # Crisp white ring on top (defines the border emoji must not cross)
        draw = ImageDraw.Draw(plate_img)
        draw.ellipse(
            (4, 4, size - 5, size - 5),
            outline=(255, 255, 255, 235),
            width=ring,
        )
        plate_img.save(dest)
        return dest
    except Exception:
        return _draw_fallback_sticker(emoji[:1], dest, size=size)


def _draw_fallback_sticker(emoji: str, dest: Path, *, size: int) -> Path | None:
    """Colored disc + short label when Twemoji unavailable (still visible, not tofu)."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFont
    except ImportError:
        return None
    color = _THEME_FALLBACK_COLORS.get(emoji[:2]) or _THEME_FALLBACK_COLORS.get(emoji[:1]) or (59, 130, 246)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ring = max(3, size // 40)
    draw.ellipse((2, 2, size - 3, size - 3), fill=(*color, 220))
    # Prefer a short ASCII/CJK label rather than broken emoji glyph
    label = {
        "📦": "货",
        "🚚": "送",
        "🚛": "车",
        "🏭": "仓",
        "⏱️": "快",
        "🏪": "店",
        "✅": "好",
        "🛒": "购",
        "🔧": "修",
        "⚙️": "配",
        "🏗️": "工",
        "✨": "优",
        "🛠️": "具",
        "📌": "点",
        "👍": "赞",
        "🏠": "本",
    }.get(emoji[:2]) or {
        "📦": "货",
        "🚚": "送",
        "👍": "赞",
        "🏠": "本",
        "🛠️": "具",
        "✨": "优",
    }.get(emoji[:1], "★")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", size=max(28, size // 3))
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 4), label, font=font, fill=(255, 255, 255, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, size - 3, size - 3), fill=255)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 5, size - 5), outline=(255, 255, 255, 235), width=ring)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def sanitize_emoji_cues(
    cues: list[dict[str, Any]] | None,
    *,
    video_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Normalize cues; drop empty; clamp into video; never carry spoken text."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(cues or []):
        if not isinstance(c, dict):
            continue
        raw = str(c.get("emoji") or c.get("sticker") or "").strip()
        found = _EMOJI_RE.findall(raw)
        emoji = "".join(found)[:8] if found else ""
        # Prefer simple single-cluster emoji: drop ZWJ tails for burn reliability
        if "\u200d" in emoji:
            emoji = emoji.split("\u200d", 1)[0]
            emoji = "".join(_EMOJI_RE.findall(emoji)) or emoji[:2]
        if not emoji:
            continue
        try:
            at = float(c.get("at_sec") if c.get("at_sec") is not None else c.get("t") or (i * 2.5))
        except (TypeError, ValueError):
            at = float(i * 2.5)
        try:
            dur = float(c.get("duration_sec") or 1.8)
        except (TypeError, ValueError):
            dur = 1.8
        if video_duration_sec and at >= float(video_duration_sec) - 0.2:
            continue
        if video_duration_sec:
            dur = min(dur, max(0.4, float(video_duration_sec) - at - 0.05))
        out.append(
            {
                "at_sec": max(0.0, round(at, 3)),
                "emoji": emoji,
                "label": str(c.get("label") or "")[:16],  # UI only — never TTS
                "duration_sec": max(0.8, round(dur, 3)),
            }
        )
    return out[:6]


def ensure_theme_emoji_cues(
    cues: list[dict[str, Any]] | None,
    *,
    theme: str = "default",
    video_duration_sec: float = 12.0,
    n: int = 3,
) -> list[dict[str, Any]]:
    """Guarantee 2–3 emoji cues for subtitle injection when Ollama omitted them."""
    cleaned = sanitize_emoji_cues(cues, video_duration_sec=video_duration_sec)
    if len(cleaned) >= 2:
        return cleaned
    bank = _fallback_emojis_local(theme, n=n)
    dur = max(8.0, float(video_duration_sec or 12.0))
    slots = [0.9, dur * 0.42, dur * 0.72]
    for i, emoji in enumerate(bank):
        if any(c.get("emoji") == emoji for c in cleaned):
            continue
        cleaned.append(
            {
                "at_sec": round(float(slots[i % len(slots)]), 3),
                "emoji": emoji,
                "label": "theme",
                "duration_sec": 1.6,
            }
        )
        if len(cleaned) >= n:
            break
    return sanitize_emoji_cues(cleaned, video_duration_sec=video_duration_sec)[:n]


def _fallback_emojis_local(theme: str, n: int = 3) -> list[str]:
    # Product-neutral fallback. Industry-specific emoji maps belong in the
    # active industry pack or Ollama response.
    _ = theme
    bank = ["✨", "👍", "✅"]
    return (bank * ((n // len(bank)) + 1))[:n]
