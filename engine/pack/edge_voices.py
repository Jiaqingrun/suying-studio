"""Edge TTS voice catalog — full Neural list for App rule selection.

Primary source: bundled snapshot (offline-safe). Optional live refresh via
``edge_tts.list_voices`` when network is up. No customer-name branches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "edge_voices_catalog.json"
_LIVE_TTL_SEC = 6 * 3600.0
_live_voices: list[dict[str, Any]] | None = None
_live_ts: float = 0.0

# Short Chinese titles for common production voices (override LocalName when needed).
_LABEL_OVERRIDES: dict[str, str] = {
    "zh-CN-XiaoxiaoNeural": "晓晓（女·默认）",
    "zh-CN-XiaoyiNeural": "晓伊（女·温柔）",
    "zh-CN-YunxiNeural": "云希（男）",
    "zh-CN-YunjianNeural": "云健（男）",
    "zh-CN-YunyangNeural": "云扬（男·新闻）",
    "zh-CN-YunxiaNeural": "云夏（男·童趣）",
    "zh-CN-liaoning-XiaobeiNeural": "晓北（女·辽宁）",
    "zh-CN-shaanxi-XiaoniNeural": "晓妮（女·陕西）",
    "zh-TW-HsiaoChenNeural": "曉臻（女·台湾）",
    "zh-TW-HsiaoYuNeural": "曉雨（女·台湾）",
    "zh-TW-YunJheNeural": "雲哲（男·台湾）",
    "zh-HK-HiuMaanNeural": "曉曼（女·香港）",
    "zh-HK-HiuGaaiNeural": "曉佳（女·香港）",
    "zh-HK-WanLungNeural": "雲龍（男·香港）",
    "en-US-JennyNeural": "Jenny（女·美式）",
    "en-US-GuyNeural": "Guy（男·美式）",
    "en-US-AriaNeural": "Aria（女·美式）",
    "en-GB-SoniaNeural": "Sonia（女·英式）",
    "ja-JP-NanamiNeural": "七海（女·日）",
    "ko-KR-SunHiNeural": "선히（女·韩）",
}


def _normalize_voice_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    vid = str(raw.get("id") or raw.get("ShortName") or "").strip()
    if not vid:
        return None
    locale = str(raw.get("locale") or raw.get("Locale") or "").strip()
    if not locale and "-" in vid:
        # zh-CN-XiaoxiaoNeural → zh-CN; zh-CN-liaoning-XiaobeiNeural → zh-CN
        parts = vid.split("-")
        if len(parts) >= 2:
            locale = f"{parts[0]}-{parts[1]}"
    gender = str(raw.get("gender") or raw.get("Gender") or "").strip()
    label = str(
        raw.get("label")
        or raw.get("LocalName")
        or raw.get("FriendlyName")
        or vid
    ).strip()
    if vid in _LABEL_OVERRIDES:
        label = _LABEL_OVERRIDES[vid]
    return {
        "id": vid,
        "locale": locale,
        "gender": gender,
        "label": label,
        "friendly": str(raw.get("friendly") or raw.get("FriendlyName") or "").strip(),
    }


@lru_cache(maxsize=1)
def _load_bundled_catalog() -> list[dict[str, Any]]:
    if not _CATALOG_PATH.is_file():
        return []
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("edge voice catalog unreadable: %s", exc)
        return []
    rows = data.get("voices") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row = _normalize_voice_row(item)
        if row:
            out.append(row)
    return out


def _try_live_voices() -> list[dict[str, Any]] | None:
    global _live_voices, _live_ts
    now = time.time()
    if _live_voices is not None and (now - _live_ts) < _LIVE_TTL_SEC:
        return _live_voices
    try:
        import edge_tts
    except ImportError:
        return None

    async def _fetch() -> list[dict[str, Any]]:
        raw = await edge_tts.list_voices()
        out: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            row = _normalize_voice_row(item)
            if row:
                out.append(row)
        out.sort(key=lambda r: (r["locale"], r["gender"], r["id"]))
        return out

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Called under an active loop (unlikely in FastAPI sync handler) — skip live.
            return _live_voices
        fetched = asyncio.run(_fetch())
        if fetched:
            _live_voices = fetched
            _live_ts = now
            return fetched
    except Exception as exc:  # noqa: BLE001 — network/offline best-effort
        log.debug("edge_tts list_voices failed: %s", exc)
    return _live_voices


def all_edge_voices(*, prefer_live: bool = False) -> list[dict[str, Any]]:
    """Return full Neural catalog (live when requested, else bundled)."""
    if prefer_live:
        live = _try_live_voices()
        if live:
            return list(live)
    bundled = _load_bundled_catalog()
    if bundled:
        return list(bundled)
    live = _try_live_voices()
    return list(live or [])


def edge_voice_label(voice_id: str) -> str:
    vid = (voice_id or "").strip()
    if not vid:
        return ""
    if vid in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[vid]
    for row in all_edge_voices():
        if row["id"] == vid:
            return str(row.get("label") or vid)
    # Humanize ShortName tail: zh-CN-XiaoyiNeural → Xiaoyi
    tail = vid.split("-")[-1].removesuffix("Neural")
    return tail or vid


def locale_prefixes_for_lang(lang: str | None) -> list[str]:
    """Map App voice_lang codes to Edge Locale prefixes."""
    code = (lang or "").strip() or "zh"
    if code in ("none",):
        return []
    if code in ("zh", "zh-CN", "zh-Hans"):
        return ["zh-CN"]
    if code in ("zh-TW", "zh-Hant"):
        return ["zh-TW"]
    if code in ("zh-HK",):
        return ["zh-HK"]
    if code.startswith("zh"):
        return ["zh-CN", "zh-TW", "zh-HK"]
    # en / en-US / en-GB → match locale startswith "en"
    base = code.split("-")[0].lower()
    return [base]


def filter_edge_voices(
    voices: list[dict[str, Any]],
    *,
    lang: str | None = None,
    locale: str | None = None,
    all_locales: bool = False,
) -> list[dict[str, Any]]:
    if all_locales:
        return list(voices)
    loc = (locale or "").strip()
    if loc:
        prefixes = [loc]
    else:
        prefixes = locale_prefixes_for_lang(lang)
    if not prefixes:
        return list(voices)
    out: list[dict[str, Any]] = []
    for row in voices:
        row_loc = str(row.get("locale") or "")
        rid = str(row.get("id") or "")
        for p in prefixes:
            pl = p.lower()
            if row_loc.lower().startswith(pl) or rid.lower().startswith(pl + "-") or rid.lower().startswith(pl):
                out.append(row)
                break
    return out


def list_edge_voices(
    *,
    lang: str | None = None,
    locale: str | None = None,
    all_locales: bool = False,
    prefer_live: bool = False,
) -> dict[str, Any]:
    voices = all_edge_voices(prefer_live=prefer_live)
    filtered = filter_edge_voices(
        voices, lang=lang, locale=locale, all_locales=all_locales
    )
    return {
        "ok": True,
        "count": len(filtered),
        "total": len(voices),
        "all_locales": bool(all_locales),
        "lang": lang,
        "locale": locale,
        "source": "live" if prefer_live and _live_voices is not None else "bundled",
        "voices": filtered,
    }
