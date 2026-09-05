#!/usr/bin/env python3
"""Backup 速影 main-repo docs + git history + ops Keychain to T2S personal space.

Remote (API):
  /nvme11/my/data/速影/主仓备份/
    README.md
    manifest.json
    git/速影.bundle
    git/HEAD.txt
    keys/ops-keychain.json   # plaintext by owner authorization (personal NAS only)
    keys/README.md
    snapshot/docs/...
    snapshot/<root meta files>
    snapshot/.cursor/rules/...

Default channel: 极空间本地 API（须本机客户端已登录 T2S）。
Does not use NFS mount ~/QR/dev/T2s/ZSPACE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from scripts.ops_keychain_backup import (  # noqa: E402
    export_keychain,
    fingerprint_payload,
    write_export,
)
from engine.ops.t2s_paths import REPO_BACKUP_ROOT, REPO_BACKUP_UI  # noqa: E402
from scripts.publish_update_repo import _client_class  # noqa: E402

TARGET_NAS_ID = "T0210023G0UWV"
REMOTE_ROOT = REPO_BACKUP_ROOT
STATE_PATH = Path.home() / ".qr" / "suying-repo-backup-state.json"
LOG_PATH = Path.home() / ".qr" / "logs" / "suying-repo-backup.log"
STAGING_ROOT = Path.home() / "Suying" / "ops" / "repo-backup"

SKIP_NAME_PARTS = {".DS_Store", "Thumbs.db", "__pycache__"}
SKIP_SUFFIXES = {".tar.gz", ".tar.xz", ".tar.bz2", ".zip", ".7z", ".dmg", ".iso"}
# Large third-party source archives live in offline legal delivery; not mirrored here.
SKIP_REL_PREFIXES = (
    "docs/legal/source-offer/",
)

ROOT_META_FILES = (
    "PROJECT.md",
    "AGENTS.md",
    "README.md",
    "requirements.txt",
    ".gitignore",
)

README_BODY = """# 速影主仓备份（T2S）

运维机自动镜像：开发文档 + Git 历史 + 本机运维 Keychain（明文，个人 NAS）。

| 路径 | 内容 |
|------|------|
| `git/速影.bundle` | 全部分支/标签的 `git bundle`（可 `git clone 速影.bundle`） |
| `git/HEAD.txt` | 最近推送时的 HEAD 摘要 |
| `keys/ops-keychain.json` | 运维机 Keychain 明文导出（换机恢复用） |
| `snapshot/docs/` | 工作区 `docs/` 可读快照（含 `archive/`） |
| `snapshot/` 根文件 | `PROJECT.md` / `AGENTS.md` 等 |
| `manifest.json` | 推送元数据（commit、时间、文件摘要） |

**不是**客户更新仓（见并列目录「速影/更新包」）。不含片库、成片、Cookie。
`docs/legal/source-offer/` 大体积源码包默认不镜像（法务交付另仓）。

换机：下载 `keys/ops-keychain.json` 后执行  
`python3 scripts/ops_keychain_backup.py restore --from <该文件>`。

推送通道：极空间本地 API → `/nvme11/my/data/速影/主仓备份`。
"""

KEYS_README = """# keys/

`ops-keychain.json` 为运维机 Keychain **明文**备份（所有者授权：个人 T2S，不加密）。

换机步骤：

1. 新机安装极空间并登录同一 T2S，下载本文件到本机（权限建议 600）
2. `cd ~/QR/dev/速影 && python3 scripts/ops_keychain_backup.py restore --from ~/Downloads/ops-keychain.json`
3. 用 `python3 scripts/sign_update_release.py init-key`（不带 `--rotate`）确认发布公钥仍匹配 `trusted_release_keys.json`
4. 从 `git/速影.bundle` 恢复代码仓（如需要）

