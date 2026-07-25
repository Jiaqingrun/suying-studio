"""Per-customer cover template sets under 05-品牌/封面模板/.

Authoritative store (sync zone):
  速影客户/<客户>/05-品牌/封面模板/{index.json, tpl_<id>/…}

Legacy (work-area, migrate-once):
  速影工作区/db/cover_templates/ + cover_templates.json
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COVER_STORE_DIRNAME = "封面模板"
BRAND_DIRNAME = "05-品牌"

# Canonical cover slot specs — single source of truth for counts + dimensions.
# Each platform entry is an ordered list of slots with different requirements.
PLATFORM_COVER_SPECS: dict[str, list[dict[str, Any]]] = {
    "douyin": [
        {
            "index": 0,
            "id": "vertical",
            "label": "竖封面",
            "aspect": "9:16",
            "width": 1080,
            "height": 1920,
            "max_mb": 5,
            "role": "信息流/主页竖展示",
        },
        {
            "index": 1,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1920,
            "height": 1080,
            "max_mb": 5,
            "role": "横版推荐位与横视频",
        },
    ],
    "kuaishou": [
        {
            "index": 0,
            "id": "vertical",
            "label": "竖封面",
            "aspect": "9:16",
            "width": 1080,
            "height": 1920,
            "max_mb": 5,
            "role": "信息流竖展示；横视频亦需竖封面",
        },
        {
            "index": 1,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1920,
            "height": 1080,
            "max_mb": 5,
            "role": "横版推荐位",
        },
    ],
    "channels": [
        {
            "index": 0,
            "id": "vertical",
            "label": "竖封面",
            "aspect": "6:7",
            "width": 1080,
            "height": 1260,
            "max_mb": 5,
            "role": "视频号官方竖比例；朋友圈分享还会裁 1:1，核心居中",
        },
        {
            "index": 1,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1920,
            "height": 1080,
            "max_mb": 5,
            "role": "横版视频封面",
        },
    ],
    "xhs": [
        {
            "index": 0,
            "id": "feed",
            "label": "信息流封面",
            "aspect": "3:4",
            "width": 1080,
            "height": 1440,
            "max_mb": 5,
            "role": "小红书信息流最优比例",
        },
    ],
    "baijiahao": [
        {
            "index": 0,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1280,
            "height": 720,
            "max_mb": 5,
            "role": "百家号视频封面；建议 ≥720P，亦可 1920×1080",
        },
    ],
    "toutiao": [
        {
            "index": 0,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1920,
            "height": 1080,
            "max_mb": 5,
            "role": "今日头条/头条号信息流横卡",
        },
    ],
    "zhihu": [
        {
            "index": 0,
            "id": "vertical",
            "label": "竖封面",
            "aspect": "9:16",
            "width": 1080,
            "height": 1920,
            "max_mb": 5,
            "role": "知乎竖版视频",
        },
        {
            "index": 1,
            "id": "horizontal",
            "label": "横封面",
            "aspect": "16:9",
            "width": 1920,
            "height": 1080,
            "max_mb": 5,
            "role": "知乎横版视频",
        },
    ],
}

# Derived from PLATFORM_COVER_SPECS — do not hand-edit separately.
DEFAULT_SLOT_COUNTS: dict[str, int] = {
    plat: max(1, len(specs)) for plat, specs in PLATFORM_COVER_SPECS.items()
}

# Legacy file-name suffixes when writing slot files to disk.
SLOT_LABELS = ("primary", "secondary", "tertiary", "quaternary")


def slot_specs_for(platform: str | None = None) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
    """Return all platform specs, or one platform's list."""
    if platform is None:
        return {p: [dict(s) for s in specs] for p, specs in PLATFORM_COVER_SPECS.items()}
    plat = (platform or "").strip().lower()
    return [dict(s) for s in PLATFORM_COVER_SPECS.get(plat, [])]


