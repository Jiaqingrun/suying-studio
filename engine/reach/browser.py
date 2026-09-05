"""Semi-auto open official creator portals (G5).

Human-in-the-loop only: we open a URL / optional local Chrome user-data-dir,
never automate login, never click publish, never bypass detection / cookie pool.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from engine.catalog.db import ReachQueueItem
from engine.reach.business_scope import (
    SCOPE_CONTENT,
    SCOPE_VIDEO,
    assert_platform_in_scope,
    normalize_scope,
    platforms_for_scope,
    require_scope_for_platform,
    scope_for_platform,
)
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

# Soft-article-only portals (must NOT appear on /reach/platforms catalog).
CONTENT_EXTRA_ENTRY: dict[str, dict[str, str]] = {
    "wechat_mp": {
        "label": "微信公众号",
        "url": "https://mp.weixin.qq.com/",
        "short": "公众号",
    },
}

PLATFORM_COOKIE_DOMAIN: dict[str, str] = {
    "douyin": "douyin.com",
    "channels": "channels.weixin.qq.com",
    "xhs": "xiaohongshu.com",
    "kuaishou": "kuaishou.com",
    "baijiahao": "baidu.com",
    "toutiao": "toutiao.com",
    "zhihu": "zhihu.com",
    "wechat_mp": "weixin.qq.com",
    "csdn": "csdn.net",
}
COOKIE_REQUIRED_PLATFORMS = frozenset({"douyin", "channels", "kuaishou"})

PLATFORM_SHORT: dict[str, str] = {
    **{k: str(v.get("short") or k) for k, v in OFFICIAL_ENTRY.items()},
    **{k: str(v.get("short") or k) for k, v in CONTENT_EXTRA_ENTRY.items()},
}

_SAFE_PROFILE_NAME = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff .\-]{0,63}$")

CHROME_APP_MAC = Path("/Applications/Google Chrome.app")
CHROME_MAC = CHROME_APP_MAC / "Contents/MacOS/Google Chrome"
ACCOUNTS_JSON = "accounts.json"
ACCOUNTS_TXT = "accounts.txt"
CUSTOM_PLATFORMS_JSON = "custom-platforms.json"


def _builtin_entries() -> dict[str, dict[str, str]]:
    entries = {**OFFICIAL_ENTRY, **CONTENT_EXTRA_ENTRY}
    try:
        from engine.content.platforms import PLATFORM_REGISTRY

        for key, raw in PLATFORM_REGISTRY.items():
            url = str(raw.get("publish_url") or "").strip()
            if not url:
                continue
            entries.setdefault(
                key,
                {
                    "label": str(raw.get("label") or key),
                    "url": url,
                    "short": str(raw.get("short") or raw.get("label") or key),
                },
            )
    except ImportError:
        pass
    return entries


def custom_platforms_path(*, customer_id: int, business_scope: str) -> Path:
    return customer_scope_root(customer_id, business_scope) / CUSTOM_PLATFORMS_JSON


def load_custom_platforms(*, customer_id: int, business_scope: str) -> dict[str, dict[str, str]]:
    scope = normalize_scope(business_scope)
    if scope != SCOPE_CONTENT:
        return {}
    path = custom_platforms_path(customer_id=customer_id, business_scope=scope)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if scope_for_platform(str(key)) != SCOPE_CONTENT or not isinstance(value, dict):
            continue
        label = str(value.get("label") or "").strip()
        url = str(value.get("url") or "").strip()
        if not label or not _valid_official_url(url):
            continue
        out[str(key)] = {"label": label, "short": label, "url": url, "custom": "true"}
    return out


def _valid_official_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def register_custom_content_platform(
    name: str,
    url: str,
    *,
    customer_id: int,
) -> tuple[str, dict[str, str]]:
    label = (name or "").strip()
    official_url = (url or "").strip()
    if not label or len(label) > 40:
        raise ValueError("自定义平台名称须为 1…40 个字符")
    if "/" in label or "\\" in label or not _SAFE_PROFILE_NAME.match(label):
        raise ValueError("自定义平台名称含非法字符")
    if not _valid_official_url(official_url):
        raise ValueError("自定义平台须填写有效的 http/https 官方入口")
    digest = hashlib.sha256(label.casefold().encode("utf-8")).hexdigest()[:12]
    platform = f"custom_{digest}"
    custom = load_custom_platforms(customer_id=customer_id, business_scope=SCOPE_CONTENT)
    entry = {"label": label, "short": label, "url": official_url, "custom": "true"}
    custom[platform] = entry
    root = customer_scope_root(customer_id, SCOPE_CONTENT)
    root.mkdir(parents=True, exist_ok=True)
    custom_platforms_path(customer_id=customer_id, business_scope=SCOPE_CONTENT).write_text(
        json.dumps(custom, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return platform, entry


def _entries_for_scope(*, customer_id: int, business_scope: str) -> dict[str, dict[str, str]]:
    entries = _builtin_entries()
    entries.update(load_custom_platforms(customer_id=customer_id, business_scope=business_scope))
    allowed = platforms_for_scope(business_scope)
    return {
        key: value
        for key, value in entries.items()
        if key in allowed or scope_for_platform(key) == normalize_scope(business_scope)
    }


def entry_for_platform(
    platform: str,
    *,
    customer_id: int | None = None,
    business_scope: str | None = None,
) -> dict[str, str]:
    p = (platform or "").strip().lower()
    entries = _builtin_entries()
    if customer_id is not None and business_scope:
        entries.update(
            load_custom_platforms(customer_id=customer_id, business_scope=business_scope)
        )
    if p in entries:
        return dict(entries[p])
    raise ValueError(f"不支持的平台: {platform}")


def _all_entries() -> dict[str, dict[str, str]]:
    return _builtin_entries()


def chrome_profiles_root() -> Path:
    env = (os.environ.get("SUYING_CHROME_PROFILES") or "").strip()
    if env:
        return Path(env).expanduser()
    # Browser profiles contain SQLite/WAL databases, file locks and encrypted
    # login cookies. They must stay on the local macOS filesystem; ExFAT/SMB
    # workspaces can make Chrome silently fall back to memory-only cookies.
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "com.qr.suying"
        / "chrome-profiles"
    )


def _legacy_workspace_chrome_profiles_roots() -> list[Path]:
    """Previous profile roots, including workspaces whose DB moved to local APFS."""
    try:
        from engine.config.settings import load_settings

        paths = load_settings().paths
        data_root = Path(paths.data_root).expanduser()
        candidates = [
            data_root.parent / "chrome-profiles"
            if data_root.name == "db"
            else data_root / "chrome-profiles",
            Path(paths.cache_root).expanduser().parent / "chrome-profiles",
            Path(paths.render_root).expanduser().parent / "chrome-profiles",
        ]
        result: list[Path] = []
        for candidate in candidates:
            if candidate not in result:
                result.append(candidate)
        return result
    except Exception:
        return []


def _legacy_workspace_chrome_profiles_root() -> Path | None:
    """Back-compatible first previous root."""
    roots = _legacy_workspace_chrome_profiles_roots()
    return roots[0] if roots else None


def _legacy_profiles_in_use(root: Path) -> bool:
    """Check Chrome's root lock without recursively walking profile databases."""
    try:
        for first in root.iterdir():
            if not first.is_dir():
                continue
            if first.name.startswith("customer-"):
                for scope in first.iterdir():
                    if not scope.is_dir():
                        continue
                    for profile in scope.iterdir():
                        if profile.is_dir() and (profile / "SingletonLock").exists():
                            return True
            elif not first.name.startswith((".", "_")) and (first / "SingletonLock").exists():
                return True
    except OSError:
        return True
    return False


