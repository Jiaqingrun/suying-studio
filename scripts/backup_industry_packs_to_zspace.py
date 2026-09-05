#!/usr/bin/env python3
"""Backup 速影 industry packs + seeds + customer overlays to T2S personal space.

Remote (API):
  /nvme11/my/data/速影/行业包备份/
    README.md
    MANIFEST.json
    current/industry/<id>/…
    current/seeds/<id>/…
    current/customers-overlay/<name>/…
    releases/<stamp>_<label>/…   # only with --snapshot / --init-baseline

Default channel: 极空间本地 API（须本机客户端已登录 T2S）。
Does not use NFS mount ~/QR/dev/T2s/ZSPACE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from engine.ops.t2s_paths import INDUSTRY_BACKUP_ROOT, INDUSTRY_BACKUP_UI  # noqa: E402
from scripts.publish_update_repo import _client_class  # noqa: E402

TARGET_NAS_ID = "T0210023G0UWV"
REMOTE_ROOT = INDUSTRY_BACKUP_ROOT
STATE_PATH = Path.home() / ".qr" / "suying-industry-pack-backup-state.json"
LOG_PATH = Path.home() / ".qr" / "logs" / "suying-industry-pack-backup.log"
STAGING_ROOT = Path.home() / "Suying" / "ops" / "industry-pack-backup"

SKIP_NAMES = {".DS_Store", "Thumbs.db", "__pycache__"}
# Brand demo videos / archives are not industry-pack config; keep overlays lean.
SKIP_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".zip",
    ".7z",
    ".dmg",
    ".tar",
    ".gz",
    ".tgz",
}

# Stable catalog for human-readable aliases (ids remain directory names).
INDUSTRY_CATALOG: dict[str, dict[str, Any]] = {
    "building-supply": {
        "name": "建材仓配",
        "aliases": ["五金批发", "仓配建材"],
        "sample_customers": ["北京始峰伟业", "北京始峰五金"],
    },
    "life-service": {
        "name": "生活服务·门店护理",
        "aliases": ["美容护理", "养生美容"],
        "sample_customers": ["臻享丽人"],
    },
    "_blank": {
        "name": "空行业模板",
        "aliases": [],
        "sample_customers": [],
    },
}

README_BODY = """# 速影行业包备份（T2S）

运维机镜像：行业包 + 起步种子 + 客户覆写。用于**人眼对照基线**，少碰 Git。

| 路径 | 内容 |
|------|------|
| `current/industry/<id>/` | 去品牌行业包（最新） |
| `current/seeds/<id>/` | 起步种子（最新） |
| `current/customers-overlay/<名>/` | 客户覆写（最新） |
| `releases/<stamp>_<label>/` | 冻结基线（只增不改） |
| `MANIFEST.json` | 总索引（先打开这个） |

## 对照方式

1. 打开本目录 `MANIFEST.json`
2. 比较 `packs[].content_sha256` 与某 `releases[].packs[].content_sha256`
3. 不同则并排打开 `current/…/pack.json` 与 `releases/<stamp>/…/pack.json`

## 硬规则

- 一级目录只用行业 id / seed id / 客户名；中文别名只在 `meta.json`
- 禁止用客户名建行业目录；禁止写入「速影/更新包」或团队空间
- 编辑真相源仍是开发仓 `configs/`；改完再推送
- 不含音色包、片库、成片、Cookie、密钥、演示视频（.mp4 等）

推送通道：极空间本地 API → `/nvme11/my/data/速影/行业包备份`。
详见仓库 `docs/INDUSTRY_PACK_BACKUP_ZSPACE.md`。
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _remote_size(item: dict[str, Any]) -> int:
    return int(item.get("size") or item.get("file_size") or 0)


def _log(msg: str) -> None:
    line = f"{_now_iso()} {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES or path.suffix == ".pyc":
        return True
    lower = path.name.lower()
    return any(lower.endswith(suf) for suf in SKIP_SUFFIXES)


def _iter_source_files(src_dir: Path) -> list[Path]:
    if not src_dir.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_file() and not _should_skip(path):
            out.append(path)
    return out


def _tree_fingerprint(files: list[tuple[str, Path]]) -> str:
    """files: list of (relative_posix, path) for content hashing."""
    entries: list[str] = []
    for rel, path in sorted(files, key=lambda x: x[0]):
        entries.append(f"{rel}:{_sha256_file(path)}")
    return _sha256_text("\n".join(entries))