禁止：把本文件放进 Git、速影/更新包、团队空间或发给客户。
客户机设备密钥不得靠拷贝跨机复用；客户换机仍须运维新签发许可。
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def _should_skip(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in SKIP_NAME_PARTS for p in parts):
        return True
    for prefix in SKIP_REL_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return True
    lower = rel.lower()
    for suffix in SKIP_SUFFIXES:
        if lower.endswith(suffix):
            return True
    return False


def _iter_docs_files() -> Iterable[Path]:
    docs = ROOT / "docs"
    if not docs.is_dir():
        return
    for path in docs.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if _should_skip(rel):
            continue
        yield path


def _iter_meta_files() -> Iterable[Path]:
    for name in ROOT_META_FILES:
        path = ROOT / name
        if path.is_file():
            yield path
    rules = ROOT / ".cursor" / "rules"
    if rules.is_dir():
        for path in rules.rglob("*"):
            if path.is_file() and path.suffix in {".mdc", ".md"}:
                yield path


def _try_keys_fingerprint() -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Return (fingerprint, payload, error). Payload may be None on failure."""
    try:
        payload = export_keychain()
        return fingerprint_payload(payload), payload, None
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)


def _fingerprint() -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    short = _git("rev-parse", "--short", "HEAD")
    subject = _git("log", "-1", "--pretty=%s")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    entries: list[str] = []
    for path in sorted(_iter_docs_files(), key=lambda p: p.as_posix()):
        st = path.stat()
        rel = path.relative_to(ROOT).as_posix()
        entries.append(f"{rel}:{st.st_size}:{int(st.st_mtime)}")
    for path in sorted(_iter_meta_files(), key=lambda p: p.as_posix()):
        st = path.stat()
        rel = path.relative_to(ROOT).as_posix()
        entries.append(f"{rel}:{st.st_size}:{int(st.st_mtime)}")
    tree_fp = _sha256_text("\n".join(entries))
    keys_fp, keys_payload, keys_err = _try_keys_fingerprint()
    return {
        "head": head,
        "short": short,
        "subject": subject,
        "branch": branch,
        "dirty": dirty,
        "tree_fingerprint": tree_fp,
        "keys_fingerprint": keys_fp,
        "keys_payload": keys_payload,
        "keys_error": keys_err,
        "file_count": len(entries),
    }


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


def _prepare_staging(fp: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    staging = STAGING_ROOT
    staging.mkdir(parents=True, exist_ok=True)
    git_dir = staging / "git"
    snap_dir = staging / "snapshot"
    keys_dir = staging / "keys"
    git_dir.mkdir(parents=True, exist_ok=True)
    keys_dir.mkdir(parents=True, exist_ok=True)
    if snap_dir.exists():
        # Keep staging lean: wipe previous snapshot tree before rebuild.
        for child in snap_dir.rglob("*"):
            if child.is_file():
                child.unlink(missing_ok=True)
        for child in sorted(snap_dir.rglob("*"), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
    snap_dir.mkdir(parents=True, exist_ok=True)

    bundle = git_dir / "速影.bundle"
    _log(f"creating git bundle → {bundle}")
    subprocess.check_call(
        ["git", "-C", str(ROOT), "bundle", "create", str(bundle), "--all"],
    )
    head_txt = (
        f"branch={fp['branch']}\n"
        f"HEAD={fp['head']}\n"
        f"short={fp['short']}\n"
        f"subject={fp['subject']}\n"
        f"dirty={fp['dirty']}\n"
        f"pushed_at={_now_iso()}\n"
    )
    (git_dir / "HEAD.txt").write_text(head_txt, encoding="utf-8")

    copied: list[dict[str, Any]] = []
    for path in list(_iter_docs_files()) + list(_iter_meta_files()):
        rel = path.relative_to(ROOT).as_posix()
        dest = snap_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        digest = _sha256_file(dest)
        copied.append(
            {
                "path": rel,
                "size": dest.stat().st_size,
                "sha256": digest,
            }
        )

    keys_meta: dict[str, Any] = {
        "included": False,
        "item_count": 0,
        "fingerprint": fp.get("keys_fingerprint"),
        "error": fp.get("keys_error"),
    }
    keys_payload = fp.get("keys_payload")
    if isinstance(keys_payload, dict):
        keys_path = write_export(keys_dir / "ops-keychain.json", keys_payload)
        (keys_dir / "README.md").write_text(KEYS_README, encoding="utf-8")
        keys_meta = {
            "included": True,
            "item_count": int(keys_payload.get("item_count") or 0),
            "fingerprint": fingerprint_payload(keys_payload),
            "path": "keys/ops-keychain.json",
            "size": keys_path.stat().st_size,
            # Never put secrets into manifest.
            "accounts": [
                {"service": i["service"], "account": i["account"], "critical": i["critical"]}
                for i in (keys_payload.get("items") or [])
            ],
        }
        _log(f"exported keychain items={keys_meta['item_count']} (secrets not logged)")
    else:
        _log(f"keychain export skipped: {fp.get('keys_error') or 'unknown'}")

    (staging / "README.md").write_text(README_BODY, encoding="utf-8")
    manifest = {
        "schema": 1,
        "name": "速影主仓备份",
        "source_root": str(ROOT),
        "remote_root_api": REMOTE_ROOT,
        "remote_root_ui": REPO_BACKUP_UI,
        "pushed_at": _now_iso(),
        "git": {
            "branch": fp["branch"],
            "head": fp["head"],
            "short": fp["short"],
            "subject": fp["subject"],
            "dirty_worktree": fp["dirty"],
            "bundle": "git/速影.bundle",
            "bundle_sha256": _sha256_file(bundle),
            "bundle_size": bundle.stat().st_size,
        },
        "keys": keys_meta,
        "tree_fingerprint": fp["tree_fingerprint"],
        "snapshot_files": copied,
        "excludes": {
            "rel_prefixes": list(SKIP_REL_PREFIXES),
            "suffixes": sorted(SKIP_SUFFIXES),
        },
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return staging, bundle, manifest


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
    # Invalidate cache for parent after mutation.
    remote_listing_cache.pop(parent, None)
    return "upload"


def push(*, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    fp = _fingerprint()
    # Drop in-memory secrets before any result serialization.
    keys_payload = fp.pop("keys_payload", None)
    fp_public = {k: v for k, v in fp.items() if k != "keys_payload"}
    state = _load_state()
    same = (
        not force
        and state.get("head") == fp["head"]
        and state.get("tree_fingerprint") == fp["tree_fingerprint"]
        and state.get("keys_fingerprint") == fp.get("keys_fingerprint")
        and state.get("ok") is True
    )
    if same:
        _log(
            f"unchanged head={fp['short']} tree={fp['tree_fingerprint'][:12]}… "
            f"keys={(fp.get('keys_fingerprint') or 'none')[:12]}… skip"
        )
        return {"ok": True, "skipped": True, "reason": "unchanged", **fp_public}

    keys_only = (
        not force
        and state.get("ok") is True
        and state.get("head") == fp["head"]
        and state.get("tree_fingerprint") == fp["tree_fingerprint"]
        and state.get("keys_fingerprint") != fp.get("keys_fingerprint")
        and keys_payload is not None
    )

    if dry_run:
        _log(
            f"dry-run would push head={fp['short']} dirty={fp['dirty']} "
            f"files≈{fp['file_count']} keys_ok={keys_payload is not None} "
            f"keys_only={keys_only}"
        )
        return {"ok": True, "dry_run": True, "keys_only": keys_only, **fp_public}

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

    uploads: list[dict[str, str]] = []
    cache: dict[str, list[dict[str, Any]]] = {}
    client.ensure_remote_dir(REMOTE_ROOT)
    client.ensure_remote_dir(f"{REMOTE_ROOT}/keys")

    if keys_only:
        _log("keys-only delta: skip git bundle / docs snapshot rebuild")
        staging = STAGING_ROOT
        keys_dir = staging / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        keys_path = write_export(keys_dir / "ops-keychain.json", keys_payload)
        (keys_dir / "README.md").write_text(KEYS_README, encoding="utf-8")
        (staging / "README.md").write_text(README_BODY, encoding="utf-8")
        keys_meta = {
            "included": True,
            "item_count": int(keys_payload.get("item_count") or 0),
            "fingerprint": fingerprint_payload(keys_payload),
            "path": "keys/ops-keychain.json",
            "size": keys_path.stat().st_size,
            "accounts": [
                {
                    "service": i["service"],
                    "account": i["account"],
                    "critical": i["critical"],
                }
                for i in (keys_payload.get("items") or [])
            ],
        }
        manifest = {
            "schema": 1,
            "name": "速影主仓备份",
            "source_root": str(ROOT),
            "remote_root_api": REMOTE_ROOT,
            "remote_root_ui": REPO_BACKUP_UI,
            "pushed_at": _now_iso(),
            "mode": "keys_only",
            "git": {
                "branch": fp["branch"],
                "head": fp["head"],
                "short": fp["short"],
                "subject": fp["subject"],
                "dirty_worktree": fp["dirty"],
                "bundle": "git/速影.bundle",
                "bundle_sha256": state.get("bundle_sha256"),
                "bundle_size": state.get("bundle_size"),
            },
            "keys": keys_meta,
            "tree_fingerprint": fp["tree_fingerprint"],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        plan: list[tuple[Path, str]] = [
            (staging / "README.md", f"{REMOTE_ROOT}/README.md"),
            (staging / "manifest.json", f"{REMOTE_ROOT}/manifest.json"),
            (keys_path, f"{REMOTE_ROOT}/keys/ops-keychain.json"),
            (keys_dir / "README.md", f"{REMOTE_ROOT}/keys/README.md"),
        ]
        for local, remote in plan:
            action = _upload_if_needed(
                client, local, remote, remote_listing_cache=cache, force=True
            )
            uploads.append(
                {"remote": remote, "action": action, "bytes": str(local.stat().st_size)}
            )
            _log(f"uploaded {remote} ({local.stat().st_size} bytes)")

        backend_keys = client.ensure_remote_dir(f"{REMOTE_ROOT}/keys")
        keys_listing = client.list_dir(backend_keys)
        khit = next(
            (item for item in keys_listing if item.get("name") == "ops-keychain.json"),
            None,
        )
        expected = int(keys_meta.get("size") or 0)
        if not khit or _remote_size(khit) != expected:
            raise RuntimeError(
                f"远端 keys 大小校验失败: local={expected} remote={_remote_size(khit or {})}"
            )

        new_state = {
            "ok": True,
            "pushed_at": manifest["pushed_at"],
            "head": fp["head"],
            "short": fp["short"],
            "tree_fingerprint": fp["tree_fingerprint"],
            "keys_fingerprint": fp.get("keys_fingerprint"),
            "keys_included": True,
            "keys_item_count": int(keys_meta.get("item_count") or 0),
            "remote_root": REMOTE_ROOT,
            "bundle_sha256": state.get("bundle_sha256"),
            "bundle_size": state.get("bundle_size"),
            "uploaded": sum(1 for u in uploads if u["action"] == "upload"),
            "skipped_files": sum(1 for u in uploads if u["action"] == "skip"),
            "mode": "keys_only",
        }
        _save_state(new_state)
        _log(
            f"done keys-only head={fp['short']} keys={new_state['keys_item_count']}"
        )
        return {"ok": True, "skipped": False, "manifest": manifest, "state": new_state}

    # Full backup path.
    fp["keys_payload"] = keys_payload
    staging, bundle, manifest = _prepare_staging(fp)
    fp.pop("keys_payload", None)

    client.ensure_remote_dir(f"{REMOTE_ROOT}/git")
    client.ensure_remote_dir(f"{REMOTE_ROOT}/snapshot")

    plan = [
        (staging / "README.md", f"{REMOTE_ROOT}/README.md"),
        (staging / "manifest.json", f"{REMOTE_ROOT}/manifest.json"),
        (bundle, f"{REMOTE_ROOT}/git/速影.bundle"),
        (staging / "git" / "HEAD.txt", f"{REMOTE_ROOT}/git/HEAD.txt"),
    ]
    for path in (staging / "snapshot").rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(staging / "snapshot").as_posix()
        plan.append((path, f"{REMOTE_ROOT}/snapshot/{rel}"))
    for path in (staging / "keys").rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(staging / "keys").as_posix()
        plan.append((path, f"{REMOTE_ROOT}/keys/{rel}"))

    for local, remote in plan:
        action = _upload_if_needed(
            client, local, remote, remote_listing_cache=cache, force=force
        )
        uploads.append({"remote": remote, "action": action, "bytes": str(local.stat().st_size)})
        if action == "upload":
            _log(f"uploaded {remote} ({local.stat().st_size} bytes)")

    backend_git = client.ensure_remote_dir(f"{REMOTE_ROOT}/git")
    listing = client.list_dir(backend_git)
    hit = next((item for item in listing if item.get("name") == "速影.bundle"), None)
    if not hit or _remote_size(hit) != bundle.stat().st_size:
        raise RuntimeError(
            f"远端 bundle 大小校验失败: local={bundle.stat().st_size} "
            f"remote={_remote_size(hit or {})}"
        )

    keys_info = manifest.get("keys") or {}
    if keys_info.get("included"):
        backend_keys = client.ensure_remote_dir(f"{REMOTE_ROOT}/keys")
        keys_listing = client.list_dir(backend_keys)
        khit = next((item for item in keys_listing if item.get("name") == "ops-keychain.json"), None)
        expected = int(keys_info.get("size") or 0)
        if not khit or _remote_size(khit) != expected:
            raise RuntimeError(
                f"远端 keys 大小校验失败: local={expected} remote={_remote_size(khit or {})}"
            )

    new_state = {
        "ok": True,
        "pushed_at": manifest["pushed_at"],
        "head": fp["head"],
        "short": fp["short"],
        "tree_fingerprint": fp["tree_fingerprint"],
        "keys_fingerprint": fp.get("keys_fingerprint"),
        "keys_included": bool(keys_info.get("included")),
        "keys_item_count": int(keys_info.get("item_count") or 0),
        "remote_root": REMOTE_ROOT,
        "bundle_sha256": manifest["git"]["bundle_sha256"],
        "bundle_size": manifest["git"]["bundle_size"],
        "uploaded": sum(1 for u in uploads if u["action"] == "upload"),
        "skipped_files": sum(1 for u in uploads if u["action"] == "skip"),
        "mode": "full",
    }
    _save_state(new_state)
    _log(
        f"done head={fp['short']} uploaded={new_state['uploaded']} "
        f"skipped_files={new_state['skipped_files']} "
        f"bundle={new_state['bundle_size']} keys={new_state['keys_item_count']}"
    )
    return {"ok": True, "skipped": False, "manifest": manifest, "state": new_state}

def main() -> int:
    parser = argparse.ArgumentParser(description="Backup 速影 main repo to T2S 速影主仓备份")
    parser.add_argument("--force", action="store_true", help="忽略本地指纹，强制重建并上传")
    parser.add_argument("--dry-run", action="store_true", help="只计算指纹，不上传")
    args = parser.parse_args()
    try:
        result = push(force=bool(args.force), dry_run=bool(args.dry_run))
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        _log(f"ERROR {exc}")
        _save_state({**_load_state(), "ok": False, "error": str(exc), "failed_at": _now_iso()})
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # Avoid buffering when run under launchd.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
