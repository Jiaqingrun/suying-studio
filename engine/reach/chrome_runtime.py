"""Managed Chrome runtime for G7 scans and G5 publish / login.

Hard isolation lock (customer requirement):
- At most one Chrome user-data-dir is live per customer + business_scope.
- Switching accounts MUST fully close the previous Chrome (CDP Browser.close so
  cookies flush) before opening the next profile.
- Opening profile A must never write into, force-release, or delete profile B.

Only a process owned/adopted by this module may be stopped by this module, plus
orphan Chromes whose --user-data-dir is under the same scoped root (exact path
match). The operation lease is shared with G5.V so publishing and message scans
cannot use the same local Chrome resources concurrently.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urlparse

from engine.config.settings import load_settings
from engine.reach.browser import (
    CHROME_MAC,
    chrome_launchservices_command,
    customer_scope_root,
    resolve_chrome_user_data_dir,
)
from engine.reach.business_scope import normalize_scope


class ChromeRuntimeError(RuntimeError):
    pass


def _ps_axww_text() -> str:
    """Process list as text; never crash on non-UTF8 argv bytes (macOS)."""
    raw = subprocess.check_output(["ps", "-axww", "-o", "pid=,command="])
    return raw.decode("utf-8", errors="replace")


_thread_lock = threading.Lock()
_lease_owner: str | None = None
_lease_priority: int = 0
_managed_instances: dict[tuple[int, str, str], "ManagedChrome"] = {}

# Publish owns Chrome above message scan for the whole batch (single process).
_priority_lock = threading.Lock()
_publish_active_owners: set[str] = set()

PRIORITY_MESSAGE = 10
PRIORITY_DEFAULT = 50
PRIORITY_PUBLISH = 100


class PublishPriorityError(ChromeRuntimeError):
    """Raised when message_scan must yield because a publish batch is active."""

    code = "publish_priority"


@dataclass
class OperationLease:
    owner: str
    file_handle: Any
    priority: int = PRIORITY_DEFAULT


def _state_dir() -> Path:
    path = load_settings().paths.data_root / "reach"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_file() -> Path:
    return _state_dir() / "chrome_runtime.json"


def _priority_file() -> Path:
    return _state_dir() / "chrome_priority.json"


def _owner_priority(owner: str) -> int:
    o = str(owner or "")
    if o.startswith("publish_run:") or o.startswith("auto_upload"):
        return PRIORITY_PUBLISH
    if o.startswith("message_scan:"):
        return PRIORITY_MESSAGE
    return PRIORITY_DEFAULT


def mark_publish_active(owner: str) -> None:
    """Register a publish batch so message scans fail fast without contending."""
    name = str(owner or "").strip()
    if not name:
        return
    with _priority_lock:
        _publish_active_owners.add(name)
        try:
            _priority_file().write_text(
                json.dumps(
                    {
                        "publish_active": sorted(_publish_active_owners),
                        "updated_at": time.time(),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass


def clear_publish_active(owner: str | None = None) -> None:
    with _priority_lock:
        if owner:
            _publish_active_owners.discard(str(owner))
        else:
            _publish_active_owners.clear()
        try:
            path = _priority_file()
            if not _publish_active_owners:
                path.unlink(missing_ok=True)
            else:
                path.write_text(
                    json.dumps(
                        {
                            "publish_active": sorted(_publish_active_owners),
                            "updated_at": time.time(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass


def is_publish_active() -> bool:
    with _priority_lock:
        if _publish_active_owners:
            return True
    owner = _lease_owner
    if owner and str(owner).startswith("publish_run:"):
        return True
    try:
        raw = _priority_file().read_text(encoding="utf-8")
        data = json.loads(raw)
        holders = data.get("publish_active") or []
        return bool(holders)
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def current_lease_owner() -> str | None:
    return _lease_owner


def acquire_operation(
    owner: str,
    *,
    blocking: bool = False,
    wait_timeout_sec: float | None = None,
) -> OperationLease:
    """Acquire process + file lock shared by scans and publish.

    Message scans never wait out a publish batch: they raise
    :class:`PublishPriorityError` immediately when publish is active.
    Publish may poll until ``wait_timeout_sec`` so a short message scan finishes.
    """
    global _lease_owner, _lease_priority
    pri = _owner_priority(owner)
    is_scan = pri <= PRIORITY_MESSAGE

    if is_scan and is_publish_active():
        raise PublishPriorityError(
            "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）"
        )

    deadline = (
        time.monotonic() + float(wait_timeout_sec)
        if wait_timeout_sec is not None and wait_timeout_sec > 0
        else None
    )
    last_exc: ChromeRuntimeError | None = None
    while True:
        if is_scan and is_publish_active():
            raise PublishPriorityError(
                "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）"
            )
        try:
            if not _thread_lock.acquire(blocking=False if deadline else blocking):
                raise ChromeRuntimeError(
                    f"Chrome 正由 {_lease_owner or '其他任务'} 使用"
                )
            handle = None
            try:
                handle = (_state_dir() / "chrome_runtime.lock").open(
                    "a+", encoding="utf-8"
                )
                flags = fcntl.LOCK_EX | (
                    0 if (deadline or not blocking) else 0
                )
                # Non-blocking flock when polling or non-blocking mode.
                if deadline is not None or not blocking:
                    flags = fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), flags)
                _lease_owner = owner
                _lease_priority = pri
                return OperationLease(owner=owner, file_handle=handle, priority=pri)
            except (BlockingIOError, OSError) as exc:
                if handle is not None:
                    handle.close()
                _thread_lock.release()
                raise ChromeRuntimeError(
                    f"Chrome 正由 {_lease_owner or '另一个速影任务'} 使用"
                ) from exc
        except ChromeRuntimeError as exc:
            last_exc = exc
            if deadline is None:
                raise
            if time.monotonic() >= deadline:
                detail = str(last_exc) if last_exc else ""
                if is_scan:
                    raise ChromeRuntimeError(
                        f"浏览器忙（等待 {int(wait_timeout_sec or 0)}s 仍被占用）。"
                        f"多半与其他发布/巡检冲突，消息巡检已延后（非登录损坏）。"
                        f" 详情: {detail}"
                    ) from last_exc
                raise ChromeRuntimeError(
                    f"浏览器忙（等待 {int(wait_timeout_sec or 0)}s 仍被占用）。"
                    f"详情: {detail}"
                ) from last_exc
            time.sleep(1.0)


def release_operation(lease: OperationLease) -> None:
    global _lease_owner, _lease_priority
    try:
        fcntl.flock(lease.file_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lease.file_handle.close()
        _lease_owner = None
        _lease_priority = 0
        _thread_lock.release()


@contextmanager
def operation(
    owner: str,
    *,
    blocking: bool = False,
    wait_timeout_sec: float | None = None,
) -> Iterator[OperationLease]:
    """Hold the shared Chrome operation lease.

    ``wait_timeout_sec``: poll until the lease is free (message scan vs publish).
    Message scans yield immediately when a publish batch is marked active.
    """
    lease = acquire_operation(
        owner,
        blocking=blocking if wait_timeout_sec is None else False,
        wait_timeout_sec=wait_timeout_sec,
    )
    try:
        yield lease
    finally:
        release_operation(lease)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if os.uname().sysname == "Darwin":
        try:
            state = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "stat="],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                return False
        except (OSError, subprocess.SubprocessError):
            pass
    return True


def _read_state() -> dict[str, Any] | None:
    path = runtime_file()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _profile_occupied(profile: Path) -> bool:
    # Chrome uses this symlink/lock while a user-data-dir is owned by a process.
    return (profile / "SingletonLock").exists()


def _user_data_dir_from_command(command: str) -> Path | None:
    """Extract our Chrome user-data-dir without prefix/substring matching."""
    marker = "--user-data-dir="
    start = command.find(marker)
    if start < 0:
        return None
    value = command[start + len(marker) :]
    # chrome_launch_args always puts this stable argument immediately after
    # user-data-dir. Keeping the boundary avoids 快手-01 matching 快手-010.
    boundary = value.find(" --no-first-run")
    if boundary < 0:
        return None
    raw = value[:boundary].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]
    return Path(raw).expanduser() if raw else None


def _command_owns_profile(command: str, profile: Path) -> bool:
    command_profile = _user_data_dir_from_command(command)
    if command_profile is None:
        return False
    return os.path.realpath(command_profile) == os.path.realpath(profile.expanduser())


def _cdp_version_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=1.0):
            return True
    except (OSError, urllib.error.URLError):
        return False


def _graceful_close_cdp(port: int, *, timeout_sec: float = 5.0) -> bool:
    """Ask Chrome to close normally so cookies / IndexedDB are flushed."""
    if not port:
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/version", timeout=1.5
        ) as response:
            version = json.loads(response.read().decode("utf-8"))
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            return False
        from engine.reach.cdp_client import CdpSession

        session = CdpSession(ws_url, timeout=min(timeout_sec, 3.0))
        try:
            session.call("Browser.close")
        finally:
            session.close()
        return True
    except Exception:
        return False


def _discover_chrome_by_port(port: int) -> dict[str, Any] | None:
    """Find the live Chrome main process that owns ``--remote-debugging-port``."""
    if os.uname().sysname != "Darwin" or not port:
        return None
    try:
        out = _ps_axww_text()
    except (OSError, subprocess.CalledProcessError):
        return None
    marker = f"--remote-debugging-port={int(port)}"
    for line in out.splitlines():
        raw = line.strip()
        if not raw or "Google Chrome" not in raw:
            continue
        if " --type=" in f" {raw}":
            continue
        if marker not in raw:
            continue
        pid = int(raw.split(None, 1)[0])
        if not _pid_alive(pid) or not _cdp_version_ok(int(port)):
            continue
        profile = _user_data_dir_from_command(raw)
        return {
            "pid": pid,
            "port": int(port),
            "profile": str(profile) if profile else "",
            "command": raw,
        }
    return None


def _discover_chrome_for_profile(profile: Path) -> dict[str, Any] | None:
    """Find a live Chrome main process that owns ``profile`` with a working CDP port."""
    hits = _discover_chromes_under_root(profile.parent, keep_profile=None)
    profile_real = os.path.realpath(profile.expanduser())
    for hit in hits:
        if os.path.realpath(str(hit.get("profile") or "")) == profile_real:
            return hit
    # Fallback: exact profile match even if parent listing missed (symlink edge).
    if os.uname().sysname != "Darwin":
        return None
    try:
        out = _ps_axww_text()
    except (OSError, subprocess.CalledProcessError):
        return None
    profile_s = str(profile.resolve() if profile.exists() else profile)
    for line in out.splitlines():
        raw = line.strip()
        if not raw or "Google Chrome" not in raw:
            continue
        # Skip GPU/renderer helpers; only the browser process owns the profile lock.
        if " --type=" in f" {raw}":
            continue
        if not _command_owns_profile(raw, profile):
            continue
        m = re.search(r"--remote-debugging-port=(\d+)", raw)
        if not m:
            continue
        pid = int(raw.split(None, 1)[0])
        port = int(m.group(1))
        if not _pid_alive(pid) or not _cdp_version_ok(port):
            continue
        return {"pid": pid, "port": port, "profile": profile_s, "command": raw}
    return None


def _discover_chromes_under_root(
    root: Path,
    *,
    keep_profile: Path | None = None,
) -> list[dict[str, Any]]:
    """List live Chrome mains whose user-data-dir is under ``root`` (exact path)."""
    if os.uname().sysname != "Darwin":
        return []
    try:
        out = _ps_axww_text()
    except (OSError, subprocess.CalledProcessError):
        return []
    root_real = os.path.realpath(root.expanduser())
    keep_real = (
        os.path.realpath(keep_profile.expanduser()) if keep_profile is not None else None
    )
    found: list[dict[str, Any]] = []
    seen_pids: set[int] = set()
    for line in out.splitlines():
        raw = line.strip()
        if not raw or "Google Chrome" not in raw:
            continue
        if " --type=" in f" {raw}":
            continue
        command_profile = _user_data_dir_from_command(raw)
        if command_profile is None:
            continue
        profile_real = os.path.realpath(command_profile)
        try:
            Path(profile_real).relative_to(root_real)
        except ValueError:
            continue
        if keep_real and profile_real == keep_real:
            continue
        m = re.search(r"--remote-debugging-port=(\d+)", raw)
        if not m:
            continue
        pid = int(raw.split(None, 1)[0])
        if pid in seen_pids or not _pid_alive(pid):
            continue
        port = int(m.group(1))
        if not _cdp_version_ok(port):
            continue
        seen_pids.add(pid)
        found.append(
            {
                "pid": pid,
                "port": port,
                "profile": profile_real,
                "command": raw,
            }
        )
    return found


def _wait_profile_unlocked(profile: Path, *, timeout_sec: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _profile_occupied(profile):
            return True
        time.sleep(0.15)
    return not _profile_occupied(profile)


def _close_discovered_chrome(hit: dict[str, Any], *, timeout_sec: float = 20.0) -> None:
    """Gracefully close one discovered Chrome; only then force that profile's lock."""
    pid = int(hit.get("pid") or 0)
    port = int(hit.get("port") or 0)
    profile = Path(str(hit.get("profile") or ""))
    if port:
        _graceful_close_cdp(port, timeout_sec=min(timeout_sec, 5.0))
    if pid:
        _wait_pid_exit(pid, timeout_sec=timeout_sec)
    if profile.parts:
        if not _wait_profile_unlocked(profile, timeout_sec=2.0):
            _force_release_profile(profile, timeout_sec=4.0)