def customer_scope_root(customer_id: int, business_scope: str) -> Path:
    """Physical root for one customer's one business domain."""
    cid = int(customer_id)
    if cid < 1:
        raise ValueError("customer_id 无效")
    scope = normalize_scope(business_scope)
    return chrome_profiles_root() / f"customer-{cid}" / scope


def unassigned_profiles_root() -> Path:
    return chrome_profiles_root() / "_unassigned"


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


def chrome_launch_args(
    user_data: Path, url: str, *, cdp_port: int | None = None, headless: bool = False
) -> list[str]:
    """Build Chrome argv; G7 may use official Chrome's auditable headless mode.

    Do **not** put the publish URL on the command line. Combined with
    ``--restore-last-session``, each cold start would restore every old
    creator tab *and* open another fresh upload tab (tab pile-up). Navigation
    is always done via CDP after the debugger port is ready.
    """
    del url  # navigation is CDP-only; keep arg for call-site compatibility
    cmd = [
        str(CHROME_MAC),
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        # Preserve session cookies across the mandatory clean close between
        # isolated accounts; the next launch still navigates to the requested
        # official URL via CDP.
        "--restore-last-session",
    ]
    if cdp_port:
        cmd.append(f"--remote-debugging-port={int(cdp_port)}")
        cmd.append("--remote-allow-origins=*")
    if headless:
        cmd.extend(["--headless=new", "--disable-gpu", "--hide-scrollbars"])
    else:
        ext = dismiss_popups_extension_dir()
        if ext is not None:
            cmd.append(f"--load-extension={ext}")
    return cmd


def chrome_launchservices_command(
    user_data: Path, url: str, *, cdp_port: int | None = None, headless: bool = False
) -> list[str]:
    """macOS LaunchServices command so Chrome can encrypt/persist cookies.

    Exec'ing ``Contents/MacOS/Google Chrome`` directly from the engine process
    leaves cookies in memory only: ``Default/Cookies`` stays empty, so switching
    accounts (Browser.close → reopen) always drops back to the login page.
    ``open -na Google Chrome.app --args …`` starts Chrome as the signed app
    bundle and Keychain-backed cookie persistence works.

    Headless G7 patrols also use LaunchServices, but must pass ``-g`` so macOS
    does not activate Chrome.app and steal keyboard focus while the user types.
    Visible login/publish opens omit ``-g`` so the window still comes forward.
    """
    args = chrome_launch_args(
        user_data, url, cdp_port=cdp_port, headless=headless
    )
    cmd = ["open", "-na", str(CHROME_APP_MAC)]
    if headless:
        cmd.append("-g")
    cmd.extend(["--args", *args[1:]])
    return cmd


def browser_config_from_profile(
    profile: dict[str, Any] | None,
    platform: str,
    *,
    business_scope: str | None = None,
) -> dict[str, Any]:
    """Per-customer browser hints from profile_json.reach[.scopes][scope].browsers[platform]."""
    profile = profile or {}
    reach = profile.get("reach") or {}
    scope = None
    try:
        scope = normalize_scope(business_scope) if business_scope else require_scope_for_platform(platform)
    except ValueError:
        scope = None
    scoped = {}
    if scope:
        scopes = reach.get("scopes") if isinstance(reach.get("scopes"), dict) else {}
        scoped = scopes.get(scope) if isinstance(scopes.get(scope), dict) else {}
    browsers = {}
    if isinstance(scoped, dict) and isinstance(scoped.get("browsers"), dict):
        browsers = scoped["browsers"]
    elif isinstance(reach.get("browsers"), dict):
        browsers = reach["browsers"]
    cfg = browsers.get(platform) or browsers.get("default") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "browser_app": str(cfg.get("browser_app") or ""),
        "profile_dir": str(cfg.get("profile_dir") or ""),
        "chrome_profile_name": str(cfg.get("chrome_profile_name") or ""),
        "note": str(cfg.get("note") or ""),
        "business_scope": scope,
    }


def validate_chrome_profile_name(name: str) -> str:
    n = (name or "").strip()
    if not n or n in (".", "..") or "/" in n or "\\" in n:
        raise ValueError("非法 Chrome 配置名")
    if n.startswith("_") or n.startswith("customer-"):
        raise ValueError(f"非法 Chrome 配置名: {name}")
    if not _SAFE_PROFILE_NAME.match(n):
        raise ValueError(f"非法 Chrome 配置名: {name}")
    return n


def resolve_chrome_user_data_dir(
    name_or_path: str,
    *,
    customer_id: int | None = None,
    business_scope: str | None = None,
    require_existing: bool = False,
) -> Path:
    """Resolve a profile name to the scoped user-data-dir.

    Fail-closed: relative names require customer_id + business_scope and must
    live under that scope directory. Absolute paths are only accepted when they
    already resolve under the expected scoped root. Profiles that only exist in
    the sibling business scope are rejected (no cross-domain fallback).
    """
    raw = (name_or_path or "").strip()
    if not raw:
        raise ValueError("未指定 Chrome 配置")
    if customer_id is None or not business_scope:
        raise ValueError("解析 Chrome 配置必须同时提供 customer_id 与 business_scope")
    scope = normalize_scope(business_scope)
    scoped_root = customer_scope_root(customer_id, scope)
    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            resolved = p.resolve()
            resolved.relative_to(scoped_root.resolve())
        except Exception as exc:
            raise ValueError(
                f"禁止跨业务域引用 Chrome 配置：绝对路径必须位于 {scoped_root}"
            ) from exc
        return resolved
    name = validate_chrome_profile_name(raw)
    path = scoped_root / name
    other = SCOPE_CONTENT if scope == SCOPE_VIDEO else SCOPE_VIDEO
    other_path = customer_scope_root(customer_id, other) / name
    meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
    other_meta = load_accounts_meta(customer_id=customer_id, business_scope=other)
    in_scope = path.exists() or name in meta
    in_other = other_path.exists() or name in other_meta
    if in_other and not in_scope:
        raise ValueError(f"配置 {name} 属于 {other} 业务域，不能用于 {scope}")
    if require_existing and not in_scope:
        raise ValueError(f"Chrome 配置不存在于 {scope}: {name}")
    return path


def accounts_json_path(*, customer_id: int, business_scope: str) -> Path:
    return customer_scope_root(customer_id, business_scope) / ACCOUNTS_JSON


