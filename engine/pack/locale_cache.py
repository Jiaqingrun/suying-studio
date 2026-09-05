"""Persist language catalog + curated locale templates into customer cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.pack.languages import LANGUAGE_CATALOG, get_language, normalize_lang_code
from engine.pack import locale as locale_mod


def locale_cache_root(data_root: Path | str) -> Path:
    root = Path(data_root) / "cache" / "locale_packs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def materialize_locale_packs(data_root: Path | str) -> dict[str, Any]:
    """Write one JSON per language under cache/locale_packs/ for offline App use."""
    root = locale_cache_root(data_root)
    written: list[str] = []
    for entry in LANGUAGE_CATALOG:
        code = normalize_lang_code(str(entry.get("code") or ""))
        if not code or code == "none":
            continue
        payload = {
            "code": code,
            "label_zh": entry.get("label_zh"),
            "label_native": entry.get("label_native"),
            "region": entry.get("region"),
            "edge_voice": entry.get("edge_voice"),
            "rtl": bool(entry.get("rtl")),
            "narration_template": locale_mod._NARRATION.get(code) or locale_mod._NARRATION.get("en"),
            "hook": locale_mod._HOOK.get(code) or locale_mod._HOOK.get("en"),
            "body": locale_mod._BODY.get(code) or locale_mod._BODY.get("en"),
            "glossary_defaults": dict(locale_mod.DEFAULT_GLOSSARY),
        }
        path = root / f"{code}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(code)
    index = {
        "version": 1,
        "languages": [
            {
                "code": e.get("code"),
                "label_zh": e.get("label_zh"),
                "label_native": e.get("label_native"),
                "region": e.get("region"),
            }
            for e in LANGUAGE_CATALOG
        ],
        "files": written,
    }
    (root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"ok": True, "root": str(root), "count": len(written), "languages": written}


def load_cached_language(data_root: Path | str, code: str) -> dict[str, Any] | None:
    code = normalize_lang_code(code)
    path = locale_cache_root(data_root) / f"{code}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def ensure_locale_cache(data_root: Path | str) -> dict[str, Any]:
    root = locale_cache_root(data_root)
    index = root / "index.json"
    if index.is_file() and any(root.glob("*.json")):
        try:
            meta = json.loads(index.read_text(encoding="utf-8"))
            if int(meta.get("version") or 0) >= 1 and meta.get("files"):
                return {"ok": True, "cached": True, "root": str(root), "count": len(meta.get("files") or [])}
        except Exception:
            pass
    return materialize_locale_packs(data_root)
