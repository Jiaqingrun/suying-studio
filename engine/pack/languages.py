"""Global language catalog for voice / subtitle / publish packs."""

from __future__ import annotations

from typing import Any

# Major regional languages for short-form commerce content.
# code → Edge Neural voice + UI labels + region bucket.
LANGUAGE_CATALOG: list[dict[str, Any]] = [
    {
        "code": "zh",
        "label_zh": "中文（简体）",
        "label_native": "简体中文",
        "region": "东亚",
        "edge_voice": "zh-CN-XiaoxiaoNeural",
        "cps": 3.8,
        "rtl": False,
    },
    {
        "code": "zh-TW",
        "label_zh": "中文（繁体）",
        "label_native": "繁體中文",
        "region": "东亚",
        "edge_voice": "zh-TW-HsiaoChenNeural",
        "cps": 3.8,
        "rtl": False,
    },
    {
        "code": "ja",
        "label_zh": "日语",
        "label_native": "日本語",
        "region": "东亚",
        "edge_voice": "ja-JP-NanamiNeural",
        "cps": 7.0,
        "rtl": False,
    },
    {
        "code": "ko",
        "label_zh": "韩语",
        "label_native": "한국어",
        "region": "东亚",
        "edge_voice": "ko-KR-SunHiNeural",
        "cps": 7.0,
        "rtl": False,
    },
    {
        "code": "en",
        "label_zh": "英语",
        "label_native": "English",
        "region": "欧美",
        "edge_voice": "en-US-JennyNeural",
        "cps": 13.0,
        "rtl": False,
    },
    {
        "code": "es",
        "label_zh": "西班牙语",
        "label_native": "Español",
        "region": "欧美",
        "edge_voice": "es-ES-ElviraNeural",
        "cps": 12.0,
        "rtl": False,
    },
    {
        "code": "pt",
        "label_zh": "葡萄牙语",
        "label_native": "Português",
        "region": "欧美",
        "edge_voice": "pt-BR-FranciscaNeural",
        "cps": 12.0,
        "rtl": False,
    },
    {
        "code": "fr",
        "label_zh": "法语",
        "label_native": "Français",
        "region": "欧美",
        "edge_voice": "fr-FR-DeniseNeural",
        "cps": 12.0,
        "rtl": False,
    },
    {
        "code": "de",
        "label_zh": "德语",
        "label_native": "Deutsch",
        "region": "欧美",
        "edge_voice": "de-DE-KatjaNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "it",
        "label_zh": "意大利语",
        "label_native": "Italiano",
        "region": "欧美",
        "edge_voice": "it-IT-ElsaNeural",
        "cps": 12.0,
        "rtl": False,
    },
    {
        "code": "ru",
        "label_zh": "俄语",
        "label_native": "Русский",
        "region": "欧亚",
        "edge_voice": "ru-RU-SvetlanaNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "ar",
        "label_zh": "阿拉伯语",
        "label_native": "العربية",
        "region": "中东/北非",
        "edge_voice": "ar-SA-ZariyahNeural",
        "cps": 10.0,
        "rtl": True,
    },
    {
        "code": "hi",
        "label_zh": "印地语",
        "label_native": "हिन्दी",
        "region": "南亚",
        "edge_voice": "hi-IN-SwaraNeural",
        "cps": 10.0,
        "rtl": False,
    },
    {
        "code": "th",
        "label_zh": "泰语",
        "label_native": "ไทย",
        "region": "东南亚",
        "edge_voice": "th-TH-PremwadeeNeural",
        "cps": 8.0,
        "rtl": False,
    },
    {
        "code": "vi",
        "label_zh": "越南语",
        "label_native": "Tiếng Việt",
        "region": "东南亚",
        "edge_voice": "vi-VN-HoaiMyNeural",
        "cps": 10.0,
        "rtl": False,
    },
    {
        "code": "id",
        "label_zh": "印尼语",
        "label_native": "Bahasa Indonesia",
        "region": "东南亚",
        "edge_voice": "id-ID-GadisNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "ms",
        "label_zh": "马来语",
        "label_native": "Bahasa Melayu",
        "region": "东南亚",
        "edge_voice": "ms-MY-YasminNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "tr",
        "label_zh": "土耳其语",
        "label_native": "Türkçe",
        "region": "欧亚",
        "edge_voice": "tr-TR-EmelNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "pl",
        "label_zh": "波兰语",
        "label_native": "Polski",
        "region": "欧美",
        "edge_voice": "pl-PL-ZofiaNeural",
        "cps": 11.0,
        "rtl": False,
    },
    {
        "code": "nl",
        "label_zh": "荷兰语",
        "label_native": "Nederlands",
        "region": "欧美",
        "edge_voice": "nl-NL-FennaNeural",
        "cps": 12.0,
        "rtl": False,
    },
]

_NONE = {
    "code": "none",
    "label_zh": "关闭",
    "label_native": "Off",
    "region": "其他",
    "edge_voice": None,
    "cps": 0,
    "rtl": False,
}

_BY_CODE = {row["code"]: row for row in LANGUAGE_CATALOG}
_BY_CODE["none"] = _NONE

# Aliases for older prefs / packs
_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh-TW",
    "zh-hk": "zh-TW",
    "pt-br": "pt",
    "pt-pt": "pt",
    "en-us": "en",
    "en-gb": "en",
}


def normalize_lang_code(code: str | None) -> str:
    raw = (code or "zh").strip()
    if not raw:
        return "zh"
    key = raw.replace("_", "-")
    low = key.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    # Exact catalog hit (preserve zh-TW casing)
    for c in _BY_CODE:
        if c.lower() == low:
            return c
    return "zh"


def language_codes(*, include_none: bool = True) -> tuple[str, ...]:
    codes = [r["code"] for r in LANGUAGE_CATALOG]
    if include_none:
        codes.append("none")
    return tuple(codes)


def get_language(code: str | None) -> dict[str, Any]:
    return dict(_BY_CODE.get(normalize_lang_code(code), _BY_CODE["zh"]))


def edge_voice_for_lang(code: str | None) -> str:
    meta = get_language(code)
    return str(meta.get("edge_voice") or "zh-CN-XiaoxiaoNeural")


def cps_for_lang(code: str | None) -> float:
    meta = get_language(code)
    return float(meta.get("cps") or 3.8)


def languages_for_api(*, include_none: bool = True) -> list[dict[str, Any]]:
    rows = list(LANGUAGE_CATALOG)
    if include_none:
        rows = rows + [_NONE]
    # Group-friendly payload
    return [
        {
            "code": r["code"],
            "label_zh": r["label_zh"],
            "label_native": r["label_native"],
            "region": r["region"],
            "rtl": bool(r.get("rtl")),
            "edge_voice": r.get("edge_voice"),
        }
        for r in rows
    ]


def is_cjk_lang(code: str | None) -> bool:
    return normalize_lang_code(code) in {"zh", "zh-TW", "ja", "ko"}