def load_accounts_meta(*, customer_id: int, business_scope: str) -> dict[str, dict[str, Any]]:
    """Load scoped accounts.json; keys are profile names."""
    path = accounts_json_path(customer_id=customer_id, business_scope=business_scope)
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
                "platform": (
                    plat
                    if scope_for_platform(plat) == normalize_scope(business_scope)
                    else ""
                ),
                "label": str(v.get("label") or ""),
                "purpose": str(v.get("purpose") or ""),
                "provisioning_status": str(v.get("provisioning_status") or ""),
                "explicit_created_at": (
                    str(v.get("explicit_created_at") or "") or None
                ),
            }
        elif isinstance(v, str):
            plat = v.strip().lower()
            out[name] = {
                "platform": (
                    plat
                    if scope_for_platform(plat) == normalize_scope(business_scope)
                    else ""
                ),
                "label": "",
            }
    return out


def save_accounts_meta(
    meta: dict[str, dict[str, Any]],
    *,
    customer_id: int,
    business_scope: str,
) -> Path:
    root = customer_scope_root(customer_id, business_scope)
    root.mkdir(parents=True, exist_ok=True)
    path = accounts_json_path(customer_id=customer_id, business_scope=business_scope)
    ordered = {k: meta[k] for k in sorted(meta.keys())}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt = root / ACCOUNTS_TXT
    lines = [
        f"# 速影 Chrome 本地配置 customer={customer_id} scope={normalize_scope(business_scope)}",
        *[f"{n}" for n in ordered],
    ]
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def inspect_chrome_profile_storage(path: Path, platform: str | None) -> dict[str, Any]:
    """Return non-secret profile health metadata without reading cookie values."""
    prefs_path = path / "Default" / "Preferences"
    cookies_candidates = [
        path / "Default" / "Network" / "Cookies",
        path / "Default" / "Cookies",
    ]
    cookies_path = next((p for p in cookies_candidates if p.is_file()), cookies_candidates[-1])
    initialized = prefs_path.is_file()
    exit_type: str | None = None
    if initialized:
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            profile_prefs = prefs.get("profile") if isinstance(prefs, dict) else {}
            if isinstance(profile_prefs, dict):
                exit_type = str(profile_prefs.get("exit_type") or "").strip() or None
        except (OSError, json.JSONDecodeError):
            pass

    cookie_count: int | None = None
    plat = str(platform or "").strip().lower()
    entry = _all_entries().get(plat) or {}
    official_host = (
        PLATFORM_COOKIE_DOMAIN.get(plat)
        or (urlparse(str(entry.get("url") or "")).hostname or "").lower()
    )
    if cookies_path.is_file() and official_host:
        try:
            connection = sqlite3.connect(
                f"file:{cookies_path}?mode=ro",
                uri=True,
                timeout=0.2,
            )
            try:
                # Chrome may store host_key as "channels.weixin.qq.com" or
                # ".channels.weixin.qq.com"; accept exact and suffix forms.
                row = connection.execute(
                    "SELECT COUNT(*) FROM cookies WHERE host_key = ? OR host_key = ? "
                    "OR host_key LIKE ? OR host_key LIKE ?",
                    (
                        official_host,
                        f".{official_host}",
                        f"%.{official_host}",
                        f"%{official_host}",
                    ),
                ).fetchone()
                cookie_count = int(row[0] if row else 0)
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            cookie_count = None

    if not initialized:
        login_data_state = "not_initialized"
    elif cookie_count is None:
        login_data_state = "unknown"
    elif cookie_count > 0:
        login_data_state = "present"
    elif plat in COOKIE_REQUIRED_PLATFORMS:
        login_data_state = "missing"
    else:
        # Some official portals (notably Xiaohongshu) can keep authentication
        # in IndexedDB/local storage with no durable platform cookie.
        login_data_state = "unknown"
    return {
        "profile_initialized": initialized,
        "cookie_store_present": cookies_path.is_file(),
        "platform_cookie_count": cookie_count,
        "login_data_state": login_data_state,
        "last_profile_exit_type": exit_type,
    }


def probe_chrome_profile_login(
    name: str,
    *,
    customer_id: int,
    business_scope: str,
    platform: str,
) -> dict[str, Any]:
    """Classify login only from the currently managed Chrome DOM.

    Cookie/local-storage presence is intentionally not accepted as login proof.
    Profiles outside the single managed slot remain stale_unknown.
    """
    from engine.reach.chrome_runtime import managed_profile_connection

    profile_name = validate_chrome_profile_name(name)
    connection = managed_profile_connection(
        customer_id=customer_id,
        business_scope=business_scope,
        profile_name=profile_name,
    )
    if not connection:
        return {
            "login_status": "stale_unknown",
            "login_checked_at": None,
            "login_status_source": "no_live_managed_session",
        }
    try:
        from engine.reach.cdp_client import CdpSession, list_tabs
        from engine.reach.publish_runner import _probe_login_ready_across_frames

        entry = entry_for_platform(
            platform,
            customer_id=customer_id,
            business_scope=business_scope,
        )
        host = (urlparse(str(entry.get("url") or "")).hostname or "").lower()
        cdp_http = f"http://127.0.0.1:{int(connection['port'])}"
        pages = [
            tab
            for tab in list_tabs(cdp_http)
            if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")
        ]
        pages.sort(
            key=lambda tab: (
                0
                if host
                and host
                in (urlparse(str(tab.get("url") or "")).hostname or "").lower()
                else 1,
                0 if "/login" not in str(tab.get("url") or "").lower() else 1,
            )
        )
        if not pages:
            raise RuntimeError("受管 Chrome 没有可探测的顶层页面")
        session = CdpSession(str(pages[0]["webSocketDebuggerUrl"]), timeout=3.0)
        try:
            probe = _probe_login_ready_across_frames(session)
        finally:
            session.close()
        status = (
            "logged_out"
            if probe.get("login")
            else "verified_logged_in"
            if probe.get("ready") and not probe.get("blocked")
            else "stale_unknown"
        )
        return {
            "login_status": status,
            "login_checked_at": datetime.now(timezone.utc).isoformat(),
            "login_status_source": "managed_chrome_dom",
        }
    except Exception:
        return {
            "login_status": "stale_unknown",
            "login_checked_at": datetime.now(timezone.utc).isoformat(),
            "login_status_source": "managed_chrome_probe_failed",
        }


def set_account_platform(
    name: str,
    platform: str,
    *,
    customer_id: int,
    business_scope: str,
) -> dict[str, Any]:
    name = validate_chrome_profile_name(name)
    plat = assert_platform_in_scope(platform, business_scope)
    entry = entry_for_platform(
        plat, customer_id=customer_id, business_scope=business_scope
    )
    meta = load_accounts_meta(customer_id=customer_id, business_scope=business_scope)
    meta[name] = {
        **(meta.get(name) or {}),
        "platform": plat,
        "label": entry["label"],
    }
    save_accounts_meta(meta, customer_id=customer_id, business_scope=business_scope)
    path = customer_scope_root(customer_id, business_scope) / name
    path.mkdir(parents=True, exist_ok=True)
    return {
        "name": name,
        "platform": plat,
        "path": str(path),
        "customer_id": int(customer_id),
        "business_scope": normalize_scope(business_scope),
    }


