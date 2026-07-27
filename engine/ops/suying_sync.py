"""速影 × 极空间同步：配置、客户目录、本机服务安装。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".qr" / "suying-sync.json"
TOOLS_DIR = Path.home() / "QR" / "tools"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST_LABEL = "com.qr.zspace-team-sync"
PLIST_PATH = LAUNCH_AGENTS / f"{PLIST_LABEL}.plist"

DEFAULT_LOCAL = Path.home() / "Suying" / "sync"
DEFAULT_WORK = Path.home() / "Suying"
VOLUME_UUID = ""


VUEX_PATH = Path.home() / "Library" / "Application Support" / "zspace" / "vuex.json"


# ------------------------------
# ZSpace read-only helpers
# ------------------------------

def load_zspace_session() -> dict[str, Any]:
    """Load active 极空间 session from vuex.json (token included)."""
    if not VUEX_PATH.exists():
        raise RuntimeError(f"找不到极空间会话: {VUEX_PATH}（请先打开并登录极空间客户端）")
    data = json.loads(VUEX_PATH.read_text(encoding="utf-8"))
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


def make_zspace_cookie(s: dict[str, Any]) -> str:
    """Cookie used by zspace-team-sync.py when calling /v2/file/* APIs."""
    parts = [
        "app=video",
        f"token={urllib.parse.quote(str(s['token']), safe='')}",
        "plat=pc",
        f"nas_id={s['nas_id']}",
        f"clientPublicIp={s['client_ip']}",
        f"version={s['version']}",
        f"device_id={s['device_id']}",
        f"device={urllib.parse.quote(str(s['device']), safe='')}",
    ]
    return "; ".join(parts)


class ZSpaceFileClient:
    """Minimal client for 极空间 /v2/file APIs (read-only by default)."""

    def __init__(self, proxy: str, session: dict[str, Any]):
        self.proxy = proxy.rstrip("/")
        self.session = session
        self.cookie = make_zspace_cookie(session)
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

    def post(self, path: str, body: dict[str, Any], timeout: int = 120) -> dict:
        url = f"{self.proxy}{path}?&rnd={int(time.time()*1000)}&webagent=v2"
        last_err: Exception | None = None
        for attempt in range(1, 5):
            try:
                req = urllib.request.Request(
                    url,
                    data="&".join(
                        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in {**self.common, **body}.items() if v is not None
                    ).encode(),
                    headers={
                        "Cookie": self.cookie,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                    return json.loads(raw) if raw else {}
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = e
                time.sleep(min(8, attempt * 1.5))
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

    def public_root(self) -> str:
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
        path = "/" + path.strip("/")
        if path == "/public" or path.startswith("/public/"):
            root = self.public_root()
            rest = path[len("/public") :].lstrip("/")
            return f"{root}/{rest}" if rest else root
        return path


def _assert_bound_zspace(session: dict[str, Any]) -> None:
    bound = get_zspace_bind()
    if not bound["username"] or not bound["nas_id"]:
        raise RuntimeError(
            "尚未绑定极空间账号。请在速影「运维 → 极空间同步」选择要同步的账号与设备。"
        )
    if session.get("username") != bound["username"]:
        raise RuntimeError(
            f"极空间账号不匹配：当前登录 {session.get('username')!r}，已绑定 {bound['username']!r}。"
        )
    if session.get("nas_id") != bound["nas_id"]:
        raise RuntimeError(
            f"极空间设备不匹配：当前 {session.get('nas_id')} ({session.get('nas_name')})，已绑定 {bound['nas_id']}。"
        )


def list_remote_folders(remote_base_rel: str) -> list[str]:
    """List immediate sub-directories under /public/<remote_base_rel>.

    remote_base_rel example: "手机相册备份"
    """
    remote_base_rel = str(remote_base_rel or "").strip().removeprefix("/public/").strip("/")
    if not remote_base_rel or ".." in remote_base_rel or "/" in remote_base_rel:
        # Keep contract simple: only one-level folder name under /public.
        raise ValueError("remote_base_rel 必须是 /public 下的单层目录名")

    session = load_zspace_session()
    _assert_bound_zspace(session)

    proxy = f"http://127.0.0.1:{session['local_port']}"
    client = ZSpaceFileClient(proxy, session)

    remote_dir = f"/public/{remote_base_rel}"
    items = client.list_dir(client.to_backend_path(remote_dir))
    folders: list[str] = []
    for it in items:
        if str(it.get("is_dir")) == "1":
            name = str(it.get("name") or "").strip()
            if name:
                folders.append(name)
    return sorted(set(folders))


def upsert_media_source(customer_name: str, remote_person: str, remote_base_rel: str = "手机相册备份") -> dict[str, Any]:
    """Write/replace v3 media_sources mapping for a single customer + remote folder name.

    It updates ~/.qr/suying-sync.json and (when the customer exists in authority DB) archives old ready assets
    so retired sources never enter future cliplet selection.
    """
    customer_name = validate_customer_name(customer_name)
    remote_person = str(remote_person or "").strip()
    if not remote_person or "/" in remote_person or "\\" in remote_person or ".." in remote_person:
        raise ValueError("remote_person 必须是团队空间目录名（不含路径分隔符）")
    remote_base_rel = str(remote_base_rel or "").strip().removeprefix("/public/").strip("/")
    if not remote_base_rel or "/" in remote_base_rel or "\\" in remote_base_rel:
        raise ValueError("remote_base_rel 必须是 /public 下的单层目录名")

    cfg = load_config()
    if not isinstance(cfg.get("media_sources"), list):
        cfg["media_sources"] = []
    sources = cfg["media_sources"]

    remote_root_rel = f"{remote_base_rel}/{remote_person}"
    local_target_rel = f"速影客户/{customer_name}/01-片库/{remote_person}"

    # Replace existing mapping for the same customer (single-source per customer).
    old_local_targets = [
        str(s.get("local_target") or s.get("local") or "").strip()
        for s in sources
        if str(s.get("customer_key") or "").strip() == customer_name
    ]
    removed_local_targets = [t for t in old_local_targets if t and t != local_target_rel]

    # Overlap check against other customers: remote roots must be unique and not nested.
    for s in sources:
        ex_customer_key = str(s.get("customer_key") or "").strip()
        if ex_customer_key == customer_name:
            continue
        ex_remote = str(s.get("remote_root") or s.get("remote") or "").strip()
        if not ex_remote:
            continue
        ex_remote_norm = ex_remote.removeprefix("/public/").strip("/")
        if ex_remote_norm == remote_root_rel:
            raise ValueError(f"远端媒体来源已被占用：{remote_root_rel}")
        if ex_remote_norm.startswith(remote_root_rel + "/") or remote_root_rel.startswith(ex_remote_norm + "/"):
            raise ValueError(f"远端媒体来源重叠：{remote_root_rel} 与现有 {ex_remote_norm}")

    # Drop all old sources for this customer, then add the new one.
    new_sources: list[dict[str, Any]] = [s for s in sources if str(s.get("customer_key") or "").strip() != customer_name]
    new_sources.append(
        {
            "customer_key": customer_name,
            "display_name": customer_name,
            "remote_root": remote_root_rel,
            "local_target": local_target_rel,
            "pull_only": True,
        }
    )

    cfg["media_sources"] = new_sources
    cfg["version"] = max(int(cfg.get("version") or 0), 3)
    save_config(cfg)

    # Archive old ready assets under retired local targets to avoid cross-source re-selection.
    archived = 0
    if removed_local_targets:
        try:
            from sqlalchemy import select
            from engine.catalog.db import Asset, Customer, get_session

            session = get_session()
            try:
                cust = session.scalar(select(Customer).where(Customer.name == customer_name))
                if cust:
                    local_root = Path(cfg.get("local_root") or DEFAULT_LOCAL)
                    for t in removed_local_targets:
                        prefix = local_root / t
                        stmt = (
                            select(Asset)
                            .where(Asset.customer_id == cust.id)
                            .where(Asset.status == "ready")
                            .where(Asset.source_path.like(f"{str(prefix)}%"))
                        )
                        for a in session.scalars(stmt).all():
                            a.status = "archived"
                            archived += 1
                    session.commit()
            finally:
                session.close()
        except Exception:
            # Archiving must be best-effort: never block sync-source update due to archive failure.
            pass

    return {
        "ok": True,
        "customer": customer_name,
        "remote_root": remote_root_rel,
        "local_target": local_target_rel,
        "count": len(new_sources),
        "archived_assets": archived,
        "removed_local_targets": removed_local_targets,
    }


def preview_media_source(
    customer_name: str, remote_person: str, remote_base_rel: str = "手机相册备份"
) -> dict[str, Any]:
    """Describe a media source change without writing config or archiving assets."""
    customer_name = validate_customer_name(customer_name)
    remote_person = str(remote_person or "").strip()
    if not remote_person or "/" in remote_person or "\\" in remote_person or ".." in remote_person:
        raise ValueError("remote_person 必须是团队空间目录名（不含路径分隔符）")
    remote_base_rel = str(remote_base_rel or "").strip().removeprefix("/public/").strip("/")
    if not remote_base_rel or "/" in remote_base_rel or "\\" in remote_base_rel:
        raise ValueError("remote_base_rel 必须是 /public 下的单层目录名")

    cfg = load_config()
    remote_root_rel = f"{remote_base_rel}/{remote_person}"
    local_target_rel = f"速影客户/{customer_name}/01-片库/{remote_person}"
    current = [
        s
        for s in cfg.get("media_sources") or []
        if str(s.get("customer_key") or "").strip() == customer_name
    ]
    old_local_targets = [
        str(s.get("local_target") or s.get("local") or "").strip()
        for s in current
        if str(s.get("local_target") or s.get("local") or "").strip() != local_target_rel
    ]
    return {
        "ok": True,
        "customer": customer_name,
        "current": current,
        "proposed": {
            "customer_key": customer_name,
            "display_name": customer_name,
            "remote_root": remote_root_rel,
            "local_target": local_target_rel,
            "pull_only": True,
        },
        "removed_local_targets": old_local_targets,
        "unchanged": bool(current) and not old_local_targets,
    }


def script_fingerprint() -> dict[str, Any]:
    """Compare repo packaging script vs installed ~/QR/tools copy."""
    repo_py = packaging_dir() / "zspace-team-sync.py"
    installed_py = TOOLS_DIR / "zspace-team-sync.py"

    def _sha(path: Path) -> str:
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    repo_hash = _sha(repo_py)
    installed_hash = _sha(installed_py)
    return {
        "repo_path": str(repo_py),
        "installed_path": str(installed_py),
        "repo_sha256": repo_hash,
        "installed_sha256": installed_hash,
        "installed_exists": installed_py.is_file(),
        "match": bool(repo_hash and repo_hash == installed_hash),
        "stale": bool(repo_hash and installed_hash and repo_hash != installed_hash),
    }


def reconcile_sync_customers() -> dict[str, Any]:
    """Compare SQLite customers vs suying-sync.json media_sources."""
    cfg = load_config()
    config_names = {
        str(c.get("name") or "").strip()
        for c in cfg.get("customers") or []
        if str(c.get("name") or "").strip()
    }
    media_keys = {
        str(s.get("customer_key") or "").strip()
        for s in cfg.get("media_sources") or []
        if str(s.get("customer_key") or "").strip()
    }
    db_names: list[str] = []
    errors: list[dict[str, str]] = []
    try:
        from sqlalchemy import select

        from engine.catalog.db import Customer, get_session

        session = get_session()
        try:
            db_names = [str(c.name) for c in session.scalars(select(Customer)).all()]
        finally:
            session.close()
    except Exception as e:
        errors.append({"type": "db_unavailable", "detail": str(e)[:200]})

    db_set = set(db_names)
    for name in sorted(media_keys):
        if name not in db_set:
            errors.append({"type": "orphan_media_source", "customer": name})
    for name in sorted(db_set):
        if cfg.get("media_sync_enabled") and name not in media_keys:
            errors.append({"type": "missing_media_source", "customer": name})

    return {
        "db_customers": db_names,
        "config_customers": sorted(config_names),
        "media_source_customers": sorted(media_keys),
        "errors": errors,
        "ok": not errors,
    }


def run_sync_dry_run(*, pull_only: bool = True) -> dict[str, Any]:
    """Run installed zspace-team-sync.py once in dry-run mode."""
    bind = check_zspace_bind()
    if not bind.get("match"):
        raise RuntimeError(bind.get("reason") or "极空间账号未绑定或未匹配")

    script = TOOLS_DIR / "zspace-team-sync.py"
    if not script.is_file():
        script = packaging_dir() / "zspace-team-sync.py"
    if not script.is_file():
        raise RuntimeError("同步脚本未安装；请先在运维页安装同步服务")

    cmd = [sys.executable, str(script), "--dry-run", "--once"]
    if pull_only:
        cmd.append("--pull-only")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    state_path = Path.home() / ".qr" / "zspace-team-sync-state.json"
    summary: dict[str, Any] | None = None
    if state_path.is_file():
        try:
            summary = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = None
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "summary": summary,
    }


def read_vuex_state() -> dict[str, Any]:
    if not VUEX_PATH.exists():
        return {}
    try:
        data = json.loads(VUEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("state") or {}


def detect_active_zspace() -> dict[str, Any] | None:
    """Current logged-in account + NAS from 极空间客户端 vuex."""
    state = read_vuex_state()
    user = state.get("user") or {}
    nas = state.get("nas") or {}
    app = state.get("app") or {}
    username = str(user.get("username") or "").strip()
    nas_id = str(nas.get("nasId") or "").strip()
    if not username and not nas_id:
        return None
    return {
        "username": username,
        "nas_id": nas_id,
        "nas_name": str(nas.get("nasName") or ""),
        "show_name": "",
        "local_port": int(app.get("localPort") or 13581),
        "active": True,
        "token_present": bool(user.get("token")),
    }


def list_zspace_accounts() -> list[dict[str, Any]]:
    """History + active session accounts known to the local client."""
    state = read_vuex_state()
    user = state.get("user") or {}
    active = detect_active_zspace()
    active_key = (
        (active.get("username") or "", active.get("nas_id") or "") if active else ("", "")
    )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for h in user.get("historyUsers") or []:
        if not isinstance(h, dict):
            continue
        username = str(h.get("username") or "").strip()
        nas_id = str(h.get("nasId") or "").strip()
        key = (username, nas_id)
        if not username and not nas_id:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "username": username,
                "nas_id": nas_id,
                "nas_name": str(h.get("nasName") or ""),
                "show_name": str(h.get("showName") or h.get("nickname") or ""),
                "device_mode": str(h.get("deviceMode") or ""),
                "active": key == active_key and bool(active and active.get("token_present")),
            }
        )

    if active and active_key not in seen:
        out.insert(
            0,
            {
                "username": active["username"],
                "nas_id": active["nas_id"],
                "nas_name": active.get("nas_name") or "",
                "show_name": "",
                "device_mode": "",
                "active": bool(active.get("token_present")),
            },
        )
    return out


def get_zspace_bind(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = cfg or load_config()
    z = cfg.get("zspace") or {}
    return {
        "username": str(z.get("username") or "").strip(),
        "nas_id": str(z.get("nas_id") or "").strip(),
        "nas_name": str(z.get("nas_name") or "").strip(),
    }


def bind_zspace_account(
    *,
    username: str,
    nas_id: str,
    nas_name: str = "",
) -> dict[str, Any]:
    username = username.strip()
    nas_id = nas_id.strip()
    if not username or not nas_id:
        raise ValueError("绑定需要 username 与 nas_id")
    cfg = load_config()
    cfg["zspace"] = {
        "username": username,
        "nas_id": nas_id,
        "nas_name": (nas_name or "").strip(),
    }
    save_config(cfg)
    active = detect_active_zspace()
    match = bool(
        active
        and active.get("username") == username
        and active.get("nas_id") == nas_id
        and active.get("token_present")
    )
    return {
        "ok": True,
        "bound": cfg["zspace"],
        "active_matches": match,
        "active": active,
        "config_path": str(CONFIG_PATH),
        "hint": None
        if match
        else "已绑定；请在极空间客户端登录该账号并选中对应设备后再同步。",
    }


def check_zspace_bind(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether current client session matches bound account."""
    bound = get_zspace_bind(cfg)
    active = detect_active_zspace()
    bound_ok = bool(bound["username"] and bound["nas_id"])
    match = bool(
        bound_ok
        and active
        and active.get("username") == bound["username"]
        and active.get("nas_id") == bound["nas_id"]
        and active.get("token_present")
    )
    reason = ""
    if not bound_ok:
        reason = "尚未绑定极空间账号；请在运维页选择要同步的账号"
    elif not active or not active.get("token_present"):
        reason = "极空间客户端未登录"
    elif active.get("username") != bound["username"]:
        reason = (
            f"当前登录账号 {active.get('username')} 与绑定 {bound['username']} 不一致"
        )
    elif active.get("nas_id") != bound["nas_id"]:
        reason = (
            f"当前设备 {active.get('nas_id')} ({active.get('nas_name')}) "
            f"与绑定 {bound['nas_id']} ({bound.get('nas_name')}) 不一致"
        )
    return {
        "bound": bound,
        "bound_ok": bound_ok,
        "active": active,
        "match": match,
        "reason": reason,
        "accounts": list_zspace_accounts(),
    }


def packaging_dir() -> Path:
    """Canonical templates live in the montage-studio repo."""
    here = Path(__file__).resolve()
    # engine/ops/suying_sync.py → repo root
    root = here.parents[2]
    cand = root / "packaging" / "zspace-sync"
    if cand.is_dir():
        return cand
    # fallback: already-installed tools
    return TOOLS_DIR


def default_config() -> dict[str, Any]:
    """Product-neutral skeleton. Sample legacy customers live only in packaging JSON.

    T2S carrier mode (default): only sync 速影载体/; media library sync is legacy opt-in.
    """
    base: dict[str, Any] = {
        # Versioned config for /public ↔ local sync.
        # 3: introduce media_sources as first-class media pull mapping (fail-closed).
        "version": 3,
        "local_root": str(DEFAULT_LOCAL),
        "work_root": str(DEFAULT_WORK),
        "volume_uuid": VOLUME_UUID,
        "volume_relpath": "极空间团队文件同步",
        # carrier_only: T2S 仅同步安装/备份/更新载体；片库默认同步关闭
        "sync_mode": "carrier_only",
        "media_sync_enabled": False,
        "carrier_relpath": "速影载体",
        "carrier_mirror": str(Path.home() / "Suying" / "carrier"),
        # media_sources: [{customer_key, display_name, remote_root, local_target, pull_only?}]
        # When present, zspace-team-sync.py uses it to compute pull scope and PATH_ALIASES.
        "media_sources": [],
        "zspace": {
            "username": "",
            "nas_id": "",
            "nas_name": "",
        },
        "customers": [],
    }
    return base


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        base = default_config()
        base.update({k: v for k, v in raw.items() if k not in ("customers", "zspace")})
        if "customers" in raw:
            base["customers"] = raw["customers"]
        if isinstance(raw.get("zspace"), dict):
            z = dict(base.get("zspace") or {})
            z.update({k: v for k, v in raw["zspace"].items() if v is not None})
            base["zspace"] = z
        return base
    return default_config()


def save_config(cfg: dict[str, Any]) -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def customer_local_paths(name: str, cfg: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = cfg or load_config()
    local_root = Path(cfg.get("local_root") or DEFAULT_LOCAL)
    work_root = Path(cfg.get("work_root") or DEFAULT_WORK)
    customer_root = (local_root / "速影客户").resolve()
    base = (customer_root / validate_customer_name(name)).resolve()
    if base.parent != customer_root:
        raise ValueError("客户名必须是单级目录名")
    return {
        "customer_root": str(base),
        "library_root": str(base / "01-片库"),
        "output_root": str(base / "02-成片"),
        "keyword_dir": str(base / "03-词池"),
        "keyword_pack_path": str(base / "03-词池" / "keyword-pack.json"),
        "music_root": str(base / "04-音乐"),
        "brand_root": str(base / "05-品牌"),
        "cover_templates_root": str(base / "05-品牌" / "封面模板"),
        "work_root": str(work_root),
        "data_root": str(work_root / "db"),
        "cache_root": str(work_root / "cache"),
        "render_root": str(work_root / "render"),
    }


def standard_customer_entry(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "layout": "standard",
        # legacy-only fields; new schema should put media pull mapping into media_sources.
        "aliases": [],
        # Default: do not push media artifacts back to /public unless explicitly enabled.
        "push_local": [],
    }


def ensure_customer_dirs(name: str, *, register: bool = True) -> dict[str, Any]:
    """Create sync + work dirs; optionally register in suying-sync.json."""
    name = validate_customer_name(name)
    cfg = load_config()
    paths = customer_local_paths(name, cfg)
    for key in (
        "library_root",
        "output_root",
        "keyword_dir",
        "music_root",
        "brand_root",
        "cover_templates_root",
    ):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    for sub in ("ready", "review", "failed"):
        (Path(paths["output_root"]) / sub).mkdir(parents=True, exist_ok=True)
    work = Path(paths["work_root"])
    for sub in ("db", "cache/frames", "cache/library", "cache/proxies", "cache/temp", "render", "logs"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    cover_readme = Path(paths["cover_templates_root"]) / "README.txt"
    if not cover_readme.exists():
        cover_readme.write_text(
            "速影封面模板套装目录（每客户固定）。\n"
            "index.json = 套装索引；tpl_<id>/ = 各平台槽位图。\n"
            "请用 App「封面设置」管理。\n",
            encoding="utf-8",
        )

    readme = Path(cfg["local_root"]) / "速影客户" / "速影目录说明.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        "# 速影客户目录说明\n\n"
        "速影客户/<客户名>/\n"
        "  01-片库/     源视频入库\n"
        "  02-成片/     ready | review | failed | packs\n"
        "  03-词池/     keyword-pack.json\n"
        "  04-音乐/     可选 BGM\n"
        "  05-品牌/     logo、字体、style_lock\n"
        "    封面模板/  每客户封面套装（index.json + tpl_*）\n\n"
        "引擎状态在 ../速影工作区（db/cache/render，不同步到极空间）。\n",
        encoding="utf-8",
    )

    registered = False
    if register:
        customers = list(cfg.get("customers") or [])
        if not any(c.get("name") == name for c in customers):
            customers.append(standard_customer_entry(name))
            cfg["customers"] = customers
            save_config(cfg)
            registered = True
        else:
            save_config(cfg)  # ensure file exists

    return {"ok": True, "registered": registered, "paths": paths, "config_path": str(CONFIG_PATH)}


def validate_customer_name(name: str) -> str:
    """Accept a human-readable single directory name, never a filesystem path."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("客户名不能为空")
    if clean in {".", ".."} or "/" in clean or "\\" in clean or "\x00" in clean:
        raise ValueError("客户名不得包含路径分隔符或路径段")
    return clean


def build_aliases(cfg: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    cfg = cfg or load_config()
    out: list[tuple[str, str]] = []
    # Prefer v3 media_sources.
    for s in cfg.get("media_sources") or []:
        remote = s.get("remote_root") or s.get("remote") or s.get("remote_root_path")
        local = s.get("local_target") or s.get("local") or s.get("local_target_path")
        if remote and local:
            out.append((str(remote), str(local)))
    if out:
        return out
    # Back-compat: legacy v1/v2 uses customers[].aliases.
    for c in cfg.get("customers") or []:
        for a in c.get("aliases") or []:
            remote, local = a.get("remote"), a.get("local")
            if remote and local:
                out.append((str(remote), str(local)))
    return out


def build_push_prefixes(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    out: list[str] = []
    for c in cfg.get("customers") or []:
        for p in c.get("push_local") or []:
            if p not in out:
                out.append(str(p))
    return out


def _plist_body(script_sh: Path) -> str:
    log = Path.home() / ".qr" / "logs" / "zspace-team-sync.launchd.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>{script_sh}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>StartOnMount</key>
  <true/>
  <key>WatchPaths</key>
  <array>
    <string>/Volumes</string>
  </array>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
  <key>Nice</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def install_service() -> dict[str, Any]:
    """Copy scripts to ~/QR/tools, write config + launchd plist, load agent."""
    src = packaging_dir()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    (Path.home() / ".qr" / "logs").mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in ("zspace-team-sync.py", "zspace-team-sync.sh"):
        s = src / name
        if not s.exists():
            # allow installing from tools onto itself
            s = TOOLS_DIR / name
        if not s.exists():
            raise FileNotFoundError(f"缺少同步脚本模板: {name}（packaging 与 tools 均无）")
        dest = TOOLS_DIR / name
        shutil.copy2(s, dest)
        if name.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | 0o111)
        copied.append(str(dest))

    if not CONFIG_PATH.exists():
        save_config(default_config())

    # settings note beside sync root
    local_root = Path(load_config()["local_root"])
    local_root.mkdir(parents=True, exist_ok=True)
    note = local_root / "设置说明.md"
    if not note.exists():
        pkg_note = src / "设置说明.md"
        if pkg_note.exists():
            shutil.copy2(pkg_note, note)

    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    script_sh = TOOLS_DIR / "zspace-team-sync.sh"
    PLIST_PATH.write_text(_plist_body(script_sh), encoding="utf-8")

    uid = os.getuid()
    domain = f"gui/{uid}"
    # unload then load
    subprocess.run(["launchctl", "bootout", domain, str(PLIST_PATH)], capture_output=True)
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    load = subprocess.run(
        ["launchctl", "bootstrap", domain, str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if load.returncode != 0:
        # older macOS
        load2 = subprocess.run(
            ["launchctl", "load", "-w", str(PLIST_PATH)],
            capture_output=True,
            text=True,
        )
        if load2.returncode != 0:
            raise RuntimeError(
                f"launchctl 加载失败: {load.stderr or load.stdout or load2.stderr}"
            )

    return {
        "ok": True,
        "copied": copied,
        "plist": str(PLIST_PATH),
        "config": str(CONFIG_PATH),
        "label": PLIST_LABEL,
    }


def service_status() -> dict[str, Any]:
    cfg = load_config()
    local_root = Path(cfg.get("local_root") or DEFAULT_LOCAL)
    work_root = Path(cfg.get("work_root") or DEFAULT_WORK)
    uid = os.getuid()
    print_out = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{PLIST_LABEL}"],
        capture_output=True,
        text=True,
    )
    loaded = print_out.returncode == 0
    state = "unknown"
    if loaded:
        for line in print_out.stdout.splitlines():
            if "state =" in line:
                state = line.split("=", 1)[-1].strip()
                break

    bind = check_zspace_bind(cfg)
    active = bind.get("active") or {}
    port = int(active.get("local_port") or 13581) if active else 13581
    zspace_ok = False
    try:
        import urllib.request

        urllib.request.urlopen(f"http://127.0.0.1:{port}/home/", timeout=2)
        zspace_ok = True
    except Exception:
        zspace_ok = False

    state_json = Path.home() / ".qr" / "zspace-team-sync-state.json"
    last = None
    if state_json.exists():
        try:
            last = json.loads(state_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            last = None

    fp = script_fingerprint()
    reconcile = reconcile_sync_customers()

    return {
        "config_path": str(CONFIG_PATH),
        "config_version": cfg.get("version") or 0,
        "config_exists": CONFIG_PATH.exists(),
        "plist_installed": PLIST_PATH.exists(),
        "launchd_loaded": loaded,
        "launchd_state": state,
        "scripts_installed": (TOOLS_DIR / "zspace-team-sync.py").exists()
        and (TOOLS_DIR / "zspace-team-sync.sh").exists(),
        "local_root": str(local_root),
        "local_root_exists": local_root.exists(),
        "work_root": str(work_root),
        "work_root_exists": work_root.exists(),
        "zspace_proxy_ok": zspace_ok,
        "zspace_proxy_port": port,
        "zspace_bound": bind.get("bound"),
        "zspace_bound_ok": bind.get("bound_ok"),
        "zspace_active": active,
        "zspace_match": bind.get("match"),
        "zspace_reason": bind.get("reason") or "",
        "zspace_accounts": bind.get("accounts") or [],
        "customers": [c.get("name") for c in (cfg.get("customers") or [])],
        "aliases": [{"remote": a, "local": b} for a, b in build_aliases(cfg)],
        "push_local": build_push_prefixes(cfg),
        "last_sync": last,
        "sync_mode": cfg.get("sync_mode") or "carrier_only",
        "media_sync_enabled": bool(cfg.get("media_sync_enabled", False)),
        "media_sources": cfg.get("media_sources") or [],
        "carrier_relpath": cfg.get("carrier_relpath") or "速影载体",
        "carrier_mirror": cfg.get("carrier_mirror")
        or str(Path.home() / "Suying" / "carrier"),
        "script_fingerprint": fp,
        "script_stale": bool(fp.get("stale")),
        "reconcile": reconcile,
    }
