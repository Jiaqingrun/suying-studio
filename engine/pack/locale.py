"""Offline locale helpers for publish_pack foreign variants.

No cloud MT — curated templates + glossary paraphrase so packs stay offline.
"""

from __future__ import annotations

import re
from typing import Any

from engine.pack.languages import get_language, is_cjk_lang, normalize_lang_code

# Product-neutral glossary; industry terms come from pack_data.locale_glossary.
DEFAULT_GLOSSARY: dict[str, str] = {
    "实拍": "Real footage",
    "真实记录": "Real stories",
    "用心服务": "Service with care",
    "品质之选": "Quality choice",
    "欢迎咨询": "Inquire today",
}

# Curated VO templates — brand slot only; never speak on-screen title.
_NARRATION: dict[str, str] = {
    "zh": "这里是{brand}。真实现场认真记录，每一个细节都看得见。用心服务，更值得信赖。",
    "zh-TW": "這裡是{brand}。真實現場認真記錄，每一個細節都看得見。用心服務，更值得信賴。",
    "en": "This is {brand}. Real work, honestly documented. Every detail is visible, with service you can trust.",
    "ja": "こちらは{brand}です。実際の現場を丁寧に記録し、細部までお見せします。信頼できるサービスです。",
    "ko": "여기는 {brand}입니다. 실제 현장을 정성껏 기록하고 모든 세부를 보여드립니다. 믿을 수 있는 서비스입니다.",
    "es": "Esto es {brand}. Mostramos trabajo real y cada detalle, con un servicio en el que puede confiar.",
    "pt": "Este é o {brand}. Mostramos o trabalho real e cada detalhe, com um serviço de confiança.",
    "fr": "Voici {brand}. Nous montrons le travail réel et chaque détail, avec un service de confiance.",
    "de": "Das ist {brand}. Wir zeigen echte Arbeit und jedes Detail, mit einem Service, dem Sie vertrauen können.",
    "it": "Questo è {brand}. Mostriamo il lavoro reale e ogni dettaglio, con un servizio affidabile.",
    "ru": "Это {brand}. Мы показываем реальную работу и каждую деталь — сервису можно доверять.",
    "ar": "هذه {brand}. نعرض العمل الحقيقي وكل التفاصيل، مع خدمة يمكنك الوثوق بها.",
    "hi": "यह {brand} है। हम असली काम और हर विवरण दिखाते हैं, भरोसेमंद सेवा के साथ।",
    "th": "นี่คือ {brand}. เรานำเสนอการทำงานจริงและทุกรายละเอียด พร้อมบริการที่ไว้วางใจได้.",
    "vi": "Đây là {brand}. Chúng tôi ghi lại công việc thực tế và mọi chi tiết, với dịch vụ đáng tin cậy.",
    "id": "Ini {brand}. Kami menampilkan pekerjaan nyata dan setiap detail, dengan layanan tepercaya.",
    "ms": "Ini {brand}. Kami memaparkan kerja sebenar dan setiap perincian, dengan perkhidmatan yang dipercayai.",
    "tr": "Bu {brand}. Gerçek çalışmayı ve her ayrıntıyı, güvenilir hizmetle gösteriyoruz.",
    "pl": "To {brand}. Pokazujemy prawdziwą pracę i każdy szczegół, oferując godną zaufania obsługę.",
    "nl": "Dit is {brand}. We tonen echt werk en elk detail, met service waarop u kunt vertrouwen.",
}

_HOOK: dict[str, str] = {
    "zh": "真实记录 · 用心服务",
    "zh-TW": "真實記錄 · 用心服務",
    "en": "Real stories · Service with care",
    "ja": "リアルな記録 · 心を込めたサービス",
    "ko": "진솔한 기록 · 정성스러운 서비스",
    "es": "Historias reales · Servicio con cuidado",
    "pt": "Histórias reais · Serviço com cuidado",
    "fr": "Histoires réelles · Service attentionné",
    "de": "Echte Einblicke · Service mit Sorgfalt",
    "it": "Storie vere · Servizio con cura",
    "ru": "Реальные истории · Заботливый сервис",
    "ar": "قصص حقيقية · خدمة باهتمام",
    "hi": "असली कहानी · सेवा में पूरा ध्यान",
    "th": "เรื่องราวจริง · บริการด้วยความใส่ใจ",
    "vi": "Câu chuyện thật · Dịch vụ tận tâm",
    "id": "Cerita nyata · Layanan penuh perhatian",
    "ms": "Kisah sebenar · Perkhidmatan penuh perhatian",
    "tr": "Gerçek hikâyeler · Özenli hizmet",
    "pl": "Prawdziwe historie · Troskliwa obsługa",
    "nl": "Echte verhalen · Zorgzame service",
}