def infer_platform_from_name(name: str) -> str:
    """Best-effort platform from Chinese prefix names like 抖音-01."""
    for plat, short in PLATFORM_SHORT.items():
        if name.startswith(short) or name.startswith(f"{short}-") or name.startswith(f"{short}_"):
            return plat
    return ""


def next_platform_profile_names(
    platform: str,
    count: int,
    *,
    customer_id: int,
    business_scope: str,
) -> list[str]:
    entry = entry_for_platform(
        platform, customer_id=customer_id, business_scope=business_scope
    )
    short = str(entry.get("short") or PLATFORM_SHORT.get(platform) or platform)
    meta = load_accounts_meta(customer_id=customer_id, business_scope=business_scope)
    root = customer_scope_root(customer_id, business_scope)
    existing = set(meta.keys())
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                existing.add(child.name)
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
    customer_id: int,
    business_scope: str,
    platform: str,
    count: int = 1,
    name_prefix: str | None = None,
    custom_platform_name: str | None = None,
    custom_platform_url: str | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Create N empty Chrome user-data dirs in the scoped customer directory."""
    scope = normalize_scope(business_scope)
    expected_purpose = "article" if scope == SCOPE_CONTENT else "video"
    profile_purpose = str(purpose or expected_purpose).strip().lower()
    if profile_purpose not in {expected_purpose, "message"}:
        raise ValueError(
            f"{scope} Chrome 配置 purpose 仅支持 {expected_purpose} 或 message"
        )
    if custom_platform_name:
        if scope != SCOPE_CONTENT:
            raise ValueError("自定义平台仅用于软文发布账号")
        plat, entry = register_custom_content_platform(
            custom_platform_name,
            custom_platform_url or "",
            customer_id=customer_id,
        )
    else:
        plat = assert_platform_in_scope(platform, scope)
        entry = entry_for_platform(
            plat, customer_id=customer_id, business_scope=scope
        )
    n = int(count)
    if n < 1 or n > 10:
        raise ValueError("count 须在 1…10")
    if name_prefix:
        base = validate_chrome_profile_name(name_prefix.strip().rstrip("-_"))
        meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
        root = customer_scope_root(customer_id, scope)
        existing = set(meta.keys())
        if root.is_dir():
            existing.update(c.name for c in root.iterdir() if c.is_dir())
        names: list[str] = []
        if n == 1:
            if base in existing:
                raise ValueError(f"Chrome 配置已存在: {base}")
            names = [base]
        i = 0
        while len(names) < n:
            i += 1
            candidate = f"{base}-{i:02d}"
            if candidate in existing:
                continue
            names.append(candidate)
            existing.add(candidate)
    else:
        names = next_platform_profile_names(
            plat, n, customer_id=customer_id, business_scope=scope
        )

    meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
    created: list[dict[str, Any]] = []
    root = customer_scope_root(customer_id, scope)
    for name in names:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        meta[name] = {
            "platform": plat,
            "label": entry["label"],
            "purpose": profile_purpose,
            "provisioning_status": "explicit",
            "explicit_created_at": datetime.now(timezone.utc).isoformat(),
        }
        created.append(
            {
                "name": name,
                "platform": plat,
                "path": str(path),
                "label": entry["label"],
                "customer_id": int(customer_id),
                "business_scope": scope,
                "purpose": profile_purpose,
            }
        )
    save_accounts_meta(meta, customer_id=customer_id, business_scope=scope)
    return {
        "ok": True,
        "customer_id": int(customer_id),
        "business_scope": scope,
        "platform": plat,
        "purpose": profile_purpose,
        "label": entry["label"],
        "created": created,
        "selected": created[0]["name"] if created else None,
        "count": len(created),
        "root": str(root),
        "auto_publish": False,
        "human_in_loop": True,
        "note": "仅创建本地配置目录并绑定平台；未打开浏览器、未登录。",
    }


def list_chrome_profiles(
    *,
    customer_id: int,
    business_scope: str,
    purpose: str | None = None,
) -> dict[str, Any]:
    """List Chrome user-data directories for one customer + business domain only."""
    scope = normalize_scope(business_scope)
    expected_purpose = "article" if scope == SCOPE_CONTENT else "video"
    requested_purpose = str(purpose or expected_purpose).strip().lower()
    if requested_purpose not in {expected_purpose, "message"}:
        raise ValueError(
            f"{scope} Chrome 配置 purpose 仅支持 {expected_purpose} 或 message"
        )
    root = customer_scope_root(customer_id, scope)
    meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
    names: list[str] = []
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
            if child.is_dir() and not child.name.startswith((".", "_")):
                try:
                    n = validate_chrome_profile_name(child.name)
                except ValueError:
                    continue
                if n not in names:
                    names.append(n)

    allowed = platforms_for_scope(scope)
    entries = _entries_for_scope(customer_id=customer_id, business_scope=scope)
    profiles: list[dict[str, Any]] = []
    legacy_profiles: list[dict[str, Any]] = []
    for n in names:
        m = meta.get(n) or {}
        plat = str(m.get("platform") or "").strip().lower()
        if not plat:
            plat = infer_platform_from_name(n)
        if plat and plat not in allowed and scope_for_platform(plat) != scope:
            continue
        label = str(m.get("label") or "")
        if plat and plat in entries and not label:
            label = entries[plat]["label"]
        purpose = str(m.get("purpose") or ("video" if scope == SCOPE_VIDEO else "legacy"))
        provisioning = str(
            m.get("provisioning_status")
            or ("explicit" if scope == SCOPE_VIDEO else "legacy_unverified")
        )
        item = {
            "name": n,
            "path": str(root / n),
            "platform": plat or None,
            "label": label or None,
            "customer_id": int(customer_id),
            "business_scope": scope,
            "purpose": purpose,
            "provisioning_status": provisioning,
            "explicit_created_at": m.get("explicit_created_at"),
            **inspect_chrome_profile_storage(root / n, plat),
        }
        entry = entries.get(plat) or {}
        item["official_url"] = str(entry.get("url") or "") or None
        item["custom_platform"] = bool(entry.get("custom"))
        if plat:
            item.update(
                probe_chrome_profile_login(
                    n,
                    customer_id=customer_id,
                    business_scope=scope,
                    platform=plat,
                )
            )
        else:
            item.update(
                {
                    "login_status": "stale_unknown",
                    "login_checked_at": None,
                    "login_status_source": "platform_unbound",
                }
            )
        if purpose == "message":
            if requested_purpose == "message":
                profiles.append(item)
            continue
        if requested_purpose == "message":
            continue
        if scope == SCOPE_CONTENT and (
            purpose != "article" or provisioning != "explicit"
        ):
            legacy_profiles.append(item)
        else:
            profiles.append(item)
    platforms = [
        {"id": k, "label": v["label"], "url": v["url"], "short": v.get("short")}
        for k, v in entries.items()
        if k in allowed or scope_for_platform(k) == scope
    ]
    unassigned = []
    ua_root = unassigned_profiles_root()
    if ua_root.is_dir():
        for child in sorted(ua_root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                unassigned.append({"name": child.name, "path": str(child)})
    return {
        "ok": True,
        "customer_id": int(customer_id),
        "business_scope": scope,
        "purpose": requested_purpose,
        "root": str(root),
        "profiles": profiles,
        "legacy_profiles": legacy_profiles,
        "platforms": platforms,
        "unassigned": unassigned,
        "chrome_installed": CHROME_MAC.is_file() if os.uname().sysname == "Darwin" else False,
        "auto_publish": False,
        "human_in_loop": True,
        "note": "按客户+业务域隔离配置；串行换号；不做 Cookie 池/矩阵群控。",
    }


def confirm_content_profile(
    name: str, *, platform: str, customer_id: int
) -> dict[str, Any]:
    """Explicitly adopt one legacy content directory as an article account."""
    profile_name = validate_chrome_profile_name(name)
    plat = assert_platform_in_scope(platform, SCOPE_CONTENT)
    resolve_chrome_user_data_dir(
        profile_name,
        customer_id=customer_id,
        business_scope=SCOPE_CONTENT,
        require_existing=True,
    )
    meta = load_accounts_meta(
        customer_id=customer_id, business_scope=SCOPE_CONTENT
    )
    entry = entry_for_platform(
        plat, customer_id=customer_id, business_scope=SCOPE_CONTENT
    )
    meta[profile_name] = {
        **(meta.get(profile_name) or {}),
        "platform": plat,
        "label": entry["label"],
        "purpose": "article",
        "provisioning_status": "explicit",
        "explicit_created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_accounts_meta(
        meta, customer_id=customer_id, business_scope=SCOPE_CONTENT
    )
    return {
        "ok": True,
        "name": profile_name,
        "platform": plat,
        "purpose": "article",
        "provisioning_status": "explicit",
    }


def assert_explicit_content_profile(name: str, *, customer_id: int) -> str:
    profile_name = validate_chrome_profile_name(name)
    meta = load_accounts_meta(
        customer_id=customer_id, business_scope=SCOPE_CONTENT
    ).get(profile_name) or {}
    if (
        meta.get("purpose") != "article"
        or meta.get("provisioning_status") != "explicit"
    ):
        raise ValueError("该配置不是显式创建/确认的软文账号")
    return profile_name


def rename_chrome_profile(
    old_name: str,
    new_name: str,
    *,
    customer_id: int,
    business_scope: str,
) -> dict[str, Any]:
    """Rename one scoped Chrome user-data directory and its local metadata."""
    scope = normalize_scope(business_scope)
    old = validate_chrome_profile_name(old_name)
    new = validate_chrome_profile_name(new_name)
    if old == new:
        raise ValueError("新名称与原名称相同")
    root = customer_scope_root(customer_id, scope)
    source = resolve_chrome_user_data_dir(
        old,
        customer_id=customer_id,
        business_scope=scope,
        require_existing=True,
    )
    target = root / new
    meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
    if target.exists() or new in meta:
        raise ValueError(f"Chrome 配置已存在: {new}")
    if (source / "SingletonLock").exists() or (source / "SingletonCookie").exists():
        raise ValueError("该 Chrome 配置正在使用，请先关闭对应 Chrome 窗口后再改名")

    source.rename(target)
    try:
        item = meta.pop(old, {})
        meta[new] = item
        save_accounts_meta(meta, customer_id=customer_id, business_scope=scope)
    except Exception:
        if target.exists() and not source.exists():
            target.rename(source)
        raise
    return {
        "ok": True,
        "old_name": old,
        "name": new,
        "path": str(target),
        "platform": str(item.get("platform") or "") or None,
        "label": str(item.get("label") or "") or None,
        "customer_id": int(customer_id),
        "business_scope": scope,
    }


def update_chrome_profile_account(
    name: str,
    *,
    customer_id: int,
    business_scope: str,
    platform: str,
    custom_platform_name: str | None = None,
    custom_platform_url: str | None = None,
) -> dict[str, Any]:
    """Update one account's platform/official entry without touching its data."""
    scope = normalize_scope(business_scope)
    profile_name = validate_chrome_profile_name(name)
    resolve_chrome_user_data_dir(
        profile_name,
        customer_id=customer_id,
        business_scope=scope,
        require_existing=True,
    )
    if custom_platform_name or custom_platform_url:
        if scope != SCOPE_CONTENT:
            raise ValueError("自定义平台仅用于软文发布账号")
        plat, entry = register_custom_content_platform(
            custom_platform_name or "",
            custom_platform_url or "",
            customer_id=customer_id,
        )
    else:
        plat = assert_platform_in_scope(platform, scope)
        entry = entry_for_platform(
            plat,
            customer_id=customer_id,
            business_scope=scope,
        )
    meta = load_accounts_meta(customer_id=customer_id, business_scope=scope)
    current = meta.get(profile_name)
    if current is None:
        raise ValueError(f"Chrome 配置不存在: {profile_name}")
    meta[profile_name] = {
        **current,
        "platform": plat,
        "label": entry["label"],
    }
    save_accounts_meta(meta, customer_id=customer_id, business_scope=scope)
    return {
        "ok": True,
        "name": profile_name,
        "platform": plat,
        "label": entry["label"],
        "official_url": entry["url"],
        "business_scope": scope,
    }


