"""Build spoken narration scripts from plan/title (no customer hardcoding)."""

from __future__ import annotations

import re
from typing import Any

# Soft spoken pace for Edge 晓晓 @ -8% (chars / sec). Used to size scripts to picture length.
DEFAULT_SPOKEN_CPS_ZH = 3.8

# Theme sentence banks — sweet, professional; no interjections / no tech jargon / no「本期主题」.
_THEME_LINES: dict[str, list[str]] = {
    "仓配": [
        "这里是本地五金仓配。",
        "配货发货我们都帮您打理好。",
        "仓库分拣又快又整齐。",
        "常备现货好找好配。",
        "按单配货少折腾。",
        "装车发货也不耽误。",
        "本地发出更省心。",
        "工地用料跟得上进度。",
        "效率看得见，服务更贴心。",
        "用着更放心。",
    ],
    "配送": [
        "本地发货更近更及时。",
        "装车就走少耽搁。",
        "车队勤跑工地线。",
        "出库装车一条龙。",
        "送到工地您手边。",
        "补料加单也好配。",
        "节奏稳，对接也顺畅。",
        "少跑几趟，一次配齐。",
        "服务贴心，用着放心。",
    ],
    "门店": [
        "门店现货好挑选。",
        "到店提货也方便。",
        "常用料常备齐。",
        "选型有人帮着看。",
        "批发采购更从容。",
        "配齐再走少返工。",
        "本地仓近好取货。",
        "合作好商量，用着更踏实。",
    ],
    "施工机械": [
        "工地机具耗材常备。",
        "钢筋机械有现货可看。",
        "焊材配件一起配。",
        "进场配套少缺料。",
        "临时加单也能跟上。",
        "装车发货不耽误。",
        "现场要用，仓里好配。",
        "服务跟得上，用着放心。",
    ],
    "产品": [
        "工地常用五金机具齐。",
        "耗材配件常备好选。",
        "同等品质更好挑。",
        "现货直发少周转。",
        "配货清楚不糊涂。",
        "批发批量更好谈。",
        "本地实拍看得见。",
        "买得省心，用得放心。",
    ],
    "default": [
        "这里是本地五金仓配。",
        "配货发货我们都帮您打理好。",
        "仓库分拣又快又整齐。",
        "装车发货也不耽误。",
        "工地送到您手边。",
        "效率看得见，服务更贴心。",
        "少跑几趟路，一次配得齐。",
        "本地实拍，现货好配。",
        "用着更放心。",
    ],
}

_INTERJECTIONS = ("嗨", "呀", "哦", "嗯", "呐", "啦", "嘿", "哈", "哇")
_TECH = ("参数", "规格", "立方", "型号参数", "技术指标")


def title_content_fragments(title: str) -> list[str]:
    """On-screen title pieces that must never appear in VO / captions."""
    raw = (title or "").replace("\r\n", "\n").replace("\r", "\n").replace("｜", "\n").strip()
    if not raw:
        return []
    frags: list[str] = []
    for part in raw.split("\n"):
        t = re.sub(r"\s+", "", part.strip())
        if len(t) >= 4:
            frags.append(t)
    compact = re.sub(r"\s+", "", raw.replace("\n", ""))
    if len(compact) >= 4 and compact not in frags:
        frags.append(compact)
    # Longest first so replacements don't leave leftovers
    return sorted(set(frags), key=len, reverse=True)


def scrub_title_from_spoken(text: str, title: str) -> str:
    """Remove on-screen title wording from spoken / caption text."""
    out = text or ""
    for frag in title_content_fragments(title):
        if frag and frag in out:
            out = out.replace(frag, "")
    # Clean doubled punctuation / empty clauses left behind
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"[。．]{2,}", "。", out)
    out = re.sub(r"，([。！？])", r"\1", out)
    out = re.sub(r"(我们帮您打理好[。．]?)", "", out)  # orphaned title-tail phrase
    return out.strip(" ，,。．")


def text_contains_title(text: str, title: str) -> bool:
    compact = re.sub(r"\s+", "", (text or "").replace("\n", ""))
    if not compact:
        return False
    for frag in title_content_fragments(title):
        if frag and frag in compact:
            return True
    return False


