"""Unified Ollama gateway: cancelable requests, shared heavy slot, machine circuit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx

# Embed / vision probes unload immediately so light health checks do not pin VRAM.
# Narration (qwen3.5 等) 冷加载可达数十秒；keep_alive=0 会导致每条成片都冷启超时 → READY 打回空转。
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_KEEP_ALIVE = 0
OLLAMA_NARRATION_KEEP_ALIVE = "30m"

_SHALLOW_TTL_SEC = 5.0
_PROBE_TTL_SEC = 30.0
_FAILURE_THRESHOLD = 3
_OPEN_COOLDOWN_SEC = 90.0
_PROBE_TEXT = "suying embed probe"
_PROBE_TIMEOUT_SEC = 12.0
_CHAT_PROBE_TIMEOUT_SEC = 90.0
_HEAVY_ACQUIRE_WAIT_SEC = 45.0

_WORKER_SNIPPET = r"""
import json, sys
import httpx
url, timeout, path = sys.argv[1], float(sys.argv[2]), sys.argv[3]
payload = json.loads(Path := __import__("pathlib").Path(path).read_text(encoding="utf-8"))
try:
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        resp = client.post(url, json=payload)
    out = {"ok": True, "status_code": resp.status_code, "body": resp.text}
except Exception as e:
    out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