def remove_chrome_profile_references(
    profile: dict[str, Any] | None,
    *,
    name: str,
    business_scope: str,
) -> dict[str, Any]:
    """Remove selected/browser references to a deleted scoped account."""
    scope = normalize_scope(business_scope)
    target = validate_chrome_profile_name(name)
    out = dict(profile or {})
    reach = dict(out.get("reach") or {})
    scopes = dict(reach.get("scopes") or {})
    scoped = dict(scopes.get(scope) or {})
    browsers = dict(scoped.get("browsers") or {})
    scoped["browsers"] = {
        key: value
        for key, value in browsers.items()
        if not isinstance(value, dict)
        or target
        not in {
            str(value.get("chrome_profile_name") or ""),
            str(value.get("profile_dir") or ""),
        }
    }
    if str(scoped.get("last_chrome_profile") or "") == target:
        scoped.pop("last_chrome_profile", None)
        scoped.pop("last_platform", None)
    scopes[scope] = scoped
    reach["scopes"] = scopes
    if scope == SCOPE_VIDEO:
        legacy = dict(reach.get("browsers") or {})
        reach["browsers"] = {
            key: value
            for key, value in legacy.items()
            if not isinstance(value, dict)
            or target
            not in {
                str(value.get("chrome_profile_name") or ""),
                str(value.get("profile_dir") or ""),
            }
        }
        if str(reach.get("last_chrome_profile") or "") == target:
            reach.pop("last_chrome_profile", None)
            reach.pop("last_platform", None)
    out["reach"] = reach
    return out