def narration_script_zh(
    title: str,
    *,
    brand: str = "品牌",
    theme: str = "default",
    description: str = "",
    clip_hints: list[str] | None = None,
    target_duration_sec: float | None = None,
    cps: float = DEFAULT_SPOKEN_CPS_ZH,
    speak_title: bool = False,
) -> str:
    """Full-length Chinese voiceover sized to cover the picture (贯穿成片).

    On-screen titles are NOT spoken by default (speak_title=False).
    """
    target = float(target_duration_sec) if target_duration_sec and target_duration_sec > 0 else 26.0
    target = max(16.0, min(target, 45.0))
    # Aim slightly under picture length so VO fills the cut without being truncated
    need_chars = int(target * max(cps, 2.5) * 0.82)
    max_chars = int(target * max(cps, 2.5) * 0.92)

    lines: list[str] = []
    brand_short = _short_brand(brand)
    title_frags = title_content_fragments(title)

    def _has_title(ln: str) -> bool:
        c = re.sub(r"\s+", "", ln or "")
        return any(f in c for f in title_frags)

    # Open: brand soft lead (not「本期主题」, not on-screen title)
    lines.append(f"这里是{brand_short}。")

    # Title content is on-screen only — never weave into VO unless explicitly allowed
    if speak_title:
        t = (title or "").replace("\n", "").replace("｜", "").strip()
        if t and t not in ("", brand_short) and len(t) >= 4:
            if not any(t in ln for ln in lines):
                lines.append(f"{t}，我们帮您打理好。")

    # Theme body (rich) — skip redundant「这里是…」openers and title echoes
    for ln in _theme_bank(theme):
        if ln.startswith("这里是") and any(x.startswith("这里是") for x in lines):
            continue
        if _has_title(ln):
            continue
        if _ok_line(ln) and ln not in lines:
            lines.append(ln)
        if _est_chars(lines) >= need_chars:
            break

    # Clip-grounded beats (real footage cues)
    for h in clip_description_hints_as_lines(clip_hints, limit=3):
        if h and h not in "".join(lines) and _ok_line(h) and not _has_title(h):
            lines.append(h if h.endswith(("。", "！", "？")) else h + "。")
        if _est_chars(lines) >= need_chars:
            break

    desc = (description or "").strip()
    if desc:
        for sep in ("。", "！", "？", "\n"):
            if sep in desc:
                desc = desc.split(sep)[0].strip()
                break
        if desc and _ok_line(desc) and desc not in "".join(lines) and not _has_title(desc):
            lines.append(desc[:36] + ("。" if not desc.endswith("。") else ""))

    # Close
    closer = f"{brand_short}，本地实拍，配货发货更省心。"
    if closer not in lines and not _has_title(closer):
        lines.append(closer)
    if "用着更放心。" not in lines:
        lines.append("用着更放心。")

    # If still short, loop theme bank extras (unique)
    if _est_chars(lines) < need_chars:
        for ln in _theme_bank(theme) + _theme_bank("default"):
            if ln.startswith("这里是") and any(x.startswith("这里是") for x in lines):
                continue
            if _has_title(ln):
                continue
            if ln not in lines and _ok_line(ln):
                lines.append(ln)
            if _est_chars(lines) >= need_chars:
                break

    out: list[str] = []
    for p in lines:
        p = p.strip(" ，,")
        if not p or not _ok_line(p) or _has_title(p):
            continue
        if not p.endswith(("。", "！", "？", ".", "!", "?")):
            p = p + "。"
        out.append(p)

    # Hard-cap: drop middle body lines first, keep open + close
    while _est_chars(out) > max_chars and len(out) > 5:
        # remove from end of body (before last 2 closers)
        out.pop(-3)

    script = "".join(out) if out else f"{brand_short}为您精选实拍仓配内容，配货发货更省心。"
    if not speak_title:
        script = scrub_title_from_spoken(script, title)
        if not script:
            script = f"{brand_short}为您精选实拍仓配内容，配货发货更省心。"
    return script

def _short_brand(brand: str) -> str:
    b = (brand or "品牌").strip()
    # Prefer短称呼 for speech
    if "始峰五金" in b:
        return "始峰五金"
    if "（" in b and "）" in b:
        inner = b[b.find("（") + 1 : b.find("）")].strip()
        if inner:
            return inner
    return b[:12] if len(b) > 12 else b


def _theme_bank(theme: str) -> list[str]:
    t = (theme or "default").strip()
    for key, lines in _THEME_LINES.items():
        if key != "default" and key in t:
            return list(lines)
    return list(_THEME_LINES["default"])


def _est_chars(lines: list[str]) -> int:
    return sum(len(re.sub(r"\s+", "", ln)) for ln in lines)


