#!/usr/bin/env python3
"""Sync ZSpace team space (/public) ↔ external disk via local client proxy.

Requires 极空间 desktop client logged in (local proxy port from vuex, often 13579/13581).
Official「文档同步」cannot select team space; this script uses the client tunnel API.

Directions from ~/.qr/suying-sync.json (installed by 速影 App):
  PULL/PUSH path maps per customer; legacy 始峰 uses 手机相册备份 aliases.

Other remote paths under /public still pull 1:1 into the local sync root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_LOCAL = Path("/Users/qr/QR-Volume/极空间团队文件同步")
DEFAULT_REMOTE = "/public"
DEFAULT_PROXY = "http://127.0.0.1:13579"
VUEX = Path.home() / "Library/Application Support/zspace/vuex.json"
LOG_DIR = Path.home() / ".qr/logs"
STATE_PATH = Path.home() / ".qr/zspace-team-sync-state.json"
LOCK_PATH = Path.home() / ".qr/zspace-team-sync.lock"
MOUNT_SCRIPT = Path.home() / "QR/tools/mount-qr-volume.sh"
CONFIG_PATH = Path.home() / ".qr" / "suying-sync.json"

PUSH_SKIP_PARTS = {".DS_Store", "rendering", "Thumbs.db"}

# Filled by load_runtime_maps()
PATH_ALIASES: list[tuple[str, str]] = []
PUSH_LOCAL_PREFIXES: list[str] = []


def _legacy_defaults() -> tuple[list[tuple[str, str]], list[str], Path]:
    aliases = [
        ("手机相册备份/徐玲飞", "速影客户/北京始峰伟业/01-片库/徐玲飞"),
        ("手机相册备份/车凯盛", "速影客户/北京始峰伟业/01-片库/车凯盛"),
        ("手机相册备份/成品视频", "速影客户/北京始峰伟业/02-成片"),
        ("手机相册备份/词池", "速影客户/北京始峰伟业/03-词池"),
    ]
    push = [
        "速影客户/北京始峰伟业/02-成片",
        "速影客户/北京始峰伟业/03-词池",
    ]
    return aliases, push, DEFAULT_LOCAL


def load_runtime_maps() -> Path:
    """Load aliases/push prefixes from ~/.qr/suying-sync.json (App 预设置)."""
    global PATH_ALIASES, PUSH_LOCAL_PREFIXES
    if not CONFIG_PATH.exists():
        PATH_ALIASES, PUSH_LOCAL_PREFIXES, local = _legacy_defaults()
        return local
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        PATH_ALIASES, PUSH_LOCAL_PREFIXES, local = _legacy_defaults()
        return local
    aliases: list[tuple[str, str]] = []
    push: list[str] = []
    for c in cfg.get("customers") or []:
        for a in c.get("aliases") or []:
            r, l = a.get("remote"), a.get("local")
            if r and l:
                aliases.append((str(r), str(l)))
        for pfx in c.get("push_local") or []:
            if pfx not in push:
                push.append(str(pfx))
    if not aliases and not push:
        PATH_ALIASES, PUSH_LOCAL_PREFIXES, local = _legacy_defaults()
        return local
    PATH_ALIASES = aliases
    PUSH_LOCAL_PREFIXES = push
    local = Path(cfg.get("local_root") or DEFAULT_LOCAL)
    return local


def log(msg: str, log_file: Path | None = None) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    if log_file:
        try:
            out = Path("/dev/stdout").resolve()
            if out == log_file.resolve():
                return
        except OSError:
            pass
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_session() -> dict:
    if not VUEX.exists():
        raise RuntimeError(f"找不到极空间会话: {VUEX}（请先打开并登录极空间客户端）")
    data = json.loads(VUEX.read_text(encoding="utf-8"))
    state = data.get("state") or {}
    user = state.get("user") or {}
    nas = state.get("nas") or {}
    app = state.get("app") or {}
    token = user.get("token")
    if not token:
        raise RuntimeError("vuex.json 中无 token，请重新登录极空间")
    return {
        "token": token,
        "username": str(user.get("username") or "").strip(),
        "nas_id": str(nas.get("nasId") or "").strip(),
        "nas_name": str(nas.get("nasName") or "").strip(),
        "client_ip": nas.get("clientPublicIp") or "",
        "version": app.get("version") or "1.0",
        "device_id": app.get("deviceId") or "",
        "device": app.get("device") or "",
        "local_port": int(app.get("localPort") or 13581),
    }


def load_bound_zspace() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return {"username": "", "nas_id": "", "nas_name": ""}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"username": "", "nas_id": "", "nas_name": ""}
    z = cfg.get("zspace") or {}
    return {
        "username": str(z.get("username") or "").strip(),
        "nas_id": str(z.get("nas_id") or "").strip(),
        "nas_name": str(z.get("nas_name") or "").strip(),
    }


def assert_bound_account(session: dict) -> None:
    """Refuse to sync unless client session matches bound username + nas_id."""
    bound = load_bound_zspace()
    if not bound["username"] or not bound["nas_id"]:
        raise RuntimeError(
            "尚未绑定极空间账号。请在速影「运维 → 极空间同步」选择要同步的账号与设备。"
        )
    if session.get("username") != bound["username"]:
        raise RuntimeError(
            f"极空间账号不匹配：当前登录 {session.get('username')!r}，"
            f"已绑定 {bound['username']!r}。请切换客户端登录后再同步。"
        )
    if session.get("nas_id") != bound["nas_id"]:
        raise RuntimeError(
            f"极空间设备不匹配：当前 {session.get('nas_id')} ({session.get('nas_name')})，"
            f"已绑定 {bound['nas_id']} ({bound.get('nas_name') or '—'})。"
            "请在客户端选中对应 NAS 后再同步。"
        )


def make_cookie(s: dict) -> str:
    parts = [
        "app=video",
        f"token={urllib.parse.quote(s['token'], safe='')}",
        "plat=pc",
        f"nas_id={s['nas_id']}",
        f"clientPublicIp={s['client_ip']}",
        f"version={s['version']}",
        f"device_id={s['device_id']}",
        f"device={urllib.parse.quote(s['device'], safe='')}",
    ]
    return "; ".join(parts)


def form_body(d: dict) -> bytes:
    return "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in d.items() if v is not None
    ).encode()


def remote_rel_to_local_rel(rel: Path) -> Path:
    s = rel.as_posix()
    for remote_prefix, local_prefix in sorted(PATH_ALIASES, key=lambda x: -len(x[0])):
        if s == remote_prefix or s.startswith(remote_prefix + "/"):
            rest = s[len(remote_prefix) :].lstrip("/")
            return Path(local_prefix) / rest if rest else Path(local_prefix)
    return rel


def local_rel_to_remote_rel(rel: Path) -> Path:
    s = rel.as_posix()
    for remote_prefix, local_prefix in sorted(PATH_ALIASES, key=lambda x: -len(x[1])):
        if s == local_prefix or s.startswith(local_prefix + "/"):
            rest = s[len(local_prefix) :].lstrip("/")
            return Path(remote_prefix) / rest if rest else Path(remote_prefix)
    return rel


def upload_uuid(mtime_ms: int, size: int, target_path: str) -> str:
    raw = f"{mtime_ms}{size}{target_path}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class ZSpaceClient:
    def __init__(self, proxy: str, session: dict):
        self.proxy = proxy.rstrip("/")
        self.session = session
        self.cookie = make_cookie(session)
        self.common = {
            "plat": "pc",
            "version": session["version"],
            "device_id": session["device_id"],
            "device": session["device"],
            "clientPublicIp": session["client_ip"],
            "token": session["token"],
            "dup": 0,
        }
        self._public_root: str | None = None

    def public_root(self) -> str:
        """Resolve /public to real backend path like /sata11/public."""
        if self._public_root:
            return self._public_root
        items = self.list_dir("/public")
        if items and items[0].get("path"):
            first = str(items[0]["path"]).rstrip("/")
            parts = [x for x in first.split("/") if x]
            if "public" in parts:
                i = parts.index("public")
                self._public_root = "/" + "/".join(parts[: i + 1])
            else:
                self._public_root = "/public"
        else:
            self._public_root = "/public"
        return self._public_root

    def to_backend_path(self, path: str) -> str:
        """Normalize /public/... to /sataXX/public/... for write APIs."""
        path = "/" + path.strip("/")
        if path == "/public" or path.startswith("/public/"):
            root = self.public_root()
            rest = path[len("/public") :].lstrip("/")
            return f"{root}/{rest}" if rest else root
        return path

    def post(self, path: str, body: dict, timeout: int = 120) -> dict:
        url = f"{self.proxy}{path}?&rnd={int(time.time()*1000)}&webagent=v2"
        req = urllib.request.Request(
            url,
            data=form_body({**self.common, **body}),
            headers={
                "Cookie": self.cookie,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        last_err: Exception | None = None
        for attempt in range(1, 5):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = e
                time.sleep(min(8, attempt * 1.5))
                # rebuild request (body already consumed otherwise on some pythons)
                req = urllib.request.Request(
                    url,
                    data=form_body({**self.common, **body}),
                    headers={
                        "Cookie": self.cookie,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    method="POST",
                )
        raise RuntimeError(f"POST {path} failed after retries: {last_err}")

    def list_dir(self, path: str) -> list[dict]:
        start = 0
        out: list[dict] = []
        while True:
            res = self.post("/v2/file/list", {"path": path, "start": start, "num": 200})
            if str(res.get("code")) != "200":
                raise RuntimeError(f"list {path}: {res.get('code')} {res.get('msg')}")
            data = res.get("data") or {}
            lst = data.get("list") or []
            out.extend(lst)
            total = int(data.get("total") or 0)
            start += len(lst)
            if not lst or start >= total:
                break
        return out

    def newdir(self, parent: str, name: str) -> str:
        parent = self.to_backend_path(parent)
        res = self.post("/v2/file/newdir", {"parent": parent, "name": name, "rename": False})
        code = str(res.get("code") or "")
        if res.get("success") or code in ("200", "N001301", "N001322"):
            data = res.get("data") or {}
            return str(data.get("path") or f"{parent.rstrip('/')}/{name}")
        raise RuntimeError(f"newdir {parent}/{name}: {code} {res.get('msg')}")

    def ensure_remote_dir(self, remote_dir: str) -> str:
        """Ensure directory exists using backend (/sataXX/public) paths."""
        backend = self.to_backend_path(remote_dir)
        root = self.public_root()
        if backend.rstrip("/") == root.rstrip("/"):
            return root
        if not backend.startswith(root + "/"):
            raise RuntimeError(f"path not under public root: {backend}")
        cur = root
        for name in backend[len(root) :].strip("/").split("/"):
            if not name:
                continue
            listing = self.list_dir(cur)
            hit = next((it for it in listing if it.get("name") == name), None)
            if hit:
                cur = hit["path"]
            else:
                cur = self.newdir(cur, name)
        return cur

    def download(self, remote_path: str, dest: Path, expected_size: int | None = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                offset = tmp.stat().st_size if tmp.exists() else 0
                q = urllib.parse.urlencode(
                    {"path": remote_path, "offset": offset, "remote_port": 8050}
                )
                url = f"{self.proxy}/v2/file/download?{q}&webagent=v2"
                req = urllib.request.Request(url, headers={"Cookie": self.cookie}, method="GET")
                mode = "ab" if offset else "wb"
                with urllib.request.urlopen(req, timeout=600) as resp, tmp.open(mode) as out:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                last_err = None
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = e
                time.sleep(min(10, attempt * 2))
        if last_err is not None:
            raise RuntimeError(f"download {remote_path} failed after retries: {last_err}")
        size = tmp.stat().st_size
        if expected_size is not None and expected_size > 0 and size != expected_size:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise RuntimeError(
                f"size mismatch {remote_path}: got {size}, expected {expected_size}"
            )
        tmp.replace(dest)
        cleanup_zspace_temps(dest)

    def upload(self, local_file: Path, remote_file_path: str) -> None:
        remote_file_path = "/" + remote_file_path.strip("/")
        backend_file = self.to_backend_path(remote_file_path)
        parent = str(Path(backend_file).parent).replace("\\", "/")
        parent_backend = self.ensure_remote_dir(parent)
        backend_file = f"{parent_backend.rstrip('/')}/{Path(backend_file).name}"

        st = local_file.stat()
        size = st.st_size
        mtime_ms = int(st.st_mtime * 1000)
        root = self.public_root()
        if parent_backend.startswith(root):
            target_path = "/public" + parent_backend[len(root) :]
        else:
            target_path = parent_backend
        uid = upload_uuid(mtime_ms, size, target_path)
        seek = 0
        headers = {
            "Cookie": self.cookie,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size - seek),
            "app": "file",
            "path": urllib.parse.quote(backend_file, safe=""),
            "size": str(size),
            "uuid": uid,
            "seek": str(seek),
            "crtime": str(mtime_ms),
            "modify_time": str(mtime_ms),
            "rename": "0",
            "token": urllib.parse.quote(self.session["token"], safe=""),
            "plat": "pc",
            "nasid": self.session["nas_id"],
            "version": self.session["version"],
            "device_id": self.session["device_id"],
            "device": urllib.parse.quote(self.session["device"], safe=""),
        }
        q = urllib.parse.urlencode(
            {"remote_port": 8050, "drnd": int(time.time() * 1000), "uuid": uid}
        )
        url = f"{self.proxy}/v2/file/upload?{q}"
        with local_file.open("rb") as body:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8", "replace")
        try:
            res = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise RuntimeError(f"upload bad json: {raw[:200]}") from e
        if not (res.get("success") or str(res.get("code")) in ("200",)):
            raise RuntimeError(
                f"upload {backend_file}: {res.get('code')} {res.get('msg') or raw[:200]}"
            )


def cleanup_zspace_temps(dest: Path) -> None:
    """Remove leftover official-client temp files like .VID_xxx.mp4.z<hash>."""
    parent = dest.parent
    if not parent.is_dir():
        return
    needle = f".{dest.name}.z"
    for p in parent.iterdir():
        name = p.name
        if name.startswith(needle) or name == f".{dest.name}.partial":
            try:
                p.unlink()
            except OSError:
                pass



def relative_under_public(remote_path: str) -> Path:
    p = remote_path.strip("/")
    parts = p.split("/")
    if "public" in parts:
        i = parts.index("public")
        return Path(*parts[i + 1 :]) if i + 1 < len(parts) else Path()
    raise ValueError(f"path not under public: {remote_path}")


def walk_files(client: ZSpaceClient, root: str) -> list[dict]:
    files: list[dict] = []
    stack = [root]
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for it in client.list_dir(cur):
            if str(it.get("is_dir")) == "1":
                stack.append(it["path"])
            else:
                files.append(it)
    return files


def acquire_lock() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        raise RuntimeError("另一同步任务正在运行") from e
    return fd


def release_lock(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def ensure_mount(log_file: Path) -> None:
    if DEFAULT_LOCAL.parent.exists() and any(DEFAULT_LOCAL.parent.iterdir()):
        return
    if MOUNT_SCRIPT.exists():
        os.system(f"/bin/bash '{MOUNT_SCRIPT}'")
    if not DEFAULT_LOCAL.parent.exists():
        raise RuntimeError("外置盘未挂载到 ~/QR-Volume，请插入 QR 盘后重试")


def sync_pull(
    client: ZSpaceClient,
    local_root: Path,
    remote_root: str,
    dry_run: bool,
    log_file: Path,
) -> dict:
    log(f"PULL 枚举远程 {remote_root}", log_file)
    files = walk_files(client, remote_root)
    log(f"远程文件数: {len(files)}", log_file)

    downloaded = skipped = failed = mapped = pending = 0
    bytes_dl = 0
    errors: list[str] = []

    # Pre-count pending so logs show the backlog clearly
    for it in files:
        try:
            rel = relative_under_public(it["path"])
        except ValueError:
            continue
        dest = local_root / remote_rel_to_local_rel(rel)
        size = int(it.get("size") or 0)
        if not (dest.exists() and dest.stat().st_size == size and size >= 0):
            pending += 1
    if pending:
        log(f"待拉取: {pending} 个文件（本地缺失或大小不一致）", log_file)

    for i, it in enumerate(files, 1):
        rpath = it["path"]
        try:
            rel = relative_under_public(rpath)
        except ValueError as e:
            failed += 1
            errors.append(str(e))
            continue
        local_rel = remote_rel_to_local_rel(rel)
        if local_rel != rel:
            mapped += 1
        dest = local_root / local_rel
        size = int(it.get("size") or 0)
        mtime = int(it.get("modify_time") or 0)

        if dest.exists() and dest.stat().st_size == size and size >= 0:
            skipped += 1
            continue

        log(
            f"[{i}/{len(files)}] {'DRY ' if dry_run else ''}↓ {rel} → {local_rel} ({size} bytes)",
            log_file,
        )
        if dry_run:
            downloaded += 1
            bytes_dl += size
            continue
        try:
            client.download(rpath, dest, expected_size=size if size > 0 else None)
            if mtime > 0:
                os.utime(dest, (mtime, mtime))
            downloaded += 1
            bytes_dl += size
        except Exception as e:
            failed += 1
            errors.append(f"{rel}: {e}")
            log(f"失败: {rel}: {e}", log_file)
            partial = dest.with_suffix(dest.suffix + ".partial")
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass

    summary = {
        "direction": "pull",
        "remote_files": len(files),
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "mapped": mapped,
        "bytes": bytes_dl,
        "errors": errors[:20],
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    log(
        f"PULL 完成: 下载={downloaded} 跳过={skipped} 映射命中={mapped} 失败={failed} 字节≈{bytes_dl}",
        log_file,
    )
    return summary


def sync_push(
    client: ZSpaceClient,
    local_root: Path,
    dry_run: bool,
    log_file: Path,
) -> dict:
    log("PUSH 扫描本机成片/词池", log_file)
    remote_index: dict[str, int] = {}
    try:
        for it in walk_files(client, DEFAULT_REMOTE):
            try:
                rel = relative_under_public(it["path"]).as_posix()
            except ValueError:
                continue
            remote_index[rel] = int(it.get("size") or 0)
    except Exception as e:
        log(f"PUSH 枚举远程失败（将尽量上传）: {e}", log_file)

    uploaded = skipped = failed = 0
    bytes_up = 0
    errors: list[str] = []

    candidates: list[Path] = []
    for prefix in PUSH_LOCAL_PREFIXES:
        root = local_root / prefix
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in PUSH_SKIP_PARTS or any(p in PUSH_SKIP_PARTS for p in path.parts):
                continue
            if path.suffix == ".partial":
                continue
            candidates.append(path)

    log(f"本机待检查推送文件: {len(candidates)}", log_file)
    for i, path in enumerate(candidates, 1):
        rel = path.relative_to(local_root)
        remote_rel = local_rel_to_remote_rel(rel)
        remote_posix = remote_rel.as_posix()
        size = path.stat().st_size
        if remote_index.get(remote_posix) == size:
            skipped += 1
            continue
        remote_file = f"/public/{remote_posix}"
        log(
            f"[{i}/{len(candidates)}] {'DRY ' if dry_run else ''}↑ {rel} → {remote_posix} ({size} bytes)",
            log_file,
        )
        if dry_run:
            uploaded += 1
            bytes_up += size
            continue
        try:
            client.upload(path, remote_file)
            uploaded += 1
            bytes_up += size
        except Exception as e:
            failed += 1
            errors.append(f"{rel}: {e}")
            log(f"上传失败: {rel}: {e}", log_file)

    summary = {
        "direction": "push",
        "local_candidates": len(candidates),
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "bytes": bytes_up,
        "errors": errors[:20],
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    log(
        f"PUSH 完成: 上传={uploaded} 跳过={skipped} 失败={failed} 字节≈{bytes_up}",
        log_file,
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="极空间团队空间 ↔ 外置盘同步（速影路径映射）")
    ap.add_argument("--local", type=Path, default=DEFAULT_LOCAL)
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="同默认；兼容 launchd")
    ap.add_argument("--pull-only", action="store_true", help="只拉取，不回传")
    ap.add_argument("--push-only", action="store_true", help="只回传成片/词池")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "zspace-team-sync.log"

    fd = None
    try:
        fd = acquire_lock()
        ensure_mount(log_file)
        cfg_local = load_runtime_maps()
        if args.local == DEFAULT_LOCAL:
            args.local = cfg_local
        args.local.mkdir(parents=True, exist_ok=True)
        log(f"配置: {CONFIG_PATH} aliases={len(PATH_ALIASES)} push={len(PUSH_LOCAL_PREFIXES)}", log_file)

        session = load_session()
        assert_bound_account(session)
        proxy = args.proxy
        if session.get("local_port"):
            proxy = f"http://127.0.0.1:{session['local_port']}"

        try:
            urllib.request.urlopen(proxy + "/home/", timeout=5)
        except Exception as e:
            raise RuntimeError(
                f"无法连接极空间本地代理 {proxy}：请保持极空间客户端已登录。({e})"
            ) from e

        log(
            f"账号绑定 OK: {session.get('username')} / {session.get('nas_id')} ({session.get('nas_name')})",
            log_file,
        )
        client = ZSpaceClient(proxy, session)
        summary: dict = {
            "aliases": PATH_ALIASES,
            "zspace": {
                "username": session.get("username"),
                "nas_id": session.get("nas_id"),
                "nas_name": session.get("nas_name"),
            },
        }

        do_pull = not args.push_only
        do_push = not args.pull_only

        if do_pull:
            summary["pull"] = sync_pull(client, args.local, args.remote, args.dry_run, log_file)
        if do_push:
            summary["push"] = sync_push(client, args.local, args.dry_run, log_file)

        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        failed = 0
        if "pull" in summary:
            failed += int(summary["pull"].get("failed") or 0)
        if "push" in summary:
            failed += int(summary["push"].get("failed") or 0)
        return 1 if failed else 0
    except Exception as e:
        log(f"错误: {e}", log_file)
        return 2
    finally:
        if fd is not None:
            release_lock(fd)


if __name__ == "__main__":
    sys.exit(main())
