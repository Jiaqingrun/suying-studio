"""Expression-layer prefs: voice_lang / subtitle_lang / subtitle_burn / dual pair (P2)."""

from __future__ import annotations

from typing import Any

from engine.pack.languages import language_codes, normalize_lang_code

SUPPORTED_LANGS = language_codes(include_none=True)
BURN_MODES = ("external", "burn_mono", "burn_dual")


def resolve_expression_prefs(
    profile: dict[str, Any] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve expression languages from customer profile + optional request overrides.

    Semantics (production + pack):
    - voice_lang: spoken narration language in the video (or none)
    - subtitle_lang: primary subtitle language (or none)
    - subtitle_burn: external | burn_mono | burn_dual
    - dual_secondary_lang: second line when burn_dual (defaults to zh if primary is foreign, else en)
    """
    expr: dict[str, Any] = {}
    if isinstance(profile, dict):
        raw = profile.get("expression") or {}
        if isinstance(raw, dict):
            expr = dict(raw)
    if overrides:
        for k in ("voice_lang", "subtitle_lang", "subtitle_burn", "dual_secondary_lang"):
            if overrides.get(k) is not None and str(overrides.get(k)).strip() != "":
                expr[k] = overrides[k]

    voice = normalize_lang_code(str(expr.get("voice_lang") or "zh"))
    if str(expr.get("voice_lang") or "").strip().lower() == "none":
        voice = "none"
    sub_raw = str(expr.get("subtitle_lang") or voice).strip()
    sub = "none" if sub_raw.lower() == "none" else normalize_lang_code(sub_raw)
    burn = str(expr.get("subtitle_burn") or "external").strip().lower() or "external"

    if voice not in SUPPORTED_LANGS:
        voice = "zh"
    if sub not in SUPPORTED_LANGS:
        sub = "zh"
    if burn not in BURN_MODES:
        burn = "external"

    dual_raw = str(expr.get("dual_secondary_lang") or "").strip()
    if dual_raw.lower() == "none":
        dual = "zh"
    elif dual_raw:
        dual = normalize_lang_code(dual_raw)
    else:
        # Sensible default for bilingual pair
        dual = "zh" if sub not in ("zh", "zh-TW", "none") else "en"
    if dual not in SUPPORTED_LANGS or dual == "none":
        dual = "zh" if sub not in ("zh", "zh-TW") else "en"
    # Avoid identical lines
    if dual == sub and burn == "burn_dual":
        dual = "en" if sub.startswith("zh") else "zh"

    return {
        "voice_lang": voice,
        "subtitle_lang": sub,
        "subtitle_burn": burn,
        "dual_secondary_lang": dual,
    }


def foreign_locales_for_prefs(prefs: dict[str, str]) -> list[str]:
    """Which foreign pack variants to emit (beyond always-on zh materials when applicable)."""
    langs: list[str] = []
    for key in ("voice_lang", "subtitle_lang", "dual_secondary_lang"):
        lang = prefs.get(key) or "zh"
        if lang not in ("zh", "zh-TW", "none") and lang not in langs:
            langs.append(lang)
        if lang == "zh-TW" and "zh-TW" not in langs:
            langs.append("zh-TW")
    return langs


def build_dual_subtitle_srt(primary_srt: str, secondary_srt: str) -> str:
    """Merge two mono SRTs into dual-line cues (timing from primary)."""

    def _parse(block: str) -> list[tuple[str, str, str]]:
        cues: list[tuple[str, str, str]] = []
        chunks = [c.strip() for c in (block or "").strip().split("\n\n") if c.strip()]
        for ch in chunks:
            lines = ch.split("\n")
            if len(lines) < 3:
                continue
            timing = lines[1]
            text = "\n".join(lines[2:]).strip()
            cues.append((lines[0], timing, text))
        return cues

    primary = _parse(primary_srt)
    secondary = _parse(secondary_srt)
    out: list[str] = []
    for i, (idx, timing, ptext) in enumerate(primary):
        stext = secondary[i][2] if i < len(secondary) else ""
        body = ptext if not stext else f"{ptext}\n{stext}"
        out.append(f"{idx}\n{timing}\n{body}\n")
    return "\n".join(out).strip() + ("\n" if out else "")


def srt_replace_cue_texts(base_srt: str, texts: list[str]) -> str:
    """Keep timings from base_srt; replace cue bodies with texts (cycled if short)."""
    chunks = [c.strip() for c in (base_srt or "").strip().split("\n\n") if c.strip()]
    if not chunks:
        return ""
    out: list[str] = []
    for i, ch in enumerate(chunks):
        lines = ch.split("\n")
        if len(lines) < 2:
            continue
        timing = lines[1] if "-->" in lines[1] else lines[0]
        idx = lines[0] if "-->" not in lines[0] else str(i + 1)
        if "-->" in lines[0]:
            timing = lines[0]
            idx = str(i + 1)
        text = texts[i] if i < len(texts) else (texts[-1] if texts else "")
        out.append(f"{idx}\n{timing}\n{text}\n")
    return "\n".join(out).strip() + ("\n" if out else "")
