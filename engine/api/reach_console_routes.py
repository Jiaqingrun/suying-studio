"""Reach messages, chrome profiles, covers, queues (zero-semantics extract from app.py)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from engine.api.scope import active_scope
from engine.catalog.db import (
    ReachMessage,
    ReachMessageAccount,
    ReachMessageScan,
    ReachNotificationEvent,
    ReplyDraft,
    RenderOutput,
    get_session,
)
from engine.config.settings import load_settings, save_settings
from engine.runtime.pause_coordinator import assert_runtime_active

router = APIRouter(tags=["reach-console"])


class ReachEnqueueRequest(BaseModel):
    platform: str
    title: str = ""
    body: str = ""
    video_path: str = ""
    pack_dir: str | None = None
    output_id: int | None = None
    note: str = ""


class ReachStatusRequest(BaseModel):
    status: str
    note: str | None = None
    error: str | None = None


def scoped_output(session, output_id: int, customer_id: int) -> RenderOutput:
    from engine.api.output_scope import scoped_output as impl

    return impl(session, output_id, customer_id)


def output_cover_paths(out: RenderOutput) -> list[str]:
    from engine.api.output_scope import output_cover_paths as impl

    return impl(out)


@router.post("/reach/queue")
def reach_enqueue(body: ReachEnqueueRequest) -> dict[str, Any]:
    from engine.reach.queue import enqueue, item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            item = enqueue(
                session,
                customer_id=customer.id,
                platform=body.platform,
                title=body.title,
                body=body.body,
                video_path=body.video_path,
                pack_dir=body.pack_dir,
                output_id=body.output_id,
                note=body.note,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "item": item_to_dict(item), "auto_publish": False}
    finally:
        session.close()


@router.get("/reach/queue")
def reach_list(status: str | None = None, platform: str | None = None, limit: int = 100) -> dict[str, Any]:
    from engine.reach.queue import item_to_dict, list_items

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        rows = list_items(
            session,
            customer_id=customer.id,
            status=status,
            platform=platform,
            limit=limit,
        )
        return {
            "ok": True,
            "items": [item_to_dict(r) for r in rows],
            "human_in_loop": True,
            "auto_publish": False,
        }
    finally:
        session.close()


@router.get("/reach/queue/{item_id}")
def reach_get(item_id: int) -> dict[str, Any]:
    from engine.reach.queue import get_item, item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        return {"ok": True, "item": item_to_dict(item)}
    finally:
        session.close()


@router.post("/reach/queue/{item_id}/status")
def reach_set_status(item_id: int, body: ReachStatusRequest) -> dict[str, Any]:
    from engine.ops.publish_trail import append_publish_trail
    from engine.reach.queue import get_item, item_to_dict, set_status

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        try:
            item = set_status(session, item, body.status, note=body.note, error=body.error)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        trail: list[dict[str, Any]] = []
        retirement: dict[str, Any] = {}
        if body.status == "published" and item.output_id:
            out = scoped_output(session, item.output_id, customer.id)
            trail = append_publish_trail(
                out,
                platform=str(item.platform or ""),
                reach_item_id=item.id,
                note=body.note or "",
            )
            from engine.reach.publication_lifecycle import (
                bind_queue_item,
                claim_target_for_submission,
                record_target_outcome,
            )

            target = bind_queue_item(session, item, source="manual_confirmation")
            if target.status == "pending":
                claim_target_for_submission(
                    session,
                    group_id=int(item.publication_group_id or 0),
                    target_id=target.id,
                )
            retirement = record_target_outcome(
                session,
                group_id=int(item.publication_group_id or 0),
                target_id=target.id,
                outcome="published",
                evidence={"reach_item_id": item.id, "manual_confirmation": True},
                note=body.note or "",
            )
        return {
            "ok": True,
            "item": item_to_dict(item),
            "publish_trail": trail,
            "retirement": retirement,
        }
    finally:
        session.close()


class ReachFromPackRequest(BaseModel):
    pack_dir: str
    platforms: list[str] | None = None
    locale: str = "zh"
    output_id: int | None = None
    mark_ready: bool = True


@router.post("/reach/queue/from-pack")
def reach_enqueue_from_pack(body: ReachFromPackRequest) -> dict[str, Any]:
    """G5: prefill queue rows from a publish_pack directory (human still clicks publish)."""
    from pathlib import Path

    from engine.reach.prefill import enqueue_from_pack
    from engine.reach.queue import item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        pack_dir = Path(body.pack_dir).expanduser().resolve()
        root = Path(customer.output_root or settings.paths.output_root).expanduser().resolve()
        if not pack_dir.is_relative_to(root):
            raise HTTPException(403, "publish_pack 必须位于当前客户成片目录")
        if body.output_id:
            out = scoped_output(session, body.output_id, customer.id)
            if (
                out.state == "published"
                or (out.output_path and "published" in Path(out.output_path).parts)
            ):
                raise HTTPException(
                    400,
                    "该成片已归档到 published/，禁止再次入队；请从待发 ready 列表选择",
                )
        try:
            items = enqueue_from_pack(
                session,
                customer_id=customer.id,
                pack_dir=pack_dir,
                platforms=body.platforms,
                locale=body.locale,
                output_id=body.output_id,
                mark_ready=body.mark_ready,
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "ok": True,
            "count": len(items),
            "items": [item_to_dict(i) for i in items],
            "auto_publish": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


class ReachOpenRequest(BaseModel):
    dry_run: bool = False
    chrome_profile: str | None = None


class ReachChromeOpenRequest(BaseModel):
    name: str
    platform: str | None = None
    dry_run: bool = False


class ReachChromeSelectRequest(BaseModel):
    name: str
    platform: str | None = None


class ReachChromeCreateRequest(BaseModel):
    platform: str
    count: int = 1
    name_prefix: str | None = None


class ReachChromeRenameRequest(BaseModel):
    old_name: str
    new_name: str


class ReachChromeDeleteRequest(BaseModel):
    names: list[str]


@router.post("/reach/queue/{item_id}/open")
def reach_open_official(item_id: int, body: ReachOpenRequest | None = None) -> dict[str, Any]:
    """Open official creator URL + write paste card. Never auto-publishes."""
    from engine.reach.browser import prepare_and_open
    from engine.reach.queue import get_item, item_to_dict

    body = body or ReachOpenRequest()
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        try:
            result = prepare_and_open(
                session,
                item,
                profile=profile,
                dry_run=bool(body.dry_run),
                chrome_profile=body.chrome_profile,
                customer_id=customer.id,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        result["item"] = item_to_dict(item)
        return result
    finally:
        session.close()


@router.get("/reach/chrome-profiles")
def reach_chrome_profiles() -> dict[str, Any]:
    """List video-scope Chrome profiles for the active customer."""
    from engine.reach.browser import list_chrome_profiles, selected_chrome_profile_from_customer
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        listed = list_chrome_profiles(customer_id=customer.id, business_scope=SCOPE_VIDEO)
        selected, selected_platform = selected_chrome_profile_from_customer(
            profile, business_scope=SCOPE_VIDEO
        )
        return {
            **listed,
            "selected": selected,
            "selected_platform": selected_platform,
        }
    finally:
        session.close()


@router.post("/reach/chrome-profiles/create")
def reach_chrome_profiles_create(body: ReachChromeCreateRequest) -> dict[str, Any]:
    """Create N empty video Chrome profiles. Does not launch Chrome."""
    from engine.reach.browser import create_chrome_profiles, set_selected_chrome_profile
    from engine.reach.business_scope import SCOPE_VIDEO, assert_platform_in_scope
    from sqlalchemy.orm.attributes import flag_modified

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            assert_platform_in_scope(body.platform, SCOPE_VIDEO)
            result = create_chrome_profiles(
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
                platform=body.platform,
                count=int(body.count or 1),
                name_prefix=body.name_prefix,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        selected = result.get("selected")
        if selected:
            profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
            customer.profile_json = set_selected_chrome_profile(
                profile,
                name=str(selected),
                platform=body.platform,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
            flag_modified(customer, "profile_json")
            session.commit()
        return result
    finally:
        session.close()


@router.post("/reach/chrome-profiles/open")
def reach_chrome_profiles_open(body: ReachChromeOpenRequest) -> dict[str, Any]:
    """Open official entry in an isolated video Chrome user-data-dir."""
    from engine.reach.browser import entry_for_platform, open_chrome_profile, resolve_profile_platform
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            plat = resolve_profile_platform(
                body.name,
                body.platform,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
            entry = entry_for_platform(plat)
            info = open_chrome_profile(
                body.name,
                url=entry["url"],
                dry_run=bool(body.dry_run),
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "ok": True,
            "platform": plat,
            "label": entry["label"],
            "open": info,
            "business_scope": SCOPE_VIDEO,
            "auto_publish": False,
            "human_in_loop": True,
            "disclaimer": "仅打开本机 Chrome 独立配置 + 对应平台官方入口；登录与发布须本人完成。",
        }
    finally:
        session.close()


@router.post("/reach/chrome-profiles/select")
def reach_chrome_profiles_select(body: ReachChromeSelectRequest) -> dict[str, Any]:
    """Remember selected video Chrome profile on active customer."""
    from engine.reach.browser import list_chrome_profiles, resolve_profile_platform, set_selected_chrome_profile
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        try:
            plat = resolve_profile_platform(
                body.name,
                body.platform,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
            updated = set_selected_chrome_profile(
                profile,
                name=body.name,
                platform=plat,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        customer.profile_json = updated
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(customer, "profile_json")
        session.commit()
        session.refresh(customer)
        listed = list_chrome_profiles(customer_id=customer.id, business_scope=SCOPE_VIDEO)
        return {
            "ok": True,
            "selected": body.name,
            "platform": plat,
            "business_scope": SCOPE_VIDEO,
            "profiles": listed.get("profiles"),
            "root": listed.get("root"),
            "auto_publish": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


@router.post("/reach/chrome-profiles/rename")
def reach_chrome_profiles_rename(body: ReachChromeRenameRequest) -> dict[str, Any]:
    """Rename a video Chrome profile and keep customer/message bindings consistent."""
    from engine.reach.browser import (
        rename_chrome_profile,
        replace_chrome_profile_name,
    )
    from engine.reach.business_scope import SCOPE_VIDEO
    from sqlalchemy.orm.attributes import flag_modified

    settings = load_settings()
    session = get_session()
    renamed = False
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            result = rename_chrome_profile(
                body.old_name,
                body.new_name,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
            renamed = True
            profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
            customer.profile_json = replace_chrome_profile_name(
                profile,
                old_name=body.old_name,
                new_name=body.new_name,
                business_scope=SCOPE_VIDEO,
            )
            flag_modified(customer, "profile_json")
            rows = session.scalars(
                select(ReachMessageAccount).where(
                    ReachMessageAccount.customer_id == customer.id,
                    ReachMessageAccount.business_scope == SCOPE_VIDEO,
                    ReachMessageAccount.profile_name == body.old_name,
                )
            ).all()
            for row in rows:
                row.profile_name = body.new_name
                if row.display_name == body.old_name:
                    row.display_name = body.new_name
            session.commit()
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception:
            session.rollback()
            if renamed:
                try:
                    rename_chrome_profile(
                        body.new_name,
                        body.old_name,
                        customer_id=customer.id,
                        business_scope=SCOPE_VIDEO,
                    )
                except Exception:
                    pass
            raise
    finally:
        session.close()


@router.delete("/reach/chrome-profiles")
def reach_chrome_profiles_delete(body: ReachChromeDeleteRequest) -> dict[str, Any]:
    """Delete one or more video accounts after all safety gates pass."""
    from engine.reach.account_management import delete_chrome_accounts
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            return delete_chrome_accounts(
                session,
                customer,
                names=body.names,
                business_scope=SCOPE_VIDEO,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    finally:
        session.close()


class ReachAutoUploadStartRequest(BaseModel):
    chrome_profile: str | None = None
    queue_id: int | None = None
    platform: str | None = None
    accept_risk: bool = False
    timeout_sec: float = 300
    dry_run: bool = False


@router.post("/reach/auto-upload/start")
def reach_auto_upload_start(body: ReachAutoUploadStartRequest) -> dict[str, Any]:
    """G5.V: open Chrome profile → wait login ready → auto upload (douyin) or handoff."""
    from engine.reach.auto_upload import pick_queue_item, start_job
    from engine.reach.browser import resolve_profile_platform, selected_chrome_profile_from_customer
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.queue import item_to_dict

    assert_runtime_active("reach_auto_upload")
    if not body.accept_risk:
        raise HTTPException(400, "须 accept_risk=true（G5.V 风控自负）")

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        plat_hint = (body.platform or "").strip() or None
        chrome_name = (body.chrome_profile or "").strip()
        if not chrome_name:
            selected, _plat = selected_chrome_profile_from_customer(
                profile, business_scope=SCOPE_VIDEO
            )
            chrome_name = (selected or "").strip()
        if not chrome_name:
            raise HTTPException(400, "未指定 Chrome 配置名（请在 App 客户配置中绑定平台浏览器）")
        try:
            plat = resolve_profile_platform(
                chrome_name,
                plat_hint,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
            item = pick_queue_item(
                session,
                customer.id,
                body.queue_id,
                platform=plat,
            )
            # Queue item platform wins when explicit queue_id
            if body.queue_id is not None:
                plat = item.platform
            if chrome_name and plat and item.platform != plat:
                try:
                    resolved = resolve_profile_platform(
                        chrome_name, plat, customer_id=customer.id, business_scope=SCOPE_VIDEO
                    )
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                if resolved != item.platform:
                    raise HTTPException(
                        400,
                        f"账号 {chrome_name} 绑定平台 {resolved} 与队列项平台 {item.platform} 不匹配",
                    )
                plat = item.platform
            status = start_job(
                chrome_profile=chrome_name,
                queue_id=int(item.id),
                customer_id=customer.id,
                platform=plat,
                title=item.title or "",
                body=item.body or "",
                video_path=item.video_path or "",
                pack_dir=item.pack_dir,
                accept_risk=True,
                timeout_sec=float(body.timeout_sec or 300),
                dry_run=bool(body.dry_run),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        status["item"] = item_to_dict(item)
        return status
    finally:
        session.close()


@router.get("/reach/auto-upload/status")
def reach_auto_upload_status() -> dict[str, Any]:
    from engine.reach.auto_upload import get_status

    return get_status()


@router.post("/reach/auto-upload/cancel")
def reach_auto_upload_cancel() -> dict[str, Any]:
    from engine.reach.auto_upload import cancel_job

    return cancel_job()


@router.get("/reach/platforms")
def reach_platforms() -> dict[str, Any]:
    from engine.reach.browser import OFFICIAL_ENTRY
    from engine.reach.business_scope import VIDEO_PLATFORMS
    from engine.reach.cover_templates import DEFAULT_SLOT_COUNTS, PLATFORM_COVER_SPECS

    return {
        "ok": True,
        "platforms": [
            {
                "id": k,
                "label": v["label"],
                "url": v["url"],
                "short": v.get("short") or k,
                "cover_slots": int(DEFAULT_SLOT_COUNTS.get(k, 1)),
            }
            for k, v in OFFICIAL_ENTRY.items()
            if k in VIDEO_PLATFORMS
        ],
        "slot_specs": {p: [dict(s) for s in specs] for p, specs in PLATFORM_COVER_SPECS.items()},
        "auto_publish": False,
        "human_in_loop": True,
    }


class CoverTemplateCreateRequest(BaseModel):
    name: str = "未命名封面套"


class CoverTemplateRenameRequest(BaseModel):
    name: str


class CoverTemplateSelectRequest(BaseModel):
    template_id: str


class CoverTemplateSlotRequest(BaseModel):
    platform: str
    slot_index: int = 0
    source_path: str = ""


class CoverTemplateSeedRequest(BaseModel):
    pack_dir: str
    platforms: list[str] | None = None


class CoverResolveRequest(BaseModel):
    platform: str
    pack_dir: str | None = None
    template_id: str | None = None


@router.get("/reach/cover-templates")
def reach_cover_templates_list() -> dict[str, Any]:
    from engine.reach.cover_templates import list_templates, resolve_cover_store_for_settings

    settings = load_settings()
    return list_templates(resolve_cover_store_for_settings(settings))


@router.post("/reach/cover-templates")
def reach_cover_templates_create(body: CoverTemplateCreateRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import create_template, resolve_cover_store_for_settings

    settings = load_settings()
    store = resolve_cover_store_for_settings(settings)
    tpl = create_template(store, name=body.name)
    return {"ok": True, "template": tpl, "store": str(store)}


@router.post("/reach/cover-templates/{template_id}/rename")
def reach_cover_templates_rename(template_id: str, body: CoverTemplateRenameRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import rename_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        tpl = rename_template(resolve_cover_store_for_settings(settings), template_id, body.name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True, "template": tpl}


@router.delete("/reach/cover-templates/{template_id}")
def reach_cover_templates_delete(template_id: str) -> dict[str, Any]:
    from engine.reach.cover_templates import delete_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        return delete_template(resolve_cover_store_for_settings(settings), template_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/reach/cover-templates/select")
def reach_cover_templates_select(body: CoverTemplateSelectRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import select_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        return select_template(resolve_cover_store_for_settings(settings), body.template_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/reach/cover-templates/{template_id}/slot")
def reach_cover_templates_set_slot(template_id: str, body: CoverTemplateSlotRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, set_slot_image

    settings = load_settings()
    try:
        tpl = set_slot_image(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            platform=body.platform,
            slot_index=int(body.slot_index),
            source_path=body.source_path,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@router.post("/reach/cover-templates/{template_id}/slot/clear")
def reach_cover_templates_clear_slot(template_id: str, body: CoverTemplateSlotRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import clear_slot, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        tpl = clear_slot(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            platform=body.platform,
            slot_index=int(body.slot_index),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@router.post("/reach/cover-templates/{template_id}/seed")
def reach_cover_templates_seed(template_id: str, body: CoverTemplateSeedRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, seed_template_from_pack

    settings = load_settings()
    try:
        tpl = seed_template_from_pack(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            pack_dir=body.pack_dir,
            platforms=body.platforms,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@router.post("/reach/cover-templates/resolve")
def reach_cover_templates_resolve(body: CoverResolveRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, resolve_covers
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    settings = load_settings()
    store = resolve_cover_store_for_settings(settings)
    resolved = resolve_covers(
        store,
        platform=body.platform,
        pack_dir=body.pack_dir,
        template_id=body.template_id,
    )
    gate: dict[str, Any] | None = None
    if body.pack_dir:
        try:
            gate = require_publish_assets(
                platform=body.platform,
                pack_dir=body.pack_dir,
                data_root=store,
                template_id=body.template_id,
            )
        except PublishAssetsError as e:
            gate = {"ok": False, "error": str(e)}
    return {"ok": True, "resolve": resolved, "gate": gate, "store": str(store)}


class ReachMessageAccountCreate(BaseModel):
    platform: str
    profile_name: str = ""
    display_name: str = ""
    enabled: bool = True
    cooldown_sec: Literal[1800] = 1800
    message_url: str | None = None


class ReachMessageAccountPatch(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    cooldown_sec: Literal[1800] | None = None
    message_url: str | None = None


class ReachMessageScanRequest(BaseModel):
    account_id: int | None = None
    dry_run: bool = False


class ReachMessageOpenRequest(BaseModel):
    dry_run: bool = False


class ReachMessagesBulkReadRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    account_id: int | None = None
    platform: str | None = None
    kind: str | None = None


class ReachNtfyConfigUpdate(BaseModel):
    enabled: bool = False
    server_url: str
    topic: str
    auth_mode: Literal["none", "token", "basic"] = "none"
    token: str | None = Field(default=None, max_length=512)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    clear_token: bool = False
    clear_username: bool = False
    clear_password: bool = False


class ReachNotificationReport(BaseModel):
    message_id: int
    channel: Literal["app", "macos"]
    status: Literal["sent", "failed", "permission_denied"]
    detail: str = Field(default="", max_length=300)


@router.get("/reach/message-accounts")
def reach_message_accounts_list() -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.message_sync import account_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        rows = session.scalars(
            select(ReachMessageAccount)
            .where(
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == SCOPE_VIDEO,
                ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
            )
            .order_by(ReachMessageAccount.id)
        ).all()
        accounts: list[dict[str, Any]] = []
        from engine.reach.message_sync import is_active_message_account

        for row in rows:
            if not is_active_message_account(row):
                continue
            item = account_to_dict(row)
            latest = session.scalar(
                select(ReachMessageScan)
                .where(ReachMessageScan.account_id == row.id)
                .order_by(ReachMessageScan.id.desc())
                .limit(1)
            )
            item["last_status"] = latest.status if latest else None
            item["last_error"] = latest.error if latest and latest.error else None
            item["last_error_code"] = (
                (latest.error_code or None) if latest else None
            )
            accounts.append(item)
        return {"ok": True, "accounts": accounts, "business_scope": SCOPE_VIDEO}
    finally:
        session.close()


@router.post("/reach/message-accounts")
def reach_message_accounts_create(body: ReachMessageAccountCreate) -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.message_sync import account_to_dict, create_message_account

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            row, profile = create_message_account(
                session,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
                platform=body.platform,
                profile_name=body.profile_name,
                display_name=body.display_name,
                enabled=body.enabled,
                message_url=body.message_url or "",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session.commit()
        session.refresh(row)
        return {
            "ok": True,
            "account": account_to_dict(row),
            "chrome_profile": (profile.get("created") or [None])[0],
        }
    finally:
        session.close()


@router.patch("/reach/message-accounts/{account_id}")
def reach_message_accounts_patch(
    account_id: int, body: ReachMessageAccountPatch
) -> dict[str, Any]:
    from engine.reach.message_sync import account_to_dict
    from engine.reach.messages_adapters import validate_official_url

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        row = session.scalar(
            select(ReachMessageAccount).where(
                ReachMessageAccount.id == account_id,
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == "video",
            )
        )
        if not row:
            raise HTTPException(404, "消息账号不存在")
        if body.display_name is not None:
            row.display_name = body.display_name[:128]
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.cooldown_sec is not None:
            row.cooldown_sec = 1800
        if body.message_url is not None:
            try:
                row.message_url = validate_official_url(row.platform, body.message_url)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        from datetime import datetime, timezone

        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "account": account_to_dict(row)}
    finally:
        session.close()


class ReachMessageAccountDelete(BaseModel):
    ids: list[int] = Field(default_factory=list, min_length=1)


@router.delete("/reach/message-accounts")
def reach_message_accounts_delete(body: ReachMessageAccountDelete) -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.message_sync import delete_message_accounts

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            return delete_message_accounts(
                session,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
                account_ids=body.ids,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/reach/message-accounts/{account_id}/open")
def reach_message_account_open(
    account_id: int, body: ReachMessageOpenRequest | None = None
) -> dict[str, Any]:
    """Open the account's official inbox, never the publishing home page."""
    from engine.reach.browser import open_chrome_profile
    from engine.reach.business_scope import SCOPE_VIDEO
    from engine.reach.messages_adapters import canonical_message_url, validate_official_url

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        account = session.scalar(
            select(ReachMessageAccount).where(
                ReachMessageAccount.id == account_id,
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == SCOPE_VIDEO,
                ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
            )
        )
        if not account:
            raise HTTPException(404, "视频消息账号不存在")
        try:
            url = validate_official_url(
                account.platform,
                account.message_url or canonical_message_url(account.platform),
            )
            opened = open_chrome_profile(
                account.profile_name,
                url=url,
                dry_run=bool(body.dry_run) if body else False,
                customer_id=customer.id,
                business_scope=SCOPE_VIDEO,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "opened": opened, "message_url": url}
    finally:
        session.close()