_BODY: dict[str, str] = {
    "zh": "真实现场与细节记录，服务更踏实。",
    "zh-TW": "真實現場與細節記錄，服務更踏實。",
    "en": "Real work and visible details. Service you can rely on.",
    "ja": "実際の仕事と細部をお見せします。信頼できるサービスです。",
    "ko": "실제 작업과 세부를 보여드립니다. 믿을 수 있는 서비스입니다.",
    "es": "Trabajo real y detalles visibles. Un servicio de confianza.",
    "pt": "Trabalho real e detalhes visíveis. Um serviço de confiança.",
    "fr": "Un travail réel et des détails visibles. Un service fiable.",
    "de": "Echte Arbeit und sichtbare Details. Ein verlässlicher Service.",
    "it": "Lavoro reale e dettagli visibili. Un servizio affidabile.",
    "ru": "Реальная работа и видимые детали. Надёжный сервис.",
    "ar": "عمل حقيقي وتفاصيل واضحة. خدمة موثوقة.",
    "hi": "असली काम और साफ़ विवरण। भरोसेमंद सेवा।",
    "th": "งานจริงและรายละเอียดที่มองเห็นได้ บริการที่ไว้วางใจได้",
    "vi": "Công việc thực tế và chi tiết rõ ràng. Dịch vụ đáng tin cậy.",
    "id": "Pekerjaan nyata dan detail yang terlihat. Layanan tepercaya.",
    "ms": "Kerja sebenar dan perincian yang jelas. Perkhidmatan dipercayai.",
    "tr": "Gerçek çalışma ve görünür ayrıntılar. Güvenilir hizmet.",
    "pl": "Prawdziwa praca i widoczne szczegóły. Godna zaufania obsługa.",
    "nl": "Echt werk en zichtbare details. Betrouwbare service.",
}


def glossary_from_pack(pack_data: dict[str, Any] | None, *, lang: str = "en") -> dict[str, str]:
    g = dict(DEFAULT_GLOSSARY)
    if not pack_data:
        return g
    locale_block = pack_data.get("locale_glossary") or {}
    code = normalize_lang_code(lang)
    extra = None
    if isinstance(locale_block, dict):
        extra = locale_block.get(code) or locale_block.get("en")
    if extra is None:
        extra = pack_data.get("locale_glossary_en")
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
    keys = sorted(gloss.keys(), key=len, reverse=True)
    out = src
    for k in keys:
        if k in out:
            out = out.replace(k, gloss[k])
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


def brand_for_spoken_lang(brand: str, lang: str) -> str:
    """Brand string safe to speak in ``lang`` (no CJK inside Thai/EN/… TTS)."""
    code = normalize_lang_code(lang)
    raw = (brand or "").strip() or "Brand"
    if code.startswith("zh"):
        # Prefer short name inside fullwidth parentheses
        if "（" in raw and "）" in raw:
            inner = raw[raw.find("（") + 1 : raw.find("）")].strip()
            if inner:
                return inner
        return raw
    # Strip CJK; retain a customer-configured Latin/native alias when present.
    latin = re.sub(r"[\u4E00-\u9FFF]+", "", raw)
    latin = re.sub(r"[（）()\s]+", " ", latin).strip()
    if latin and not re.search(r"[\u4E00-\u9FFF]", latin):
        return latin
    return "Brand"


def narration_script_for_lang(
    lang: str,
    *,
    brand: str,
    title_zh: str = "",
    glossary: dict[str, str] | None = None,
    speak_title: bool = False,
) -> str:
    """Build spoken script for a target language. Title is on-screen only by default."""
    code = normalize_lang_code(lang)
    if code == "none":
        return ""
    brand_s = brand_for_spoken_lang(brand, code)
    tpl = _NARRATION.get(code) or _NARRATION["en"]
    script = tpl.format(brand=brand_s)
    if speak_title and title_zh.strip():
        # Rare path: append a soft paraphrase for EN-like languages only
        if code == "en":
            hook = translate_phrase(title_zh.replace("\n", " "), glossary)
            script = f"{script} {hook}."
        elif code in ("zh", "zh-TW"):
            # Still avoid reading exact title; keep brand bed
            pass
    return script


