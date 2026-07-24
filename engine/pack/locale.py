"""Offline locale helpers for publish_pack foreign variants (G4).

No cloud MT — glossary + structural paraphrase so smoke/CI stay offline.
Industry packs may later override via pack_data['locale_glossary'].
"""

from __future__ import annotations

import re
from typing import Any

# Common building-supply / retail hooks → English (extend via pack_data)
DEFAULT_GLOSSARY: dict[str, str] = {
    "仓配一体": "Warehouse + delivery in one",
    "工地一站配齐": "One-stop jobsite supply",
    "本地发货": "Ships from local stock",
    "发货快": "Fast dispatch",
    "今日达": "Same-day delivery",
    "门店实拍": "Shot in our store",
    "仓配": "Warehouse logistics",
    "配送": "Delivery",
    "门店": "Store",
    "五金": "Hardware",
    "批发": "Wholesale",
    "实拍": "Real footage",
    "欢迎咨询": "Inquire today",
}


def glossary_from_pack(pack_data: dict[str, Any] | None) -> dict[str, str]:
    g = dict(DEFAULT_GLOSSARY)
    if not pack_data:
        return g
    extra = (pack_data.get("locale_glossary") or {}).get("en") or pack_data.get("locale_glossary_en")
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and v:
                g[str(k)] = str(v)
    return g


def translate_phrase(text: str, glossary: dict[str, str] | None = None) -> str:
    """Replace longest glossary hits; leftover CJK → short English fallback."""
    src = (text or "").strip()
    if not src:
        return ""
    gloss = glossary or DEFAULT_GLOSSARY
    # Prefer longer keys first
    keys = sorted(gloss.keys(), key=len, reverse=True)
    out = src
    for k in keys:
        if k in out:
            out = out.replace(k, gloss[k])
    # Split on common separators and paraphrase residual CJK chunks
    parts = re.split(r"([｜|\n/·，,。！？!?]+)", out)
    rebuilt: list[str] = []
    for p in parts:
        if not p:
            continue
        if re.fullmatch(r"[｜|\n/·，,。！？!?]+", p):
            rebuilt.append(" — " if p in "｜|" else (" " if p in "，,、" else ". " if p in "。！？" else p))
            continue
        if re.search(r"[\u4e00-\u9fff]", p):
            rebuilt.append("Local real-shot supply")
        else:
            rebuilt.append(p.strip())
    en = " ".join(x for x in rebuilt if x and x.strip())
    en = re.sub(r"\s{2,}", " ", en).strip(" —.")
    return en or "Local supply highlight"


def build_platform_copy_en(
    *,
    title_zh: str,
    brand: str,
    theme: str,
    hashtags: list[str],
    music_credit: str,
    glossary: dict[str, str] | None = None,
    description: str = "",
) -> dict[str, Any]:
    from engine.pack.publish import PLATFORMS, _split_title_lines

    gloss = glossary or DEFAULT_GLOSSARY
    lines = _split_title_lines(title_zh)
    hook = translate_phrase(lines[0], gloss)
    sub = translate_phrase(lines[1], gloss) if len(lines) > 1 else f"{brand} local footage"
    theme_en = translate_phrase(theme, gloss) if theme else "supply"
    tags = [f"#{theme_en.replace(' ', '')}", "#shorts", f"#{brand}"]
    if hashtags:
        tags = [f"#{translate_phrase(t.lstrip('#'), gloss).replace(' ', '')}" for t in hashtags[:4]] + tags[:2]
    tag_line = " ".join(dict.fromkeys(tags))  # de-dupe preserve order

    platforms = {
        "douyin": {
            "platform": "douyin",
            "title": hook[:40],
            "body": f"{hook}\n{sub}\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags[:6],
        },
        "channels": {
            "platform": "channels",
            "title": f"{hook} · {brand}"[:64],
            "body": f"{sub}. Real warehouse/store footage from {brand}.\n{tag_line}",
            "hashtags": tags[:6],
        },
        "xhs": {
            "platform": "xhs",
            "title": f"「{hook}」{sub}"[:40],
            "body": f"{hook} | {sub}\nLocal dispatch tips\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags[:6],
        },
        "wechat_mp": {
            "platform": "wechat_mp",
            "title": f"{brand} | {hook}",
            "body": (
                f"Lead: {hook}.\n\n"
                f"{sub}. Companion short is local real-shot montage.\n\n"
                f"{translate_phrase(description, gloss) if description else ''}\n\n"
                f"(Music: {music_credit})"
            ).strip(),
            "hashtags": tags[:3],
        },
    }
    # keep key set aligned with zh
    _ = PLATFORMS
    return {
        "version": "1.0",
        "locale": "en",
        "brand": brand,
        "theme": theme_en,
        "source_title_zh": title_zh,
        "title_en": f"{hook} — {sub}" if sub else hook,
        "platforms": platforms,
    }


def narration_script_en(title_zh: str, *, brand: str, glossary: dict[str, str] | None = None) -> str:
    gloss = glossary or DEFAULT_GLOSSARY
    from engine.pack.publish import _split_title_lines

    lines = _split_title_lines(title_zh)
    parts = [translate_phrase(x, gloss) for x in lines]
    parts.append(f"From {brand}. Local stock, ready to ship.")
    return ". ".join(p.rstrip(".") for p in parts if p) + "."