def _force_release_profile(profile: Path, *, timeout_sec: float = 6.0) -> bool:
    """Stop Chrome processes that hold ``profile`` so ManagedChrome can relaunch."""
    if os.uname().sysname != "Darwin":
        return not _profile_occupied(profile)
    try:
        out = _ps_axww_text()
    except (OSError, subprocess.CalledProcessError):
        return not _profile_occupied(profile)
    pids: list[int] = []
    for line in out.splitlines():
        raw = line.strip()
        if not raw or "Google Chrome" not in raw:
            continue
        if " --type=" in f" {raw}":
            continue
        if not _command_owns_profile(raw, profile):
            continue
        pids.append(int(raw.split(None, 1)[0]))
    for pid in sorted(set(pids)):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        alive = [p for p in pids if _pid_alive(p)]
        if not alive and not _profile_occupied(profile):
            return True
        time.sleep(0.2)
    for pid in pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    time.sleep(0.3)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink(missing_ok=True)
        except OSError:
            pass
    return not _profile_occupied(profile)


class ManagedChrome:
    def __init__(
        self,
        profile_name: str,
        url: str,
        *,
        customer_id: int,
        business_scope: str,
        dry_run: bool = False,
        headless: bool = True,
    ):
        self.profile = resolve_chrome_user_data_dir(
            profile_name,
            customer_id=customer_id,
            business_scope=business_scope,
            # dry_run 只做路径演算，不得因配置尚未创建而失败，也不得 mkdir。
            require_existing=not dry_run,
        )
        self.url = url
        self.customer_id = int(customer_id)
        self.business_scope = business_scope
        self.dry_run = dry_run
        self.headless = headless
        self.port: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.adopted_pid: int | None = None

    def _write_state(self, pid: int) -> dict[str, Any]:
        state = {
            "pid": pid,
            "profile": str(self.profile),
            "port": self.port,
            "url": self.url,
            "started_by_pid": os.getpid(),
            "mode": "headless" if self.headless else "visible",
            "adopted": bool(self.adopted_pid),
        }
        runtime_file().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def attach_existing(self, *, pid: int, port: int) -> dict[str, Any]:
        """Take ownership of an already-running managed Chrome for this profile."""
        if not _pid_alive(pid) or not _cdp_version_ok(port):
            raise ChromeRuntimeError(f"无法接管已有 Chrome（pid={pid}, port={port}）")
        self.port = int(port)
        self.adopted_pid = int(pid)
        self.process = None
        return {"started": True, "reused": True, "adopted": True, **self._write_state(pid)}

    def start(
        self,
        timeout_sec: float = 12.0,
        *,
        allow_other_managed: bool = False,
        allow_bootstrap: bool = False,
    ) -> dict[str, Any]:
        if self.dry_run:
            return {
                "dry_run": True,
                "started": False,
                "profile": str(self.profile),
                "url": self.url,
                "pid": None,
                "port": None,
            }
        if os.uname().sysname != "Darwin" or not CHROME_MAC.is_file():
            raise ChromeRuntimeError("未找到 macOS Google Chrome")
        state = _read_state()
        if state and _pid_alive(int(state.get("pid") or 0)):
            state_profile = str(state.get("profile") or "")
            state_port = int(state.get("port") or 0)
            if state_profile == str(self.profile) and state_port and _cdp_version_ok(state_port):
                return self.attach_existing(pid=int(state["pid"]), port=state_port)
            if not allow_other_managed:
                raise ChromeRuntimeError(
                    f"已有受管 Chrome 运行（pid={state.get('pid')}, profile={state.get('profile')}）"
                )
        if _profile_occupied(self.profile):
            discovered = _discover_chrome_for_profile(self.profile)
            if discovered:
                return self.attach_existing(pid=int(discovered["pid"]), port=int(discovered["port"]))
            # Only force-release THIS profile's SingletonLock — never sibling dirs.
            if not _force_release_profile(self.profile):
                raise ChromeRuntimeError(f"Chrome profile 已被占用: {self.profile}")
        # 发布/扫描不得 mkdir 出空壳配置；否则会表现为「登录被覆盖、每次验证」
        # 显式新建后的首次「打开官方页」允许 bootstrap（Chrome 自己写 Preferences）。
        prefs = self.profile / "Default" / "Preferences"
        if not prefs.is_file() and not allow_bootstrap:
            raise ChromeRuntimeError(
                f"Chrome 配置尚未完成登录或不完整: {self.profile.name}。"
                "请在 App 发布页选用已有账号目录并先打开官方页登录，禁止使用未登录的空配置名。"
            )
        if allow_bootstrap:
            self.profile.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        if not _port_available(self.port):
            raise ChromeRuntimeError(f"动态 CDP 端口冲突: {self.port}")
        # MUST use LaunchServices on macOS. Direct Popen of the Chrome binary
        # cannot write encrypted cookies, so account switches lose login state.
        cmd = chrome_launchservices_command(
            self.profile,
            self.url,
            cdp_port=self.port,
            headless=self.headless,
        )
        opened = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if opened.returncode != 0:
            detail = (opened.stderr or opened.stdout or "").strip()
            raise ChromeRuntimeError(
                f"LaunchServices 启动 Chrome 失败（exit={opened.returncode}）"
                + (f": {detail}" if detail else "")
            )
        # `open` returns immediately; adopt the real Chrome PID once CDP is up.
        self.process = None
        self.adopted_pid = None
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if not _cdp_version_ok(int(self.port)):
                time.sleep(0.2)
                continue
            discovered = _discover_chrome_for_profile(self.profile)
            if not (
                discovered and int(discovered.get("port") or 0) == int(self.port)
            ):
                discovered = _discover_chrome_by_port(int(self.port))
            if discovered:
                self.adopted_pid = int(discovered["pid"])
                state = self._write_state(self.adopted_pid)
                return {
                    "started": True,
                    "launch": "launchservices",
                    **state,
                }
            time.sleep(0.2)
        self.stop()
        raise ChromeRuntimeError("受管 Chrome CDP 启动超时")

    def _owned_pid(self) -> int | None:
        if self.process is not None:
            return int(self.process.pid)
        if self.adopted_pid:
            return int(self.adopted_pid)
        return None

    def _clear_state(self) -> None:
        state = _read_state()
        owned = self._owned_pid()
        if state and owned and int(state.get("pid") or 0) == owned:
            runtime_file().unlink(missing_ok=True)

    def stop(self, timeout_sec: float = 20.0) -> None:
        """Terminate only the Chrome process owned by this instance.

        Prefer CDP Browser.close so cookies flush into this profile only, then
        wait until SingletonLock for THIS profile is gone before returning.
        """
        process = self.process
        if process is not None:
            if process.poll() is None:
                _graceful_close_cdp(int(self.port or 0), timeout_sec=timeout_sec)
                try:
                    process.wait(timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            _wait_profile_unlocked(self.profile, timeout_sec=min(timeout_sec, 4.0))
            self._clear_state()
            return
        pid = self.adopted_pid
        port = int(self.port or 0)
        if not pid and port:
            # LaunchServices start may have CDP before pid discovery; still flush.
            hit = _discover_chrome_by_port(port) or _discover_chrome_for_profile(self.profile)
            if hit:
                pid = int(hit["pid"])
                self.adopted_pid = pid
        if not pid:
            if port:
                _graceful_close_cdp(port, timeout_sec=min(timeout_sec, 5.0))
            _wait_profile_unlocked(self.profile, timeout_sec=min(timeout_sec, 4.0))
            self._clear_state()
            return
        if _pid_alive(pid):
            _graceful_close_cdp(port, timeout_sec=timeout_sec)
            _wait_pid_exit(pid, timeout_sec=timeout_sec)
        if not _wait_profile_unlocked(self.profile, timeout_sec=min(timeout_sec, 4.0)):
            _force_release_profile(self.profile, timeout_sec=4.0)
        self._clear_state()
        self.adopted_pid = None

    def __enter__(self) -> ManagedChrome:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


def forget_managed(chrome: ManagedChrome) -> None:
    """Remove an app-owned instance after its exact process is stopped."""
    for key, instance in list(_managed_instances.items()):
        if instance is chrome:
            _managed_instances.pop(key, None)


def managed_profile_connection(
    *,
    customer_id: int,
    business_scope: str,
    profile_name: str,
) -> dict[str, Any] | None:
    """Return a live managed CDP connection for exactly one scoped profile."""
    scope = normalize_scope(business_scope)
    key = (int(customer_id), scope, profile_name)
    instance = _managed_instances.get(key)
    if (
        instance
        and instance._owned_pid()
        and _pid_alive(int(instance._owned_pid() or 0))
        and instance.port
        and _cdp_version_ok(int(instance.port))
    ):
        return {
            "pid": int(instance._owned_pid() or 0),
            "port": int(instance.port),
            "profile": str(instance.profile),
            "managed": True,
        }

    profile = resolve_chrome_user_data_dir(
        profile_name,
        customer_id=customer_id,
        business_scope=scope,
        require_existing=True,
    )
    state = _read_state()
    if (
        state
        and str(state.get("profile") or "") == str(profile)
        and _pid_alive(int(state.get("pid") or 0))
        and int(state.get("port") or 0)
        and _cdp_version_ok(int(state.get("port") or 0))
    ):
        return {
            "pid": int(state["pid"]),
            "port": int(state["port"]),
            "profile": str(profile),
            "managed": True,
        }
    discovered = _discover_chrome_for_profile(profile)
    if discovered and _cdp_version_ok(int(discovered.get("port") or 0)):
        return {
            "pid": int(discovered["pid"]),
            "port": int(discovered["port"]),
            "profile": str(profile),
            "managed": True,
        }
    return None


def _cdp_close_tab(port: int, tab_id: str) -> None:
    """Best-effort close one Chrome tab via the DevTools HTTP endpoint."""
    tid = str(tab_id or "").strip()
    if not port or not tid or "/" in tid or ".." in tid:
        return
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/close/{quote(tid, safe='')}",
            timeout=2,
        ):
            return
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return