def replace_chrome_profile_name(
    profile: dict[str, Any] | None,
    *,
    old_name: str,
    new_name: str,
    business_scope: str,
) -> dict[str, Any]:
    """Update selected/browser references in customer.profile_json for one scope."""
    scope = normalize_scope(business_scope)
    old = validate_chrome_profile_name(old_name)
    new = validate_chrome_profile_name(new_name)
    out = dict(profile or {})
    reach = dict(out.get("reach") or {})
    scopes = dict(reach.get("scopes") or {})
    scoped = dict(scopes.get(scope) or {})
    browsers = dict(scoped.get("browsers") or {})
    for platform, raw in list(browsers.items()):
        if not isinstance(raw, dict):
            continue
        cfg = dict(raw)
        for key in ("chrome_profile_name", "profile_dir"):
            if str(cfg.get(key) or "") == old:
                cfg[key] = new
        browsers[platform] = cfg
    scoped["browsers"] = browsers
    if str(scoped.get("last_chrome_profile") or "") == old:
        scoped["last_chrome_profile"] = new
    scopes[scope] = scoped
    reach["scopes"] = scopes

    if scope == SCOPE_VIDEO:
        if str(reach.get("last_chrome_profile") or "") == old:
            reach["last_chrome_profile"] = new
        legacy = dict(reach.get("browsers") or {})
        for platform, raw in list(legacy.items()):
            if not isinstance(raw, dict):
                continue
            cfg = dict(raw)
            for key in ("chrome_profile_name", "profile_dir"):
                if str(cfg.get(key) or "") == old:
                    cfg[key] = new
            legacy[platform] = cfg
        reach["browsers"] = legacy
    out["reach"] = reach
    return out


def resolve_profile_platform(
    name: str,
    override: str | None = None,
    *,
    customer_id: int | None = None,
    business_scope: str | None = None,
) -> str:
    if override and override.strip():
        p = override.strip().lower()
        if business_scope:
            assert_platform_in_scope(p, business_scope)
        if customer_id is not None and business_scope:
            name = validate_chrome_profile_name(name)
            meta = load_accounts_meta(
                customer_id=customer_id,
                business_scope=business_scope,
            ).get(name) or {}
            bound = str(meta.get("platform") or "").strip().lower()
            if bound and bound != p:
                raise ValueError(
                    f"配置 {name} 已绑定平台 {bound}，不能改作 {p} 使用"
                )
        entry_for_platform(
            p, customer_id=customer_id, business_scope=business_scope
        )
        return p
    if customer_id is None or not business_scope:
        raise ValueError("解析平台必须提供 customer_id 与 business_scope")
    meta = (
        load_accounts_meta(customer_id=customer_id, business_scope=business_scope).get(
            validate_chrome_profile_name(name)
        )
        or {}
    )
    plat = str(meta.get("platform") or "").strip().lower()
    if plat and scope_for_platform(plat) == normalize_scope(business_scope):
        entry_for_platform(
            plat, customer_id=customer_id, business_scope=business_scope
        )
        assert_platform_in_scope(plat, business_scope)
        return plat
    inferred = infer_platform_from_name(name)
    if inferred:
        assert_platform_in_scope(inferred, business_scope)
        return inferred
    # Fail closed rather than guessing across domains.
    raise ValueError(f"无法判定配置 {name} 的平台，请显式指定 platform")


def open_chrome_profile(
    name_or_path: str,
    *,
    url: str,
    customer_id: int,
    business_scope: str,
    dry_run: bool = False,
    cdp_port: int | None = None,
) -> dict[str, Any]:
    """Launch Chrome with an isolated scoped --user-data-dir (background)."""
    scope = normalize_scope(business_scope)
    user_data = resolve_chrome_user_data_dir(
        name_or_path,
        customer_id=customer_id,
        business_scope=scope,
        require_existing=True,
    )
    result: dict[str, Any] = {
        "opened": False,
        "dry_run": dry_run,
        "url": url,
        "user_data_dir": str(user_data),
        "profile_name": user_data.name,
        "customer_id": int(customer_id),
        "business_scope": scope,
        "method": "chrome --user-data-dir",
        "auto_publish": False,
        "human_in_loop": True,
        "cdp_port": cdp_port,
    }
    if dry_run:
        result["chrome_path"] = str(CHROME_MAC)
        result["chrome_installed"] = CHROME_MAC.is_file() if os.uname().sysname == "Darwin" else False
        return result

    # 打开官方页：仅在「显式新建」后的空目录允许补齐；已声明 exist 的不得再 mkdir 空壳
    prefs = user_data / "Default" / "Preferences"
    if not prefs.is_file() and not user_data.exists():
        raise ValueError(
            f"Chrome 配置不存在: {user_data.name}。请先在账号管理中创建配置，再打开官方页登录。"
        )
    user_data.mkdir(parents=True, exist_ok=True)
    if os.uname().sysname != "Darwin" or not CHROME_MAC.is_file():
        raise ValueError("未找到 Google Chrome（仅支持 macOS /Applications）")

    from engine.reach.chrome_runtime import ChromeRuntimeError, start_or_reuse_managed

    # HARD LOCK: switch = close previous Chrome (flush) → open this profile only.
    # allow_bootstrap: explicit-create empty dirs may open once for first login.
    try:
        _chrome, info = start_or_reuse_managed(
            user_data.name,
            url,
            customer_id=customer_id,
            business_scope=scope,
            headless=False,
            exclusive=True,
            allow_bootstrap=not prefs.is_file(),
        )
    except ChromeRuntimeError as exc:
        raise ValueError(str(exc)) from exc
    result["opened"] = True
    result["method"] = "managed-chrome"
    result["cdp_port"] = info.get("port")
    result["pid"] = info.get("pid")
    result["reused"] = bool(info.get("reused"))
    result["navigated"] = bool(info.get("navigated"))
    result["load_extension"] = str(dismiss_popups_extension_dir() or "")
    return result


