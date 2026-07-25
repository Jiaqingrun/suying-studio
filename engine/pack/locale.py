"""Offline locale helpers for publish_pack foreign variants.

No cloud MT — curated templates + glossary paraphrase so packs stay offline.
"""

from __future__ import annotations

import re
from typing import Any

from engine.pack.languages import get_language, is_cjk_lang, normalize_lang_code

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

# Curated VO templates — brand slot only; never speak on-screen title.
_NARRATION: dict[str, str] = {
    "zh": "这里是{brand}。本地仓配发货，现货更省心。实拍配货装车，用着更放心。",
    "zh-TW": "這裡是{brand}。在地倉配出貨，現貨更省心。實拍配貨裝車，用著更放心。",
    "en": "This is {brand}. Local stock and delivery, ready when you need it. Real warehouse footage you can trust.",
    "ja": "こちらは{brand}です。現地在庫と配送で、必要なときにすぐ対応。倉庫の実写映像です。",
    "ko": "여기는 {brand}입니다. 현지 재고와 배송으로 필요할 때 바로 대응합니다. 창고 실사 영상입니다.",
    "es": "Esto es {brand}. Stock local y envíos listos cuando lo necesite. Imágenes reales de nuestro almacén.",
    "pt": "Este é o {brand}. Estoque local e entrega pronta quando você precisar. Imagens reais do nosso depósito.",
    "fr": "Voici {brand}. Stock local et livraison prêts quand vous en avez besoin. Images réelles de notre entrepôt.",
    "de": "Das ist {brand}. Lokaler Bestand und Lieferung, bereit wenn Sie sie brauchen. Echte Lageraufnahmen.",
    "it": "Questo è {brand}. Stock locale e consegna pronti quando ti servono. Riprese reali del magazzino.",
    "ru": "Это {brand}. Местный склад и доставка — готовы, когда нужно. Реальные кадры со склада.",
    "ar": "هذه {brand}. مخزون محلي وتوصيل جاهز عند الحاجة. لقطات حقيقية من مستودعنا.",
    "hi": "यह {brand} है। लोकल स्टॉक और डिलीवरी, जब जरूरत हो तैयार। हमारे वेयरहाउस की असली फुटेज।",
    "th": (
        "นี่คือ {brand}. "
        "เรามีสต็อกในพื้นที่และการจัดส่ง พร้อมเมื่อคุณต้องการ. "
        "ภาพจริงจากคลังสินค้าของเรา. "
        "จัดของและจัดส่งจริง บริการที่ไว้วางใจได้. "
        "คัดของเร็ว ส่งไว ใช้งานมั่นใจ."
    ),
    "vi": "Đây là {brand}. Hàng sẵn tại kho và giao hàng khi bạn cần. Hình quay thật từ kho của chúng tôi.",
    "id": "Ini {brand}. Stok lokal dan pengiriman siap saat Anda butuh. Cuplikan nyata dari gudang kami.",
    "ms": "Ini {brand}. Stok tempatan dan penghantaran sedia bila anda perlukan. Rakaman sebenar dari gudang kami.",
    "tr": "Bu {brand}. Yerel stok ve teslimat, ihtiyacınız olduğunda hazır. Depomuzdan gerçek görüntüler.",
    "pl": "To {brand}. Lokalny magazyn i dostawa, gotowe gdy potrzebujesz. Prawdziwe ujęcia z naszego magazynu.",
    "nl": "Dit is {brand}. Lokale voorraad en levering, klaar wanneer u wilt. Echte beelden uit ons magazijn.",
}

_HOOK: dict[str, str] = {
    "zh": "本地仓配 · 发货更省心",
    "zh-TW": "在地倉配 · 出貨更省心",
    "en": "Local stock · Ready to ship",
    "ja": "現地在庫 · すぐ発送",
    "ko": "현지 재고 · 바로 출고",
    "es": "Stock local · Listo para enviar",
    "pt": "Estoque local · Pronto para enviar",
    "fr": "Stock local · Prêt à expédier",
    "de": "Lokaler Bestand · Versandbereit",
    "it": "Stock locale · Pronto per la spedizione",
    "ru": "Местный склад · Готово к отправке",
    "ar": "مخزون محلي · جاهز للشحن",
    "hi": "लोकल स्टॉक · भेजने को तैयार",
    "th": "สต็อกท้องถิ่น · พร้อมส่ง",
    "vi": "Hàng sẵn kho · Sẵn sàng giao",
    "id": "Stok lokal · Siap kirim",
    "ms": "Stok tempatan · Sedia hantar",
    "tr": "Yerel stok · Gönderime hazır",
    "pl": "Lokalny magazyn · Gotowe do wysyłki",
    "nl": "Lokale voorraad · Klaar om te verzenden",
}