print(json.dumps(out, ensure_ascii=False))
"""


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class OllamaErrorKind(str, Enum):
    OK = "ok"
    DAEMON_UNREACHABLE = "daemon_unreachable"
    RUNNER_CRASH = "runner_crash"
    TIMEOUT = "timeout"
    MODEL_MISSING = "model_missing"
    BAD_RESPONSE = "bad_response"
    SLOT_BUSY = "slot_busy"
    CIRCUIT_OPEN = "circuit_open"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class _Circuit:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    last_error_kind: str = ""
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_success_model: str | None = None
    half_open_probe_inflight: bool = False


_circuit = _Circuit()
_circuit_lock = threading.Lock()
# Legacy alias kept for tests; heavy traffic now uses ResourceGate.ollama_heavy.
_embed_slot = threading.Lock()
_shallow_cache: tuple[float, bool, str] = (0.0, False, "")
_probe_cache: tuple[float, bool, str, float | None] = (0.0, False, "", None)
_func_cache: dict[str, Any] = {
    "chat": {"ok": False, "message": "", "latency_ms": None, "at": 0.0, "model": None},
    "embed": {"ok": False, "message": "", "latency_ms": None, "at": 0.0, "model": None},
}


def _now() -> float:
    return time.monotonic()


def classify_ollama_error(error: str | None, *, status_code: int | None = None) -> OllamaErrorKind:
    text = str(error or "").strip().lower()
    if status_code == 404 or "model" in text and ("not found" in text or "missing" in text):
        return OllamaErrorKind.MODEL_MISSING
    if status_code in (499, 502, 503) or "runner" in text or "signal: killed" in text:
        return OllamaErrorKind.RUNNER_CRASH
    if any(n in text for n in ("timed out", "timeout", "readtimeout", "wall-clock", "wall clock")):
        return OllamaErrorKind.TIMEOUT
    if any(
        n in text
        for n in (
            "connect",
            "connection",
            "refused",
            "unreachable",
            "daemon",
            "name or service",
        )
    ):
        return OllamaErrorKind.DAEMON_UNREACHABLE
    if "slot_busy" in text or "resource_gate" in text or "lease_expired" in text:
        return OllamaErrorKind.SLOT_BUSY
    if "circuit" in text:
        return OllamaErrorKind.CIRCUIT_OPEN
    if "cancel" in text:
        return OllamaErrorKind.CANCELLED
    if status_code and status_code >= 400:
        return OllamaErrorKind.BAD_RESPONSE
    if text:
        return OllamaErrorKind.UNKNOWN
    return OllamaErrorKind.OK


def _shallow_tags_probe(timeout_sec: float = 2.0) -> tuple[bool, str]:
    global _shallow_cache
    now = _now()
    cached_at, ok, msg = _shallow_cache
    if now - cached_at < _SHALLOW_TTL_SEC:
        return ok, msg
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_sec), trust_env=False) as client:
            resp = client.get(f"{OLLAMA_URL}/api/tags")
        if resp.status_code == 200:
            ok, msg = True, "tags_ok"
        else:
            ok, msg = False, f"tags_http_{resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"tags_{type(exc).__name__}"
    _shallow_cache = (now, ok, msg)
    return ok, msg


def record_success(*, model: str | None = None) -> None:
    with _circuit_lock:
        _circuit.state = CircuitState.CLOSED
        _circuit.consecutive_failures = 0
        _circuit.last_success_at = _now()
        _circuit.last_error = ""
        _circuit.last_error_kind = ""
        _circuit.half_open_probe_inflight = False
        if model:
            _circuit.last_success_model = model


def record_failure(error: str, *, kind: OllamaErrorKind | None = None) -> None:
    err_kind = kind or classify_ollama_error(error)
    with _circuit_lock:
        _circuit.consecutive_failures += 1
        _circuit.last_failure_at = _now()
        _circuit.last_error = error
        _circuit.last_error_kind = err_kind.value
        _circuit.half_open_probe_inflight = False
        if _circuit.consecutive_failures >= _FAILURE_THRESHOLD:
            _circuit.state = CircuitState.OPEN
            _circuit.open_until = _now() + _OPEN_COOLDOWN_SEC


# Backward-compatible aliases
def record_embed_success() -> None:
    record_success()


def record_embed_failure(error: str) -> None:
    record_failure(error)


def _maybe_half_open() -> None:
    with _circuit_lock:
        if _circuit.state == CircuitState.OPEN and _now() >= _circuit.open_until:
            _circuit.state = CircuitState.HALF_OPEN
            _circuit.half_open_probe_inflight = False


def circuit_allows_request(*, for_probe: bool = False) -> bool:
    """Machine-level gate. Half-open allows exactly one probe."""
    _maybe_half_open()
    with _circuit_lock:
        if _circuit.state == CircuitState.CLOSED:
            return True
        if _circuit.state == CircuitState.OPEN:
            return False
        # HALF_OPEN
        if for_probe:
            if _circuit.half_open_probe_inflight:
                return False
            _circuit.half_open_probe_inflight = True
            return True
        return False


def embed_circuit_allows_request() -> bool:
    return circuit_allows_request(for_probe=True)


def circuit_snapshot() -> dict[str, Any]:
    _maybe_half_open()
    with _circuit_lock:
        return {
            "state": _circuit.state.value,
            "consecutive_failures": _circuit.consecutive_failures,
            "open_until_monotonic": _circuit.open_until,
            "open_remaining_sec": max(0.0, _circuit.open_until - _now())
            if _circuit.state == CircuitState.OPEN
            else 0.0,
            "last_error": _circuit.last_error,
            "last_error_kind": _circuit.last_error_kind,
            "last_success_model": _circuit.last_success_model,
            "allows_request": _circuit.state != CircuitState.OPEN,
            "half_open_probe_inflight": _circuit.half_open_probe_inflight,
        }


def acquire_embed_slot(timeout_sec: float = 30.0) -> bool:
    return _embed_slot.acquire(timeout=timeout_sec)


def release_embed_slot() -> None:
    if _embed_slot.locked():
        _embed_slot.release()


def _try_acquire_heavy(token: str, wait_sec: float) -> bool:
    try:
        from engine.runtime.resource_gate import gate as resource_gate
    except Exception:  # noqa: BLE001
        return True
    deadline = time.monotonic() + max(0.0, wait_sec)
    while time.monotonic() <= deadline:
        if resource_gate.try_acquire("ollama_heavy", token):
            return True
        time.sleep(0.25)
    return False


def _release_heavy(token: str) -> None:
    try:
        from engine.runtime.resource_gate import gate as resource_gate

        resource_gate.release("ollama_heavy", token)
    except Exception:  # noqa: BLE001
        pass


def run_cancelable_post(
    *,
    path: str,
    payload: dict[str, Any],
    timeout_sec: float,
    cancel_event: threading.Event | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """POST via subprocess so wall-clock timeout can SIGKILL the HTTP client."""
    req_id = request_id or uuid.uuid4().hex[:12]
    url = f"{OLLAMA_URL}{path}"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        payload_path = handle.name
    proc: subprocess.Popen[str] | None = None
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER_SNIPPET, url, str(timeout_sec), payload_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + max(1.0, float(timeout_sec) + 2.0)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _kill_proc(proc)
                return {
                    "ok": False,
                    "error": "cancelled",
                    "error_kind": OllamaErrorKind.CANCELLED.value,
                    "request_id": req_id,
                    "latency_ms": (time.perf_counter() - started) * 1000.0,
                }
            rc = proc.poll()
            if rc is not None:
                break
            if time.monotonic() >= deadline:
                _kill_proc(proc)
                return {
                    "ok": False,
                    "error": f"wall-clock timeout ({timeout_sec:.0f}s)",
                    "error_kind": OllamaErrorKind.TIMEOUT.value,
                    "request_id": req_id,
                    "latency_ms": (time.perf_counter() - started) * 1000.0,
                }
            time.sleep(0.05)
        stdout, stderr = proc.communicate(timeout=2)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if proc.returncode != 0 and not (stdout or "").strip():
            err = (stderr or "").strip() or f"subprocess_exit_{proc.returncode}"
            kind = classify_ollama_error(err)
            return {
                "ok": False,
                "error": err[:400],
                "error_kind": kind.value,
                "request_id": req_id,
                "latency_ms": latency_ms,
            }
        try:
            parsed = json.loads((stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "bad_subprocess_json",
                "error_kind": OllamaErrorKind.BAD_RESPONSE.value,
                "request_id": req_id,
                "latency_ms": latency_ms,
                "raw": (stdout or "")[:300],
            }
        if not parsed.get("ok"):
            err = str(parsed.get("error") or "request_failed")
            kind = classify_ollama_error(err)
            return {
                "ok": False,
                "error": err,
                "error_kind": kind.value,
                "request_id": req_id,
                "latency_ms": latency_ms,
            }
        status = int(parsed.get("status_code") or 0)
        body_text = str(parsed.get("body") or "")
        try:
            body = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            body = {"_raw": body_text[:500]}
        if status != 200:
            err = f"ollama HTTP {status}"
            kind = classify_ollama_error(err, status_code=status)
            if isinstance(body, dict):
                detail = str(body.get("error") or "")
                if detail:
                    err = f"{err}: {detail}"
                    kind = classify_ollama_error(err, status_code=status)
            return {
                "ok": False,
                "error": err,
                "error_kind": kind.value,
                "status_code": status,
                "body": body,
                "request_id": req_id,
                "latency_ms": latency_ms,
            }
        return {
            "ok": True,
            "status_code": status,
            "body": body,
            "request_id": req_id,
            "latency_ms": latency_ms,
        }
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass
        if proc is not None and proc.poll() is None:
            _kill_proc(proc)


def _kill_proc(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        pass


def heavy_request(
    *,
    kind: str,
    path: str,
    payload: dict[str, Any],
    timeout_sec: float,
    model: str | None = None,
    cancel_event: threading.Event | None = None,
    acquire_wait_sec: float = _HEAVY_ACQUIRE_WAIT_SEC,
    record_circuit: bool = True,
    bypass_circuit: bool = False,
) -> dict[str, Any]:
    """Single entry for narration/embed/vision/rule heavy calls."""
    if not bypass_circuit and not circuit_allows_request(for_probe=False):
        snap = circuit_snapshot()
        return {
            "ok": False,
            "error": "ollama circuit_open",
            "error_kind": OllamaErrorKind.CIRCUIT_OPEN.value,
            "infra": True,
            "circuit": snap,
            "kind": kind,
            "model": model,
        }

    token = f"{kind}:{uuid.uuid4().hex[:12]}"
    if not _try_acquire_heavy(token, acquire_wait_sec):
        return {
            "ok": False,
            "error": "resource_gate ollama_heavy busy",
            "error_kind": OllamaErrorKind.SLOT_BUSY.value,
            "infra": True,
            "kind": kind,
            "model": model,
            "token": token,
        }

    try:
        result = run_cancelable_post(
            path=path,
            payload=payload,
            timeout_sec=timeout_sec,
            cancel_event=cancel_event,
        )
        result["kind"] = kind
        result["model"] = model
        result["token"] = token
        if result.get("ok"):
            if record_circuit:
                record_success(model=model)
            result["infra"] = False
            return result
        err = str(result.get("error") or "failed")
        try:
            err_kind = OllamaErrorKind(str(result.get("error_kind") or ""))
        except ValueError:
            err_kind = classify_ollama_error(err, status_code=result.get("status_code"))
        infra = err_kind in {
            OllamaErrorKind.TIMEOUT,
            OllamaErrorKind.DAEMON_UNREACHABLE,
            OllamaErrorKind.RUNNER_CRASH,
            OllamaErrorKind.MODEL_MISSING,
            OllamaErrorKind.SLOT_BUSY,
            OllamaErrorKind.CIRCUIT_OPEN,
            OllamaErrorKind.UNKNOWN,
        }
        result["infra"] = infra
        result["error_kind"] = err_kind.value
        if record_circuit and infra and err_kind != OllamaErrorKind.CANCELLED:
            record_failure(err, kind=err_kind)
        return result
    finally:
        _release_heavy(token)


def chat_completion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    timeout_sec: float = 60.0,
    keep_alive: Any = OLLAMA_NARRATION_KEEP_ALIVE,
    think: bool = False,
    options: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
    kind: str = "chat",
    budget_sec: float | None = None,
) -> dict[str, Any]:
    remaining = float(budget_sec if budget_sec is not None else timeout_sec)
    if remaining <= 1.0:
        return {
            "ok": False,
            "error": "budget_exhausted",
            "error_kind": OllamaErrorKind.TIMEOUT.value,
            "infra": True,
            "model": model,
        }
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "think": bool(think),
        "keep_alive": keep_alive,
        "messages": messages,
    }
    if options:
        payload["options"] = options
    return heavy_request(
        kind=kind,
        path="/api/chat",
        payload=payload,
        timeout_sec=remaining,
        model=model,
        cancel_event=cancel_event,
    )


def embeddings(
    *,
    model: str,
    prompt: str,
    timeout_sec: float = 30.0,
    keep_alive: Any = OLLAMA_KEEP_ALIVE,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    return heavy_request(
        kind="embed",
        path="/api/embeddings",
        payload={
            "model": model,
            "prompt": prompt,
            "keep_alive": keep_alive,
        },
        timeout_sec=timeout_sec,
        model=model,
        cancel_event=cancel_event,
    )


def functional_embed_probe(
    *,
    model: str | None = None,
    timeout_sec: float = _PROBE_TIMEOUT_SEC,
    force: bool = False,
) -> dict[str, Any]:
    """Real embedding call — shallow /api/tags is not enough."""
    global _probe_cache
    now = _now()
    cached_at, ok, msg, latency = _probe_cache
    if not force and now - cached_at < _PROBE_TTL_SEC:
        return {
            "ok": ok,
            "message": msg,
            "latency_ms": latency,
            "cached": True,
        }

    if model is None:
        try:
            from engine.catalog.ollama_status import active_models_from_settings

            embed, _ = active_models_from_settings()
            model = embed or "nomic-embed-text"
        except Exception:
            model = "nomic-embed-text"

    if not circuit_allows_request(for_probe=True):
        return {
            "ok": False,
            "message": "circuit_open",
            "latency_ms": None,
            "cached": False,
            "model": model,
        }

    result = heavy_request(
        kind="embed_probe",
        path="/api/embeddings",
        payload={
            "model": model,
            "prompt": _PROBE_TEXT,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        },
        timeout_sec=timeout_sec,
        model=model,
        acquire_wait_sec=5.0,
        record_circuit=True,
        bypass_circuit=True,  # already passed half-open / closed check
    )
    ok = bool(result.get("ok")) and bool((result.get("body") or {}).get("embedding"))
    msg = "embed_ok" if ok else str(result.get("error") or "probe_failed")
    latency_ms = result.get("latency_ms")
    _probe_cache = (now, ok, msg, latency_ms)
    _func_cache["embed"] = {
        "ok": ok,
        "message": msg,
        "latency_ms": latency_ms,
        "at": now,
        "model": model,
    }
    return {
        "ok": ok,
        "message": msg,
        "latency_ms": latency_ms,
        "cached": False,
        "model": model,
    }


def functional_chat_probe(
    *,
    model: str | None = None,
    timeout_sec: float = _CHAT_PROBE_TIMEOUT_SEC,
    force: bool = False,
) -> dict[str, Any]:
    now = _now()
    cached = _func_cache.get("chat") or {}
    if not force and now - float(cached.get("at") or 0.0) < _PROBE_TTL_SEC:
        return {
            "ok": bool(cached.get("ok")),
            "message": cached.get("message") or "",
            "latency_ms": cached.get("latency_ms"),
            "cached": True,
            "model": cached.get("model"),
        }
    if model is None:
        try:
            from engine.config.settings import load_settings

            model = (
                str(getattr(load_settings(), "ollama_narration_model", "") or "").strip()
                or "qwen3.5:9b"
            )
        except Exception:
            model = "qwen3.5:9b"
    if not circuit_allows_request(for_probe=True):
        return {"ok": False, "message": "circuit_open", "cached": False, "model": model}
    result = heavy_request(
        kind="chat_probe",
        path="/api/chat",
        payload={
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "options": {"num_predict": 8},
        },
        timeout_sec=timeout_sec,
        model=model,
        acquire_wait_sec=5.0,
        bypass_circuit=True,
    )
    content = ""
    body = result.get("body") or {}
    if isinstance(body, dict):
        content = str(((body.get("message") or {}).get("content")) or "").strip()
    ok = bool(result.get("ok")) and bool(content)
    msg = "chat_ok" if ok else str(result.get("error") or "chat_probe_failed")
    latency_ms = result.get("latency_ms")
    _func_cache["chat"] = {
        "ok": ok,
        "message": msg,
        "latency_ms": latency_ms,
        "at": now,
        "model": model,
    }
    return {
        "ok": ok,
        "message": msg,
        "latency_ms": latency_ms,
        "cached": False,
        "model": model,
        "content": content[:80],
    }


def warmup_narration_model(model: str | None = None) -> dict[str, Any]:
    return functional_chat_probe(model=model, force=True)


def embed_gateway_snapshot() -> dict[str, Any]:
    shallow_ok, shallow_msg = _shallow_tags_probe()
    probe_at, probe_ok, probe_msg, probe_latency = _probe_cache
    circuit = circuit_snapshot()
    chat = _func_cache.get("chat") or {}
    return {
        "shallow_reachable": shallow_ok,
        "shallow_message": shallow_msg,
        "functional_probe_ok": probe_ok,
        "functional_probe_message": probe_msg,
        "functional_probe_latency_ms": probe_latency,
        "functional_probe_cached_age_sec": max(0.0, _now() - probe_at) if probe_at else None,
        "chat_probe_ok": bool(chat.get("ok")),
        "chat_probe_message": chat.get("message"),
        "chat_probe_model": chat.get("model"),
        "chat_probe_latency_ms": chat.get("latency_ms"),
        "embed_slot_locked": _embed_slot.locked(),
        "circuit": circuit,
        "url": OLLAMA_URL,
    }


def ollama_health_snapshot() -> dict[str, Any]:
    """Cached functional + circuit view for /health/ollama (no expensive inference)."""
    return embed_gateway_snapshot()
