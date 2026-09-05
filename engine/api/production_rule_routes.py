"""GVideoRules API — production rule lab (parse / CRUD / approve / activate)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.catalog.db import get_session
from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
from engine.config.settings import load_settings
from engine.template.rule_parser import parse_production_rules_with_ollama, resolve_rule_model
from engine.template.rule_schema import (
    FIELD_SCHEMA,
    HARD_LOCK_READONLY,
    SCHEMA_METADATA,
    SCHEMA_VERSION,
    empty_rules,
    orientation_defaults,
    validate_and_clamp,
)
from engine.template.rule_store import (
    activate_rule,
    approve_rule,
    archive_rule,
    copy_to_draft,
    create_draft,
    customer_rotation_enabled,
    delete_draft,
    drafts_from_pack,
    get_active_rule,
    get_rule,
    list_rotation_pool,
    list_rules,
    rule_to_dict,
    set_customer_rotation_enabled,
    set_rule_rotation,
    update_draft,
)

router = APIRouter(prefix="/production-rules", tags=["production-rules"])


def _scope():
    settings = load_settings()
    session = get_session()
    customer = require_active_customer(session, settings)
    scoped = settings_with_customer_paths(settings, customer)
    return settings, session, customer, scoped


class ParseIn(BaseModel):
    source_text: str = Field(min_length=4, max_length=8000)
    model: str = ""


class RuleIn(BaseModel):
    content_category: str = Field(default="default", min_length=1, max_length=128)
    orientation: Literal["portrait", "landscape"] = "portrait"
    name: str = "未命名规则"
    source_text: str = ""
    rules: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    parse_warnings: list[str] = Field(default_factory=list)


class RulePatch(BaseModel):
    name: str | None = None
    source_text: str | None = None
    rules: dict[str, Any] | None = None


class ApproveIn(BaseModel):
    approved_by: str = "operator"


class ValidateIn(BaseModel):
    rules: dict[str, Any] = Field(default_factory=dict)
    strict_semantic_v1: bool = False


class CopyIn(BaseModel):
    """GRuleLabOpt: optional target category/name when cloning to a draft."""

    content_category: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)


class RotationIn(BaseModel):
    enabled: bool


class DraftsFromPackIn(BaseModel):
    content_category: str = Field(default="default", max_length=128)
    orientation: Literal["portrait", "landscape"] = "portrait"
    facets: list[str] = Field(default_factory=list)
    force: bool = False


@router.get("/schema")
def production_rules_schema() -> dict[str, Any]:
    from engine.pack.fx_assets import schema_public_bundle

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "metadata": SCHEMA_METADATA,
        "empty_rules": empty_rules(),
        "orientation_defaults": orientation_defaults(),
        "fields": FIELD_SCHEMA,
        "hard_locks": HARD_LOCK_READONLY,
        "statuses": ["draft", "approved", "archived"],
        "recommended_content_categories": list(
            SCHEMA_METADATA.get("recommended_content_categories") or ["default", "premium"]
        ),
        "fx_assets": schema_public_bundle(),
    }


@router.post("/validate")
def production_rules_validate(body: ValidateIn) -> dict[str, Any]:
    report = validate_and_clamp(body.rules, job_strict_semantic=body.strict_semantic_v1)
    return {"ok": True, **report}


@router.post("/parse")
def production_rules_parse(body: ParseIn) -> dict[str, Any]:
    from engine.runtime.pause_coordinator import assert_runtime_active

    assert_runtime_active("production_rule_parse")
    settings, session, _, _ = _scope()
    try:
        model = (body.model or "").strip() or resolve_rule_model(settings)
        result = parse_production_rules_with_ollama(source_text=body.source_text, model=model)
        if not result.get("ok"):
            raise HTTPException(502, result.get("error") or "本地 AI 解析失败")
        return {"ok": True, "source_text": body.source_text, **result}
    finally:
        session.close()


@router.get("/content-facets")
def production_rules_content_facets() -> dict[str, Any]:
    from engine.catalog.keyword_pack import get_active_pack, list_content_facets

    _, session, customer, _ = _scope()
    try:
        pack = get_active_pack(session, customer.name)
        facets = list_content_facets(pack)
        return {
            "ok": True,
            "customer_id": customer.id,
            "pack_id": pack.id if pack else None,
            "pack_revision": int(pack.revision or pack.version or 1) if pack else None,
            "facets": facets,
        }
    finally:
        session.close()


@router.post("/drafts-from-pack")
def production_rules_drafts_from_pack(body: DraftsFromPackIn | None = None) -> dict[str, Any]:
    payload = body or DraftsFromPackIn()
    _, session, customer, _ = _scope()
    try:
        try:
            rows = drafts_from_pack(
                session,
                customer=customer,
                orientation=payload.orientation,
                content_category=payload.content_category,
                facets=payload.facets or None,
                force=payload.force,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "ok": True,
            "created": [rule_to_dict(r) for r in rows],
            "created_count": len(rows),
        }
    finally:
        session.close()


@router.get("")
def production_rules_list(
    content_category: str | None = None,
    orientation: Literal["portrait", "landscape"] = "portrait",
    include_archived: bool = False,
) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = list_rules(
            session,
            customer_id=customer.id,
            content_category=content_category,
            orientation=orientation,
            include_archived=include_archived,
        )
        categories = sorted({(row.content_category or "default") for row in list_rules(
            session,
            customer_id=customer.id,
            orientation=orientation,
            include_archived=True,
        )} | {"default"})
        active_by_category = {
            category: (active.id if (active := get_active_rule(
                session,
                customer_id=customer.id,
                content_category=category,
                orientation=orientation,
            )) else None)
            for category in categories
        }
        pool = list_rotation_pool(
            session, customer_id=customer.id, orientation=orientation
        )
        return {
            "ok": True,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "rules": [rule_to_dict(r) for r in rows],
            "active_id": active_by_category.get(content_category or "default"),
            "active_by_category": active_by_category,
            "categories": categories,
            "orientation": orientation,
            "rotation_policy": customer_rotation_enabled(customer),
            "rotation_pool": [
                {
                    "id": r.id,
                    "name": r.name,
                    "revision": r.revision,
                    "content_category": r.content_category or "default",
                }
                for r in pool
            ],
        }
    finally:
        session.close()


@router.get("/active")
def production_rules_active(
    content_category: str = "default",
    orientation: Literal["portrait", "landscape"] = "portrait",
) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_active_rule(
            session,
            customer_id=customer.id,
            content_category=content_category,
            orientation=orientation,
        )
        return {
            "ok": True,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "rule": rule_to_dict(row) if row else None,
        }
    finally:
        session.close()


@router.post("/rotation-policy")
def production_rules_rotation_policy(body: RotationIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        set_customer_rotation_enabled(session, customer, enabled=body.enabled)
        return {"ok": True, "rotation_policy": bool(body.enabled)}
    finally:
        session.close()


@router.get("/{rule_id}")
def production_rules_get(rule_id: int) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.post("")
def production_rules_create(body: RuleIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = create_draft(
            session,
            customer_id=customer.id,
            content_category=body.content_category,
            orientation=body.orientation,
            name=body.name,
            source_text=body.source_text,
            rules=body.rules,
            model=body.model,
            extra_warnings=body.parse_warnings,
        )
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.patch("/{rule_id}")
def production_rules_patch(rule_id: int, body: RulePatch) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        try:
            row = update_draft(
                session,
                row,
                name=body.name,
                source_text=body.source_text,
                rules=body.rules,
            )
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.post("/{rule_id}/approve")
def production_rules_approve(rule_id: int, body: ApproveIn | None = None) -> dict[str, Any]:
    body = body or ApproveIn()
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        try:
            row = approve_rule(session, row, approved_by=body.approved_by)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.post("/{rule_id}/activate")
def production_rules_activate(rule_id: int) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        try:
            row = activate_rule(session, row, customer)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.post("/{rule_id}/archive")
def production_rules_archive(rule_id: int) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        row = archive_rule(session, row, customer)
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.post("/{rule_id}/copy")
def production_rules_copy(rule_id: int, body: CopyIn | None = None) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        payload = body or CopyIn()
        copied = copy_to_draft(
            session,
            row,
            name=payload.name,
            content_category=payload.content_category,
        )
        return {"ok": True, "rule": rule_to_dict(copied)}
    finally:
        session.close()


@router.post("/{rule_id}/rotation")
def production_rules_set_rotation(rule_id: int, body: RotationIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        try:
            row = set_rule_rotation(session, row, enabled=body.enabled)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "rule": rule_to_dict(row)}
    finally:
        session.close()


@router.delete("/{rule_id}")
def production_rules_delete(rule_id: int) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = get_rule(session, customer_id=customer.id, rule_id=rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        try:
            delete_draft(session, row)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted_id": rule_id}
    finally:
        session.close()