def _main_screen_bounds() -> dict[str, int] | None:
    """Logical points of the macOS main desktop (Finder). Best-effort."""
    if os.uname().sysname != "Darwin":
        return None
    try:
        raw = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "Finder" to get bounds of window of desktop',
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = [p.strip() for p in raw.replace("{", "").replace("}", "").split(",") if p.strip()]
    if len(parts) != 4:
        return None
    try:
        left, top, right, bottom = (int(float(p)) for p in parts)
    except ValueError:
        return None
    width = right - left
    height = bottom - top
    if width < 640 or height < 480:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def _reveal_visible_chrome(port: int) -> dict[str, Any]:
    """Move a visible managed Chrome onto the main screen and un-minimize it.

    Customer Macs often keep an HDMI/UGREEN display online. Chrome restores
    the last window onto that screen; App then reports 200 while the laptop
    shows nothing. Reuse/adopt paths never call ``open -na`` again, so we
    must CDP-move the window on every visible open.
    """
    result: dict[str, Any] = {"ok": False, "port": int(port or 0)}
    if not port:
        return result
    screen = _main_screen_bounds()
    if screen:
        result["screen"] = screen
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/list", timeout=2
        ) as response:
            tabs = json.loads(response.read().decode("utf-8"))
        pages = [
            item
            for item in tabs
            if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
        ]
        if not pages:
            return result
        from engine.reach.cdp_client import CdpSession

        session = CdpSession(str(pages[0]["webSocketDebuggerUrl"]), timeout=3.0)
        try:
            win = session.call("Browser.getWindowForTarget") or {}
            window_id = win.get("windowId")
            old_bounds = win.get("bounds") if isinstance(win.get("bounds"), dict) else {}
            result["from"] = old_bounds
            if window_id is not None:
                if screen:
                    margin = 48
                    width = min(1280, max(900, int(screen["width"]) - 2 * margin))
                    height = min(900, max(640, int(screen["height"]) - 2 * margin))
                    left = int(screen["left"]) + margin
                    top = int(screen["top"]) + margin
                else:
                    left, top, width, height = 80, 60, 1280, 800
                session.call(
                    "Browser.setWindowBounds",
                    {
                        "windowId": window_id,
                        "bounds": {
                            "left": left,
                            "top": top,
                            "width": width,
                            "height": height,
                            "windowState": "normal",
                        },
                    },
                )
                result["to"] = {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "windowState": "normal",
                }
            try:
                session.call("Page.bringToFront")
            except Exception:
                pass
        finally:
            session.close()
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)[:240]
    if result.get("ok") and os.uname().sysname == "Darwin":
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", 'tell application "Google Chrome" to activate'],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def _cdp_open_url(port: int, url: str) -> bool:
    """Reuse one matching tab (or any page), navigate it, prune duplicates.

    Chrome 120+ rejects GET on ``/json/new`` (HTTP 405); use PUT only when
    there is literally no page target. Never leave a growing pile of
    creator/upload tabs across publishes.
    """
    if not port or not url:
        return False
    pages: list[dict[str, Any]] = []
    try:
        target_host = (urlparse(url).hostname or "").lower()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{int(port)}/json/list", timeout=2
        ) as response:
            tabs = json.loads(response.read().decode("utf-8"))
        pages = [
            item
            for item in tabs
            if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
        ]
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        pages = []
        target_host = (urlparse(url).hostname or "").lower()
    try:
        def _host_of(item: dict[str, Any]) -> str:
            return (urlparse(str(item.get("url") or "")).hostname or "").lower()

        def _is_internal(item: dict[str, Any]) -> bool:
            raw = str(item.get("url") or "").lower()
            return raw.startswith(
                ("chrome://", "chrome-extension://", "devtools://", "about:")
            )

        pages.sort(
            key=lambda item: (
                0 if target_host and _host_of(item) == target_host else 1,
                0 if "/login" not in str(item.get("url") or "").lower() else 1,
                0 if not _is_internal(item) else 1,
            )
        )
        keep: dict[str, Any] | None = None
        if pages:
            keep = pages[0]
            from engine.reach.cdp_client import CdpSession

            session = CdpSession(str(keep["webSocketDebuggerUrl"]), timeout=3.0)
            try:
                session.call("Page.navigate", {"url": url})
            finally:
                session.close()
            # Close surplus same-host pages (and blank internals) so each
            # publish profile keeps a single working tab.
            keep_id = str(keep.get("id") or "")
            for item in pages:
                item_id = str(item.get("id") or "")
                if not item_id or item_id == keep_id:
                    continue
                host = _host_of(item)
                same_host = bool(target_host) and host == target_host
                if same_host or _is_internal(item):
                    _cdp_close_tab(int(port), item_id)
            return True

        encoded = quote(url, safe=":/?&=#%")
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}/json/new?{encoded}",
            data=b"",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5):
            return True
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait_pid_exit(pid: int, timeout_sec: float = 5.0) -> bool:
    if pid <= 0:
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.15)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)
    return not _pid_alive(pid)


