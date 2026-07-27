"""Text sanitizers for locked short-form output."""

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
        # Mostly CJK → locked style: no punctuation/spaces on captions
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


# Prefer break *after* these phrases (口播呼吸点)
_ZH_BREATH_PHRASES = (
    "效率看得见",
    "服务更贴心",
    "服务贴心",
    "省心放心",
    "用着更放心",
    "本地实拍",
    "快速又整齐",
    "又快又整齐",
    "少折腾",
    "更省心",
    "精心打理",
    "真实记录",
    "欢迎咨询",
)

_ZH_SOFT_BREAK = re.compile(r"[，,、；;]")


def ensure_zh_speech_breaks(script: str, *, max_chars: int = 18) -> str:
    """Guarantee short, speakable Chinese breaths for Edge oneshot.

    Ollama / templates sometimes return run-on text with no ``。``.
    Lock: strip punctuation for *display*, but TTS still needs sentence ends
    so 晓晓 pauses and SRT can clear between breaths.

    Returns text with ``。`` between units (ready for ``split_then_strip_sentences``).
    """
    raw = (script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    # Normalize soft pauses → hard sentence ends so split sees them
    soft = _ZH_SOFT_BREAK.sub("。", raw)
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*", soft) if p and p.strip()]
    if not parts:
        parts = [soft]
    cap = max(8, int(max_chars))
    breaths: list[str] = []
    for part in parts:
        body = strip_all_punctuation(part)
        if not body:
            continue
        while body:
            if len(body) <= cap:
                breaths.append(body)
                break
            cut = _find_zh_breath_cut(body, cap)
            breaths.append(body[:cut].strip())
            body = body[cut:].strip()
    if not breaths:
        t = strip_all_punctuation(raw)
        return (t + "。") if t else ""
    return "。".join(breaths) + "。"


def _find_zh_breath_cut(body: str, cap: int) -> int:
    """Pick a cut index in [4, cap] preferring phrase ends / light particles."""
    lo = 4
    # Prefer the longest phrase that ends within the window
    best = -1
    best_len = -1
    for ph in sorted(_ZH_BREATH_PHRASES, key=len, reverse=True):
        idx = body.find(ph)
        while idx >= 0:
            end = idx + len(ph)
            if lo <= end <= cap and (end > best or (end == best and len(ph) > best_len)):
                best = end
                best_len = len(ph)
            idx = body.find(ph, idx + 1)
    if best >= lo:
        return best
    # Prefer break after light particles / business nouns within window
    mid = max(lo, cap // 2)
    for i in range(cap, mid - 1, -1):
        if body[i - 1] in "的了着过里中上到好齐快件货心地时":
            return i
    return cap
