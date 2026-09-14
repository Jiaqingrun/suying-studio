"""Ollama daemon ownership probe, install gate, and bounded recovery metadata."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from engine.catalog.ollama_runtime import (
    OLLAMA_URL,
    functional_chat_probe,
    functional_embed_probe,
    record_success,
    warmup_narration_model,
)

log = logging.getLogger("montage.ollama_service")

MANAGED_LABEL = "com.qr.suying.ollama"
MANAGED_BIN = Path.home() / "Suying" / "runtime" / "tools" / "bin" / "ollama"
MANAGED_MODELS = Path.home() / "Suying" / "runtime" / "ollama" / "models"
USER_MODELS = Path.home() / ".ollama" / "models"
STATE_FILE = Path.home() / "Suying" / "runtime" / "ollama" / "service_state.json"
MAX_KICKSTART_WINDOW = 3
KICKSTART_WINDOW_SEC = 600.0
# macOS sleep/wake can leave ollama in STAT=T while 11434 still LISTENs;
# TCP then hangs until client timeout and freezes App /health probes.
CONT_COOLDOWN_SEC = 15.0
# Scheduler pause path ticks every 2s; without a scan floor we spam lsof/pgrep
# forever when nothing is stopped (last_cont_at only updates on SIGCONT).
CONT_SCAN_COOLDOWN_SEC = 8.0
_LAST_CONT_SCAN_AT = 0.0


def _run(argv: list[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def listener_pid(port: int = 11434) -> int | None:
    proc = _run(["lsof", "-t", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN"])
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0])
    except ValueError:
        return None


def process_command(pid: int) -> str:
    proc = _run(["ps", "-p", str(pid), "-o", "command="])
    return (proc.stdout or "").strip()


def process_state_code(pid: int) -> str:
    """Return macOS/BSD `ps` state code (e.g. S, R, T). Empty if missing."""
    proc = _run(["ps", "-p", str(pid), "-o", "state="])
    return (proc.stdout or "").strip()


def is_stopped_state(state: str) -> bool:
    """True when process is job-control stopped (SIGSTOP / debugger / sleep glitch)."""
    code = (state or "").strip().upper()
    return bool(code) and code[0] == "T"


def list_ollama_related_pids() -> list[int]:
    """PIDs for ollama serve / runner (listener first). Best-effort, deduped."""
    found: list[int] = []
    seen: set[int] = set()
    listen = listener_pid()
    if listen is not None:
        seen.add(listen)
        found.append(listen)
    proc = _run(["pgrep", "-f", "[o]llama"])
    if proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid in seen:
                continue
            cmd = process_command(pid).lower()
            if "ollama" not in cmd:
                continue
            seen.add(pid)
            found.append(pid)
    return found


def resume_stopped_ollama_processes(*, force: bool = False) -> dict[str, Any]:
    """SIGCONT any stopped ollama processes so 11434 stops black-holing TCP.

    Safe for external Homebrew/App Ollama: only continues a frozen process,
    never kills or replaces it. Rate-limited unless ``force``.
    """
    global _LAST_CONT_SCAN_AT
    state = read_state()
    now = time.time()
    last = float(state.get("last_cont_at") or 0.0)
    if not force and last and (now - last) < CONT_COOLDOWN_SEC:
        return {
            "ok": True,
            "action": "cooldown",
            "resumed_pids": [],
            "checked_pids": [],
            "cooldown_remaining_sec": round(CONT_COOLDOWN_SEC - (now - last), 1),
        }
    # Separate from SIGCONT cooldown: healthy hosts never set last_cont_at, so
    # without this floor the paused 2s scheduler tick would shell lsof/pgrep forever.
    if (
        not force
        and _LAST_CONT_SCAN_AT
        and (now - _LAST_CONT_SCAN_AT) < CONT_SCAN_COOLDOWN_SEC
    ):
        return {
            "ok": True,
            "action": "scan_cooldown",
            "resumed_pids": [],
            "checked_pids": [],
            "cooldown_remaining_sec": round(
                CONT_SCAN_COOLDOWN_SEC - (now - _LAST_CONT_SCAN_AT), 1
            ),
        }
    _LAST_CONT_SCAN_AT = now
    resumed: list[int] = []
    checked: list[dict[str, Any]] = []
    for pid in list_ollama_related_pids():
        st = process_state_code(pid)
        row: dict[str, Any] = {"pid": pid, "state": st}
        checked.append(row)
        if not is_stopped_state(st):
            continue
        # Command only when we may SIGCONT — list_ollama_related_pids already
        # filtered via ps; avoid a second process_command on every healthy pid.
        row["command"] = process_command(pid)[:160]
        try:
            os.kill(pid, signal.SIGCONT)
            resumed.append(pid)
            row["cont"] = True
        except OSError as exc:  # noqa: BLE001
            row["cont"] = False
            row["error"] = str(exc)
    if resumed:
        merge_state(
            last_cont_at=now,
            last_cont_pids=resumed,
            last_cont_reason="stopped_state_T",
            last_recovery="sigcont",
        )
        log.warning("resumed stopped ollama pid(s) via SIGCONT: %s", resumed)
    return {
        "ok": True,
        "action": "sigcont" if resumed else "none",
        "resumed_pids": resumed,
        "checked_pids": checked,
    }


def ensure_ollama_listener_responsive(
    *,
    probe_timeout: float = 1.0,
    force_cont: bool = False,
) -> dict[str, Any]:
    """Unfreeze stopped listeners, then shallow-probe /api/tags with a short timeout."""
    cont = resume_stopped_ollama_processes(force=force_cont)
    pid = listener_pid()
    if pid is None:
        return {
            "ok": False,
            "action": cont.get("action") or "none",
            "reason": "no_listener",
            "cont": cont,
        }
    st = process_state_code(pid)
    if is_stopped_state(st):
        # First pass may have been on cooldown; force once.
        cont = resume_stopped_ollama_processes(force=True)
        st = process_state_code(pid)
    try:
        import httpx

        with httpx.Client(timeout=probe_timeout, trust_env=False) as client:
            resp = client.get(f"{OLLAMA_URL}/api/tags")
        ok = resp.status_code == 200
        return {
            "ok": ok,
            "action": cont.get("action") or "probe",
            "listener_pid": pid,
            "listener_state": st,
            "http_status": resp.status_code,
            "cont": cont,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "action": cont.get("action") or "probe",
            "listener_pid": pid,
            "listener_state": st,
            "error": str(exc)[:240],
            "cont": cont,
        }


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def codesign_verify(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "error": "missing"}
    proc = _run(["codesign", "--verify", "--strict", str(path)], timeout=10.0)
    detail = _run(["codesign", "-dv", "--verbose=4", str(path)], timeout=10.0)
    cdhash = ""
    for line in (detail.stderr or "").splitlines():
        if line.startswith("CDHash="):
            cdhash = line.split("=", 1)[1].strip()
            break
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "cdhash": cdhash or None,
        "stderr": (proc.stderr or "").strip()[:300],
    }


def classify_listener(pid: int | None, command: str) -> str:
    if pid is None:
        return "none"
    managed = str(MANAGED_BIN)
    if managed in command or "Suying/runtime/tools/bin/ollama" in command:
        return "suying_managed"
    if "ollama" in command.lower():
        return "external"
    return "unknown"


def launchagent_loaded() -> bool:
    uid = os.getuid()
    proc = _run(["launchctl", "print", f"gui/{uid}/{MANAGED_LABEL}"])
    return proc.returncode == 0


def models_dir_snapshot() -> dict[str, Any]:
    managed = MANAGED_MODELS
    user = USER_MODELS
    managed_exists = managed.exists()
    managed_is_link = managed.is_symlink()
    managed_target = None
    if managed_is_link:
        try:
            managed_target = str(managed.resolve())
        except Exception:  # noqa: BLE001
            managed_target = str(managed)
    forked = False
    if (
        managed_exists
        and not managed_is_link
        and (managed / "manifests").is_dir()
        and (user / "manifests").is_dir()
    ):
        try:
            forked = managed.resolve() != user.resolve()
        except Exception:  # noqa: BLE001
            forked = True
    return {
        "managed_models": str(managed),
        "managed_exists": managed_exists,
        "managed_is_symlink": managed_is_link,
        "managed_target": managed_target,
        "user_models": str(user),
        "user_exists": user.exists(),
        "fork_detected": forked,
    }


def read_state() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_state(**updates: Any) -> dict[str, Any]:
    """Merge keys into service state without wiping kickstart counters."""
    state = read_state()
    state.update(updates)
    write_state(state)
    return state


def ownership_snapshot() -> dict[str, Any]:
    pid = listener_pid()
    command = process_command(pid) if pid else ""
    ownership = classify_listener(pid, command)
    exe = command.split()[0] if command else ""
    exe_path = Path(exe) if exe else None
    api_version: dict[str, Any] = {}
    if ownership != "none":
        try:
            import httpx

            with httpx.Client(timeout=3.0, trust_env=False) as client:
                resp = client.get(f"{OLLAMA_URL}/api/version")
            if resp.status_code == 200:
                api_version = resp.json()
        except Exception as exc:  # noqa: BLE001
            api_version = {"error": str(exc)}
    sign = codesign_verify(MANAGED_BIN)
    return {
        "ownership": ownership,
        "listener_pid": pid,
        "command": command,
        "managed_binary": str(MANAGED_BIN),
        "managed_binary_exists": MANAGED_BIN.is_file(),
        "managed_binary_sha256": sha256_file(MANAGED_BIN),
        "managed_codesign": sign,
        "listener_executable_sha256": sha256_file(exe_path) if exe_path else None,
        "launchagent_label": MANAGED_LABEL,
        "launchagent_loaded": launchagent_loaded(),
        "api_version": api_version,
        "models": models_dir_snapshot(),
    }


def install_gate_block_reason() -> str | None:
    snap = ownership_snapshot()
    if snap["ownership"] == "external":
        return (
            f"11434 被外部 Ollama 占用 (pid={snap.get('listener_pid')})；"
            "请关闭外部 Ollama 后重装速影受管服务"
        )
    if not MANAGED_BIN.is_file():
        return f"缺少受管 Ollama 二进制: {MANAGED_BIN}"
    sign = snap.get("managed_codesign") or {}
    if not sign.get("ok"):
        return f"受管 Ollama 签名无效: {MANAGED_BIN}"
    models = snap.get("models") or {}
    if models.get("fork_detected"):
        return (
            "模型目录分叉："
            f"{models.get('managed_models')} 与 {models.get('user_models')} 同时存在实体内容；"
            "请合并为单一权威目录（推荐 symlink）后再安装"
        )
    return None


def bounded_kickstart(reason: str = "functional_probe_failed") -> dict[str, Any]:
    """Kickstart only our LaunchAgent; bounded rolling window."""
    state = read_state()
    now = time.time()
    window_start = float(state.get("kickstart_window_start") or 0.0)
    count = int(state.get("kickstart_count") or 0)
    if now - window_start > KICKSTART_WINDOW_SEC:
        window_start = now
        count = 0
    if count >= MAX_KICKSTART_WINDOW:
        return {
            "ok": False,
            "action": "circuit_open",
            "reason": "kickstart_budget_exhausted",
            "kickstart_count": count,
        }
    if not launchagent_loaded():
        return {"ok": False, "action": "skipped", "reason": "launchagent_not_loaded"}
    uid = os.getuid()
    proc = _run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{MANAGED_LABEL}"],
        timeout=15.0,
    )
    count += 1
    merge_state(
        kickstart_window_start=window_start,
        kickstart_count=count,
        last_kickstart_at=now,
        last_kickstart_reason=reason,
        last_kickstart_exit=proc.returncode,
    )
    return {
        "ok": proc.returncode == 0,
        "action": "kickstart",
        "exit_code": proc.returncode,
        "kickstart_count": count,
        "stderr": (proc.stderr or "").strip()[:400],
    }


def maybe_recover_ollama_service(*, kind: str = "chat") -> dict[str, Any]:
    """Bounded global recovery shared by chat + embed callers.

    On consecutive functional failures (chat or embed probe): at most one
    managed kickstart, then warm the narration model back up, then close the
    machine circuit via ``record_success``. Once the kickstart budget inside
    ``bounded_kickstart`` is exhausted, recovery stops and surfaces
    ``action: manual`` so operators are not stuck in a silent retry loop.
    """
    # Sleep/wake often freezes ollama (STAT=T) before functional probes time out.
    responsive = ensure_ollama_listener_responsive(probe_timeout=1.0)
    if responsive.get("cont", {}).get("resumed_pids"):
        merge_state(last_recovery="sigcont_before_probe", last_recovery_kind=kind)
    probe_fn = functional_embed_probe if kind == "embed" else functional_chat_probe
    # Shared counter/state key (pre-existing name) so chat and embed callers
    # trip the same bounded-kickstart budget instead of doubling it.
    state_key = "consecutive_probe_failures"
    probe = probe_fn(force=True)
    if probe.get("ok"):
        merge_state(
            last_recovery="probe_ok",
            last_recovery_kind=kind,
            last_probe_at=time.time(),
            **{state_key: 0},
        )
        record_success()
        return {"ok": True, "action": "none", "probe": probe}
    state = read_state()
    fails = int(state.get(state_key) or 0) + 1
    merge_state(
        **{state_key: fails},
        last_probe_at=time.time(),
        last_probe_message=probe.get("message"),
    )
    if fails < 2:
        return {"ok": False, "action": "probe_failed", "probe": probe, "failures": fails}
    kick = bounded_kickstart(reason=str(probe.get("message") or f"{kind}_probe_failed"))
    # Preserve kickstart counters written by bounded_kickstart; only reset probe fails.
    merge_state(**{state_key: 0})
    if kick.get("action") == "circuit_open":
        # Kickstart budget exhausted this window — stop auto-retrying, surface manual.
        return {"ok": False, "action": "manual", "kick": kick, "probe": probe}
    if not kick.get("ok"):
        return {"ok": False, "action": "kickstart", "kick": kick, "probe": probe}
    warm = warmup_narration_model()
    warm_ok = bool(warm.get("ok"))
    if kind == "embed":
        warm = functional_embed_probe(force=True)
        warm_ok = warm_ok and bool(warm.get("ok"))
    if warm_ok:
        record_success()
    return {"ok": warm_ok, "action": "kickstart", "kick": kick, "probe": warm}


def maybe_recover_embed_service() -> dict[str, Any]:
    """Thin wrapper kept for existing embed-path callers."""
    return maybe_recover_ollama_service(kind="embed")


def service_snapshot() -> dict[str, Any]:
    state = read_state()
    ownership = ownership_snapshot()
    listener = ownership.get("listener_pid")
    listener_state = process_state_code(int(listener)) if listener else ""
    return {
        **ownership,
        "listener_state": listener_state,
        "listener_stopped": is_stopped_state(listener_state),
        "state": state,
        "install_gate_blocked": install_gate_block_reason(),
    }
