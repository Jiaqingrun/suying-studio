#!/usr/bin/env python3
"""Sync ZSpace team space (/public) ↔ external disk via local client proxy.

Requires 极空间 desktop client logged in (local proxy port from vuex, often 13579/13581).
Official「文档同步」cannot select team space; this script uses the client tunnel API.

Directions from ~/.qr/suying-sync.json (installed by 速影 App).
Default carrier_only mode synchronizes configuration/update/backup files only.

Other remote paths under /public still pull 1:1 into the local sync root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LOCAL = Path.home() / "Movies" / "速影工作区"
DEFAULT_REMOTE = "/public"
DEFAULT_PROXY = "http://127.0.0.1:13579"
VUEX = Path.home() / "Library/Application Support/zspace/vuex.json"
LOG_DIR = Path.home() / ".qr/logs"
STATE_PATH = Path.home() / ".qr/zspace-team-sync-state.json"
LOCK_DIR = Path.home() / ".qr/zspace-team-sync.locks"
LOCK_REGISTRY_PATH = LOCK_DIR / ".registry.lock"
CONFIG_PATH = Path.home() / ".qr" / "suying-sync.json"

PUSH_SKIP_PARTS = {".DS_Store", "rendering", "Thumbs.db"}

# Filled by load_runtime_maps()
# PATH_ALIASES entries: (remote_prefix_under_public, local_prefix_under_local_root)
# NOTE: keep comments free of fixed customer names to satisfy smoke checks.
PATH_ALIASES: list[tuple[str, str]] = []
PUSH_LOCAL_PREFIXES: list[str] = []


def _normalized_relative_path(value: object, *, field: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{field} 必须是安全的相对路径")
    return path.as_posix()


def _paths_overlap(left: Path, right: Path) -> bool:
    a = left.expanduser().resolve()
    b = right.expanduser().resolve()
    return a == b or a in b.parents or b in a.parents


def _validate_media_sources(sources: list[dict]) -> list[dict]:
    parsed: list[dict] = []
    for source in sources:
        remote = _normalized_relative_path(
            source.get("remote_root") or source.get("remote"),
            field="media_sources.remote_root",
        )
        local = _normalized_relative_path(
            source.get("local_target") or source.get("local"),
            field="media_sources.local_target",
        )
        customer = str(source.get("customer_key") or "").strip()
        if not customer:
            raise RuntimeError("media_sources.customer_key 不能为空")
        parsed.append({**source, "_remote": remote, "_local": local, "_customer": customer})
    for index, left in enumerate(parsed):
        for right in parsed[index + 1 :]:
            remote_overlap = _paths_overlap(Path(left["_remote"]), Path(right["_remote"]))
            local_overlap = _paths_overlap(Path(left["_local"]), Path(right["_local"]))
            if remote_overlap or local_overlap:
                raise RuntimeError(
                    "媒体同步规则重叠，拒绝并行/串行写入: "
                    f"{left['_customer']}({left['_remote']} → {left['_local']}) 与 "
                    f"{right['_customer']}({right['_remote']} → {right['_local']})"
                )
    return parsed


def load_runtime_maps(only_media_customers: set[str] | None = None) -> dict:
    """Load aliases/push prefixes from ~/.qr/suying-sync.json (App 预设置)."""
    global PATH_ALIASES, PUSH_LOCAL_PREFIXES
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"缺少同步配置: {CONFIG_PATH}，请先在速影安装向导中配置")
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"同步配置无效: {CONFIG_PATH}: {e}") from e
    mode = str(cfg.get("sync_mode") or "carrier_only")
    if mode == "carrier_only":
        if only_media_customers:
            raise RuntimeError("--only-media-customer 仅适用于 media 同步模式")
        PATH_ALIASES = []
        # The signed update repository is publisher-owned and client pull-only.
        PUSH_LOCAL_PREFIXES = []
        configured_remote = str(cfg.get("carrier_remote_root") or "").strip()
        remote = configured_remote or f"/public/{str(cfg.get('carrier_relpath') or '速影载体').strip('/')}"
        if not remote.startswith("/") or ".." in Path(remote).parts:
            raise RuntimeError("carrier_remote_root 必须是安全的极空间绝对路径")
        mirror = Path(cfg.get("carrier_mirror") or Path.home() / "Suying" / "carrier")
        if configured_remote and mirror.name != "app":
            mirror = mirror / "app"
        return {
            "mode": mode,
            "local_root": mirror,
            "remote_root": remote,
            "pull_remote_roots": [remote],
            "destination_roots": [mirror],
        }
    if not bool(cfg.get("media_sync_enabled", False)):
        raise RuntimeError("媒体同步未显式启用")
    volume_uuid = str(cfg.get("volume_uuid") or "").strip()
    if not volume_uuid:
        raise RuntimeError("媒体同步需要配置外置盘 volume_uuid")
    info = subprocess.run(
        ["diskutil", "info", "-plist", volume_uuid],
        capture_output=True,
        check=False,
    )
    if info.returncode != 0:
        raise RuntimeError(f"指定外置盘未连接: {volume_uuid}")
    volume = plistlib.loads(info.stdout)
    mount_point = str(volume.get("MountPoint") or "").strip()
    if not mount_point:
        raise RuntimeError(f"指定外置盘尚未挂载: {volume_uuid}")
    # v3: media_sources as first-class media pull mapping (fail-closed)
    # v1/v2: customers[].aliases back-compat
    aliases: list[tuple[str, str]] = []
    pull_roots: list[str] = []
    push: list[str] = []

    # 1) Push candidates (legacy behavior, can be enabled explicitly by customer.push_local).
    for c in cfg.get("customers") or []:
        customer_name = str(c.get("name") or "").strip()
        if only_media_customers and customer_name not in only_media_customers:
            continue
        for pfx in c.get("push_local") or []:
            if pfx not in push:
                push.append(str(pfx))

    # 2) Media pull mapping.
    raw_sources = cfg.get("media_sources")
    if isinstance(raw_sources, list) and raw_sources:
        validated_sources = _validate_media_sources(raw_sources)
        for s in validated_sources:
            if only_media_customers and s["_customer"] not in only_media_customers:
                continue
            remote_rel = s["_remote"].removeprefix("public/").strip("/")
            aliases.append((remote_rel, s["_local"]))
            pull_roots.append(f"/public/{remote_rel}")
    else:
        # Back-compat for legacy config.
        if only_media_customers:
            raise RuntimeError(
                "--only-media-customer 要求使用 v3 media_sources；旧 customers[].aliases 不支持筛选"
            )
        legacy_sources: list[dict] = []
        for c in cfg.get("customers") or []:
            for a in c.get("aliases") or []:
                r, l = a.get("remote"), a.get("local")
                if not r or not l:
                    continue
                legacy_sources.append(
                    {
                        "customer_key": str(c.get("name") or "legacy"),
                        "remote_root": r,
                        "local_target": l,
                    }
                )
        for source in _validate_media_sources(legacy_sources):
            remote_rel = source["_remote"].removeprefix("public/").strip("/")
            aliases.append((remote_rel, source["_local"]))
            pull_roots.append(f"/public/{remote_rel}")

        # Optional explicit narrow pull scope (if user already provided it).
        raw_roots = cfg.get("pull_remote_roots")
        if isinstance(raw_roots, list):
            for item in raw_roots:
                rel = str(item or "").strip().removeprefix("/public/").strip("/")
                if rel:
                    pull_roots.append(f"/public/{rel}")

    # Fail-closed: no mapping => refuse to pull anything under /public.
    # This prevents client-side mixing when multiple customers share the same team space parents.
    # Also de-duplicate pull roots.
    pull_roots = sorted({r for r in pull_roots if r})
    if not aliases or not pull_roots:
        selected = f"（筛选客户: {sorted(only_media_customers)}）" if only_media_customers else ""
        raise RuntimeError(
            "媒体同步未配置任何有效媒体来源"
            f"{selected}（media_sources 或 customers[].aliases 为空）"
        )

    PATH_ALIASES = aliases
    PUSH_LOCAL_PREFIXES = push
    local_root = Path(mount_point) / str(cfg.get("volume_relpath") or "极空间团队文件同步")
    return {
        "mode": mode,
        "local_root": local_root,
        "remote_root": DEFAULT_REMOTE,
        "pull_remote_roots": pull_roots,
        "destination_roots": [local_root / local for _, local in aliases],
    }


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
            # Upload conflict policy:
            # - 0 = fail when filename exists
            # - 1 = server-side rename/backup (may create *-1 variants)
            # - 3 = best-effort overwrite (used for update repo publishing)
            "rename": "3",
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
    """Walk remote tree. Resolve /public/... to backend (/sataXX/public/...) first.

    Listing /public itself works; listing /public/<subdir> often returns N001411.
    Child entries already carry backend paths from the API.
    """
    files: list[dict] = []
    # Resolve once up front so pull_remote_roots like /public/手机相册备份/徐玲飞 work.
    stack = [client.to_backend_path(root)]
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


def protected_local_roots() -> list[Path]:
    """Local application state that sync downloads must never touch."""
    home = Path.home()
    roots = [
        home / "Suying" / "data",
        home / "Suying" / "cache",
        home / "Suying" / "render",
        home / "Suying" / "runtime",
        home / "Library" / "Application Support" / "com.qr.suying",
    ]
    settings_path = home / "Suying" / "data" / "settings.json"
    if settings_path.is_file():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            paths = raw.get("paths") or {}
            for key in ("data_root", "cache_root", "render_root"):
                value = str(paths.get(key) or "").strip()
                if value:
                    roots.append(Path(value))
        except (OSError, json.JSONDecodeError):
            # Static local roots remain protected. Invalid settings are reported by the engine.
            pass
    unique: dict[str, Path] = {}
    for root in roots:
        resolved = root.expanduser().resolve()
        unique[str(resolved)] = resolved
    return list(unique.values())


def assert_safe_destination_roots(local_root: Path, destination_roots: list[Path]) -> list[Path]:
    sandbox = local_root.expanduser().resolve()
    if not destination_roots:
        raise RuntimeError("同步未解析出任何本地目的地")
    protected = protected_local_roots()
    safe: list[Path] = []
    for root in destination_roots:
        resolved = root.expanduser().resolve()
        if resolved != sandbox and sandbox not in resolved.parents:
            raise RuntimeError(f"同步目的地越出写入沙盒: {resolved}（沙盒 {sandbox}）")
        for blocked in protected:
            if _paths_overlap(resolved, blocked):
                raise RuntimeError(f"同步目的地命中本机保护目录: {resolved} ↔ {blocked}")
        safe.append(resolved)
    for index, left in enumerate(safe):
        for right in safe[index + 1 :]:
            if _paths_overlap(left, right):
                raise RuntimeError(f"本次同步目的地相互重叠: {left} ↔ {right}")
    return sorted(set(safe), key=str)


def assert_safe_destination(
    destination: Path,
    *,
    allowed_roots: list[Path],
    protected_roots: list[Path],
) -> Path:
    resolved = destination.expanduser().resolve()
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise RuntimeError(f"下载目标不在本次规则的显式目的地内: {resolved}")
    for blocked in protected_roots:
        if resolved == blocked or blocked in resolved.parents:
            raise RuntimeError(f"下载目标命中本机保护目录: {resolved}")
    return resolved


@dataclass
class DestinationLease:
    fd: int
    path: Path
    roots: list[Path]


def _registry_fd() -> int:
    import fcntl

    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_REGISTRY_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_registry(fd: int) -> None:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def acquire_destination_lease(destination_roots: list[Path]) -> DestinationLease:
    """Atomically reject overlapping active destinations while allowing disjoint rules."""
    import fcntl

    roots = sorted({root.expanduser().resolve() for root in destination_roots}, key=str)
    registry = _registry_fd()
    try:
        for lease_path in LOCK_DIR.glob("*.lease"):
            try:
                active_fd = os.open(str(lease_path), os.O_RDWR)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(active_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    try:
                        payload = json.loads(lease_path.read_text(encoding="utf-8"))
                        active_roots = [Path(item).resolve() for item in payload.get("roots") or []]
                    except (OSError, json.JSONDecodeError):
                        raise RuntimeError(f"活动同步租约不可读，拒绝并发: {lease_path}")
                    if any(_paths_overlap(root, active) for root in roots for active in active_roots):
                        raise RuntimeError(
                            "另一同步任务正在写入重叠目的地: "
                            + ", ".join(str(root) for root in active_roots)
                        )
                    continue
                # Lock can be acquired: the owning process is gone, so this is stale.
                lease_path.unlink(missing_ok=True)
            finally:
                os.close(active_fd)

        digest = hashlib.sha256("\n".join(str(root) for root in roots).encode()).hexdigest()[:16]
        lease_path = LOCK_DIR / f"{os.getpid()}-{digest}.lease"
        fd = os.open(str(lease_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = {
                "pid": os.getpid(),
                "roots": [str(root) for root in roots],
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            os.write(fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode())
            os.fsync(fd)
        except Exception:
            os.close(fd)
            lease_path.unlink(missing_ok=True)
            raise
        return DestinationLease(fd=fd, path=lease_path, roots=roots)
    finally:
        _release_registry(registry)


def release_destination_lease(lease: DestinationLease) -> None:
    import fcntl

    registry = _registry_fd()
    try:
        lease.path.unlink(missing_ok=True)
        fcntl.flock(lease.fd, fcntl.LOCK_UN)
        os.close(lease.fd)
    finally:
        _release_registry(registry)


def relative_under_remote(path: str, remote_root: str) -> Path:
    rel = relative_under_public(path)
    prefix = remote_root.removeprefix("/public").strip("/")
    if not prefix:
        return rel
    parts = Path(prefix).parts
    if rel.parts[: len(parts)] != parts:
        raise ValueError(f"路径不在远程角色目录内: {path}")
    return Path(*rel.parts[len(parts) :])


def sync_pull(
    client: ZSpaceClient,
    local_root: Path,
    remote_root: str,
    dry_run: bool,
    log_file: Path,
    allowed_destination_roots: list[Path],
    pull_roots: list[str] | None = None,
    strict_mapped_only: bool = False,
) -> dict:
    roots = [r for r in (pull_roots or [remote_root]) if r]
    blocked_roots = protected_local_roots()
    if not roots:
        roots = [remote_root]
    log(f"PULL 枚举远程 {roots}", log_file)
    files: list[dict] = []
    seen_paths: set[str] = set()
    for root in roots:
        walk_root = client.to_backend_path(root)
        if walk_root != root:
            log(f"PULL root → backend: {root} → {walk_root}", log_file)
        for it in walk_files(client, walk_root):
            p = str(it.get("path") or "")
            if p in seen_paths:
                continue
            seen_paths.add(p)
            files.append(it)
    log(f"远程文件数: {len(files)}", log_file)

    downloaded = skipped = failed = mapped = pending = unmapped_skipped = 0
    bytes_dl = 0
    errors: list[str] = []

    # Pre-count pending so logs show the backlog clearly
    for it in files:
        try:
            rel = relative_under_remote(it["path"], remote_root)
        except ValueError:
            continue
        local_rel = remote_rel_to_local_rel(rel)
        if strict_mapped_only and local_rel == rel:
            continue
        dest = assert_safe_destination(
            local_root / local_rel,
            allowed_roots=allowed_destination_roots,
            protected_roots=blocked_roots,
        )
        size = int(it.get("size") or 0)
        if not (dest.exists() and dest.stat().st_size == size and size >= 0):
            pending += 1
    if pending:
        log(f"待拉取: {pending} 个文件（本地缺失或大小不一致）", log_file)

    for i, it in enumerate(files, 1):
        rpath = it["path"]
        try:
            rel = relative_under_remote(rpath, remote_root)
        except ValueError as e:
            failed += 1
            errors.append(str(e))
            continue
        local_rel = remote_rel_to_local_rel(rel)
        if local_rel != rel:
            mapped += 1
        elif strict_mapped_only:
            unmapped_skipped += 1
            continue
        dest = assert_safe_destination(
            local_root / local_rel,
            allowed_roots=allowed_destination_roots,
            protected_roots=blocked_roots,
        )
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
        "unmapped_skipped": unmapped_skipped,
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
    remote_root: str,
    dry_run: bool,
    log_file: Path,
) -> dict:
    log(f"PUSH 扫描本机角色目录 → {remote_root}", log_file)
    remote_index: dict[str, int] = {}
    try:
        for it in walk_files(client, remote_root):
            try:
                rel = relative_under_remote(it["path"], remote_root).as_posix()
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
        remote_file = f"{remote_root.rstrip('/')}/{remote_posix}"
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
    ap.add_argument(
        "--only-media-customer",
        action="append",
        default=[],
        help="仅运行指定 customer_key 的 media_sources 规则；可重复",
    )
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    lease: DestinationLease | None = None
    try:
        selected_customers = {
            str(item).strip() for item in args.only_media_customer if str(item).strip()
        }
        runtime = load_runtime_maps(selected_customers or None)
        runtime_local = Path(runtime["local_root"]).expanduser().resolve()
        runtime_destinations = [
            Path(item).expanduser().resolve() for item in runtime.get("destination_roots") or []
        ]
        if args.local == DEFAULT_LOCAL:
            args.local = runtime_local
            destination_roots = runtime_destinations
        else:
            args.local = args.local.expanduser().resolve()
            destination_roots = []
            for root in runtime_destinations:
                try:
                    relative = root.relative_to(runtime_local)
                except ValueError as exc:
                    raise RuntimeError(f"运行时目的地不在配置 local_root 内: {root}") from exc
                destination_roots.append(args.local / relative)
        if args.remote == DEFAULT_REMOTE:
            args.remote = runtime["remote_root"]
        destination_roots = assert_safe_destination_roots(args.local, destination_roots)
        scope_hash = hashlib.sha256(
            "\n".join(str(root) for root in destination_roots).encode()
        ).hexdigest()[:12]
        filtered_run = bool(selected_customers)
        log_file = LOG_DIR / (
            f"zspace-team-sync.{scope_hash}.log" if filtered_run else "zspace-team-sync.log"
        )
        state_path = (
            STATE_PATH.with_name(f"zspace-team-sync-state.{scope_hash}.json")
            if filtered_run
            else STATE_PATH
        )
        lease = acquire_destination_lease(destination_roots)
        args.local.mkdir(parents=True, exist_ok=True)
        log(
            f"配置: {CONFIG_PATH} aliases={len(PATH_ALIASES)} "
            f"push={len(PUSH_LOCAL_PREFIXES)} destinations={destination_roots}",
            log_file,
        )

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

        pull_roots = list(runtime.get("pull_remote_roots") or [args.remote])
        summary["pull_remote_roots"] = pull_roots
        if do_pull:
            summary["pull"] = sync_pull(
                client,
                args.local,
                args.remote,
                args.dry_run,
                log_file,
                destination_roots,
                pull_roots=pull_roots,
                strict_mapped_only=(runtime.get("mode") != "carrier_only"),
            )
        if do_push:
            summary["push"] = sync_push(client, args.local, args.remote, args.dry_run, log_file)

        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        failed = 0
        if "pull" in summary:
            failed += int(summary["pull"].get("failed") or 0)
        if "push" in summary:
            failed += int(summary["push"].get("failed") or 0)
        return 1 if failed else 0
    except Exception as e:
        fallback_log = locals().get("log_file", LOG_DIR / "zspace-team-sync.log")
        log(f"错误: {e}", fallback_log)
        return 2
    finally:
        if lease is not None:
            release_destination_lease(lease)


if __name__ == "__main__":
    sys.exit(main())