def narration_script_en(title_zh: str, *, brand: str, glossary: dict[str, str] | None = None) -> str:
    """Back-compat EN helper (does not speak title by default)."""
    return narration_script_for_lang("en", brand=brand, title_zh=title_zh, glossary=glossary, speak_title=False)


def build_platform_copy_for_lang(
    lang: str,
    *,
    title_zh: str,
    brand: str,
    theme: str,
    hashtags: list[str],
    music_credit: str,
    glossary: dict[str, str] | None = None,
    description: str = "",
) -> dict[str, Any]:
    from engine.pack.publish import PLATFORMS, _split_title_lines, limit_platform_hashtags

    code = normalize_lang_code(lang)
    if code in ("zh", "zh-TW"):
        # Caller should use zh copy builder; provide a light traditional remint for zh-TW
        hook = _HOOK.get(code, _HOOK["zh"])
        body = _BODY.get(code, _BODY["zh"])
    elif code == "en":
        gloss = glossary or DEFAULT_GLOSSARY
        lines = _split_title_lines(title_zh)
        hook = translate_phrase(lines[0], gloss) if lines else _HOOK["en"]
        body = translate_phrase(lines[1], gloss) if len(lines) > 1 else _BODY["en"]
    else:
        hook = _HOOK.get(code, _HOOK["en"])
        body = _BODY.get(code, _BODY["en"])

    theme_label = theme or "supply"
    if code == "en":
        theme_label = translate_phrase(theme, glossary) if theme else "supply"
    tags = [f"#{str(theme_label).replace(' ', '')}", "#shorts", f"#{brand}"]
    if hashtags and code == "en":
        gloss = glossary or DEFAULT_GLOSSARY
        tags = [f"#{translate_phrase(t.lstrip('#'), gloss).replace(' ', '')}" for t in hashtags[:4]] + tags[:2]
    tag_line = " ".join(dict.fromkeys(tags))
    kuaishou_body, kuaishou_tags = limit_platform_hashtags(
        "kuaishou",
        f"{hook}\n{body}\n{tag_line}",
        tags,
    )

    from engine.pack.publish import ensure_ai_generated_disclosure

    platforms = {
        "douyin": {
            "platform": "douyin",
            "title": hook[:40],
            "body": ensure_ai_generated_disclosure(
                f"{hook}\n{body}\n{tag_line}\n🎵 {music_credit}"
            ),
            "hashtags": tags[:6],
        },
        "channels": {
            "platform": "channels",
            "title": f"{hook} · {brand}"[:64],
            "body": ensure_ai_generated_disclosure(f"{body}\n{tag_line}"),
            "hashtags": tags[:6],
        },
        "xhs": {
            "platform": "xhs",
            "title": f"{hook}"[:40],
            "body": ensure_ai_generated_disclosure(
                f"{hook} | {body}\n{tag_line}\n🎵 {music_credit}"
            ),
            "hashtags": tags[:6],
        },
        "kuaishou": {
            "platform": "kuaishou",
            "title": hook[:30],
            "body": ensure_ai_generated_disclosure(kuaishou_body),
            "hashtags": kuaishou_tags,
        },
    }
    optional_articles = {
        "wechat_mp": {
            "platform": "wechat_mp",
            "title": f"{brand} | {hook}",
            "body": (
                f"{hook}\n\n{body}\n\n"
                f"{description.strip() if description and is_cjk_lang(code) else ''}\n\n"
                f"(Music: {music_credit})"
            ).strip(),
            "hashtags": tags[:3],
        },
    }
    _ = PLATFORMS
    meta = get_language(code)
    return {
        "version": "1.1",
        "locale": code,
        "locale_label": meta.get("label_native") or code,
        "brand": brand,
        "theme": theme_label,
        "source_title_zh": title_zh,
        "title_localized": f"{hook} — {body}" if body else hook,
        "title_en": f"{hook} — {body}" if code == "en" else None,
        "platforms": platforms,
        "optional_articles": optional_articles,
    }


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
    return build_platform_copy_for_lang(
        "en",
        title_zh=title_zh,
        brand=brand,
        theme=theme,
        hashtags=hashtags,
        music_credit=music_credit,
        glossary=glossary,
        description=description,
    )


def subtitle_cue_text(lang: str, *, brand: str) -> str:
    """Short caption seed when no timed VO cues exist."""
    code = normalize_lang_code(lang)
    hook = _HOOK.get(code, _HOOK["en"])
    return f"{hook}｜{brand}"
