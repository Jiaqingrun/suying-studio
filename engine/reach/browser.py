"""Semi-auto open official creator portals (G5).

Human-in-the-loop only: we open a URL / optional local Chrome user-data-dir,
never automate login, never click publish, never bypass detection / cookie pool.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from engine.catalog.db import ReachQueueItem
from engine.reach.queue import _append_log

# Official / creator entry points (public sites). Not scraper targets.
OFFICIAL_ENTRY: dict[str, dict[str, str]] = {
    "douyin": {
        "label": "抖音创作者中心",
        "url": "https://creator.douyin.com/",
        "short": "抖音",
    },
    "channels": {
        "label": "微信视频号助手",
        "url": "https://channels.weixin.qq.com/",
        "short": "视频号",
    },
    "xhs": {
        "label": "小红书创作者服务平台",
        "url": "https://creator.xiaohongshu.com/",
        "short": "小红书",
    },
    "kuaishou": {
        "label": "快手创作者服务平台",
        "url": "https://cp.kuaishou.com/",
        "short": "快手",
    },
    "baijiahao": {
        "label": "百家号",
        "url": "https://baijiahao.baidu.com/",
        "short": "百家号",
    },
    "toutiao": {
        "label": "头条号",
        "url": "https://mp.toutiao.com/",
        "short": "头条",
    },
    "zhihu": {
        "label": "知乎创作者中心",
        "url": "https://www.zhihu.com/creator",
        "short": "知乎",
    },
}

PLATFORM_SHORT: dict[str, str] = {k: str(v.get("short") or k) for k, v in OFFICIAL_ENTRY.items()}

_SAFE_PROFILE_NAME = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff .\-]{0,63}$")

CHROME_MAC = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
ACCOUNTS_JSON = "accounts.json"
ACCOUNTS_TXT = "accounts.txt"


def entry_for_platform(platform: str) -> dict[str, str]:
    p = (platform or "").strip().lower()
    if p not in OFFICIAL_ENTRY:
        raise ValueError(f"不支持的平台: {platform}")
    return dict(OFFICIAL_ENTRY[p])


def chrome_profiles_root() -> Path:
    env = (os.environ.get("SUYING_CHROME_PROFILES") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "QR-Volume" / "速影工作区" / "chrome-profiles"


def dismiss_popups_extension_dir() -> Path | None:
    """Path to unpacked 速影弹窗拦截 extension (shared under chrome-profiles)."""
    root = chrome_profiles_root()
    shared = root / "_extensions" / "suying-dismiss-popups"
    if (shared / "manifest.json").is_file():
        return shared
    repo = Path(__file__).resolve().parents[2] / "tools" / "chrome-ext-dismiss-popups"
    if (repo / "manifest.json").is_file():
        return repo
    return None


def chrome_launch_args(user_data: Path, url: str, *, cdp_port: int | None = None) -> list[str]:
    """Build Chrome argv with popup-blocker extension loaded."""
    cmd = [
        str(CHROME_MAC),
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if cdp_port:
        cmd.append(f"--remote-debugging-port={int(cdp_port)}")
        cmd.append("--remote-allow-origins=*")
    ext = dismiss_popups_extension_dir()
    if ext is not None:
        cmd.append(f"--load-extension={ext}")
    cmd.append(url)
    return cmd


def browser_config_from_profile(profile: dict[str, Any] | None, platform: str) -> dict[str, Any]:
    """Per-customer browser hints from profile_json.reach.browsers[platform]."""
    profile = profile or {}
    reach = profile.get("reach") or {}
    browsers = reach.get("browsers") or {}
    cfg = browsers.get(platform) or browsers.get("default") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    # chrome_profile_name: logical name under chrome_profiles_root (preferred)
    # profile_dir: absolute path OR name (legacy / absolute user-data-dir)
    return {
        "browser_app": str(cfg.get("browser_app") or ""),  # e.g. "Google Chrome"
        "profile_dir": str(cfg.get("profile_dir") or ""),
        "chrome_profile_name": str(cfg.get("chrome_profile_name") or ""),
        "note": str(cfg.get("note") or ""),
    }


def validate_chrome_profile_name(name: str) -> str:
    n = (name or "").strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n:
        raise ValueError("非法 Chrome 配置名")
    if not _SAFE_PROFILE_NAME.match(n):
        raise ValueError(f"非法 Chrome 配置名: {name}")
    return n


def resolve_chrome_user_data_dir(name_or_path: str) -> Path:
    """Resolve a profile name or absolute path to a user-data-dir."""
    raw = (name_or_path or "").strip()
    if not raw:
        raise ValueError("未指定 Chrome 配置")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    name = validate_chrome_profile_name(raw)
    return chrome_profiles_root() / name


def accounts_json_path() -> Path:
    return chrome_profiles_root() / ACCOUNTS_JSON


def load_accounts_meta() -> dict[str, dict[str, Any]]:
    """Load accounts.json; keys are profile names."""
    path = accounts_json_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        try:
            name = validate_chrome_profile_name(str(k))
        except ValueError:
            continue
        if isinstance(v, dict):
            plat = str(v.get("platform") or "").strip().lower()
            out[name] = {
                "platform": plat if plat in OFFICIAL_ENTRY else "",
                "label": str(v.get("label") or ""),
            }
        elif isinstance(v, str):
            plat = v.strip().lower()
            out[name] = {"platform": plat if plat in OFFICIAL_ENTRY else "", "label": ""}
    return out


def save_accounts_meta(meta: dict[str, dict[str, Any]]) -> Path:
    root = chrome_profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    path = accounts_json_path()
    # Stable sort for readability
    ordered = {k: meta[k] for k in sorted(meta.keys())}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Keep accounts.txt in sync (names only) for older tools
    txt = root / ACCOUNTS_TXT
    lines = ["# 速影 Chrome 本地配置（见 accounts.json 平台绑定）", *[f"{n}" for n in ordered]]
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def set_account_platform(name: str, platform: str) -> dict[str, Any]:
    name = validate_chrome_profile_name(name)
    entry = entry_for_platform(platform)
    meta = load_accounts_meta()
    meta[name] = {"platform": platform.strip().lower(), "label": entry["label"]}
    save_accounts_meta(meta)
    (chrome_profiles_root() / name).mkdir(parents=True, exist_ok=True)
    return {"name": name, "platform": meta[name]["platform"], "path": str(chrome_profiles_root() / name)}


def infer_platform_from_name(name: str) -> str:
    """Best-effort platform from Chinese prefix names like 抖音-01."""
    for plat, short in PLATFORM_SHORT.items():
        if name.startswith(short) or name.startswith(f"{short}-") or name.startswith(f"{short}_"):
            return plat
    return ""


def next_platform_profile_names(platform: str, count: int) -> list[str]:
    entry = entry_for_platform(platform)
    short = str(entry.get("short") or PLATFORM_SHORT.get(platform) or platform)
    meta = load_accounts_meta()
    root = chrome_profiles_root()
    existing = set(meta.keys())
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                existing.add(child.name)
    # Find max numeric suffix for this short prefix
    prefix = f"{short}-"
    max_n = 0
    for n in existing:
        if n.startswith(prefix):
            tail = n[len(prefix) :]
            if tail.isdigit():
                max_n = max(max_n, int(tail))
    names: list[str] = []
    i = max_n
    while len(names) < count:
        i += 1
        candidate = f"{short}-{i:02d}"
        if candidate in existing:
            continue
        names.append(candidate)
        existing.add(candidate)
    return names


def create_chrome_profiles(
    *,
    platform: str,
    count: int = 1,
    name_prefix: str | None = None,
) -> dict[str, Any]:
    """Create N empty Chrome user-data dirs bound to a platform. Does NOT launch Chrome."""
    entry = entry_for_platform(platform)
    plat = platform.strip().lower()
    n = int(count)
    if n < 1 or n > 10:
        raise ValueError("count 须在 1…10")
    if name_prefix:
        # Custom single-prefix batch: prefix-01 …
        base = validate_chrome_profile_name(name_prefix.strip().rstrip("-_"))
        meta = load_accounts_meta()
        root = chrome_profiles_root()
        existing = set(meta.keys())
        if root.is_dir():
            existing.update(c.name for c in root.iterdir() if c.is_dir())
        names: list[str] = []
        i = 0
        while len(names) < n:
            i += 1
            candidate = f"{base}-{i:02d}"
            if candidate in existing:
                continue
            names.append(candidate)
            existing.add(candidate)
    else:
        names = next_platform_profile_names(plat, n)

    meta = load_accounts_meta()
    created: list[dict[str, Any]] = []
    root = chrome_profiles_root()
    for name in names:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        meta[name] = {"platform": plat, "label": entry["label"]}
        created.append({"name": name, "platform": plat, "path": str(path), "label": entry["label"]})
    save_accounts_meta(meta)
    return {
        "ok": True,
        "platform": plat,
        "label": entry["label"],
        "created": created,
        "selected": created[0]["name"] if created else None,
        "count": len(created),
        "auto_publish": False,
        "human_in_loop": True,
        "note": "仅创建本地配置目录并绑定平台；未打开浏览器、未登录。",
    }


def list_chrome_profiles() -> dict[str, Any]:
    """List local Chrome user-data directories with platform binding."""
    root = chrome_profiles_root()
    meta = load_accounts_meta()
    names: list[str] = []
    # Prefer accounts.json keys order, then dirs / accounts.txt
    for n in meta:
        if n not in names:
            names.append(n)
    accounts_file = root / ACCOUNTS_TXT
    if accounts_file.is_file():
        for line in accounts_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                n = validate_chrome_profile_name(line)
            except ValueError:
                continue
            if n not in names:
                names.append(n)
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                try:
                    n = validate_chrome_profile_name(child.name)
                except ValueError:
                    continue
                if n not in names:
                    names.append(n)

    profiles: list[dict[str, Any]] = []
    for n in names:
        m = meta.get(n) or {}
        plat = str(m.get("platform") or "").strip().lower()
        if not plat:
            plat = infer_platform_from_name(n)
        label = str(m.get("label") or "")
        if plat and plat in OFFICIAL_ENTRY and not label:
            label = OFFICIAL_ENTRY[plat]["label"]
        profiles.append(
            {
                "name": n,
                "path": str(root / n),
                "platform": plat or None,
                "label": label or None,
            }
        )
    return {
        "ok": True,
        "root": str(root),
        "profiles": profiles,
        "platforms": [
            {"id": k, "label": v["label"], "url": v["url"], "short": v.get("short")}
            for k, v in OFFICIAL_ENTRY.items()
        ],
        "chrome_installed": CHROME_MAC.is_file() if os.uname().sysname == "Darwin" else False,
        "auto_publish": False,
        "human_in_loop": True,
        "note": "按需创建配置并绑定平台；串行换号；不做 Cookie 池/矩阵群控。",
    }


def resolve_profile_platform(name: str, override: str | None = None) -> str:
    if override and override.strip():
        p = override.strip().lower()
        entry_for_platform(p)
        return p
    meta = load_accounts_meta().get(validate_chrome_profile_name(name)) or {}
    plat = str(meta.get("platform") or "").strip().lower()
    if plat in OFFICIAL_ENTRY:
        return plat
    inferred = infer_platform_from_name(name)
    if inferred:
        return inferred
    return "douyin"


def open_chrome_profile(
    name_or_path: str,
    *,
    url: str,
    dry_run: bool = False,
    cdp_port: int | None = None,
) -> dict[str, Any]:
    """Launch Chrome with an isolated --user-data-dir (background).

    When cdp_port is set (e.g. 9222), enables remote debugging for CDP auto-upload.
    """
    user_data = resolve_chrome_user_data_dir(name_or_path)
    result: dict[str, Any] = {
        "opened": False,
        "dry_run": dry_run,
        "url": url,
        "user_data_dir": str(user_data),
        "profile_name": user_data.name,
        "method": "chrome --user-data-dir",
        "auto_publish": False,
        "human_in_loop": True,
        "cdp_port": cdp_port,
    }
    if dry_run:
        result["chrome_path"] = str(CHROME_MAC)
        result["chrome_installed"] = CHROME_MAC.is_file() if os.uname().sysname == "Darwin" else False
        return result

    user_data.mkdir(parents=True, exist_ok=True)
    if os.uname().sysname != "Darwin" or not CHROME_MAC.is_file():
        raise ValueError("未找到 Google Chrome（仅支持 macOS /Applications）")

    cmd = chrome_launch_args(user_data, url, cdp_port=cdp_port)
    # Detach so the engine process is not blocked by Chrome.
    subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    result["opened"] = True
    result["load_extension"] = str(dismiss_popups_extension_dir() or "")
    return result


def write_paste_card(
    item: ReachQueueItem,
    dest: Path | None = None,
    covers: list[str] | None = None,
) -> Path:
    """Write a human paste card (title/body/video/cover paths) beside pack or cache."""
    if dest is None:
        if item.pack_dir:
            dest = Path(item.pack_dir) / f"reach_paste_{item.platform}.txt"
        else:
            dest = Path(item.video_path).parent / f"reach_paste_{item.id}_{item.platform}.txt"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    entry = entry_for_platform(item.platform)

    cover_paths = list(covers or [])
    if not cover_paths:
        try:
            from engine.config.settings import load_settings
            from engine.reach.cover_templates import resolve_cover_store_for_settings, resolve_covers

            store = resolve_cover_store_for_settings(load_settings())
            resolved = resolve_covers(
                store,
                platform=item.platform,
                pack_dir=item.pack_dir,
            )
            cover_paths = list(resolved.get("covers") or [])
        except Exception:
            if item.pack_dir:
                pdir = Path(item.pack_dir)
                for name in ("cover.jpg", "cover_2.jpg", "cover_3.jpg"):
                    c = pdir / name
                    if c.is_file():
                        cover_paths.append(str(c))

    cover_block = "\n".join(f"  [{i}] {p}" for i, p in enumerate(cover_paths)) or "  （无解析到的封面，发布前须补齐）"
    lines = [
        "速影 · 触达粘贴卡（文案+封面齐套才可点发布）",
        f"平台: {entry['label']} ({item.platform})",
        f"入口: {entry['url']}",
        "",
        f"标题:\n{item.title}",
        "",
        f"正文:\n{item.body}",
        "",
        f"视频路径:\n{item.video_path}",
        "",
        f"封面槽位（按序上传）:\n{cover_block}",
        "",
        "请用已登录的本人账号在官方页面手动上传并点击发布。",
        "缺文案或缺任一封面槽位时不要点发布。",
        "本产品不提供绕检测、矩阵养号。",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def open_official_entry(
    *,
    platform: str,
    profile: dict[str, Any] | None = None,
    dry_run: bool = False,
    chrome_profile: str | None = None,
) -> dict[str, Any]:
    """Open the official URL; prefer Chrome user-data-dir when configured."""
    entry = entry_for_platform(platform)
    bcfg = browser_config_from_profile(profile, platform)
    result: dict[str, Any] = {
        "platform": platform,
        "label": entry["label"],
        "url": entry["url"],
        "opened": False,
        "dry_run": dry_run,
        "browser": bcfg,
        "auto_publish": False,
        "human_in_loop": True,
        "method": "webbrowser",
    }
    url = entry["url"]

    # Prefer explicit request override, then chrome_profile_name, then profile_dir.
    chrome_target = (
        (chrome_profile or "").strip()
        or (bcfg.get("chrome_profile_name") or "").strip()
        or (bcfg.get("profile_dir") or "").strip()
    )
    if chrome_target:
        try:
            chrome_info = open_chrome_profile(chrome_target, url=url, dry_run=dry_run)
        except ValueError as e:
            result["browser_open_error"] = str(e)
            if dry_run:
                return result
            raise
        result.update(
            {
                "opened": chrome_info.get("opened", False),
                "dry_run": dry_run,
                "method": chrome_info.get("method"),
                "user_data_dir": chrome_info.get("user_data_dir"),
                "chrome_profile": chrome_info.get("profile_name"),
            }
        )
        if dry_run:
            result["chrome"] = chrome_info
        return result

    if dry_run:
        return result

    app = (bcfg.get("browser_app") or "").strip()
    # Optional: macOS `open -a App url` when customer configured a browser name.
    if app and os.uname().sysname == "Darwin":
        cmd = ["open", "-a", app, url]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            result["opened"] = True
            result["method"] = f"open -a {app}"
            return result
        result["browser_open_error"] = (proc.stderr or proc.stdout or "").strip()

    ok = webbrowser.open(url, new=2)
    result["opened"] = bool(ok)
    result["method"] = "webbrowser.open"
    return result


def set_selected_chrome_profile(
    profile: dict[str, Any] | None,
    *,
    name: str,
    platform: str | None = None,
) -> dict[str, Any]:
    """Return updated customer profile_json with selected Chrome profile name."""
    name = validate_chrome_profile_name(name)
    plat = resolve_profile_platform(name, platform)
    set_account_platform(name, plat)
    out = dict(profile or {})
    reach = dict(out.get("reach") or {})
    browsers = dict(reach.get("browsers") or {})
    cfg = dict(browsers.get(plat) or {})
    cfg["chrome_profile_name"] = name
    cfg["browser_app"] = cfg.get("browser_app") or "Google Chrome"
    cfg["profile_dir"] = name
    browsers[plat] = cfg
    # Also remember last selected regardless of platform
    reach["last_chrome_profile"] = name
    reach["last_chrome_platform"] = plat
    reach["browsers"] = browsers
    out["reach"] = reach
    return out


def prepare_and_open(
    session,  # noqa: ANN001
    item: ReachQueueItem,
    *,
    profile: dict[str, Any] | None = None,
    dry_run: bool = False,
    chrome_profile: str | None = None,
) -> dict[str, Any]:
    """Write paste card, open official entry, log on the queue item."""
    from datetime import datetime, timezone

    card = write_paste_card(item)
    open_info = open_official_entry(
        platform=item.platform,
        profile=profile,
        dry_run=dry_run,
        chrome_profile=chrome_profile,
    )
    _append_log(
        item,
        "open_official_entry",
        url=open_info.get("url"),
        opened=open_info.get("opened"),
        dry_run=dry_run,
        paste_card=str(card),
        chrome_profile=open_info.get("chrome_profile"),
    )
    item.updated_at = datetime.now(timezone.utc)
    # Keep awaiting_human if already there; queued → awaiting_human
    if item.status == "queued":
        from engine.reach.queue import set_status

        set_status(session, item, "awaiting_human", note=item.note or "opened_official_entry")
    else:
        session.commit()
        session.refresh(item)

    return {
        "ok": True,
        "item_id": item.id,
        "paste_card": str(card),
        "open": open_info,
        "prefill": {
            "title": item.title,
            "body": item.body,
            "video_path": item.video_path,
            "pack_dir": item.pack_dir,
        },
        "auto_publish": False,
        "human_in_loop": True,
        "disclaimer": "仅打开官方入口并给出粘贴卡；发布须本人点击，不做自动发帖/反检测。",
    }