def _wipe_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _copy_tree_files(src: Path, dest: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    dest.mkdir(parents=True, exist_ok=True)
    for path in _iter_source_files(src):
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        copied.append(
            {
                "path": rel,
                "size": target.stat().st_size,
                "sha256": _sha256_file(target),
            }
        )
    return copied


def _read_industry_pack_id(pack_json: Path) -> str | None:
    try:
        data = json.loads(pack_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("id")
    return str(pid) if pid else None


def _read_customer_industry_pack(customer_dir: Path) -> str | None:
    profile = customer_dir / "profile.sample.json"
    if not profile.is_file():
        return None
    try:
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pack = data.get("industry_pack")
    return str(pack) if pack else None


def _collect_units() -> list[dict[str, Any]]:
    """Discover industry / seed / customer units from the repo."""
    units: list[dict[str, Any]] = []

    industry_root = ROOT / "configs" / "samples" / "industry"
    if industry_root.is_dir():
        for child in sorted(industry_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            pack = child / "pack.json"
            if not pack.is_file():
                continue
            catalog = INDUSTRY_CATALOG.get(child.name, {})
            units.append(
                {
                    "kind": "industry",
                    "id": child.name,
                    "name": catalog.get("name") or child.name,
                    "aliases": list(catalog.get("aliases") or []),
                    "sample_customers": list(catalog.get("sample_customers") or []),
                    "source": f"configs/samples/industry/{child.name}/",
                    "src_path": child,
                    "dest_rel": f"industry/{child.name}",
                }
            )

    seeds_root = ROOT / "configs" / "seeds"
    if seeds_root.is_dir():
        for child in sorted(seeds_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            units.append(
                {
                    "kind": "seed",
                    "id": child.name,
                    "name": child.name,
                    "aliases": [],
                    "sample_customers": [],
                    "source": f"configs/seeds/{child.name}/",
                    "src_path": child,
                    "dest_rel": f"seeds/{child.name}",
                }
            )

    customers_root = ROOT / "configs" / "customers"
    if customers_root.is_dir():
        for child in sorted(customers_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            units.append(
                {
                    "kind": "customer_overlay",
                    "id": child.name,
                    "name": child.name,
                    "aliases": [],
                    "sample_customers": [],
                    "industry_pack": _read_customer_industry_pack(child),
                    "source": f"configs/customers/{child.name}/",
                    "src_path": child,
                    "dest_rel": f"customers-overlay/{child.name}",
                }
            )

    return units


def _build_staging(*, backed_up_at: str) -> tuple[Path, dict[str, Any], str]:
    """Build current/ tree + root MANIFEST; return (staging, manifest, content_fp)."""
    _wipe_dir(STAGING_ROOT)
    current = STAGING_ROOT / "current"
    current.mkdir(parents=True, exist_ok=True)

    units = _collect_units()
    pack_entries: list[dict[str, Any]] = []
    fingerprint_files: list[tuple[str, Path]] = []

    for unit in units:
        dest = current / unit["dest_rel"]
        file_metas = _copy_tree_files(unit["src_path"], dest)
        # Fingerprint content files only (exclude meta.json we write next).
        content_files = [
            (f"{unit['dest_rel']}/{m['path']}", dest / m["path"]) for m in file_metas
        ]
        content_sha = _tree_fingerprint(content_files)
        for rel, path in content_files:
            fingerprint_files.append((rel, path))

        meta: dict[str, Any] = {
            "kind": unit["kind"],
            "id": unit["id"],
            "name": unit["name"],
            "aliases": unit["aliases"],
            "sample_customers": unit.get("sample_customers") or [],
            "source": unit["source"],
            "content_sha256": content_sha,
            "backed_up_at": backed_up_at,
            "files": file_metas,
        }
        if unit["kind"] == "customer_overlay" and unit.get("industry_pack"):
            meta["industry_pack"] = unit["industry_pack"]
        # Validate pack.json id when present
        pack_json = unit["src_path"] / "pack.json"
        if pack_json.is_file():
            pid = _read_industry_pack_id(pack_json)
            if pid and pid != unit["id"]:
                _log(f"WARN pack.json id={pid} dir={unit['id']}")

        meta_path = dest / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pack_entries.append(
            {
                "kind": unit["kind"],
                "id": unit["id"],
                "name": unit["name"],
                "aliases": unit["aliases"],
                "sample_customers": unit.get("sample_customers") or [],
                "industry_pack": unit.get("industry_pack"),
                "source": unit["source"],
                "content_sha256": content_sha,
                "path": f"current/{unit['dest_rel']}/",
            }
        )

    content_fp = _tree_fingerprint(fingerprint_files)
    head = _git("rev-parse", "HEAD")
    short = _git("rev-parse", "--short", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    subject = _git("log", "-1", "--pretty=%s")

    manifest: dict[str, Any] = {
        "schema": 1,
        "name": "速影行业包备份",
        "source_root": str(ROOT),
        "remote_root_api": REMOTE_ROOT,
        "remote_root_ui": INDUSTRY_BACKUP_UI,
        "backed_up_at": backed_up_at,
        "git_head": head,
        "git": {
            "branch": branch,
            "head": head,
            "short": short,
            "subject": subject,
        },
        "content_fingerprint": content_fp,
        "packs": pack_entries,
        "releases": [],  # filled after listing remote or from state on push
        "excludes": [
            "configs/voice_packs/",
            "片库",
            "成片",
            "Cookie",
            "密钥",
            "*.mp4/*.mov/*.zip",
        ],
    }

    (STAGING_ROOT / "README.md").write_text(README_BODY, encoding="utf-8")
    (STAGING_ROOT / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return STAGING_ROOT, manifest, content_fp


def _copy_current_to_release(
    staging: Path,
    stamp_label: str,
    *,
    backed_up_at: str,
    content_fp: str,
    packs: list[dict[str, Any]],
    git: dict[str, Any],
) -> Path:
    """Copy current/ into releases/<stamp_label>/ plus snapshot MANIFEST."""
    release_dir = staging / "releases" / stamp_label
    _wipe_dir(release_dir)
    src_current = staging / "current"
    for path in src_current.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src_current).as_posix()
        dest = release_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())

    snap_manifest = {
        "schema": 1,
        "name": "速影行业包备份·基线快照",
        "stamp_label": stamp_label,
        "backed_up_at": backed_up_at,
        "content_fingerprint": content_fp,
        "git": git,
        "packs": [
            {
                **{k: p[k] for k in p if k != "path"},
                "path": f"{p['path'].replace('current/', '', 1)}",
            }
            for p in packs
        ],
    }
    (release_dir / "MANIFEST.json").write_text(
        json.dumps(snap_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return release_dir


def _upload_if_needed(
    client: Any,
    local: Path,
    remote_file: str,
    *,
    remote_listing_cache: dict[str, list[dict[str, Any]]],
    force: bool,
) -> str:
    parent = str(Path(remote_file).parent).replace("\\", "/")
    backend_parent = client.ensure_remote_dir(parent)
    name = Path(remote_file).name
    if parent not in remote_listing_cache:
        remote_listing_cache[parent] = client.list_dir(backend_parent)
    listing = remote_listing_cache[parent]
    hit = next((item for item in listing if item.get("name") == name), None)
    local_size = local.stat().st_size
    if hit and not force and _remote_size(hit) == local_size:
        return "skip"
    client.upload(local, remote_file)
    remote_listing_cache.pop(parent, None)
    return "upload"


def _list_remote_releases(client: Any) -> list[dict[str, Any]]:
    """Best-effort list of existing release directory names on NAS."""
    releases_root = f"{REMOTE_ROOT}/releases"
    try:
        backend = client.ensure_remote_dir(releases_root)
        listing = client.list_dir(backend)
    except Exception as exc:  # noqa: BLE001
        _log(f"list releases skipped: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for item in listing:
        name = str(item.get("name") or "")
        if not name or name.startswith("."):
            continue
        # directories are directories; zspace may mark is_dir
        is_dir = item.get("is_dir")
        if is_dir is False:
            continue
        out.append({"stamp_label": name, "path": f"releases/{name}/"})
    return sorted(out, key=lambda x: x["stamp_label"])


def push(
    *,
    force: bool = False,
    dry_run: bool = False,
    snapshot: bool = False,
    init_baseline: bool = False,
    snapshot_label: str | None = None,
) -> dict[str, Any]:
    if init_baseline and snapshot:
        raise ValueError("不要同时传 --init-baseline 与 --snapshot")

    do_release = bool(snapshot or init_baseline)
    label = snapshot_label
    if init_baseline:
        label = label or "baseline"
    elif snapshot:
        label = label or "snapshot"

    backed_up_at = _now_iso()
    staging, manifest, content_fp = _build_staging(backed_up_at=backed_up_at)
    state = _load_state()

    same = (
        not force
        and not do_release
        and state.get("content_fingerprint") == content_fp
        and state.get("ok") is True
    )
    if same:
        _log(f"unchanged content={content_fp[:12]}… skip")
        return {
            "ok": True,
            "skipped": True,
            "reason": "unchanged",
            "content_fingerprint": content_fp,
            "git_head": manifest.get("git_head"),
        }

    stamp_label: str | None = None
    if do_release:
        stamp_label = f"{_stamp_utc()}_{label}"
        _copy_current_to_release(
            staging,
            stamp_label,
            backed_up_at=backed_up_at,
            content_fp=content_fp,
            packs=list(manifest["packs"]),
            git=dict(manifest.get("git") or {}),
        )
        _log(f"prepared release {stamp_label}")

    if dry_run:
        _log(
            f"dry-run would push content={content_fp[:12]}… "
            f"packs={len(manifest['packs'])} release={stamp_label or 'none'} "
            f"force={force}"
        )
        return {
            "ok": True,
            "dry_run": True,
            "content_fingerprint": content_fp,
            "pack_count": len(manifest["packs"]),
            "release_stamp": stamp_label,
            "packs": [
                {"kind": p["kind"], "id": p["id"], "content_sha256": p["content_sha256"]}
                for p in manifest["packs"]
            ],
        }

    session = load_zspace_session()
    if str(session.get("nas_id") or "") != TARGET_NAS_ID:
        raise RuntimeError(
            f"当前极空间不是目标 T2S（{TARGET_NAS_ID}），"
            f"实际={session.get('nas_id')} ({session.get('nas_name')})"
        )
    client = _client_class()(
        f"http://127.0.0.1:{session['local_port']}",
        session,
    )

    client.ensure_remote_dir(REMOTE_ROOT)
    client.ensure_remote_dir(f"{REMOTE_ROOT}/current")
    client.ensure_remote_dir(f"{REMOTE_ROOT}/releases")

    known_releases = _list_remote_releases(client)
    if stamp_label:
        # Ensure we never overwrite an existing release stamp.
        if any(r["stamp_label"] == stamp_label for r in known_releases):
            raise RuntimeError(f"远端已存在 releases/{stamp_label}/，拒绝覆盖")
        known_releases.append(
            {
                "stamp_label": stamp_label,
                "path": f"releases/{stamp_label}/",
                "content_fingerprint": content_fp,
                "backed_up_at": backed_up_at,
            }
        )
    manifest["releases"] = known_releases
    (staging / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    cache: dict[str, list[dict[str, Any]]] = {}
    uploads: list[dict[str, str]] = []

    plan: list[tuple[Path, str]] = [
        (staging / "README.md", f"{REMOTE_ROOT}/README.md"),
        (staging / "MANIFEST.json", f"{REMOTE_ROOT}/MANIFEST.json"),
    ]
    for path in (staging / "current").rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(staging / "current").as_posix()
        plan.append((path, f"{REMOTE_ROOT}/current/{rel}"))

    if stamp_label:
        release_local = staging / "releases" / stamp_label
        for path in release_local.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(release_local).as_posix()
            plan.append((path, f"{REMOTE_ROOT}/releases/{stamp_label}/{rel}"))

    for local, remote in plan:
        # Always force overwrite for current + root; releases are new paths only.
        is_release = "/releases/" in remote
        action = _upload_if_needed(
            client,
            local,
            remote,
            remote_listing_cache=cache,
            force=force or not is_release,
        )
        uploads.append(
            {"remote": remote, "action": action, "bytes": str(local.stat().st_size)}
        )
        if action == "upload":
            _log(f"uploaded {remote} ({local.stat().st_size} bytes)")

    new_state = {
        "ok": True,
        "pushed_at": backed_up_at,
        "content_fingerprint": content_fp,
        "git_head": manifest.get("git_head"),
        "remote_root": REMOTE_ROOT,
        "pack_count": len(manifest["packs"]),
        "last_release": stamp_label if stamp_label else state.get("last_release"),
        "baseline_initialized": bool(
            init_baseline or state.get("baseline_initialized")
        ),
        "baseline_stamp": state.get("baseline_stamp"),
        "uploaded": sum(1 for u in uploads if u["action"] == "upload"),
        "skipped_files": sum(1 for u in uploads if u["action"] == "skip"),
    }
    if init_baseline:
        new_state["baseline_initialized"] = True
        new_state["baseline_stamp"] = stamp_label
    if stamp_label:
        new_state["last_release"] = stamp_label
    _save_state(new_state)
    _log(
        f"done content={content_fp[:12]}… uploaded={new_state['uploaded']} "
        f"skipped_files={new_state['skipped_files']} release={stamp_label or 'none'}"
    )
    return {
        "ok": True,
        "skipped": False,
        "content_fingerprint": content_fp,
        "release_stamp": stamp_label,
        "state": new_state,
        "pack_count": len(manifest["packs"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup 速影 industry packs to T2S 速影行业包备份"
    )
    parser.add_argument("--force", action="store_true", help="忽略本地指纹，强制重推 current")
    parser.add_argument("--dry-run", action="store_true", help="只计算指纹与暂存，不上传")
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="推送 current 并新建 releases/<stamp>_snapshot/",
    )
    parser.add_argument(
        "--init-baseline",
        action="store_true",
        help="首次正式备份：推送 current + releases/<stamp>_baseline/",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="覆盖 release 标签后缀（默认 baseline / snapshot）",
    )
    args = parser.parse_args()
    try:
        result = push(
            force=bool(args.force),
            dry_run=bool(args.dry_run),
            snapshot=bool(args.snapshot),
            init_baseline=bool(args.init_baseline),
            snapshot_label=args.label,
        )
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        _log(f"ERROR {exc}")
        _save_state(
            {**_load_state(), "ok": False, "error": str(exc), "failed_at": _now_iso()}
        )
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
