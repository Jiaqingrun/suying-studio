"""Backup and published-video cleanup APIs."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import get_session
from engine.config.settings import load_settings
from engine.runtime.pause_coordinator import assert_runtime_active
from engine.version import ENGINE_VERSION

router = APIRouter(prefix="/ops", tags=["maintenance"])


def _runtime_dir() -> Path:
    return Path(
        os.environ.get("SUYING_APP_RUNTIME_DIR")
        or Path.home() / "Library/Application Support/com.qr.suying/runtime"
    )


def _require_ops_token(token: str | None) -> None:
    try:
        expected = (_runtime_dir() / "system_token.txt").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(503, "高级操作验证暂不可用，请重启速影") from exc
    if (
        len(expected) != 64
        or not token
        or not hmac.compare_digest(expected, token.strip())
    ):
        raise HTTPException(403, "高级功能已锁定，请先输入密码")


class BackupPolicyUpdate(BaseModel):
    enabled: bool | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int | None = Field(default=None, ge=0, le=23)
    retention_count: int | None = Field(default=None, ge=3, le=52)
    include_chrome: bool | None = None
    root: str | None = None


class BackupPruneRequest(BaseModel):
    dry_run: bool = True
    retention_count: int | None = Field(default=None, ge=3, le=52)
    confirm: bool = False


class PublishedCleanupPolicyUpdate(BaseModel):
    enabled: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    trash_grace_days: int | None = Field(default=None, ge=1, le=90)


class PublishedCleanupRequest(BaseModel):
    output_ids: list[int] | None = None
    older_than_days: int | None = Field(default=None, ge=1, le=3650)
    confirm: bool = False


class DiskCleanupPolicyUpdate(BaseModel):
    failed: dict[str, Any] | None = None
    published: dict[str, Any] | None = None
    work_cache: dict[str, Any] | None = None


class DiskCleanupRunRequest(BaseModel):
    tiers: list[str] = Field(default_factory=list)
    confirm: bool = False


@router.get("/backups/status")
def backup_status() -> dict[str, Any]:
    from engine.ops.backup_service import status

    return {"ok": True, **status()}


@router.get("/backups")
def backup_list() -> dict[str, Any]:
    from engine.ops.backup_service import list_backups

    return {"ok": True, "backups": list_backups()}


@router.post("/backups")
def backup_start(
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    assert_runtime_active("backup")
    from engine.ops.backup_service import start_backup

    return start_backup("manual")


@router.put("/backups/policy")
def backup_policy_update(
    body: BackupPolicyUpdate,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    from engine.ops.backup_service import save_policy

    policy = save_policy(body.model_dump(exclude_none=True))
    agent: dict[str, Any] | None = None
    if policy.get("enabled"):
        import subprocess

        script = Path(__file__).resolve().parents[2] / "scripts" / "install-backup-agent.sh"
        if script.is_file():
            completed = subprocess.run(
                ["bash", str(script)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            agent = {
                "ok": completed.returncode == 0,
                "message": (completed.stdout or completed.stderr).strip()[:300],
            }
    return {"ok": True, "policy": policy, "agent": agent}


@router.post("/backups/prune")
def backup_prune(
    body: BackupPruneRequest,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    if not body.dry_run and not body.confirm:
        raise HTTPException(400, "删除历史备份前必须明确确认")
    from engine.ops.backup_service import prune_backups

    return prune_backups(body.retention_count, dry_run=body.dry_run)


@router.get("/published-cleanup/policy")
def published_cleanup_policy() -> dict[str, Any]:
    from engine.ops.published_cleanup import load_policy

    return {"ok": True, "policy": load_policy()}


@router.put("/published-cleanup/policy")
def published_cleanup_policy_update(
    body: PublishedCleanupPolicyUpdate,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    from engine.ops.published_cleanup import save_policy

    return {"ok": True, "policy": save_policy(body.model_dump(exclude_none=True))}


@router.get("/published-cleanup/preview")
def published_cleanup_preview(
    older_than_days: int | None = None,
) -> dict[str, Any]:
    from engine.ops.published_cleanup import preview

    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        return preview(
            session,
            customer_id=customer.id,
            output_root=customer.output_root or settings.paths.output_root,
            older_than_days=older_than_days,
        )
    finally:
        session.close()


@router.post("/published-cleanup")
def published_cleanup_run(
    body: PublishedCleanupRequest,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    if not body.confirm:
        raise HTTPException(400, "清理已发布视频前必须明确确认")
    assert_runtime_active("published_video_cleanup")
    from engine.ops.published_cleanup import cleanup

    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        return cleanup(
            session,
            customer_id=customer.id,
            output_root=customer.output_root or settings.paths.output_root,
            older_than_days=body.older_than_days,
            output_ids=body.output_ids,
        )
    finally:
        session.close()


def _customer_paths():
    from engine.catalog.customer_scope import settings_with_customer_paths

    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
        return settings, session, customer, scoped
    except Exception:
        session.close()
        raise


@router.get("/disk-cleanup/report")
def disk_cleanup_report() -> dict[str, Any]:
    """Disk usage by allowlisted category + merged policy (read-only)."""
    from engine.ops.disk_cleanup import report

    settings, session, customer, scoped = _customer_paths()
    try:
        return report(
            session,
            customer_id=customer.id,
            cache_root=scoped.paths.cache_root,
            render_root=scoped.paths.render_root,
            output_root=customer.output_root or scoped.paths.output_root,
        )
    finally:
        session.close()


@router.get("/disk-cleanup/policy")
def disk_cleanup_policy() -> dict[str, Any]:
    from engine.ops.disk_cleanup import load_merged_policy

    return {"ok": True, "policy": load_merged_policy()}


@router.put("/disk-cleanup/policy")
def disk_cleanup_policy_update(
    body: DiskCleanupPolicyUpdate,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    from engine.ops.disk_cleanup import save_merged_policy

    patch = body.model_dump(exclude_none=True)
    return {"ok": True, "policy": save_merged_policy(patch)}


@router.post("/disk-cleanup/run")
def disk_cleanup_run(
    body: DiskCleanupRunRequest,
    x_suying_ops_token: str | None = Header(default=None, alias="X-Suying-Ops-Token"),
) -> dict[str, Any]:
    _require_ops_token(x_suying_ops_token)
    if not body.confirm:
        raise HTTPException(400, "清理前必须明确确认")
    assert_runtime_active("disk_cleanup")
    from engine.ops.disk_cleanup import run as run_cleanup

    settings, session, customer, scoped = _customer_paths()
    try:
        try:
            return run_cleanup(
                session,
                customer_id=customer.id,
                cache_root=scoped.paths.cache_root,
                render_root=scoped.paths.render_root,
                output_root=customer.output_root or scoped.paths.output_root,
                settings=settings,
                tiers=body.tiers,
                confirm=True,
                actor="user",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.get("/one-way-media/status")
def one_way_media_status() -> dict[str, Any]:
    """片库单向同步状态（无密钥：只读摘要）。"""
    from engine.ops.one_way_media import status

    return {"ok": True, **status()}


@router.post("/one-way-media/run")
def one_way_media_run(
    force: bool = True,
    limit: int = 0,
) -> dict[str, Any]:
    """在引擎进程内触发单向片库同步（可写外置盘，不弹 Terminal）。"""
    from engine.ops.one_way_media import run_in_background

    return run_in_background(force=force, limit=max(0, int(limit or 0)))


@router.get("/engine-supervisor")
def engine_supervisor() -> dict[str, Any]:
    """分层探针：listen / control plane / business readiness + boot 相位。"""
    from engine.api.readiness import build_readiness_snapshot
    from engine.runtime import boot_state

    ready = build_readiness_snapshot()
    boot = boot_state.snapshot()
    return {
        "ok": True,
        "listen": True,
        "control_plane": True,
        "business_ready": bool(ready.get("ready")),
        "boot": boot,
        "offline_class": ready.get("offline_class"),
        "offline_detail": ready.get("offline_detail"),
        "readiness": ready,
        "engine_version": ENGINE_VERSION,
    }