def _stop_managed_instance(chrome: ManagedChrome) -> None:
    try:
        chrome.stop()
    except Exception:
        pid = chrome._owned_pid() or 0
        if pid and _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            _wait_pid_exit(pid)
        if not _wait_profile_unlocked(chrome.profile, timeout_sec=2.0):
            _force_release_profile(chrome.profile, timeout_sec=4.0)
    forget_managed(chrome)


def enforce_single_chrome_slot(
    *,
    customer_id: int,
    business_scope: str,
    keep_profile: Path | None = None,
) -> dict[str, Any]:
    """Hard lock: at most one live Chrome under this customer+scope.

    Closes every other managed instance and any orphan Chrome whose
    --user-data-dir is under the scoped root. Never touches profiles outside
    that root. ``keep_profile`` (exact path) may remain open.
    """
    scope = normalize_scope(business_scope)
    root = customer_scope_root(int(customer_id), scope)
    keep_real = (
        os.path.realpath(keep_profile.expanduser()) if keep_profile is not None else None
    )
    closed_managed = 0
    for other_key, other in list(_managed_instances.items()):
        cid, bscope, _name = other_key
        if int(cid) != int(customer_id) or normalize_scope(str(bscope)) != scope:
            continue
        other_real = os.path.realpath(other.profile)
        if keep_real and other_real == keep_real:
            continue
        _stop_managed_instance(other)
        closed_managed += 1

    closed_orphan = 0
    for hit in _discover_chromes_under_root(root, keep_profile=keep_profile):
        _close_discovered_chrome(hit)
        closed_orphan += 1

    return {
        "closed_managed": closed_managed,
        "closed_orphan": closed_orphan,
        "keep_profile": str(keep_profile) if keep_profile else None,
        "scope_root": str(root),
    }