_BODY: dict[str, str] = {
    "zh": "仓库实拍配货发货，服务更踏实。",
    "zh-TW": "倉庫實拍配貨出貨，服務更踏實。",
    "en": "Real warehouse picking and dispatch. Service you can rely on.",
    "ja": "倉庫の実写でピッキング・発送。安心してご利用ください。",
    "ko": "창고 실사로 피킹·출고. 믿을 수 있는 서비스.",
    "es": "Preparación y envío reales desde el almacén. Servicio de confianza.",
    "pt": "Separação e envio reais do depósito. Serviço em que você confia.",
    "fr": "Préparation et expédition réelles depuis l’entrepôt. Un service fiable.",
    "de": "Echte Kommissionierung und Versand aus dem Lager. Service, dem Sie vertrauen.",
    "it": "Picking e spedizione reali dal magazzino. Un servizio di cui fidarsi.",
    "ru": "Реальная комплектация и отгрузка со склада. Сервис, которому можно доверять.",
    "ar": "تجهيز وشحن حقيقيان من المستودع. خدمة يمكن الاعتماد عليها.",
    "hi": "वेयरहाउस से असली पिकिंग और डिस्पैच। भरोसेमंद सेवा।",
    "th": "จัดของและจัดส่งจริงจากคลัง บริการที่ไว้วางใจได้",
    "vi": "Soạn hàng và xuất kho thật. Dịch vụ đáng tin cậy.",
    "id": "Picking dan pengiriman nyata dari gudang. Layanan yang bisa dipercaya.",
    "ms": "Pengumpulan dan penghantaran sebenar dari gudang. Perkhidmatan yang boleh dipercayai.",
    "tr": "Depodan gerçek toplama ve sevkiyat. Güvenebileceğiniz hizmet.",
    "pl": "Prawdziwa kompletacja i wysyłka z magazynu. Usługa, której możesz zaufać.",
    "nl": "Echte picking en verzending uit het magazijn. Service waarop u kunt vertrouwen.",
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


# Latin / native spoken brand aliases (avoid feeding CJK into foreign TTS)
_BRAND_SPOKEN: dict[str, dict[str, str]] = {
    "始峰五金": {
        "en": "Shifeng",
        "th": "Shifeng",
        "ja": "シーフェン",
        "ko": "시펑",
        "vi": "Shifeng",
        "id": "Shifeng",
        "ms": "Shifeng",
        "default": "Shifeng",
    },
    "始峰": {
        "en": "Shifeng",
        "th": "Shifeng",
        "default": "Shifeng",
    },
}


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
    # Explicit aliases
    for key, table in _BRAND_SPOKEN.items():
        if key in raw:
            return table.get(code) or table.get("default") or "Shifeng"
    # Strip CJK; keep Latin leftovers
    latin = re.sub(r"[\u4E00-\u9FFF]+", "", raw)
    latin = re.sub(r"[（）()\s]+", " ", latin).strip()
    if latin and not re.search(r"[\u4E00-\u9FFF]", latin):
        return latin
    return "Shifeng"


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
    from engine.pack.publish import PLATFORMS, _split_title_lines

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

    platforms = {
        "douyin": {
            "platform": "douyin",
            "title": hook[:40],
            "body": f"{hook}\n{body}\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags[:6],
        },
        "channels": {
            "platform": "channels",
            "title": f"{hook} · {brand}"[:64],
            "body": f"{body}\n{tag_line}",
            "hashtags": tags[:6],
        },
        "xhs": {
            "platform": "xhs",
            "title": f"{hook}"[:40],
            "body": f"{hook} | {body}\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags[:6],
        },
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