def _ok_line(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("本期主题"):
        return False
    if any(x in t for x in _INTERJECTIONS):
        return False
    if any(x in t for x in _TECH):
        return False
    return True


def clip_description_hints_as_lines(hints: list[str] | None, *, limit: int = 3) -> list[str]:
    out: list[str] = []
    for h in hints or []:
        d = _clean_hint(h)
        if d and d not in out:
            out.append(d)
        if len(out) >= limit:
            break
    return out


def clip_description_hints(plan_or_clips: Any, *, limit: int = 3) -> list[str]:
    clips = getattr(plan_or_clips, "clips", None) or plan_or_clips or []
    out: list[str] = []
    for c in clips:
        candidates: list[str] = []
        if hasattr(c, "description"):
            candidates.append(str(getattr(c, "description", "") or ""))
        if hasattr(c, "scene"):
            candidates.append(str(getattr(c, "scene", "") or ""))
        if isinstance(c, dict):
            candidates.extend(
                [
                    str(c.get("description") or ""),
                    str(c.get("scene") or ""),
                    str(c.get("caption") or ""),
                ]
            )
        for raw in candidates:
            d = _clean_hint(raw)
            if d and d not in out:
                out.append(d)
                break
        if len(out) >= limit:
            break
    return out


def _clean_hint(raw: str) -> str:
    """Drop path-like / catalog metadata that sounds bad when spoken."""
    d = (raw or "").strip()
    if not d:
        return ""

    chunks = re.split(r"[；;|\n]+", d)
    scored: list[tuple[int, str]] = []
    for ch in chunks:
        ch = ch.strip()
        if not ch:
            continue
        if re.search(r"片段\s*\d|秒共|\d+\.\d+\s*-\s*\d+|竖屏|实拍业务素材|素材[:：]|分类[:：]|文件[:：]", ch):
            continue
        if re.fullmatch(r"[\w./\\-]+\.(mp4|mov|mxf|jpg|png)", ch, flags=re.I):
            continue
        ch = re.sub(r"\b[\w./\\-]+\.(mp4|mov|mxf|jpg|png)\b", "", ch, flags=re.I)
        ch = re.sub(r"\d{6,}", "", ch)
        ch = re.sub(r"\s+", " ", ch).strip(" ，,.;；：:")
        if len(ch) < 6:
            continue
        han = len(re.findall(r"[\u4e00-\u9fff]", ch))
        if han < 4:
            continue
        score = han * 2 + min(len(ch), 40)
        if "画面" in ch or "现场" in ch or "工人" in ch or "货架" in ch or "装车" in ch or "仓库" in ch:
            score += 12
        scored.append((score, ch))

    if not scored:
        d = re.sub(r"^(分类|素材|片段|文件|路径)\s*[:：]\s*", "", d)
        d = re.sub(r"[；;]\s*(分类|素材|片段|文件|竖屏|实拍业务素材)[^；;]*", "", d)
        d = re.sub(r"片段\s*\d[\d.\-–—秒共\s]*", "", d)
        d = re.sub(r"\s+", " ", d).strip(" ，,.;；")
        han = len(re.findall(r"[\u4e00-\u9fff]", d))
        if han < 6 or len(d) < 8:
            return ""
        return d[:48]

    scored.sort(key=lambda x: -x[0])
    return scored[0][1][:48]


def srt_from_narration_segments(
    segments: list[Any],
    *,
    video_duration_sec: float | None = None,
    bottom_dual_line: bool = True,
    max_chars_per_line: int = 14,
    tail_trim_seconds: float = 0.04,
    inter_sentence_gap_seconds: float = 0.08,
    forbid_title: str | None = None,
) -> str:
    """Build SRT from TTS segment timings (index/text/duration_sec).

    Locked caption rules: cue end tracks spoken audio; optional dual-line wrap;
    slight tail trim + gap so caption never outlives voice.
    """
    def _ts(sec: float) -> str:
        if sec < 0:
            sec = 0.0
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms >= 1000:
            s += 1
            ms = 0
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _wrap_dual(text: str) -> str:
        t = text.replace("\n", "").strip()
        if not bottom_dual_line or len(t) <= max_chars_per_line:
            return t
        mid = (len(t) + 1) // 2
        break_at = mid
        for i in range(mid, max(2, mid - 5), -1):
            if t[i - 1] in " ，,、；;":
                break_at = i
                break
        line1 = t[:break_at].strip(" ，,、；;")
        line2 = t[break_at:].strip(" ，,、；;")
        return f"{line1}\n{line2}" if line2 else line1

    lines: list[str] = []
    t = 0.0
    idx = 1
    for seg in segments:
        if hasattr(seg, "text"):
            text = str(seg.text or "").strip()
            dur = float(getattr(seg, "duration_sec", 0) or 0)
        elif isinstance(seg, dict):
            text = str(seg.get("text") or "").strip()
            dur = float(seg.get("duration_sec") or 0)
        else:
            continue
        if not text or dur <= 0.05:
            continue
        if text.startswith("本期主题"):
            t += dur + inter_sentence_gap_seconds
            continue
        if forbid_title:
            text = scrub_title_from_spoken(text, forbid_title)
        if not text:
            t += dur + inter_sentence_gap_seconds
            continue
        start = t
        end = t + max(0.05, dur - tail_trim_seconds)
        if video_duration_sec and start >= video_duration_sec:
            break
        if video_duration_sec:
            end = min(end, video_duration_sec)
        display = _wrap_dual(text)
        from engine.pack.text_sanitize import subtitle_display_text

        # CJK: strip punct/spaces (始峰 lock). Thai/Latin: keep spaces so glyphs stay readable.
        display = subtitle_display_text(display, keep_newlines=True)
        if not display:
            t = end + inter_sentence_gap_seconds
            continue
        lines.append(str(idx))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(display)
        lines.append("")
        t = end + inter_sentence_gap_seconds
        idx += 1
    if not lines and video_duration_sec:
        from engine.pack.publish import build_subtitle_srt

        return build_subtitle_srt("精彩内容", duration_sec=min(6.0, video_duration_sec))
    return "\n".join(lines).strip() + ("\n" if lines else "")