@router.post("/reach/messages/scan")
def reach_messages_scan(body: ReachMessageScanRequest) -> dict[str, Any]:
    from engine.reach.message_sync import message_sync

    assert_runtime_active("message_scan")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        query = select(ReachMessageAccount.id).where(
            ReachMessageAccount.customer_id == customer.id,
            ReachMessageAccount.business_scope == "video",
            ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
            ReachMessageAccount.enabled.is_(True),
        )
        if body.account_id is not None:
            query = query.where(ReachMessageAccount.id == body.account_id)
        account_ids = list(session.scalars(query).all())
        if body.account_id is not None and not account_ids:
            raise HTTPException(404, "消息账号不存在或未启用")
        if not account_ids:
            raise HTTPException(400, "当前客户没有已启用的消息账号")
        return {"ok": True, **message_sync.enqueue(account_ids, dry_run=body.dry_run)}
    finally:
        session.close()


@router.get("/reach/messages/status")
def reach_messages_status() -> dict[str, Any]:
    from engine.reach.message_sync import message_sync

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        scans = session.scalars(
            select(ReachMessageScan)
            .where(
                ReachMessageScan.customer_id == customer.id,
                ReachMessageScan.account_id.in_(
                    select(ReachMessageAccount.id).where(
                        ReachMessageAccount.customer_id == customer.id,
                        ReachMessageAccount.business_scope == "video",
                    )
                ),
            )
            .order_by(ReachMessageScan.id.desc())
            .limit(20)
        ).all()
        worker_status = message_sync.status()
        worker_account_id = worker_status.get("account_id")
        if worker_account_id is not None:
            owned = session.scalar(
                select(ReachMessageAccount.id).where(
                    ReachMessageAccount.id == worker_account_id,
                    ReachMessageAccount.customer_id == customer.id,
                )
            )
            if owned is None:
                worker_status = {
                    **worker_status,
                    "account_id": None,
                    "platform": None,
                    "task_id": None,
                    "error": None,
                    "error_code": None,
                    "phase": "other_customer_active" if worker_status.get("active") else "idle",
                }
        return {
            "ok": True,
            "worker": worker_status,
            "scans": [
                {
                    "id": row.id,
                    "account_id": row.account_id,
                    "status": row.status,
                    "source": row.source,
                    "found_count": row.found_count,
                    "error_code": row.error_code,
                    "error": row.error,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                }
                for row in scans
            ],
        }
    finally:
        session.close()


