#!/usr/bin/env python3
"""客户机单向媒体同步：极空间团队空间 → 本机片库（只拉不推、远端删除不删本地）。

默认映射（始峰 · 徐玲飞 iPhone「最近项目」→ HWABR-Q Camera）：
  远程 团队空间/手机相册备份/徐玲飞/Iphone/iPhone 11 Pro Max/iPhone 11 Pro Max相册备份/最近项目
  本地 {settings.library_root}/徐玲飞/HWABR-Q/HWABR-Q相册备份/Camera
  （例 /Users/xlf/Movies/速影工作区/速影客户/北京始峰伟业/01-片库/…/Camera）

依赖本机已登录的极空间客户端本地代理。复用 zspace-team-sync.py 的下载与安全目录校验。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC_CANDIDATES = [
    HERE / "zspace-team-sync.py",
    Path.home() / "QR" / "tools" / "zspace-team-sync.py",
]
LOG_DIR = Path.home() / ".qr" / "logs"
STATE_PATH = Path.home() / ".qr" / "one-way-media-pull-state.json"
CONFIG_PATH = Path.home() / ".qr" / "suying-sync.json"
SETTINGS_PATH = Path.home() / "Suying" / "data" / "settings.json"

DEFAULT_REMOTE = (
    "/public/手机相册备份/徐玲飞/Iphone/iPhone 11 Pro Max/"
    "iPhone 11 Pro Max相册备份/最近项目"
)
# 相对片库 library_root 的子路径（两端机器一致）
LOCAL_UNDER_LIBRARY = Path("徐玲飞/HWABR-Q/HWABR-Q相册备份/Camera")
# 相对本机工作区根的客户片库路径（无 settings 时回退）
LOCAL_UNDER_WORKSPACE = Path(
    "速影客户/北京始峰伟业/01-片库/徐玲飞/HWABR-Q/HWABR-Q相册备份/Camera"
)
DEFAULT_LOCAL = Path.home() / "Movies" / "速影工作区" / LOCAL_UNDER_WORKSPACE


def _load_sync_module():
    for path in SYNC_CANDIDATES:
        if not path.is_file():
            continue
        name = "suying_zspace_team_sync"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        # 必须先注册，否则 @dataclass 在 Py3.13 下解析注解会失败
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod, path
    raise RuntimeError(
        "找不到 zspace-team-sync.py（请放在 packaging/zspace-sync 或 ~/QR/tools）"
    )


def _library_root_from_settings() -> Path | None:
    """客户机片库真相：~/Suying/data/settings.json 的 paths.library_root。"""
    if not SETTINGS_PATH.is_file():
        return None
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    lib = str((raw.get("paths") or {}).get("library_root") or "").strip()
    if not lib:
        return None
    path = Path(lib).expanduser()
    return path if path.exists() else path


def _resolve_default_local(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()

    # 1) settings.library_root + 固定子路径（Movies 工作区切径后的权威）
    lib = _library_root_from_settings()
    if lib is not None:
        candidate = lib / LOCAL_UNDER_LIBRARY
        if candidate.parent.exists() or candidate.exists() or lib.exists():
            return candidate

    # 2) 本机默认：~/Movies/速影工作区/…
    if DEFAULT_LOCAL.parent.exists() or DEFAULT_LOCAL.exists():
        return DEFAULT_LOCAL

    # 3) 兼容旧外置盘布局（仅回退）
    volumes = Path("/Volumes")
    if volumes.is_dir():
        for vol in volumes.iterdir():
            if not vol.is_dir() or vol.name.startswith("."):
                continue
            hit = vol / "极空间团队文件同步" / LOCAL_UNDER_WORKSPACE
            if hit.parent.exists() or hit.exists():
                return hit

    return DEFAULT_LOCAL


def main() -> int:
    ap = argparse.ArgumentParser(
        description="极空间团队空间 → 本机片库 Camera 单向媒体同步（只拉不推）"
    )
    ap.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help="团队空间路径（可用团队空间/… 或 /public/…）",
    )
    ap.add_argument(
        "--local",
        type=Path,
        default=None,
        help="本机目的目录（默认 library_root 下 Camera）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只枚举与比对，不下载")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多下载 N 个待补文件（0=不限；验收可用 1 或 2）",
    )
    ap.add_argument(
        "--skip-bound-check",
        action="store_true",
        help="跳过 ~/.qr/suying-sync.json 账号绑定校验（仅运维临时）",
    )
    args = ap.parse_args()

    sync, sync_path = _load_sync_module()
    log_file = LOG_DIR / "one-way-media-pull.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    remote = str(args.remote or "").strip().replace("\\", "/")
    if remote.startswith("团队空间/"):
        remote = "/public/" + remote[len("团队空间/") :].lstrip("/")
    elif not remote.startswith("/"):
        remote = "/public/" + remote.lstrip("/")
    if ".." in Path(remote).parts:
        raise SystemExit("remote 路径不合法")
    remote = "/" + remote.strip("/")

    local = _resolve_default_local(args.local)
    if not local.parent.exists() and not args.dry_run:
        # 允许目的叶目录不存在（会创建）；但父路径到片库侧必须已挂载
        raise SystemExit(f"本地目的父路径不存在（外置盘是否挂载？）: {local.parent}")

    lease = None
    try:
        session = sync.load_session()
        if not args.skip_bound_check:
            sync.assert_bound_account(session)
        proxy = f"http://127.0.0.1:{int(session.get('local_port') or 13581)}"
        try:
            urllib.request.urlopen(proxy + "/home/", timeout=5)
        except Exception as e:
            raise RuntimeError(
                f"无法连接极空间本地代理 {proxy}：请保持极空间客户端已登录。({e})"
            ) from e

        client = sync.ZSpaceClient(proxy, session)
        backend = client.to_backend_path(remote)
        sync.log(
            f"one-way PULL start remote={remote} backend={backend} local={local} "
            f"dry_run={args.dry_run} limit={args.limit or '∞'} sync_mod={sync_path}",
            log_file,
        )
        sync.log(
            f"账号: {session.get('username')} / {session.get('nas_id')} ({session.get('nas_name')})",
            log_file,
        )

        files = sync.walk_files(client, backend)
        # 仅直接目录下的文件时也兼容子树（walk 已递归）
        sync.log(f"远程文件数: {len(files)}", log_file)

        local.mkdir(parents=True, exist_ok=True)
        allowed = sync.assert_safe_destination_roots(local, [local])
        blocked = sync.protected_local_roots()
        lease = sync.acquire_destination_lease(allowed)

        pending: list[dict] = []
        skipped_same = 0
        for it in files:
            name = str(it.get("name") or "")
            if not name or name in {".DS_Store", "Thumbs.db"}:
                continue
            size = int(it.get("size") or 0)
            dest = local / name
            if dest.is_file() and dest.stat().st_size == size and size > 0:
                skipped_same += 1
                continue
            if dest.is_file() and size > 0 and dest.stat().st_size != size:
                sync.log(
                    f"size 不一致将覆盖: {name} local={dest.stat().st_size} remote={size}",
                    log_file,
                )
            pending.append(it)

        pending_total = len(pending)
        total_bytes = sum(int(it.get("size") or 0) for it in pending)
        sync.log(
            f"比对: 跳过={skipped_same} 待下载={pending_total} "
            f"≈{total_bytes / (1024**3):.3f} GiB",
            log_file,
        )

        if args.limit and args.limit > 0:
            pending = pending[: args.limit]
            sync.log(f"--limit 生效，本次最多下载 {len(pending)} 个", log_file)

        downloaded = failed = 0
        bytes_dl = 0
        errors: list[str] = []
        for idx, it in enumerate(pending, 1):
            name = str(it.get("name") or "")
            size = int(it.get("size") or 0)
            rpath = str(it.get("path") or "")
            dest = local / name
            try:
                sync.assert_safe_destination(
                    dest, allowed_roots=allowed, protected_roots=blocked
                )
            except Exception as e:
                failed += 1
                errors.append(f"{name}: {e}")
                sync.log(f"拒绝: {name}: {e}", log_file)
                continue
            if args.dry_run:
                sync.log(f"[dry-run] [{idx}/{len(pending)}] {name} ({size})", log_file)
                downloaded += 1
                bytes_dl += size
                continue
            try:
                sync.log(f"下载 [{idx}/{len(pending)}] {name} ({size})", log_file)
                client.download(rpath, dest, expected_size=size if size > 0 else None)
                downloaded += 1
                bytes_dl += size if size > 0 else dest.stat().st_size
            except Exception as e:
                failed += 1
                errors.append(f"{name}: {e}")
                sync.log(f"失败: {name}: {e}", log_file)

        summary = {
            "remote": remote,
            "backend": backend,
            "local": str(local),
            "dry_run": args.dry_run,
            "remote_files": len(files),
            "skipped_same": skipped_same,
            "pending_total": pending_total,
            "download_batch": len(pending),
            "downloaded": downloaded,
            "failed": failed,
            "bytes": bytes_dl,
            "errors": errors[:30],
            "zspace": {
                "username": session.get("username"),
                "nas_id": session.get("nas_id"),
                "nas_name": session.get("nas_name"),
            },
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        STATE_PATH.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sync.log(
            f"完成: downloaded={downloaded} failed={failed} skipped_same={skipped_same} "
            f"bytes≈{bytes_dl}",
            log_file,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    except Exception as e:
        sync.log(f"错误: {e}", log_file)
        print(f"错误: {e}", file=sys.stderr)
        return 2
    finally:
        if lease is not None:
            sync.release_destination_lease(lease)


if __name__ == "__main__":
    sys.exit(main())
