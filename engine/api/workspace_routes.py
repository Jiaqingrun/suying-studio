"""Workspace probe / reconnect routes (no SQLite required when disk missing)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.config.settings import load_settings, save_settings
from engine.config.workspace import (
    STATE_LOCAL,
    STATE_READY,
    ensure_identity_on_ready,
    probe_workspace,
    volume_info_for_path,
)

router = APIRouter(tags=["workspace"])


class WorkspaceReconnectBody(BaseModel):
    """Manual reconnect: re-probe and optionally bind identity. Never copies DB."""

    bind_identity: bool = True
    force_rebind: bool = False


@router.get("/workspace/status")
def workspace_status() -> dict[str, Any]:
    settings = load_settings()
    probe = probe_workspace(settings)
    return {
        "ok": probe.ok,
        "state": probe.state,
        "workspace": probe.to_dict(),
        "sync_semantics": "reattach_refresh",
        "note": "仅重连权威工作区并刷新 App；不复制、不合并、不覆盖数据库或媒体",
    }


@router.post("/workspace/probe")
def workspace_probe() -> dict[str, Any]:
    settings = load_settings()
    probe = probe_workspace(settings)
    return {
        "ok": probe.ok,
        "state": probe.state,
        "workspace": probe.to_dict(),
    }


@router.post("/workspace/reconnect")
def workspace_reconnect(body: WorkspaceReconnectBody | None = None) -> dict[str, Any]:
    """Re-probe external workspace. If ready, bind identity and start services if needed.

    Does NOT copy or restore any database. App/Tauri should restart engine when
    the process was started in control-plane-only mode.
    """
    body = body or WorkspaceReconnectBody()
    settings = load_settings()
    probe = probe_workspace(settings)
    if probe.state not in (STATE_READY, STATE_LOCAL):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_unavailable",
                "message": "；".join(probe.reasons) or f"工作区不可用（{probe.state}）",
                "workspace": probe.to_dict(),
            },
        )

    bound = False
    if body.bind_identity and probe.is_external and probe.state == STATE_READY:
        if settings.workspace_id and not body.force_rebind:
            # Keep existing bind; still refresh marker if missing.
            ensure_identity_on_ready(settings, volume_uuid=probe.volume_uuid or settings.workspace_volume_uuid)
            save_settings(settings)
            bound = True
        else:
            vol = probe.volume_uuid
            if not vol:
                vol_info = volume_info_for_path(settings.paths.data_root)
                vol = vol_info.get("volume_uuid")
            ensure_identity_on_ready(settings, volume_uuid=vol)
            save_settings(settings)
            bound = True

    services_started = False
    try:
        from engine.api.app import try_start_workspace_services

        services_started = bool(try_start_workspace_services(settings))
    except Exception:  # noqa: BLE001
        services_started = False

    probe2 = probe_workspace(settings)
    return {
        "ok": True,
        "state": probe2.state,
        "bound": bound,
        "services_started": services_started,
        "workspace": probe2.to_dict(),
        "hint": "若引擎仍处于控制面模式，请由 App 重启引擎后再 refreshAll",
    }
