"""Load industry packs from configs/samples/industry/<id>/pack.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Repo root: engine/catalog/industry_pack.py → ../../..
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDUSTRY_ROOT = _REPO_ROOT / "configs" / "samples" / "industry"

# Minimal fallback if no pack file exists (product must boot without any customer fixture)
_FALLBACK: dict[str, Any] = {
    "id": "_builtin",
    "hooks": ["今日推荐", "现货速达", "品质之选", "欢迎咨询", "马上行动"],
    "content_themes": ["default"],
    "theme_rules": [],
    "pack_to_content": {"default": "default"},
    "content_to_pack": {"default": ["default"]},
    "template_bindings": {},
}


def industry_root() -> Path:
    return _INDUSTRY_ROOT


def list_industry_pack_ids() -> list[str]:
    if not _INDUSTRY_ROOT.is_dir():
        return []
    ids: list[str] = []
    for p in sorted(_INDUSTRY_ROOT.iterdir()):
        if p.is_dir() and (p / "pack.json").is_file():
            ids.append(p.name)
    return ids


@lru_cache(maxsize=16)
def load_industry_pack(pack_id: str | None = None) -> dict[str, Any]:
    """Load pack by id. Default `_blank` (product-neutral)."""
    pid = (pack_id or "_blank").strip() or "_blank"
    path = _INDUSTRY_ROOT / pid / "pack.json"
    if not path.is_file():
        # try blank then builtin
        blank = _INDUSTRY_ROOT / "_blank" / "pack.json"
        if blank.is_file():
            data = json.loads(blank.read_text(encoding="utf-8"))
        else:
            data = dict(_FALLBACK)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    # normalize theme_rules to list[tuple[str, tuple[str,...]]] consumers expect
    rules = data.get("theme_rules") or []
    data["_theme_rules_tuples"] = [
        (str(r["theme"]), tuple(str(k) for k in (r.get("keywords") or []))) for r in rules if r.get("theme")
    ]
    data.setdefault("hooks", list(_FALLBACK["hooks"]))
    data.setdefault("pack_to_content", {"default": "default"})
    data.setdefault("content_to_pack", {"default": ["default"]})
    data.setdefault("template_bindings", {})
    data.setdefault("content_themes", ["default"])
    return data


def clear_pack_cache() -> None:
    load_industry_pack.cache_clear()


def hooks_for_pack(pack_id: str | None = None) -> list[str]:
    return list(load_industry_pack(pack_id).get("hooks") or _FALLBACK["hooks"])


def theme_rules_for_pack(pack_id: str | None = None) -> list[tuple[str, tuple[str, ...]]]:
    return list(load_industry_pack(pack_id).get("_theme_rules_tuples") or [])


def pack_to_content_map(pack_id: str | None = None) -> dict[str, str]:
    return dict(load_industry_pack(pack_id).get("pack_to_content") or {"default": "default"})


def content_to_pack_map(pack_id: str | None = None) -> dict[str, list[str]]:
    raw = load_industry_pack(pack_id).get("content_to_pack") or {"default": ["default"]}
    return {str(k): list(v) for k, v in raw.items()}


def template_bindings_for_pack(pack_id: str | None = None) -> dict[str, str]:
    return {str(k).strip().lower(): str(v) for k, v in (load_industry_pack(pack_id).get("template_bindings") or {}).items()}


def piece_type_rules_for_pack(pack_id: str | None = None) -> dict[str, Any]:
    """Piece-type montage rules: slot prefer_scenes/objects + continuity."""
    raw = load_industry_pack(pack_id).get("piece_type_rules") or {}
    return {str(k): dict(v) if isinstance(v, dict) else {} for k, v in raw.items()}


def resolve_piece_type(content_theme: str | None, *, pack_id: str | None = None) -> str:
    """Map content theme → piece type id (pack.piece_type_bindings or defaults)."""
    theme = (content_theme or "default").strip() or "default"
    bindings = load_industry_pack(pack_id).get("piece_type_bindings") or {}
    if theme in bindings:
        return str(bindings[theme])
    # sensible defaults for building-supply-like packs
    defaults = {
        "配送": "配送承诺",
        "仓配": "配送承诺",
        "产品": "单品介绍",
        "施工机械": "单品介绍",
        "门店": "门店实力",
        "default": "综合日更",
    }
    return str(defaults.get(theme, "综合日更"))


def resolve_pack_id_from_profile(profile: dict[str, Any] | None) -> str | None:
    if not profile:
        return None
    pid = profile.get("industry_pack")
    return str(pid).strip() if pid else None


def pack_id_for_customer(name: str | None, profile: dict[str, Any] | None = None) -> str:
    """Resolve industry pack: profile → configs/customers/<name>/profile.sample.json → _blank."""
    pid = resolve_pack_id_from_profile(profile)
    if pid:
        return pid
    if name:
        sample = _REPO_ROOT / "configs" / "customers" / name / "profile.sample.json"
        if sample.is_file():
            try:
                data = json.loads(sample.read_text(encoding="utf-8"))
                sp = resolve_pack_id_from_profile(data if isinstance(data, dict) else None)
                if sp:
                    return sp
            except (OSError, json.JSONDecodeError):
                pass
    return "_blank"
