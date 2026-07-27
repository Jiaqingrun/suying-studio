"""T2S carrier: install/backup/update payload only (no media library sync)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CARRIER_DIRNAME = "速影载体"
DEFAULT_MIRROR = Path.home() / "Suying" / "carrier"


def carrier_rel_parts() -> tuple[str, ...]:
    return (CARRIER_DIRNAME,)


def default_mirror_root() -> Path:
    return DEFAULT_MIRROR


def discover_carrier_roots() -> list[Path]:
    """Candidate local mirrors / mounts that contain 速影载体/."""
    candidates: list[Path] = []
    home = Path.home()
    configured = (os.environ.get("SUYING_CARRIER_ROOT") or "").strip()
    roots = [
        DEFAULT_MIRROR,
        home / "Desktop" / CARRIER_DIRNAME,
    ]
    if configured:
        roots.append(Path(configured).expanduser())
    volumes = Path("/Volumes")
    if volumes.is_dir():
        roots.extend(p / CARRIER_DIRNAME for p in volumes.iterdir() if p.is_dir())
    for p in roots:
        if not p.is_dir():
            continue
        if (p / "app").is_dir() or (p / "seed").exists() or (p / "backups").exists():
            candidates.append(p)
    # De-dupe
    seen: set[str] = set()
    out: list[Path] = []
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def resolve_carrier_root(value: str | Path | None = None) -> Path:
    """Return an approved carrier root; never accept arbitrary filesystem paths."""
    allowed = [DEFAULT_MIRROR, *discover_carrier_roots()]
    if value is None:
        return (allowed[0] if allowed else DEFAULT_MIRROR).expanduser().resolve()
    requested = Path(value).expanduser().resolve()
    for root in allowed:
        if requested == root.expanduser().resolve():
            return requested
    raise ValueError("载体路径必须是已发现的速影载体目录")


def resolve_carrier_backup(value: str | Path) -> Path:
    """Validate a backup file is inside an approved carrier's backups directory."""
    requested = Path(value).expanduser().resolve()
    for root in [DEFAULT_MIRROR, *discover_carrier_roots()]:
        backups = (root.expanduser().resolve() / "backups")
        if requested.is_relative_to(backups):
            return requested
    raise ValueError("备份文件必须位于已发现载体的 backups 目录")


