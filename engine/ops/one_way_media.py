"""Run one-way Z-Space → library pull inside the App/engine process.

LaunchAgent/bash cannot write external volumes (macOS TCC → EPERM).
The engine is started by 速影 Studio and inherits library volume access.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("montage.one_way_media")

TOOLS = Path.home() / "QR" / "tools"
SCRIPT = TOOLS / "one-way-media-pull.py"
LOCK = Path.home() / ".qr" / "one-way-media-pull.session.lock"
STATE = Path.home() / ".qr" / "one-way-media-pull-state.json"
ENGINE_TICK_STATE = Path.home() / ".qr" / "one-way-media-pull.engine-tick.json"
LOG_DIR = Path.home() / ".qr" / "logs"

DEFAULT_INTERVAL_SEC = 600
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def script_installed() -> bool:
    return SCRIPT.is_file()


def last_summary() -> dict[str, Any] | None:
    if not STATE.is_file():
        return None
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _lock_held_by_other() -> bool:
    if not LOCK.is_file():
        return False
    try:
        raw = LOCK.read_text(encoding="utf-8").strip().splitlines()
        pid = int(raw[0]) if raw else 0
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return pid != os.getpid()
    except OSError:
        return False


def _load_tick() -> dict[str, Any]:
    if not ENGINE_TICK_STATE.is_file():
        return {}
    try:
        return json.loads(ENGINE_TICK_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_tick(data: dict[str, Any]) -> None:
    ENGINE_TICK_STATE.parent.mkdir(parents=True, exist_ok=True)
    ENGINE_TICK_STATE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _due(interval_sec: int) -> bool:
    last = float(_load_tick().get("started_at") or 0)
    if last <= 0:
        return True
    return (time.time() - last) >= max(60, interval_sec)


def status() -> dict[str, Any]:
    summary = last_summary() or {}
    tick = _load_tick()
    running = _lock_held_by_other() or (
        _thread is not None and _thread.is_alive()
    )
    return {
        "script_installed": script_installed(),
        "script": str(SCRIPT),
        "running": running,
        "last_tick": tick,
        "last_summary": {
            k: summary.get(k)
            for k in (
                "finished_at",
                "downloaded",
                "failed",
                "skipped_same",
                "pending_total",
                "bytes",
                "local",
            )
        },
        "errors_sample": (summary.get("errors") or [])[:5],
    }


def _run_once(*, limit: int = 0) -> dict[str, Any]:
    if not script_installed():
        return {"ok": False, "error": "one-way script missing", "script": str(SCRIPT)}
    if _lock_held_by_other():
        return {"ok": True, "skipped": "already_running"}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "one-way-media-pull.engine.log"
    cmd = [sys.executable, str(SCRIPT)]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])

    started = time.time()
    _save_tick(
        {
            "started_at": started,
            "pid": os.getpid(),
            "cmd": cmd,
            "via": "engine",
        }
    )
    log.info("one-way media pull start: %s", cmd)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} engine-start {' '.join(cmd)}\n"
            )
            fh.flush()
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=None,
                check=False,
                cwd=str(TOOLS),
                env={**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
            if proc.stdout:
                fh.write(proc.stdout)
                if not proc.stdout.endswith("\n"):
                    fh.write("\n")
            if proc.stderr:
                fh.write(proc.stderr)
                if not proc.stderr.endswith("\n"):
                    fh.write("\n")
            fh.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} engine-end rc={proc.returncode}\n"
            )
        summary = last_summary() or {}
        ok = proc.returncode == 0
        result = {
            "ok": ok,
            "returncode": proc.returncode,
            "elapsed_sec": round(time.time() - started, 1),
            "summary": {
                k: summary.get(k)
                for k in (
                    "finished_at",
                    "downloaded",
                    "failed",
                    "skipped_same",
                    "pending_total",
                    "bytes",
                    "local",
                )
            },
            "stderr_tail": (proc.stderr or "")[-800:],
        }
        if not ok:
            log.warning("one-way media pull failed rc=%s", proc.returncode)
        else:
            log.info(
                "one-way media pull done downloaded=%s failed=%s",
                summary.get("downloaded"),
                summary.get("failed"),
            )
        tick = _load_tick()
        tick["finished_at"] = time.time()
        tick["returncode"] = proc.returncode
        tick["ok"] = ok
        _save_tick(tick)
        return result
    except Exception as exc:
        log.exception("one-way media pull crashed")
        tick = _load_tick()
        tick["finished_at"] = time.time()
        tick["error"] = str(exc)
        _save_tick(tick)
        return {"ok": False, "error": str(exc)}


def run_in_background(*, force: bool = False, limit: int = 0) -> dict[str, Any]:
    """Start pull on a daemon thread (no launchd / no Terminal)."""
    global _thread
    if not script_installed():
        return {"ok": False, "started": False, "error": "script_missing"}
    if not force and not _due(DEFAULT_INTERVAL_SEC):
        return {
            "ok": True,
            "started": False,
            "skipped": "not_due",
            "interval_sec": DEFAULT_INTERVAL_SEC,
        }
    if _lock_held_by_other():
        return {"ok": True, "started": False, "skipped": "already_running"}

    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return {"ok": True, "started": False, "skipped": "thread_busy"}

        def _target() -> None:
            try:
                _run_once(limit=limit)
            except Exception:
                log.exception("one-way background pull failed")

        _thread = threading.Thread(
            target=_target, daemon=True, name="one-way-media-pull"
        )
        _thread.start()
        return {"ok": True, "started": True, "via": "engine_thread"}


def maybe_run_scheduled() -> dict[str, Any] | None:
    """Scheduler tick: at most once per DEFAULT_INTERVAL_SEC while App is up."""
    if not script_installed():
        return None
    out = run_in_background(force=False)
    if out.get("started"):
        return out
    return None
