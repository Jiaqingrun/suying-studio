"""Text sanitizers for locked 始峰 / short-form output."""

from __future__ import annotations

import re

# All common CJK / Latin punctuation & decorative dividers
_PUNCT_RE = re.compile(
    r"[。！？!?，,、；;：:…·•\.\"“”‘’'（）\(\)【】\[\]《》<>—\-～~｜|／/\\\s]+"
)


def strip_all_punctuation(text: str, *, keep_newlines: bool = False) -> str:
    """Remove every punctuation mark. Optionally preserve newlines for dual-line titles."""
    raw = text or ""
    if keep_newlines:
        parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        cleaned = [_PUNCT_RE.sub("", p) for p in parts]
        return "\n".join(p for p in cleaned if p)
    return _PUNCT_RE.sub("", raw)


_CJK_CHAR_RE = re.compile(r"[\u4E00-\u9FFF]")


def subtitle_display_text(text: str, *, keep_newlines: bool = True) -> str:
    """Prepare subtitle body: strip punctuation for CJK-heavy lines; keep spaces for Thai/Latin."""
    raw = text or ""
    if keep_newlines:
        parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    else:
        parts = [raw]
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        cjk = len(_CJK_CHAR_RE.findall(s))
        # Mostly CJK → 始峰 lock: no punctuation/spaces on captions
        if cjk >= max(2, len(s) // 3):
            s = strip_all_punctuation(s, keep_newlines=False)
        else:
            # Foreign: keep word spaces; drop only heavy CJK brackets noise
            s = re.sub(r"[（）【】《》]", "", s)
            s = re.sub(r"[ \t]{2,}", " ", s).strip()
        if s:
            out.append(s)
    return "\n".join(out) if keep_newlines else " ".join(out)


def split_then_strip_sentences(script: str) -> list[str]:
    """Split on sentence punctuation first, then strip marks — keeps VO pacing."""
    raw = (script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    # Split keeping Chinese sentence ends
    parts = re.split(r"(?<=[。！？!?])\s*", raw)
    out: list[str] = []
    for p in parts:
        t = strip_all_punctuation(p)
        if t:
            out.append(t)
    if not out:
        t = strip_all_punctuation(raw)
        if t:
            out.append(t)
    return out