def migrate_legacy_chrome_profiles(
    session: Any | None = None,
    *,
    force_workspace_migrate: bool = False,
) -> dict[str, Any]:
    """Idempotent move of flat chrome-profiles into customer/scope trees.

    Ambiguous or conflicting dirs go to `_unassigned`. Never guesses login reuse.

    Cross-volume workspace merges are deferred by default (write report only) so
    engine lifespan /health is never blocked by multi-GB ExFAT→APFS copies.
    Ops may force via ``force_workspace_migrate=True`` or
    ``SUYING_FORCE_WORKSPACE_CHROME_MIGRATE=1``.
    """
    import shutil
    from datetime import datetime, timezone

    from sqlalchemy import select

    from engine.catalog.db import ReachMessageAccount, get_session

    root = chrome_profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".scope_layout_v1"
    report: dict[str, Any] = {
        "ok": True,
        "moved": [],
        "workspace_moved": [],
        "unassigned": [],
        "skipped": [],
        "backed_up_accounts": False,
    }
    skip_workspace = (os.environ.get("SUYING_SKIP_WORKSPACE_CHROME_MIGRATE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    force_cross = bool(force_workspace_migrate) or (
        (os.environ.get("SUYING_FORCE_WORKSPACE_CHROME_MIGRATE") or "").strip().lower()
        in {"1", "true", "yes"}
    )
    if not (os.environ.get("SUYING_CHROME_PROFILES") or "").strip() and not skip_workspace:
        # The local root may already exist because startup creates it before DB
        # migration. Merge every known old workspace root even in that case.
        # Existing local files always win; conflicts are reported, never overwritten.
        def _local_has_scoped_profiles() -> bool:
            try:
                for child in root.iterdir():
                    if child.is_dir() and child.name.startswith("customer-"):
                        return True
            except OSError:
                return False
            return False

        def _same_volume(a: Path, b: Path) -> bool:
            try:
                return a.stat().st_dev == b.stat().st_dev
            except OSError:
                return False

        def merge_tree(src: Path, dest: Path, relative: Path = Path()) -> None:
            dest.mkdir(parents=True, exist_ok=True)
            for child in sorted(src.iterdir()):
                name = child.name
                # Never migrate Finder junk / wipe trash — cross-volume copy can
                # block engine lifespan (health) for many minutes on ExFAT→APFS.
                if name in {".scope_layout_v1", ".DS_Store"} or name.startswith(
                    (".", "_trash", "trash")
                ):
                    if name.startswith((".trash", "_trash", "trash")):
                        report["skipped"].append(
                            {
                                "name": str(relative / name) if relative.parts else name,
                                "reason": "trash_or_hidden",
                                "source": str(child),
                            }
                        )
                    continue
                target = dest / child.name
                rel = relative / child.name
                if not target.exists():
                    shutil.move(str(child), str(target))
                    report["workspace_moved"].append(
                        {"source": str(child), "path": str(target), "relative": str(rel)}
                    )
                elif child.is_dir() and target.is_dir():
                    parts = rel.parts
                    is_scoped_profile = (
                        len(parts) == 3
                        and parts[0].startswith("customer-")
                        and parts[1] in {SCOPE_VIDEO, SCOPE_CONTENT}
                    )
                    is_flat_profile = len(parts) == 1 and not parts[0].startswith(
                        (".", "_", "customer-")
                    )
                    if is_scoped_profile or is_flat_profile:
                        report["skipped"].append(
                            {
                                "name": str(rel),
                                "reason": "local_profile_exists",
                                "source": str(child),
                                "target": str(target),
                            }
                        )
                        continue
                    merge_tree(child, target, rel)
                    try:
                        child.rmdir()
                    except OSError:
                        pass
                else:
                    report["skipped"].append(
                        {
                            "name": str(rel),
                            "reason": "local_target_exists",
                            "source": str(child),
                            "target": str(target),
                        }
                    )

        local_ready = marker.is_file() or _local_has_scoped_profiles()
        for workspace_root in _legacy_workspace_chrome_profiles_roots():
            if (
                workspace_root == root
                or not workspace_root.is_dir()
                or _legacy_profiles_in_use(workspace_root)
            ):
                if workspace_root != root and workspace_root.is_dir():
                    report["skipped"].append(
                        {"name": str(workspace_root), "reason": "chrome_profile_in_use"}
                    )
                continue
            # Cross-volume merge of multi-GB ExFAT trees blocks /health during deploy.
            # Default: always defer; ops POST /ops/chrome-profiles/migrate-workspace?force=1.
            if not force_cross and not _same_volume(workspace_root, root):
                report["skipped"].append(
                    {
                        "name": str(workspace_root),
                        "reason": (
                            "cross_volume_deferred_local_ready"
                            if local_ready
                            else "cross_volume_deferred"
                        ),
                        "target": str(root),
                    }
                )
                continue
            merge_tree(workspace_root, root)
    elif skip_workspace:
        report["skipped"].append(
            {"name": "workspace_roots", "reason": "SUYING_SKIP_WORKSPACE_CHROME_MIGRATE"}
        )

    legacy_accounts = root / ACCOUNTS_JSON
    if legacy_accounts.is_file() and not (root / "accounts.json.bak_scope_v1").is_file():
        shutil.copy2(legacy_accounts, root / "accounts.json.bak_scope_v1")
        report["backed_up_accounts"] = True

    own_session = session is None
    sess = session or get_session()
    try:
        accounts = list(sess.scalars(select(ReachMessageAccount)).all())
        # name -> list[(customer_id, scope, platform)]
        by_name: dict[str, list[tuple[int, str, str]]] = {}
        for row in accounts:
            scope = getattr(row, "business_scope", None) or scope_for_platform(row.platform) or ""
            if scope not in (SCOPE_VIDEO, SCOPE_CONTENT):
                continue
            by_name.setdefault(row.profile_name, []).append(
                (int(row.customer_id), scope, row.platform)
            )

        legacy_meta: dict[str, dict[str, Any]] = {}
        if legacy_accounts.is_file():
            try:
                raw = json.loads(legacy_accounts.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        try:
                            name = validate_chrome_profile_name(str(k))
                        except ValueError:
                            continue
                        if isinstance(v, dict):
                            plat = str(v.get("platform") or "").strip().lower()
                            legacy_meta[name] = {
                                "platform": plat if plat in _all_entries() else "",
                                "label": str(v.get("label") or ""),
                            }
                        elif isinstance(v, str):
                            plat = v.strip().lower()
                            legacy_meta[name] = {
                                "platform": plat if plat in _all_entries() else "",
                                "label": "",
                            }
            except (OSError, json.JSONDecodeError):
                pass

        candidates: list[Path] = []
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if child.name.startswith((".", "_")) or child.name.startswith("customer-"):
                    continue
                candidates.append(child)

        for src in candidates:
            name = src.name
            try:
                validate_chrome_profile_name(name)
            except ValueError:
                report["skipped"].append({"name": name, "reason": "invalid_name"})
                continue
            marker_file = src / ".migrated_to"
            if marker_file.is_file():
                report["skipped"].append({"name": name, "reason": "already_migrated"})
                continue

            targets = by_name.get(name) or []
            dest_customer: int | None = None
            dest_scope: str | None = None
            dest_plat = ""
            if len(targets) == 1:
                dest_customer, dest_scope, dest_plat = targets[0]
            else:
                plat = str((legacy_meta.get(name) or {}).get("platform") or "") or infer_platform_from_name(
                    name
                )
                inferred_scope = scope_for_platform(plat) if plat else None
                if inferred_scope and len({t[0] for t in targets}) <= 1:
                    # Use DB customer if unique, else leave unassigned when multi-customer conflict
                    cust_ids = {t[0] for t in targets}
                    if len(cust_ids) == 1:
                        dest_customer = next(iter(cust_ids))
                        dest_scope = inferred_scope
                        dest_plat = plat
                    elif not targets and inferred_scope:
                        # Platform known but no DB binding — cannot invent customer.
                        dest_customer = None
                        dest_scope = None
                if len(targets) > 1 and len({(t[0], t[1]) for t in targets}) > 1:
                    dest_customer = None
                    dest_scope = None

            if dest_customer is None or dest_scope is None:
                dest = unassigned_profiles_root() / name
                if dest.exists():
                    dest = unassigned_profiles_root() / f"{name}__conflict_{int(datetime.now(timezone.utc).timestamp())}"
                unassigned_profiles_root().mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                report["unassigned"].append({"name": name, "path": str(dest), "reason": "ambiguous_or_unknown"})
                continue

            dest_root = customer_scope_root(dest_customer, dest_scope)
            dest_root.mkdir(parents=True, exist_ok=True)
            dest = dest_root / name
            if dest.exists():
                conflict = unassigned_profiles_root() / f"{name}__conflict_{int(datetime.now(timezone.utc).timestamp())}"
                unassigned_profiles_root().mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(conflict))
                report["unassigned"].append(
                    {"name": name, "path": str(conflict), "reason": "target_exists"}
                )
                continue

            shutil.move(str(src), str(dest))
            meta = load_accounts_meta(customer_id=dest_customer, business_scope=dest_scope)
            if dest_plat or name in legacy_meta:
                plat = dest_plat or str((legacy_meta.get(name) or {}).get("platform") or "")
                label = ""
                if plat in OFFICIAL_ENTRY:
                    label = OFFICIAL_ENTRY[plat]["label"]
                meta[name] = {
                    "platform": plat if plat in _all_entries() else "",
                    "label": label or str((legacy_meta.get(name) or {}).get("label") or ""),
                }
                save_accounts_meta(meta, customer_id=dest_customer, business_scope=dest_scope)
            # Leave a breadcrumb beside parent so re-runs are idempotent if move partially fails later.
            report["moved"].append(
                {
                    "name": name,
                    "customer_id": dest_customer,
                    "business_scope": dest_scope,
                    "path": str(dest),
                }
            )

        marker.write_text(
            json.dumps(
                {"applied_at": datetime.now(timezone.utc).isoformat(), **report},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        if own_session:
            sess.close()
    return report


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
    customer_id: int | None = None,
    business_scope: str | None = None,
) -> dict[str, Any]:
    """Open the official URL; prefer Chrome user-data-dir when configured."""
    entry = entry_for_platform(platform)
    scope = normalize_scope(business_scope) if business_scope else require_scope_for_platform(platform)
    bcfg = browser_config_from_profile(profile, platform, business_scope=scope)
    result: dict[str, Any] = {
        "platform": platform,
        "label": entry["label"],
        "url": entry["url"],
        "opened": False,
        "dry_run": dry_run,
        "browser": bcfg,
        "business_scope": scope,
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
        if customer_id is None:
            raise ValueError("打开官方入口使用 Chrome 配置时必须提供 customer_id")
        try:
            chrome_info = open_chrome_profile(
                chrome_target,
                url=url,
                dry_run=dry_run,
                customer_id=customer_id,
                business_scope=scope,
            )
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
    customer_id: int,
    business_scope: str,
    platform: str | None = None,
) -> dict[str, Any]:
    """Return updated customer profile_json with per-scope selected Chrome profile."""
    scope = normalize_scope(business_scope)
    name = validate_chrome_profile_name(name)
    plat = resolve_profile_platform(
        name, platform, customer_id=customer_id, business_scope=scope
    )
    set_account_platform(name, plat, customer_id=customer_id, business_scope=scope)
    out = dict(profile or {})
    reach = dict(out.get("reach") or {})
    scopes = dict(reach.get("scopes") or {})
    scoped = dict(scopes.get(scope) or {})
    browsers = dict(scoped.get("browsers") or {})
    cfg = dict(browsers.get(plat) or {})
    cfg["chrome_profile_name"] = name
    cfg["browser_app"] = cfg.get("browser_app") or "Google Chrome"
    cfg["profile_dir"] = name
    browsers[plat] = cfg
    scoped["browsers"] = browsers
    scoped["last_chrome_profile"] = name
    scoped["last_chrome_platform"] = plat
    scopes[scope] = scoped
    reach["scopes"] = scopes
    # Keep legacy keys for the video scope only (backward-compatible reads).
    if scope == SCOPE_VIDEO:
        reach["last_chrome_profile"] = name
        reach["last_chrome_platform"] = plat
        legacy_browsers = dict(reach.get("browsers") or {})
        legacy_browsers[plat] = dict(cfg)
        reach["browsers"] = legacy_browsers
    out["reach"] = reach
    return out


def clear_selected_chrome_profile(
    profile: dict[str, Any] | None,
    *,
    business_scope: str,
) -> dict[str, Any]:
    """Clear last-selected Chrome bindings for one business scope (App shows empty)."""
    scope = normalize_scope(business_scope)
    out = dict(profile or {})
    reach = dict(out.get("reach") or {})
    scopes = dict(reach.get("scopes") or {})
    scoped = dict(scopes.get(scope) or {})
    scoped.pop("last_chrome_profile", None)
    scoped.pop("last_chrome_platform", None)
    scoped["browsers"] = {}
    scopes[scope] = scoped
    reach["scopes"] = scopes
    if scope == SCOPE_VIDEO:
        reach.pop("last_chrome_profile", None)
        reach.pop("last_chrome_platform", None)
        reach["browsers"] = {}
    out["reach"] = reach
    return out


def selected_chrome_profile_from_customer(
    profile: dict[str, Any] | None,
    *,
    business_scope: str,
) -> tuple[str | None, str | None]:
    scope = normalize_scope(business_scope)
    reach = (profile or {}).get("reach") or {}
    scopes = reach.get("scopes") if isinstance(reach.get("scopes"), dict) else {}
    scoped = scopes.get(scope) if isinstance(scopes.get(scope), dict) else {}
    selected = str((scoped or {}).get("last_chrome_profile") or "").strip()
    selected_platform = str((scoped or {}).get("last_chrome_platform") or "").strip() or None
    if selected:
        return selected, selected_platform
    if scope == SCOPE_VIDEO:
        selected = str(reach.get("last_chrome_profile") or "").strip()
        selected_platform = str(reach.get("last_chrome_platform") or "").strip() or None
        if not selected:
            browsers = reach.get("browsers") or {}
            if isinstance(browsers, dict):
                for cfg in browsers.values():
                    if isinstance(cfg, dict) and cfg.get("chrome_profile_name"):
                        selected = str(cfg.get("chrome_profile_name")).strip()
                        break
    return selected or None, selected_platform


def prepare_and_open(
    session,  # noqa: ANN001
    item: ReachQueueItem,
    *,
    profile: dict[str, Any] | None = None,
    dry_run: bool = False,
    chrome_profile: str | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Write paste card, open official entry, log on the queue item."""
    from datetime import datetime, timezone

    card = write_paste_card(item)
    cid = int(customer_id if customer_id is not None else item.customer_id)
    open_info = open_official_entry(
        platform=item.platform,
        profile=profile,
        dry_run=dry_run,
        chrome_profile=chrome_profile,
        customer_id=cid,
        business_scope=SCOPE_VIDEO,
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
