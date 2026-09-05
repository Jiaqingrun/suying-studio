"""Engine readiness: distinguish process-alive /health from business-usable state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from engine.config.settings import load_settings
from engine.config.workspace import probe_workspace
from engine.jobs.worker import worker
from engine.ops.scheduler import scheduler
from engine.runtime.services import get


def _trusted_keys_path() -> Path:
    return Path(__file__).resolve().parent.parent / "security" / "trusted_release_keys.json"


def build_readiness_snapshot() -> dict[str, Any]:
    from engine.runtime import boot_state
    from engine.security.license import is_packaged_runtime, license_status

    boot = boot_state.snapshot()
    settings = load_settings()
    probe = probe_workspace(settings)
    workspace_state = probe.state
    workspace_ready = bool(probe.can_init_db and probe.state in ("ready", "local"))

    development_build = not is_packaged_runtime()
    runtime_source_valid = True
    runtime_source_reason = ""

    if not development_build:
        keys_path = _trusted_keys_path()
        if not keys_path.is_file():
            runtime_source_valid = False
            runtime_source_reason = f"缺失许可证公钥文件: {keys_path}"

    license_authorized = True
    license_locked_reason = ""
    license_kind = "development" if development_build else ""

    if not development_build:
        try:
            status = license_status(
                allow_cached_device_binding=(
                    os.environ.get("SUYING_ALLOW_CACHED_LICENSE_BINDING") == "1"
                ),
            )
            license_kind = str(status.get("license_kind") or "")
            if not bool(status.get("authorized")):
                license_authorized = False
                license_locked_reason = str(status.get("locked_reason") or "许可证不可用")
        except Exception as exc:  # noqa: BLE001
            license_authorized = False
            license_locked_reason = str(exc)

    worker_running = worker.is_alive()
    scheduler_running = scheduler.is_alive()
    watcher = get("watcher")
    watcher_running = bool(watcher and watcher.is_alive())
    services_ready = worker_running and scheduler_running
    boot_ok = boot.get("boot_phase") in ("ready", "blocked", "uninitialized")
    if boot.get("boot_phase") == "failed":
        boot_ok = False
        runtime_source_valid = False
        runtime_source_reason = str(boot.get("boot_error") or "engine boot failed")
    if boot.get("boot_phase") == "starting":
        boot_ok = False

    ready = (
        boot_ok
        and workspace_ready
        and runtime_source_valid
        and license_authorized
        and boot.get("boot_phase") in ("ready", "uninitialized")
    )

    offline_class = "ok"
    offline_detail = ""
    if boot.get("boot_phase") == "starting":
        offline_class = "starting"
        offline_detail = "引擎正在启动服务"
    elif boot.get("boot_phase") == "failed":
        offline_class = "boot_failed"
        offline_detail = str(boot.get("boot_error") or "")
    elif not license_authorized:
        offline_class = "license"
        offline_detail = license_locked_reason
    elif not workspace_ready:
        offline_class = "workspace"
        offline_detail = f"工作区未就绪: {workspace_state}"
    elif not runtime_source_valid:
        offline_class = "integrity"
        offline_detail = runtime_source_reason
    elif boot.get("boot_phase") == "blocked":
        offline_class = "control_plane"
        offline_detail = str(boot.get("boot_error") or "控制面模式")
    elif not ready:
        offline_class = "not_ready"
        offline_detail = "业务未就绪"

    from engine.version import ENGINE_VERSION

    ollama_narration_model = ""
    ollama_circuit: dict[str, Any] = {}
    chat_probe_ok = False
    narration_model_missing = False
    try:
        from engine.catalog.ollama_runtime import ollama_health_snapshot
        from engine.catalog.ollama_status import check_ollama
        from engine.pack.ollama_narration import resolve_narration_model

        ollama_narration_model = resolve_narration_model(settings)
        gateway = ollama_health_snapshot()
        ollama_circuit = gateway.get("circuit") if isinstance(gateway.get("circuit"), dict) else {}
        chat_probe_ok = bool(gateway.get("chat_probe_ok"))
        base = check_ollama()
        tags = {str(n).split(":")[0] for n in (base.get("models") or [])}
        # Also accept full tag match from model list if present
        model_names = {str(n) for n in (base.get("models") or [])}
        want = str(ollama_narration_model or "").strip()
        if want and bool(base.get("reachable")):
            short = want.split(":")[0]
            narration_model_missing = want not in model_names and short not in tags and want not in tags
    except Exception:  # noqa: BLE001
        pass

    return {
        "ready": ready,
        "engine_version": ENGINE_VERSION,
        "process_alive": True,
        "development_build": development_build,
        "license_authorized": license_authorized,
        "license_locked_reason": license_locked_reason,
        "license_kind": license_kind,
        "workspace_ready": workspace_ready,
        "workspace_state": workspace_state,
        "runtime_source_valid": runtime_source_valid,
        "runtime_source_reason": runtime_source_reason,
        "services_ready": services_ready,
        "worker_running": worker_running,
        "scheduler_running": scheduler_running,
        "watcher_running": watcher_running,
        "engine_root": str(Path(__file__).resolve().parent.parent.parent),
        "trusted_keys_path": str(_trusted_keys_path()),
        "boot": boot,
        "offline_class": offline_class,
        "offline_detail": offline_detail,
        "listen": True,
        "control_plane": True,
        "business_ready": ready,
        "ollama_narration_enabled": bool(getattr(settings, "ollama_narration_enabled", False)),
        "ollama_narration_model": ollama_narration_model,
        "ollama_circuit": ollama_circuit,
        "chat_probe_ok": chat_probe_ok,
        "narration_model_missing": narration_model_missing,
        "narration_model_warning": (
            f"旁白模型未安装: {ollama_narration_model}" if narration_model_missing else ""
        ),
    }