@router.post("/reach/messages/cancel")
def reach_messages_cancel() -> dict[str, Any]:
    from engine.reach.message_sync import message_sync

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        account_ids = set(
            session.scalars(
                select(ReachMessageAccount.id).where(
                    ReachMessageAccount.customer_id == customer.id,
                    ReachMessageAccount.business_scope == "video",
                    ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                )
            ).all()
        )
        return {"ok": True, **message_sync.cancel(account_ids)}
    finally:
        session.close()


@router.get("/reach/messages")
def reach_messages_list(
    account_id: int | None = None,
    unread: bool | None = True,
    history: bool = False,
    platform: str | None = None,
    kind: str | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    from sqlalchemy import func

    from engine.reach.message_sync import message_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        query = select(ReachMessage).where(
            ReachMessage.customer_id == customer.id,
            ReachMessage.business_scope == "video",
            ReachMessage.purged_at.is_(None),
        )
        if account_id is not None:
            query = query.where(ReachMessage.account_id == account_id)
        if not history and unread is not None:
            query = query.where(ReachMessage.unread.is_(unread))
        if platform:
            query = query.where(ReachMessage.platform == platform.strip().lower())
        if kind:
            query = query.where(ReachMessage.kind == kind.strip().lower())
        if before_id is not None:
            query = query.where(ReachMessage.id < before_id)
        rows = session.scalars(
            query.order_by(
                func.coalesce(
                    ReachMessage.platform_event_at,
                    ReachMessage.last_seen_at,
                    ReachMessage.first_seen_at,
                ).desc(),
                ReachMessage.id.desc(),
            ).limit(max(1, min(limit, 500)))
        ).all()
        unread_query = (
            select(func.count())
            .select_from(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == "video",
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
        )
        if account_id is not None:
            unread_query = unread_query.where(ReachMessage.account_id == account_id)
        if platform:
            unread_query = unread_query.where(
                ReachMessage.platform == platform.strip().lower()
            )
        if kind:
            unread_query = unread_query.where(ReachMessage.kind == kind.strip().lower())
        unread_count = session.scalar(unread_query)
        return {
            "ok": True,
            "messages": [message_to_dict(row) for row in rows],
            "unread_count": int(unread_count or 0),
            "next_before_id": rows[-1].id if len(rows) >= max(1, min(limit, 500)) else None,
        }
    finally:
        session.close()


@router.post("/reach/messages/{message_id}/read")
def reach_messages_mark_read(message_id: int) -> dict[str, Any]:
    from datetime import datetime, timezone
    from engine.reach.message_sync import message_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        row = session.scalar(
            select(ReachMessage).where(
                ReachMessage.id == message_id,
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == "video",
            )
        )
        if not row:
            raise HTTPException(404, "消息不存在")
        row.unread = False
        row.read_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "message": message_to_dict(row)}
    finally:
        session.close()


@router.post("/reach/messages/read-all")
@router.post("/reach/messages/bulk-read")
def reach_messages_bulk_read(body: ReachMessagesBulkReadRequest) -> dict[str, Any]:
    from sqlalchemy import func, update

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        query = (
            update(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == "video",
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
            .values(unread=False, read_at=datetime.now(timezone.utc))
        )
        ids = sorted({int(value) for value in body.ids if int(value) > 0})
        if ids:
            query = query.where(ReachMessage.id.in_(ids))
        elif body.account_id is not None:
            query = query.where(ReachMessage.account_id == body.account_id)
        if body.platform:
            query = query.where(ReachMessage.platform == body.platform.strip().lower())
        if body.kind:
            query = query.where(ReachMessage.kind == body.kind.strip().lower())
        result = session.execute(query)
        session.commit()
        unread_count = session.scalar(
            select(func.count())
            .select_from(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == "video",
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
        )
        return {
            "ok": True,
            "updated": int(result.rowcount or 0),
            "unread_count": int(unread_count or 0),
        }
    finally:
        session.close()


class ReachReplyDraftIn(BaseModel):
    message_id: int
    extra_context: str = ""
    brand: str = ""


@router.post("/reach/reply-drafts")
def reach_reply_drafts_create(body: ReachReplyDraftIn) -> dict[str, Any]:
    from engine.content.reply_drafts import create_reply_drafts_for_message
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        brand = body.brand or str((profile.get("brand") or {}).get("display_name") or customer.name)
        try:
            drafts = create_reply_drafts_for_message(
                session,
                customer_id=customer.id,
                message_id=body.message_id,
                brand=brand,
                extra_context=body.extra_context,
                business_scope=SCOPE_VIDEO,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "drafts": drafts,
            "business_scope": SCOPE_VIDEO,
            "warning": "基于脱敏摘要生成，上下文可能不完整；不会自动发送",
        }
    finally:
        session.close()


@router.post("/reach/reply-drafts/{draft_id}/copied")
def reach_reply_draft_copied(draft_id: int) -> dict[str, Any]:
    from datetime import datetime, timezone

    from engine.content.store import reply_draft_to_dict
    from engine.reach.business_scope import SCOPE_VIDEO

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        row = session.scalar(
            select(ReplyDraft).where(
                ReplyDraft.id == draft_id,
                ReplyDraft.customer_id == customer.id,
                ReplyDraft.business_scope == SCOPE_VIDEO,
            )
        )
        if not row:
            raise HTTPException(404, "回复草稿不存在")
        row.status = "copied"
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "draft": reply_draft_to_dict(row)}
    finally:
        session.close()


@router.post("/reach/notifications/claim")
def reach_notifications_claim(limit: int = 20) -> dict[str, Any]:
    """Claim each unread summary once for local macOS notification delivery."""
    from datetime import datetime, timezone
    from engine.reach.message_sync import message_to_dict, notification_claim_lock

    with notification_claim_lock:
        settings = load_settings()
        session = get_session()
        try:
            _, customer, _ = active_scope(session, settings)
            rows = session.scalars(
                select(ReachMessage)
                .where(
                    ReachMessage.customer_id == customer.id,
                    ReachMessage.business_scope == "video",
                    ReachMessage.unread.is_(True),
                    ReachMessage.notification_claimed_at.is_(None),
                )
                .order_by(ReachMessage.first_seen_at)
                .limit(max(1, min(limit, 100)))
            ).all()
            claimed_at = datetime.now(timezone.utc)
            for row in rows:
                row.notification_claimed_at = claimed_at
            session.commit()
            return {"ok": True, "messages": [message_to_dict(row) for row in rows]}
        finally:
            session.close()


@router.get("/reach/notifications/ntfy")
def reach_notifications_ntfy_get() -> dict[str, Any]:
    from dataclasses import asdict

    from engine.reach.notifications import public_config

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return {"ok": True, "config": asdict(public_config(customer.id))}
    finally:
        session.close()


@router.put("/reach/notifications/ntfy")
def reach_notifications_ntfy_put(body: ReachNtfyConfigUpdate) -> dict[str, Any]:
    from dataclasses import asdict

    from engine.reach.notifications import save_config

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        try:
            config = save_config(customer.id, body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "config": asdict(config)}
    finally:
        session.close()


@router.post("/reach/notifications/ntfy/test")
def reach_notifications_ntfy_test() -> dict[str, Any]:
    from engine.reach.notifications import send_ntfy

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        event = ReachNotificationEvent(
            customer_id=customer.id,
            message_id=None,
            channel="ntfy",
            status="sending",
            is_test=True,
        )
        session.add(event)
        session.commit()
        try:
            result = send_ntfy(
                customer.id,
                title="速影 ntfy 测试通知",
                summary="这是测试通知，不代表收到真实平台消息。",
                reply_url="",
                test=True,
            )
            event.status = str(result.get("status") or "sent")
            event.detail = "test"
            session.commit()
            return {"ok": True, "sent": bool(result.get("sent")), "test": True}
        except Exception as exc:  # noqa: BLE001
            event.status = "failed"
            event.detail = str(exc)[:300]
            session.commit()
            raise HTTPException(502, str(exc)) from exc
    finally:
        session.close()


@router.post("/reach/notifications/report")
def reach_notifications_report(body: ReachNotificationReport) -> dict[str, Any]:
    from datetime import datetime, timezone

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        message = session.scalar(
            select(ReachMessage).where(
                ReachMessage.id == body.message_id,
                ReachMessage.customer_id == customer.id,
            )
        )
        if message is None:
            raise HTTPException(404, "消息不存在")
        event = session.scalar(
            select(ReachNotificationEvent).where(
                ReachNotificationEvent.customer_id == customer.id,
                ReachNotificationEvent.message_id == message.id,
                ReachNotificationEvent.channel == body.channel,
            )
        )
        if event is None:
            event = ReachNotificationEvent(
                customer_id=customer.id,
                message_id=message.id,
                channel=body.channel,
                status=body.status,
                is_test=False,
            )
            session.add(event)
        event.status = body.status
        event.detail = body.detail[:300]
        event.updated_at = datetime.now(timezone.utc)
        session.commit()
        return {"ok": True}
    finally:
        session.close()


@router.get("/reach/notifications/events")
def reach_notifications_events(limit: int = 50) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        rows = session.scalars(
            select(ReachNotificationEvent)
            .where(ReachNotificationEvent.customer_id == customer.id)
            .order_by(ReachNotificationEvent.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
        return {
            "ok": True,
            "events": [
                {
                    "id": row.id,
                    "message_id": row.message_id,
                    "channel": row.channel,
                    "status": row.status,
                    "is_test": row.is_test,
                    "detail": row.detail,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in rows
            ],
        }
    finally:
        session.close()


@router.post("/reach/messages/{message_id}/open")
def reach_messages_open(
    message_id: int, body: ReachMessageOpenRequest | None = None
) -> dict[str, Any]:
    """Open only the saved official reply URL; never fills or sends a reply."""
    from engine.reach.browser import open_chrome_profile
    from engine.reach.chrome_runtime import ChromeRuntimeError, operation
    from engine.reach.messages_adapters import validate_official_url

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        row = session.scalar(
            select(ReachMessage).where(
                ReachMessage.id == message_id,
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == "video",
            )
        )
        if not row:
            raise HTTPException(404, "消息不存在")
        account = session.get(ReachMessageAccount, row.account_id)
        if not account or account.customer_id != customer.id:
            raise HTTPException(404, "消息账号不存在")
        if getattr(account, "profile_role", "") == "archived":
            raise HTTPException(409, "该消息账号已归档，只能查看历史摘要")
        try:
            url = validate_official_url(row.platform, row.reply_url)
            with operation("message_open"):
                opened = open_chrome_profile(
                    account.profile_name,
                    url=url,
                    dry_run=bool(body.dry_run) if body else False,
                    customer_id=customer.id,
                    business_scope=getattr(account, "business_scope", None) or "video",
                )
        except (ValueError, ChromeRuntimeError) as exc:
            raise HTTPException(409 if isinstance(exc, ChromeRuntimeError) else 400, str(exc)) from exc
        return {
            "ok": True,
            "opened": opened,
            "auto_reply": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


@router.get("/reach/inbox")
def reach_inbox(limit: int = 50) -> dict[str, Any]:
    """G5 message hub: local unread summaries + official deep links (no auto-reply)."""
    from engine.reach.inbox import build_inbox

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return build_inbox(session, customer_id=customer.id, limit=limit)
    finally:
        session.close()



