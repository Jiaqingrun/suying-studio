"""Safe lifecycle operations for customer-scoped Chrome accounts."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from engine.catalog.db import (
    ContentPublishJob,
    ReachMessageAccount,
    ReachMessageScan,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachPublishSchedule,
)
from engine.reach.browser import (
    accounts_json_path,
    customer_scope_root,
    load_accounts_meta,
    remove_chrome_profile_references,
    resolve_chrome_user_data_dir,
    save_accounts_meta,
    validate_chrome_profile_name,
)
from engine.reach.business_scope import SCOPE_CONTENT, SCOPE_VIDEO, normalize_scope
from engine.reach.chrome_runtime import managed_profile_connection

ACTIVE_RUN_STATUSES = ("queued", "running", "paused_human", "outcome_unknown", "stopping")
ACTIVE_ITEM_PHASES = (
    "queued",
    "switching_profile",
    "waiting_login",
    "uploading",
    "filling_copy",
    "setting_cover",
    "submitting",
    "verifying",
    "paused_human",
    "outcome_unknown",
    "awaiting_confirmation",
)
ACTIVE_CONTENT_STATUSES = ("queued", "running", "need_human", "pending_review", "blocked")
ACTIVE_SCAN_STATUSES = ("queued", "running")


def _profile_blockers(
    session: Session,
    *,
    customer_id: int,
    business_scope: str,
    name: str,
) -> list[str]:
    blockers: list[str] = []
    if managed_profile_connection(
        customer_id=customer_id,
        business_scope=business_scope,
        profile_name=name,
    ):
        blockers.append("受管 Chrome 会话正在运行")

    if business_scope == SCOPE_VIDEO:
        active_item = session.scalar(
            select(ReachPublishRunItem.id)
            .join(ReachPublishRun, ReachPublishRunItem.run_id == ReachPublishRun.id)
            .where(
                ReachPublishRunItem.customer_id == customer_id,
                ReachPublishRunItem.chrome_profile == name,
                ReachPublishRun.status.in_(ACTIVE_RUN_STATUSES),
                ReachPublishRunItem.phase.in_(ACTIVE_ITEM_PHASES),
            )
            .limit(1)
        )
        if active_item is not None:
            blockers.append("存在运行中或未完成的视频发布")
        schedule = session.scalar(
            select(ReachPublishSchedule.id).where(
                ReachPublishSchedule.customer_id == customer_id,
                ReachPublishSchedule.chrome_profile == name,
                ReachPublishSchedule.enabled.is_(True),
            )
        )
        if schedule is not None:
            blockers.append("存在有效发布计划")
    elif business_scope == SCOPE_CONTENT:
        content_job = session.scalar(
            select(ContentPublishJob.id).where(
                ContentPublishJob.customer_id == customer_id,
                ContentPublishJob.business_scope == SCOPE_CONTENT,
                ContentPublishJob.profile_name == name,
                ContentPublishJob.status.in_(ACTIVE_CONTENT_STATUSES),
            )
        )
        if content_job is not None:
            blockers.append("存在运行中或未完成的软文发布")

    account_ids = list(
        session.scalars(
            select(ReachMessageAccount.id).where(
                ReachMessageAccount.customer_id == customer_id,
                ReachMessageAccount.business_scope == business_scope,
                ReachMessageAccount.profile_name == name,
            )
        ).all()
    )
    if account_ids:
        scan = session.scalar(
            select(ReachMessageScan.id).where(
                ReachMessageScan.account_id.in_(account_ids),
                ReachMessageScan.status.in_(ACTIVE_SCAN_STATUSES),
            )
        )
        if scan is not None:
            blockers.append("消息巡检正在运行")
    return blockers


def delete_chrome_accounts(
    session: Session,
    customer: Any,
    *,
    names: list[str],
    business_scope: str,
) -> dict[str, Any]:
    """Delete scoped account directories while preserving historical records."""
    scope = normalize_scope(business_scope)
    clean_names = list(dict.fromkeys(validate_chrome_profile_name(name) for name in names))
    if not clean_names:
        raise ValueError("至少选择一个 Chrome 账号")
    root = customer_scope_root(int(customer.id), scope).resolve()
    paths: dict[str, Path] = {}
    all_blockers: dict[str, list[str]] = {}
    for name in clean_names:
        path = resolve_chrome_user_data_dir(
            name,
            customer_id=int(customer.id),
            business_scope=scope,
            require_existing=True,
        )
        if path.resolve().parent != root or path.is_symlink():
            raise ValueError(f"拒绝删除非直属或符号链接配置目录: {name}")
        if any((path / lock).exists() for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket")):
            all_blockers.setdefault(name, []).append("Chrome 配置目录仍被占用")
        blockers = _profile_blockers(
            session,
            customer_id=int(customer.id),
            business_scope=scope,
            name=name,
        )
        if blockers:
            all_blockers.setdefault(name, []).extend(blockers)
        paths[name] = path
    if all_blockers:
        details = "；".join(f"{name}: {'、'.join(reasons)}" for name, reasons in all_blockers.items())
        raise ValueError(f"账号删除被拒绝：{details}")

    original_meta = load_accounts_meta(customer_id=int(customer.id), business_scope=scope)
    tombstones: dict[str, Path] = {}
    try:
        for name, path in paths.items():
            tombstone = root / f".deleting-{name}-{datetime.now(timezone.utc).timestamp():.0f}"
            path.rename(tombstone)
            tombstones[name] = tombstone

        meta = dict(original_meta)
        for name in clean_names:
            meta.pop(name, None)
        save_accounts_meta(meta, customer_id=int(customer.id), business_scope=scope)

        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        for name in clean_names:
            profile = remove_chrome_profile_references(
                profile,
                name=name,
                business_scope=scope,
            )
        customer.profile_json = profile
        flag_modified(customer, "profile_json")

        bindings = session.scalars(
            select(ReachMessageAccount).where(
                ReachMessageAccount.customer_id == int(customer.id),
                ReachMessageAccount.business_scope == scope,
                ReachMessageAccount.profile_name.in_(clean_names),
            )
        ).all()
        for binding in bindings:
            old_name = binding.profile_name
            binding.enabled = False
            binding.profile_name = f"deleted-{binding.id}-{old_name}"[:128]
            binding.updated_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        save_accounts_meta(
            original_meta,
            customer_id=int(customer.id),
            business_scope=scope,
        )
        for name, tombstone in tombstones.items():
            original = root / name
            if tombstone.exists() and not original.exists():
                tombstone.rename(original)
        raise

    cleanup_errors: list[str] = []
    for name, tombstone in tombstones.items():
        try:
            shutil.rmtree(tombstone)
        except OSError:
            cleanup_errors.append(name)
    return {
        "ok": True,
        "deleted": clean_names,
        "business_scope": scope,
        "message_bindings_disabled": len(bindings),
        "cleanup_pending": cleanup_errors,
        "metadata_path": str(
            accounts_json_path(customer_id=int(customer.id), business_scope=scope)
        ),
    }