def _slot_spec(platform: str, slot_index: int) -> dict[str, Any]:
    plat = (platform or "").strip().lower()
    specs = PLATFORM_COVER_SPECS.get(plat) or []
    if 0 <= slot_index < len(specs):
        return dict(specs[slot_index])
    return {
        "index": slot_index,
        "id": SLOT_LABELS[slot_index] if slot_index < len(SLOT_LABELS) else f"slot{slot_index + 1}",
        "label": f"槽{slot_index + 1}",
        "aspect": "",
        "width": 0,
        "height": 0,
        "max_mb": 5,
        "role": "",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def templates_root(store_root: Path) -> Path:
    """Cover store root itself (holds tpl_* dirs + index.json)."""
    root = Path(store_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def index_path(store_root: Path) -> Path:
    return Path(store_root) / "index.json"


def customer_cover_store(customer_root: Path | str) -> Path:
    """Fixed per-customer path: <customer_root>/05-品牌/封面模板/."""
    root = Path(customer_root) / BRAND_DIRNAME / COVER_STORE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.txt"
    if not readme.exists():
        readme.write_text(
            "速影封面模板套装目录（每客户固定）。\n"
            "index.json = 套装索引；tpl_<id>/ = 各平台槽位图。\n"
            "请用 App「封面设置」管理，勿手工改文件名结构。\n",
            encoding="utf-8",
        )
    return root


def legacy_cover_store(data_root: Path | str) -> Path:
    return Path(data_root) / "cover_templates"


def legacy_index_path(data_root: Path | str) -> Path:
    return Path(data_root) / "cover_templates.json"


def _migrate_legacy_store(legacy_data_root: Path, dest: Path) -> bool:
    """Copy legacy work-area templates into customer store if dest is empty."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_idx = index_path(dest)
    if dest_idx.exists():
        return False
    legacy_idx = legacy_index_path(legacy_data_root)
    legacy_root = legacy_cover_store(legacy_data_root)
    if not legacy_idx.exists() and not legacy_root.is_dir():
        return False
    if legacy_root.is_dir():
        for child in legacy_root.iterdir():
            target = dest / child.name
            if target.exists():
                continue
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            elif child.is_file():
                shutil.copy2(child, target)
    if legacy_idx.exists() and not dest_idx.exists():
        try:
            raw = json.loads(legacy_idx.read_text(encoding="utf-8"))
        except Exception:
            raw = _empty_index()
        dest_idx.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    elif not dest_idx.exists():
        save_index(dest, _empty_index())
    return True


def resolve_cover_store(
    *,
    customer_root: Path | str | None = None,
    data_root: Path | str | None = None,
    migrate: bool = True,
) -> Path:
    """Resolve authoritative cover store for the active customer.

    Prefer <customer_root>/05-品牌/封面模板/. If customer_root omitted, derive from
    settings.paths.output_root.parent. Optionally migrate legacy data_root store once.
    """
    root: Path | None = Path(customer_root) if customer_root else None
    if root is None:
        try:
            from engine.config.settings import load_settings

            settings = load_settings()
            out = Path(settings.paths.output_root)
            # standard: …/<客户>/02-成片 → parent is customer_root
            root = out.parent
            if data_root is None:
                data_root = settings.paths.data_root
        except Exception:
            root = None
    if root is None:
        # last resort: legacy work-area store
        dr = Path(data_root) if data_root else Path.home() / "QR-Volume" / "速影工作区" / "db"
        return templates_root(legacy_cover_store(dr))

    store = customer_cover_store(root)
    if migrate and data_root is not None:
        try:
            _migrate_legacy_store(Path(data_root), store)
        except Exception:
            pass
    return store


def resolve_cover_store_for_settings(settings: Any | None = None) -> Path:
    from engine.config.settings import load_settings

    s = settings or load_settings()
    return resolve_cover_store(
        customer_root=Path(s.paths.output_root).parent,
        data_root=s.paths.data_root,
        migrate=True,
    )


def slot_count_for(platform: str, index: dict[str, Any] | None = None) -> int:
    """Required slots for platform. Never below PRODUCT default (specs length)."""
    plat = (platform or "").strip().lower()
    product = max(1, int(DEFAULT_SLOT_COUNTS.get(plat, 1)))
    overrides = (index or {}).get("slot_counts") or {}
    if plat in overrides:
        return max(product, int(overrides[plat]))
    return product


def _merge_slot_counts(raw: dict[str, Any] | None) -> dict[str, int]:
    """Merge persisted counts with product defaults; lift undersized values."""
    merged = dict(DEFAULT_SLOT_COUNTS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            plat = str(k).strip().lower()
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            product = int(DEFAULT_SLOT_COUNTS.get(plat, 1))
            merged[plat] = max(product, max(1, n))
    return merged


def _empty_index() -> dict[str, Any]:
    return {
        "version": 1,
        "selected_id": None,
        "slot_counts": dict(DEFAULT_SLOT_COUNTS),
        "templates": [],
        "updated_at": _now(),
    }


def load_index(data_root: Path) -> dict[str, Any]:
    path = index_path(data_root)
    if not path.exists():
        idx = _empty_index()
        save_index(data_root, idx)
        return idx
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    idx = _empty_index()
    idx.update({k: v for k, v in raw.items() if k in idx or k in ("templates", "selected_id", "slot_counts")})
    if not isinstance(idx.get("templates"), list):
        idx["templates"] = []
    before = dict(idx.get("slot_counts") or {}) if isinstance(idx.get("slot_counts"), dict) else {}
    idx["slot_counts"] = _merge_slot_counts(idx.get("slot_counts") if isinstance(idx.get("slot_counts"), dict) else None)
    # Persist lifted defaults so old indexes catch up on disk.
    if before != idx["slot_counts"] or set(before) != set(idx["slot_counts"]):
        try:
            save_index(data_root, idx)
        except Exception:
            pass
    return idx


def save_index(data_root: Path, index: dict[str, Any]) -> Path:
    path = index_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    index = dict(index)
    index["updated_at"] = _now()
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def template_dir(data_root: Path, template_id: str) -> Path:
    d = templates_root(data_root) / template_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel_slot_path(template_id: str, platform: str, slot_idx: int) -> str:
    spec = _slot_spec(platform, slot_idx)
    fallback = SLOT_LABELS[slot_idx] if slot_idx < len(SLOT_LABELS) else f"slot{slot_idx + 1}"
    file_id = str(spec.get("id") or fallback)
    if file_id not in ("vertical", "horizontal", "feed", "cover"):
        file_id = fallback
    return f"{template_id}/{platform}_{file_id}.jpg"


def list_templates(data_root: Path) -> dict[str, Any]:
    idx = load_index(data_root)
    templates = []
    for t in idx.get("templates") or []:
        templates.append(enrich_template(data_root, t, idx))
    counts = idx.get("slot_counts") or dict(DEFAULT_SLOT_COUNTS)
    return {
        "ok": True,
        "selected_id": idx.get("selected_id"),
        "slot_counts": counts,
        "slot_specs": slot_specs_for(),
        "templates": templates,
        "root": str(templates_root(data_root)),
    }


def enrich_template(data_root: Path, tpl: dict[str, Any], index: dict[str, Any] | None = None) -> dict[str, Any]:
    idx = index or load_index(data_root)
    tid = str(tpl.get("id") or "")
    slots_meta: dict[str, list[dict[str, Any]]] = {}
    completeness: dict[str, bool] = {}
    for plat, n in (idx.get("slot_counts") or DEFAULT_SLOT_COUNTS).items():
        n = max(1, int(n))
        files = list((tpl.get("slots") or {}).get(plat) or [])
        entries = []
        ok = True
        for i in range(n):
            rel = files[i] if i < len(files) else None
            # Also accept legacy primary/secondary filenames if semantic path missing
            abs_path = None
            exists = False
            if rel:
                abs_path = templates_root(data_root) / rel
                exists = abs_path.is_file()
            if not exists:
                # legacy fallback: platform_primary.jpg / platform_secondary.jpg
                legacy = SLOT_LABELS[i] if i < len(SLOT_LABELS) else f"slot{i + 1}"
                legacy_rel = f"{tid}/{plat}_{legacy}.jpg"
                legacy_path = templates_root(data_root) / legacy_rel
                if legacy_path.is_file():
                    abs_path = legacy_path
                    rel = legacy_rel
                    exists = True
            if not exists:
                ok = False
            spec = _slot_spec(plat, i)
            entries.append(
                {
                    "index": i,
                    "id": spec.get("id"),
                    "label": spec.get("label") or (SLOT_LABELS[i] if i < len(SLOT_LABELS) else f"slot{i + 1}"),
                    "aspect": spec.get("aspect") or "",
                    "width": int(spec.get("width") or 0),
                    "height": int(spec.get("height") or 0),
                    "max_mb": int(spec.get("max_mb") or 5),
                    "role": spec.get("role") or "",
                    "rel": rel,
                    "path": str(abs_path) if exists else None,
                    "filled": exists,
                }
            )
        slots_meta[plat] = entries
        completeness[plat] = ok
    out = dict(tpl)
    out["slots_detail"] = slots_meta
    out["complete"] = completeness
    out["selected"] = tid == idx.get("selected_id")
    return out


def get_template(data_root: Path, template_id: str) -> dict[str, Any] | None:
    idx = load_index(data_root)
    for t in idx.get("templates") or []:
        if t.get("id") == template_id:
            return enrich_template(data_root, t, idx)
    return None


def create_template(data_root: Path, *, name: str) -> dict[str, Any]:
    idx = load_index(data_root)
    tid = f"tpl_{uuid.uuid4().hex[:8]}"
    template_dir(data_root, tid)
    slots: dict[str, list[str]] = {p: [] for p in (idx.get("slot_counts") or DEFAULT_SLOT_COUNTS)}
    tpl = {
        "id": tid,
        "name": (name or tid).strip() or tid,
        "slots": slots,
        "created_at": _now(),
        "updated_at": _now(),
    }
    idx.setdefault("templates", []).append(tpl)
    if not idx.get("selected_id"):
        idx["selected_id"] = tid
    save_index(data_root, idx)
    return enrich_template(data_root, tpl, idx)


def rename_template(data_root: Path, template_id: str, name: str) -> dict[str, Any]:
    idx = load_index(data_root)
    for t in idx.get("templates") or []:
        if t.get("id") == template_id:
            t["name"] = (name or t.get("name") or template_id).strip()
            t["updated_at"] = _now()
            save_index(data_root, idx)
            return enrich_template(data_root, t, idx)
    raise ValueError(f"封面模板不存在: {template_id}")


def delete_template(data_root: Path, template_id: str) -> dict[str, Any]:
    idx = load_index(data_root)
    before = list(idx.get("templates") or [])
    idx["templates"] = [t for t in before if t.get("id") != template_id]
    if len(idx["templates"]) == len(before):
        raise ValueError(f"封面模板不存在: {template_id}")
    if idx.get("selected_id") == template_id:
        idx["selected_id"] = idx["templates"][0]["id"] if idx["templates"] else None
    save_index(data_root, idx)
    d = templates_root(data_root) / template_id
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return list_templates(data_root)


def select_template(data_root: Path, template_id: str) -> dict[str, Any]:
    idx = load_index(data_root)
    if not any(t.get("id") == template_id for t in idx.get("templates") or []):
        raise ValueError(f"封面模板不存在: {template_id}")
    idx["selected_id"] = template_id
    save_index(data_root, idx)
    return list_templates(data_root)


def set_slot_image(
    data_root: Path,
    *,
    template_id: str,
    platform: str,
    slot_index: int,
    source_path: str | Path,
) -> dict[str, Any]:
    plat = (platform or "").strip().lower()
    idx = load_index(data_root)
    n = slot_count_for(plat, idx)
    if slot_index < 0 or slot_index >= n:
        raise ValueError(f"槽位越界: {plat} 需要 0..{n - 1}，收到 {slot_index}")
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"封面源文件不存在: {src}")

    tpl = None
    for t in idx.get("templates") or []:
        if t.get("id") == template_id:
            tpl = t
            break
    if not tpl:
        raise ValueError(f"封面模板不存在: {template_id}")

    rel = _rel_slot_path(template_id, plat, slot_index)
    dest = templates_root(data_root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)

    slots = dict(tpl.get("slots") or {})
    files = list(slots.get(plat) or [])
    while len(files) <= slot_index:
        files.append("")
    files[slot_index] = rel
    # trim trailing empties beyond required? keep length at least n
    while len(files) < n:
        files.append("")
    slots[plat] = files[: max(n, len(files))]
    tpl["slots"] = slots
    tpl["updated_at"] = _now()
    save_index(data_root, idx)
    return enrich_template(data_root, tpl, idx)


def clear_slot(
    data_root: Path,
    *,
    template_id: str,
    platform: str,
    slot_index: int,
) -> dict[str, Any]:
    plat = (platform or "").strip().lower()
    idx = load_index(data_root)
    for t in idx.get("templates") or []:
        if t.get("id") != template_id:
            continue
        slots = dict(t.get("slots") or {})
        files = list(slots.get(plat) or [])
        if 0 <= slot_index < len(files):
            rel = files[slot_index]
            if rel:
                p = templates_root(data_root) / rel
                if p.is_file():
                    p.unlink(missing_ok=True)
            files[slot_index] = ""
            slots[plat] = files
            t["slots"] = slots
            t["updated_at"] = _now()
            save_index(data_root, idx)
            return enrich_template(data_root, t, idx)
    raise ValueError(f"封面模板不存在: {template_id}")


def resolve_covers(
    data_root: Path,
    *,
    platform: str,
    pack_dir: str | Path | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """Resolve cover file paths for a platform.

    Prefer selected (or explicit) template slots; fall back to publish_pack covers.
    """
    plat = (platform or "").strip().lower()
    idx = load_index(data_root)
    need = slot_count_for(plat, idx)
    tid = (template_id or idx.get("selected_id") or "").strip() or None
    covers: list[Path] = []
    source = "none"
    missing: list[int] = []

    if tid:
        tpl = next((t for t in (idx.get("templates") or []) if t.get("id") == tid), None)
        if tpl:
            files = list((tpl.get("slots") or {}).get(plat) or [])
            for i in range(need):
                rel = files[i] if i < len(files) else None
                p = templates_root(data_root) / rel if rel else None
                if not (p and p.is_file()):
                    legacy = SLOT_LABELS[i] if i < len(SLOT_LABELS) else f"slot{i + 1}"
                    legacy_p = templates_root(data_root) / f"{tid}/{plat}_{legacy}.jpg"
                    if legacy_p.is_file():
                        p = legacy_p
                    else:
                        # semantic name without index entry
                        spec = _slot_spec(plat, i)
                        sid = str(spec.get("id") or "")
                        if sid:
                            sem = templates_root(data_root) / f"{tid}/{plat}_{sid}.jpg"
                            if sem.is_file():
                                p = sem
                if p and p.is_file():
                    covers.append(p)
                else:
                    missing.append(i)
            source = "template"
            if not missing and len(covers) >= need:
                return {
                    "ok": True,
                    "platform": plat,
                    "needed": need,
                    "covers": [str(p) for p in covers[:need]],
                    "source": source,
                    "template_id": tid,
                    "missing_slots": [],
                }

    # Fallback: pack covers
    pack_covers: list[Path] = []
    if pack_dir:
        pdir = Path(pack_dir)
        for name in ("cover.jpg", "cover_2.jpg", "cover_3.jpg", "cover_4.jpg"):
            c = pdir / name
            if c.is_file():
                pack_covers.append(c)
        # also cover_2.png etc.
        for c in sorted(pdir.glob("cover*")):
            if c.is_file() and c not in pack_covers:
                pack_covers.append(c)

    covers = pack_covers[:need]
    source = "pack" if covers else source
    missing = [i for i in range(need) if i >= len(covers)]
    ok = len(covers) >= need and not missing
    return {
        "ok": ok,
        "platform": plat,
        "needed": need,
        "covers": [str(p) for p in covers],
        "source": source,
        "template_id": tid,
        "missing_slots": missing,
        "error": None
        if ok
        else (
            f"封面槽位不齐：{plat} 需要 {need} 张，现有 {len(covers)}"
            + (f"（缺槽位 {missing}）" if missing else "")
            + ("；请在 App 封面模板补齐或导出足够 cover" if missing else "")
        ),
    }


def seed_template_from_pack(
    data_root: Path,
    *,
    template_id: str,
    pack_dir: str | Path,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Fill empty slots from publish_pack covers (non-destructive for filled slots)."""
    pdir = Path(pack_dir)
    pack_covers = []
    for name in ("cover.jpg", "cover_2.jpg", "cover_3.jpg"):
        c = pdir / name
        if c.is_file():
            pack_covers.append(c)
    if not pack_covers:
        raise FileNotFoundError(f"物料包无封面: {pdir}")
    idx = load_index(data_root)
    plats = platforms or list(idx.get("slot_counts") or DEFAULT_SLOT_COUNTS)
    last = None
    for plat in plats:
        n = slot_count_for(plat, idx)
        for i in range(n):
            # skip if already filled
            tpl = get_template(data_root, template_id)
            if not tpl:
                raise ValueError(f"封面模板不存在: {template_id}")
            detail = (tpl.get("slots_detail") or {}).get(plat) or []
            if i < len(detail) and detail[i].get("filled"):
                continue
            src = pack_covers[min(i, len(pack_covers) - 1)]
            last = set_slot_image(
                data_root,
                template_id=template_id,
                platform=plat,
                slot_index=i,
                source_path=src,
            )
    return last or get_template(data_root, template_id) or {}