def _reclaim_runtime_lock(*, keep_pid: int | None = None) -> None:
    """Clear chrome_runtime.json / kill orphan Chrome left after engine restart."""
    state = _read_state()
    if not state:
        return
    pid = int(state.get("pid") or 0)
    if keep_pid and pid == keep_pid:
        return
    for inst in list(_managed_instances.values()):
        owned = inst._owned_pid()
        if owned and owned == pid:
            return
    if pid and _pid_alive(pid):
        port = int(state.get("port") or 0)
        if port:
            _graceful_close_cdp(port, timeout_sec=5.0)
        _wait_pid_exit(pid)
        profile_raw = str(state.get("profile") or "")
        if profile_raw:
            profile = Path(profile_raw)
            if not _wait_profile_unlocked(profile, timeout_sec=2.0):
                _force_release_profile(profile, timeout_sec=4.0)
    runtime_file().unlink(missing_ok=True)


def start_or_reuse_managed(
    profile_name: str,
    url: str,
    *,
    customer_id: int,
    business_scope: str,
    headless: bool = False,
    dry_run: bool = False,
    timeout_sec: float = 20.0,
    exclusive: bool = True,
    allow_bootstrap: bool = False,
) -> tuple[ManagedChrome, dict[str, Any]]:
    """Open one app-owned Chrome with hard single-slot isolation.

    Switching accounts always closes every other Chrome under the same
    customer + business_scope (managed + orphan), via CDP flush, before the
    new profile starts. Same profile is reused and navigated to ``url``.

    ``exclusive=False`` is accepted for API compatibility but still enforces
    the single-slot lock (customer bottom line).

    After engine restart, an already-open managed Chrome for the same profile is
    adopted via its CDP port instead of raising profile_busy.
    """
    del exclusive  # hard lock: always single-slot
    key = (int(customer_id), business_scope, profile_name)

    def _visible_info(chrome: ManagedChrome, info: dict[str, Any]) -> dict[str, Any]:
        if chrome.headless or not chrome.port:
            return info
        info["window"] = _reveal_visible_chrome(int(chrome.port))
        return info

    existing = _managed_instances.get(key)
    if (
        existing
        and existing._owned_pid()
        and _pid_alive(int(existing._owned_pid() or 0))
        and existing.port
        and _cdp_version_ok(int(existing.port))
    ):
        # Same account: keep this window, but still close any sibling Chromes.
        enforce_single_chrome_slot(
            customer_id=customer_id,
            business_scope=business_scope,
            keep_profile=existing.profile,
        )
        navigated = _cdp_open_url(int(existing.port), url)
        existing.url = url
        return existing, _visible_info(existing, {
            "started": True,
            "reused": True,
            "navigated": navigated,
            "pid": existing._owned_pid(),
            "profile": str(existing.profile),
            "port": existing.port,
            "url": url,
            "mode": "headless" if existing.headless else "visible",
            "single_slot": True,
        })

    if dry_run:
        chrome = ManagedChrome(
            profile_name,
            url,
            customer_id=customer_id,
            business_scope=business_scope,
            headless=headless,
            dry_run=True,
        )
        return chrome, chrome.start(timeout_sec=timeout_sec, allow_bootstrap=allow_bootstrap)

    chrome = ManagedChrome(
        profile_name,
        url,
        customer_id=customer_id,
        business_scope=business_scope,
        headless=headless,
        dry_run=False,
    )

    # HARD LOCK: close every other account Chrome under this scope first.
    slot = enforce_single_chrome_slot(
        customer_id=customer_id,
        business_scope=business_scope,
        keep_profile=chrome.profile,
    )

    # Prefer adopting the already-open window for this profile (common after
    # 「打开官方页」or engine restart) before reclaiming/killing anything.
    discovered = _discover_chrome_for_profile(chrome.profile)
    if discovered:
        info = chrome.attach_existing(pid=int(discovered["pid"]), port=int(discovered["port"]))
        navigated = _cdp_open_url(int(chrome.port or 0), url)
        chrome.url = url
        _managed_instances[key] = chrome
        return chrome, _visible_info(chrome, {
            **info,
            "navigated": navigated,
            "url": url,
            "single_slot": True,
            "slot": slot,
        })

    state = _read_state()
    if (
        state
        and str(state.get("profile") or "") == str(chrome.profile)
        and _pid_alive(int(state.get("pid") or 0))
        and int(state.get("port") or 0)
        and _cdp_version_ok(int(state.get("port") or 0))
    ):
        info = chrome.attach_existing(pid=int(state["pid"]), port=int(state["port"]))
        navigated = _cdp_open_url(int(chrome.port or 0), url)
        chrome.url = url
        _managed_instances[key] = chrome
        return chrome, _visible_info(chrome, {
            **info,
            "navigated": navigated,
            "url": url,
            "single_slot": True,
            "slot": slot,
        })

    _reclaim_runtime_lock()
    info = chrome.start(
        timeout_sec=timeout_sec,
        allow_other_managed=False,
        allow_bootstrap=allow_bootstrap,
    )
    # Always CDP-navigate after start. Launch argv no longer includes the
    # publish URL (avoids restore-last-session + extra tab pile-up).
    navigated = _cdp_open_url(int(chrome.port or 0), url)
    chrome.url = url
    info = {
        **info,
        "navigated": navigated,
        "url": url,
        "single_slot": True,
        "slot": slot,
    }
    _managed_instances[key] = chrome
    return chrome, _visible_info(chrome, info)
