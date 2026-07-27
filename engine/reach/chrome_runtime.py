"""Managed Chrome runtime for G7 read-only message scans.

Only a process started by this module may be stopped by this module. The
operation lease is shared with G5.V so publishing and message scans cannot use
the same local Chrome resources concurrently.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from engine.config.settings import load_settings
from engine.reach.browser import CHROME_MAC, chrome_launch_args, resolve_chrome_user_data_dir


class ChromeRuntimeError(RuntimeError):
    pass


_thread_lock = threading.Lock()
_lease_owner: str | None = None


@dataclass
class OperationLease:
    owner: str
    file_handle: Any


def _state_dir() -> Path:
    path = load_settings().paths.data_root / "reach"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_file() -> Path:
    return _state_dir() / "chrome_runtime.json"


def acquire_operation(owner: str, *, blocking: bool = False) -> OperationLease:
    """Acquire process + file lock shared by scans and auto-upload."""
    global _lease_owner
    if not _thread_lock.acquire(blocking=blocking):
        raise ChromeRuntimeError(f"Chrome 正由 {_lease_owner or '其他任务'} 使用")
    handle = None
    try:
        handle = (_state_dir() / "chrome_runtime.lock").open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), flags)
        _lease_owner = owner
        return OperationLease(owner=owner, file_handle=handle)
    except (BlockingIOError, OSError) as exc:
        if handle is not None:
            handle.close()
        _thread_lock.release()
        raise ChromeRuntimeError("Chrome 正由另一个速影任务使用") from exc


def release_operation(lease: OperationLease) -> None:
    global _lease_owner
    try:
        fcntl.flock(lease.file_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lease.file_handle.close()
        _lease_owner = None
        _thread_lock.release()


@contextmanager
def operation(owner: str, *, blocking: bool = False) -> Iterator[OperationLease]:
    lease = acquire_operation(owner, blocking=blocking)
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
        return True
    except OSError:
        return False


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


class ManagedChrome:
    def __init__(self, profile_name: str, url: str, *, dry_run: bool = False):
        self.profile = resolve_chrome_user_data_dir(profile_name)
        self.url = url
        self.dry_run = dry_run
        self.port: int | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, timeout_sec: float = 12.0) -> dict[str, Any]:
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
            raise ChromeRuntimeError(
                f"已有受管 Chrome 运行（pid={state.get('pid')}, profile={state.get('profile')}）"
            )
        if _profile_occupied(self.profile):
            raise ChromeRuntimeError(f"Chrome profile 已被占用: {self.profile}")
        self.profile.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        if not _port_available(self.port):
            raise ChromeRuntimeError(f"动态 CDP 端口冲突: {self.port}")
        cmd = chrome_launch_args(
            self.profile,
            self.url,
            cdp_port=self.port,
            headless=True,
        )
        self.process = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        state = {
            "pid": self.process.pid,
            "profile": str(self.profile),
            "port": self.port,
            "url": self.url,
            "started_by_pid": os.getpid(),
            "mode": "headless",
        }
        runtime_file().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._clear_state()
                raise ChromeRuntimeError(f"受管 Chrome 启动失败，退出码 {self.process.returncode}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=0.5
                ):
                    return {"started": True, **state}
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise ChromeRuntimeError("受管 Chrome CDP 启动超时")

    def _clear_state(self) -> None:
        state = _read_state()
        if state and self.process and int(state.get("pid") or 0) == self.process.pid:
            runtime_file().unlink(missing_ok=True)

    def stop(self, timeout_sec: float = 5.0) -> None:
        """Terminate only the exact Popen created by this instance."""
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._clear_state()

    def __enter__(self) -> ManagedChrome:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()