def ensure_carrier_layout(root: Path) -> Path:
    """Create app/ seed/ backups/ under carrier root."""
    root = Path(root)
    for sub in ("app", "seed", "seed/industry", "seed/templates", "backups"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    defaults = root / "seed" / "install-defaults.json"
    if not defaults.is_file():
        defaults.write_text(
            json.dumps(
                {
                    "version": 1,
                    "vector_default_off": True,
                    "media_sync_default_off": True,
                    "carrier_only": True,
                    "workspace_under": "~/Suying/customers/<name>",
                    "notes": "T2S 仅同步本载体目录；片库留在客户本机。",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    readme = root / "README.txt"
    if not readme.is_file():
        readme.write_text(
            "速影载体（T2S）\n"
            "- app/     安装包 + latest.json（更新服务）\n"
            "- seed/    行业包与安装默认配置\n"
            "- backups/ 客户配置级备份（不含片库视频）\n"
            "禁止把 01-片库 放进本目录。\n",
            encoding="utf-8",
        )
    return root


def read_latest_manifest(carrier_root: Path | None = None) -> dict[str, Any] | None:
    roots = [carrier_root] if carrier_root else discover_carrier_roots()
    for root in roots:
        if not root:
            continue
        man = Path(root) / "app" / "latest.json"
        if not man.is_file():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_carrier_root"] = str(root)
                data["_manifest_path"] = str(man)
                return data
        except Exception:
            continue
    return None


def carrier_status(*, bound_nas_ok: bool | None = None) -> dict[str, Any]:
    roots = discover_carrier_roots()
    primary = roots[0] if roots else None
    man = read_latest_manifest(primary) if primary else read_latest_manifest()
    backups = 0
    if primary and (Path(primary) / "backups").is_dir():
        backups = sum(1 for _ in (Path(primary) / "backups").rglob("manifest.json"))
    return {
        "ok": bool(primary),
        "carrier_visible": bool(primary),
        "carrier_roots": [str(r) for r in roots],
        "primary_root": str(primary) if primary else None,
        "latest": {
            "version": (man or {}).get("version"),
            "force": bool((man or {}).get("force")),
            "notes": (man or {}).get("notes") or "",
        }
        if man
        else None,
        "backup_count": backups,
        "bound_nas_ok": bound_nas_ok,
        "media_sync_default": False,
        "whitelist": ["app/", "seed/", "backups/"],
    }


def export_config_backup(
    *,
    carrier_root: Path,
    customer_id: str | int,
    customer_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Write config-only snapshot under backups/<id>/<ts>/."""
    root = ensure_carrier_layout(Path(carrier_root))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = root / "backups" / str(customer_id) / ts
    dest.mkdir(parents=True, exist_ok=True)
    # Strip secrets
    safe = dict(payload)
    for k in ("api_key", "cursor_api_key", "token", "password", "secret"):
        safe.pop(k, None)
    if isinstance(safe.get("settings"), dict):
        s2 = dict(safe["settings"])
        for k in list(s2.keys()):
            if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower():
                s2.pop(k, None)
        safe["settings"] = s2
    (dest / "backup.json").write_text(
        json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    man = {
        "version": 1,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "excludes": ["library_videos", "output_mp4", "cache", "render", "full_db"],
    }
    (dest / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "path": str(dest), "manifest": man}


def restore_config_backup(
    backup_dir: Path,
    *,
    apply_to_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load config-level backup (no media). Returns payload; optional merge into settings dict."""
    root = Path(backup_dir)
    bak = root / "backup.json"
    if not bak.is_file():
        return {"ok": False, "error": f"缺少 backup.json: {bak}"}
    try:
        data = json.loads(bak.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if apply_to_settings is not None and isinstance(data.get("settings"), dict):
        for k, v in data["settings"].items():
            if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower():
                continue
            apply_to_settings[k] = v
    return {
        "ok": True,
        "path": str(root),
        "customer_name": data.get("customer_name") or data.get("name"),
        "keys": list(data.keys()) if isinstance(data, dict) else [],
        "payload": data,
    }


def seed_industry_into_carrier(carrier_root: Path, repo_configs: Path) -> dict[str, Any]:
    """Copy industry pack JSON samples into seed/industry/."""
    root = ensure_carrier_layout(Path(carrier_root))
    src_dir = Path(repo_configs)
    copied: list[str] = []
    industry = src_dir / "industry"
    if industry.is_dir():
        for f in industry.glob("*.json"):
            dest = root / "seed" / "industry" / f.name
            shutil.copy2(f, dest)
            copied.append(f.name)
    # also building-supply style packs under configs/
    for f in src_dir.glob("**/pack.json"):
        name = f.parent.name
        dest = root / "seed" / "industry" / f"{name}.pack.json"
        shutil.copy2(f, dest)
        copied.append(dest.name)
    customer_seeds = src_dir / "seeds"
    if customer_seeds.is_dir():
        for seed_dir in customer_seeds.iterdir():
            if not seed_dir.is_dir():
                continue
            dest = root / "seed" / "customers" / seed_dir.name
            shutil.copytree(seed_dir, dest, dirs_exist_ok=True)
            copied.append(f"customers/{seed_dir.name}")
    return {"ok": True, "copied": copied, "seed": str(root / "seed")}


_SEED_TARGET_PREFIXES = ("03-词池/", "04-音乐/", "05-品牌/")
_SEED_FORBIDDEN_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".db", ".sqlite", ".env"}


def list_customer_seeds(carrier_root: Path | None = None) -> list[dict[str, Any]]:
    """List validated, optional customer configuration seeds."""
    roots = [Path(carrier_root)] if carrier_root else discover_carrier_roots()
    seed_roots = [root / "seed" / "customers" for root in roots]
    packaged = Path(__file__).resolve().parents[2] / "configs" / "seeds"
    if packaged.is_dir():
        seed_roots.append(packaged)
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for seeds_root in seed_roots:
        if not seeds_root.is_dir():
            continue
        for seed_dir in sorted(p for p in seeds_root.iterdir() if p.is_dir()):
            manifest = seed_dir / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                files = data.get("files") or []
                if int(data.get("version") or 0) != 1 or not isinstance(files, list):
                    continue
                seed_id = str(data.get("id") or seed_dir.name)
                if seed_id in seen_ids:
                    continue
                seen_ids.add(seed_id)
                out.append(
                    {
                        "id": seed_id,
                        "name": str(data.get("name") or seed_dir.name),
                        "description": str(data.get("description") or ""),
                        "profile": data.get("profile") if isinstance(data.get("profile"), dict) else {},
                        "files": [
                            {"source": str(x.get("source") or ""), "target": str(x.get("target") or "")}
                            for x in files
                            if isinstance(x, dict)
                        ],
                        "_seed_dir": str(seed_dir),
                        "_carrier_root": str(seeds_root),
                    }
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    return out


def import_customer_seed(
    *,
    seed_id: str,
    customer_root: Path,
    overwrite: bool = False,
    carrier_root: Path | None = None,
) -> dict[str, Any]:
    """Copy an allowlisted seed into one customer's local config directories."""
    selected = next((s for s in list_customer_seeds(carrier_root) if s["id"] == seed_id), None)
    if not selected:
        raise ValueError(f"未找到客户种子: {seed_id}")
    seed_dir = Path(selected["_seed_dir"]).resolve()
    target_root = Path(customer_root).expanduser().resolve()
    copied: list[str] = []
    skipped: list[str] = []
    for item in selected["files"]:
        source_rel = Path(item["source"])
        target_rel = Path(item["target"])
        source_text = source_rel.as_posix()
        target_text = target_rel.as_posix()
        if (
            source_rel.is_absolute()
            or target_rel.is_absolute()
            or ".." in source_rel.parts
            or ".." in target_rel.parts
            or not target_text.startswith(_SEED_TARGET_PREFIXES)
            or source_rel.suffix.lower() in _SEED_FORBIDDEN_SUFFIXES
            or target_rel.suffix.lower() in _SEED_FORBIDDEN_SUFFIXES
        ):
            raise ValueError(f"客户种子含非法路径或文件类型: {source_text} -> {target_text}")
        src = (seed_dir / source_rel).resolve()
        dest = (target_root / target_rel).resolve()
        if not src.is_relative_to(seed_dir) or not dest.is_relative_to(target_root) or not src.is_file():
            raise ValueError(f"客户种子文件无效: {source_text}")
        if dest.exists() and not overwrite:
            skipped.append(target_text)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(target_text)
    return {
        "ok": True,
        "seed_id": seed_id,
        "name": selected["name"],
        "profile": selected["profile"],
        "copied": copied,
        "skipped": skipped,
        "overwrite": overwrite,
    }
