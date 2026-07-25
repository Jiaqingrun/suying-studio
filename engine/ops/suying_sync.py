"""速影 × 极空间同步：配置、客户目录、本机服务安装。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".qr" / "suying-sync.json"
TOOLS_DIR = Path.home() / "QR" / "tools"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST_LABEL = "com.qr.zspace-team-sync"
PLIST_PATH = LAUNCH_AGENTS / f"{PLIST_LABEL}.plist"

DEFAULT_LOCAL = Path.home() / "QR-Volume" / "极空间团队文件同步"
DEFAULT_WORK = Path.home() / "QR-Volume" / "速影工作区"
VOLUME_UUID = "EF259876-00CC-4DB0-928C-15CC218A007D"


VUEX_PATH = Path.home() / "Library" / "Application Support" / "zspace" / "vuex.json"


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
    """Product-neutral skeleton. Sample legacy customers live only in packaging JSON."""
    base: dict[str, Any] = {
        "version": 1,
        "local_root": str(DEFAULT_LOCAL),
        "work_root": str(DEFAULT_WORK),
        "volume_uuid": VOLUME_UUID,
        "zspace": {
            "username": "",
            "nas_id": "",
            "nas_name": "",
        },
        "customers": [],
    }
    sample = packaging_dir() / "suying-sync.default.json"
    if sample.is_file():
        try:
            data = json.loads(sample.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Keep runtime volume defaults if sample omits them
                for k in ("local_root", "work_root", "volume_uuid", "zspace", "customers", "version"):
                    if k in data:
                        base[k] = data[k]
        except (OSError, json.JSONDecodeError):
            pass
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
    base = local_root / "速影客户" / name
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
        "aliases": [],
        "push_local": [
            f"速影客户/{name}/02-成片",
            f"速影客户/{name}/03-词池",
        ],
    }


def ensure_customer_dirs(name: str, *, register: bool = True) -> dict[str, Any]:
    """Create sync + work dirs; optionally register in suying-sync.json."""
    name = name.strip()
    if not name:
        raise ValueError("客户名不能为空")
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


def build_aliases(cfg: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    cfg = cfg or load_config()
    out: list[tuple[str, str]] = []
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

    return {
        "config_path": str(CONFIG_PATH),
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
    }
