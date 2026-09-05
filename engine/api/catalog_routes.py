"""Customers, assets, keywords, templates, dry-run (zero-semantics extract)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from engine.api.scope import active_scope, resolve_job_customer
from engine.catalog.customer_scope import (
    customer_to_dict,
    get_or_create_customer,
    list_customers,
    resolve_customer,
    settings_with_customer_paths,
)
from engine.catalog.db import Asset, Customer, Template, get_session
from engine.catalog.keyword_pack import install_keyword_pack
from engine.config.settings import load_settings, save_settings
from engine.jobs.queue import TopicIntentRequest
from engine.runtime.pause_coordinator import assert_runtime_active
from engine.template.engine import build_plan, plan_to_dict

router = APIRouter(tags=["catalog"])


class CustomerCreate(BaseModel):
    name: str
    library_root: str
    output_root: str
    library_roots: list[str] = Field(default_factory=list)
    keyword_pack_path: str | None = None


class CustomerUpdate(BaseModel):
    library_root: str | None = None
    library_roots: list[str] | None = None
    output_root: str | None = None
    keyword_pack_path: str | None = None
    active: bool | None = None
    brand: dict[str, Any] | None = None
    expression: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    industry_pack: str | None = None


class CustomerActivate(BaseModel):
    name: str


class DryRunRequest(BaseModel):
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    content_category: str | None = None
    asset_category: str | None = None
    orientation: str = Field(default="portrait", pattern="^(portrait|landscape)$")
    customer_name: str | None = None
    seed: int | None = None
    strict_semantic_v1: bool = False
    rule_profile_id: int | None = None
    use_active_rule: bool = True
    rule_rotation: bool | None = None
    topic_intent: TopicIntentRequest | None = None


def _watcher():
    from engine.api import app as app_mod

    return getattr(app_mod, "watcher", None)


def _run_scan_background(limit: int | None) -> None:
    from engine.api.app import _run_scan_background as run_bg

    run_bg(limit)


@router.get("/customers")
def get_customers() -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        rows = list_customers(session)
        return [customer_to_dict(c) for c in rows]
    finally:
        session.close()


@router.post("/customers")
def post_customer(body: CustomerCreate) -> dict[str, Any]:
    session = get_session()
    try:
        row = get_or_create_customer(
            session,
            body.name,
            library_root=body.library_root,
            library_roots=body.library_roots,
            output_root=body.output_root,
        )
        if body.keyword_pack_path is not None:
            row.keyword_pack_path = body.keyword_pack_path
            session.commit()
            session.refresh(row)
        return customer_to_dict(row)
    finally:
        session.close()


@router.patch("/customers/{customer_id}")
def patch_customer(customer_id: int, body: CustomerUpdate) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.get(Customer, customer_id)
        if not row:
            raise HTTPException(404, "客户不存在")
        if body.library_root is not None:
            row.library_root = body.library_root
        if body.library_roots is not None:
            row.library_roots = body.library_roots
        if body.output_root is not None:
            row.output_root = body.output_root
        if body.keyword_pack_path is not None:
            row.keyword_pack_path = body.keyword_pack_path
        if body.active is not None:
            row.active = body.active
        if body.brand is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.render.logo import merge_brand_patch

            row.profile_json = merge_brand_patch(
                row.profile_json if isinstance(row.profile_json, dict) else {},
                body.brand,
            )
            flag_modified(row, "profile_json")
        if body.expression is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.pack.expression import resolve_expression_prefs

            profile = dict(row.profile_json) if isinstance(row.profile_json, dict) else {}
            prefs = resolve_expression_prefs(profile, overrides=body.expression)
            profile["expression"] = prefs
            row.profile_json = profile
            flag_modified(row, "profile_json")
        if body.review is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.catalog.review_auto import merge_review_prefs

            row.profile_json = merge_review_prefs(
                row.profile_json if isinstance(row.profile_json, dict) else {},
                body.review,
            )
            flag_modified(row, "profile_json")
        if body.industry_pack is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.pack.scene_tour_skeleton import resolve_industry_key

            pack = str(body.industry_pack).strip()
            if not pack:
                raise HTTPException(400, "industry_pack 不能为空")
            key = resolve_industry_key({"industry_pack": pack}) or pack
            profile = dict(row.profile_json) if isinstance(row.profile_json, dict) else {}
            profile["industry_pack"] = key
            profile["industry_key"] = key
            row.profile_json = profile
            flag_modified(row, "profile_json")
        session.commit()
        session.refresh(row)
        return customer_to_dict(row)
    finally:
        session.close()


@router.post("/customers/activate")
def activate_customer(body: CustomerActivate) -> dict[str, Any]:
    from engine.catalog.customer_scope import bind_active_customer_into_settings

    settings = load_settings()
    session = get_session()
    try:
        row = resolve_customer(session, body.name)
        if not row:
            raise HTTPException(404, f"客户不存在: {body.name}")
        # Absolute isolation: paths must follow the active customer, not the previous tenant.
        bind_active_customer_into_settings(settings, row)
        save_settings(settings)
        w = _watcher()
        if w:
            w.reload()
        scoped = settings_with_customer_paths(settings, row)
        return {
            "active_customer": row.name,
            "active_customer_id": row.id,
            "paths": scoped.paths.model_dump(mode="json"),
        }
    finally:
        session.close()


@router.get("/assets")
def list_assets(category: str | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        q = select(Asset).where(Asset.customer_id == customer.id).order_by(Asset.id.desc())
        if category:
            q = q.where(Asset.category == category)
        rows = session.scalars(q).all()
        return [
            {
                "id": a.id,
                "uuid": a.uuid,
                "customer_id": a.customer_id,
                "category": a.category,
                "status": a.status,
                "duration_sec": a.duration_sec,
                "width": a.width,
                "height": a.height,
                "orientation": getattr(a, "orientation", "unknown"),
                "source_path": a.source_path,
            }
            for a in rows
        ]
    finally:
        session.close()


@router.post("/assets/scan")
def scan_assets(limit: int | None = 20, background: bool = True) -> dict[str, Any]:
    """Scan libraries. Default limit=20; pass limit=0 for full scan.
    By default runs in a background thread so the API stays responsive.
    """
    import threading

    from engine.ops.scan_state import scan_state as _scan_state

    assert_runtime_active("assets_scan")
    watcher = _watcher()
    if not watcher:
        raise HTTPException(503, "Watcher not ready")
    if _scan_state.get("running"):
        return {"status": "already_running", **{k: _scan_state.get(k) for k in ("ingested", "limit", "started_at")}}
    real_limit = None if limit == 0 else limit
    if background:
        from engine.runtime.quiesce import clear_scan_cancel

        clear_scan_cancel()
        t = threading.Thread(target=_run_scan_background, args=(real_limit,), daemon=True, name="montage-scan")
        t.start()
        return {"status": "started", "limit": real_limit, "background": True}
    count = watcher.scan_existing(limit=real_limit)
    return {"ingested": count, "limit": real_limit, "status": "completed"}


@router.get("/assets/scan/status")
def scan_status() -> dict[str, Any]:
    from engine.ops.scan_state import scan_state as _scan_state

    return dict(_scan_state)


@router.post("/assets/reconcile")
def reconcile_assets(
    limit: int = 50,
    use_vision: bool = False,
    mode: str = "incremental",
) -> dict[str, Any]:
    """Compatibility entry: freeze a count run in the unified executor."""
    _ = use_vision
    assert_runtime_active("assets_reconcile")
    if mode == "rebuild":
        raise HTTPException(400, "统一向量执行器禁止破坏性 rebuild")
    from engine.api.vectorization_routes import (
        VectorizationRunCreate,
        vectorization_create_run,
    )

    return vectorization_create_run(
        VectorizationRunCreate(mode="count", count=max(1, min(limit, 100000)))
    )


@router.get("/assets/vectorization-status")
def vectorization_status() -> dict[str, Any]:
    """Compatibility alias for the durable executor status."""
    from engine.api.vectorization_routes import vectorization_status as current_status

    return current_status()


@router.post("/vectorization/enable")
def enable_vectorization_endpoint(
    kick_reconcile: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Compatibility entry; opens incremental mode and optionally freezes a batch."""
    assert_runtime_active("vector_reconcile")
    from engine.api.vectorization_routes import vectorization_enable_switch

    result = vectorization_enable_switch(
        kick_reconcile=kick_reconcile, limit=max(1, min(limit, 100000))
    )
    # legacy fields
    result.setdefault("mode", result.get("vectorization_mode") or "incremental")
    return result

@router.post("/assets/proxies/backfill")
def backfill_asset_proxies(limit: int = 500) -> dict[str, Any]:
    """Generate low-bitrate proxies for analysis on existing assets."""
    from engine.ingest.metadata import backfill_proxies

    assert_runtime_active("proxy_backfill")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return backfill_proxies(session, settings, limit=limit, customer_id=customer.id)
    finally:
        session.close()


@router.post("/keywords/import")
def import_keywords(customer_name: str, path: str) -> dict[str, Any]:
    from pathlib import Path

    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == customer_name))
        if not customer:
            raise HTTPException(404, "客户不存在")
        try:
            result = install_keyword_pack(session, customer, Path(path), source_kind="api")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        pack = result["pack"]
        return {
            "id": pack.id,
            "name": pack.name,
            "customer_id": pack.customer_id,
            "revision": pack.revision,
            "sha256": pack.content_sha256,
            "path": result["path"],
            "idempotent": result["idempotent"],
        }
    finally:
        session.close()


@router.post("/keywords/import-file")
async def import_keywords_file(customer_name: str = "default", file: UploadFile = File(...)) -> dict[str, Any]:
    import tempfile
    from pathlib import Path

    content = await file.read()
    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == customer_name))
        if not customer:
            raise HTTPException(404, "客户不存在")
        if not customer.keyword_pack_path:
            raise HTTPException(400, "请先配置该客户的唯一 canonical 词池目录")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            handle.write(content)
            tmp = Path(handle.name)
        try:
            result = install_keyword_pack(session, customer, tmp, source_kind="upload")
        finally:
            tmp.unlink(missing_ok=True)
        pack = result["pack"]
        return {
            "id": pack.id,
            "name": pack.name,
            "revision": pack.revision,
            "sha256": pack.content_sha256,
            "path": result["path"],
        }
    finally:
        session.close()


@router.get("/keywords/active-summary")
def keywords_active_summary(customer_id: int | None = None) -> dict[str, Any]:
    """Active keyword pack summary including on-screen title_pool."""
    from engine.catalog.keyword_pack import (
        get_active_pack,
        list_title_pool,
        refresh_canonical_pack,
        title_pool_meta,
    )

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            row = session.get(Customer, customer_id)
            if not row:
                raise HTTPException(404, "客户不存在")
            customer_name = row.name
            pack_path = row.keyword_pack_path
        else:
            _, row, _ = active_scope(session, settings)
            customer_name = row.name
            pack_path = row.keyword_pack_path
        load_result = refresh_canonical_pack(session, row)
        pack = get_active_pack(session, customer_name)
        if not pack:
            return {
                "customer": customer_name,
                "keyword_pack_path": pack_path,
                "loaded": False,
                "title_pool_count": 0,
                "title_pool_sample": [],
                "hooks_count": 0,
                "last_load_result": load_result,
            }
        titles = list_title_pool(pack)
        hooks = (pack.data_json.get("keyword_pool") or {}).get("hooks") or []
        meta = title_pool_meta(pack)
        return {
            "customer": customer_name,
            "keyword_pack_path": pack_path,
            "loaded": True,
            "pack_id": pack.id,
            "version": pack.version,
            "revision": pack.revision,
            "sha256": pack.content_sha256,
            "schema": pack.schema_version,
            "validation_report": pack.validation_report or {},
            "title_pool_count": len(titles),
            "title_pool_sample": titles[:40],
            "title_pool_meta": meta,
            "hooks_count": len(hooks) if isinstance(hooks, list) else 0,
            "max_chars_per_line": int(meta.get("max_chars_per_line") or 24),
            "last_load_result": load_result,
        }
    finally:
        session.close()


@router.post("/keywords/reload-from-path")
def reload_keywords_from_path(customer_id: int | None = None) -> dict[str, Any]:
    """Reload keyword pack from Customer.keyword_pack_path."""
    from pathlib import Path

    from engine.catalog.keyword_pack import list_title_pool

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            row = session.get(Customer, customer_id)
            if not row:
                raise HTTPException(404, "客户不存在")
        else:
            _, row, _ = active_scope(session, settings)
        path_str = row.keyword_pack_path
        if not path_str:
            raise HTTPException(400, "未配置词池路径 keyword_pack_path")
        path = Path(path_str)
        if not path.exists():
            raise HTTPException(400, f"词池文件不存在: {path}")
        try:
            result = install_keyword_pack(session, row, path, source_kind="canonical-refresh")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        pack = result["pack"]
        titles = list_title_pool(pack)
        return {
            "id": pack.id,
            "name": pack.name,
            "customer_id": pack.customer_id,
            "path": str(path),
            "version": pack.version,
            "revision": pack.revision,
            "sha256": pack.content_sha256,
            "idempotent": bool(result.get("idempotent")),
            "title_pool_count": len(titles),
        }
    finally:
        session.close()


@router.get("/keywords/history")
def keyword_history(customer_id: int | None = None) -> list[dict[str, Any]]:
    from engine.catalog.db import KeywordPack

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            customer = session.get(Customer, customer_id)
            if not customer:
                raise HTTPException(404, "客户不存在")
        else:
            _, customer, _ = active_scope(session, settings)
        rows = list(
            session.scalars(
                select(KeywordPack)
                .where(KeywordPack.customer_id == customer.id)
                .order_by(KeywordPack.revision.desc(), KeywordPack.id.desc())
            ).all()
        )
        return [
            {
                "id": row.id,
                "revision": row.revision,
                "sha256": row.content_sha256,
                "schema": row.schema_version,
                "status": row.status,
                "source_path": row.source_path,
                "source_kind": row.source_kind,
                "validation_report": row.validation_report or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        session.close()


@router.post("/keywords/rollback/{pack_id}")
def rollback_keyword_pack(pack_id: int, customer_id: int | None = None) -> dict[str, Any]:
    import tempfile

    from engine.catalog.db import KeywordPack

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            customer = session.get(Customer, customer_id)
            if not customer:
                raise HTTPException(404, "客户不存在")
        else:
            _, customer, _ = active_scope(session, settings)
        source = session.get(KeywordPack, pack_id)
        if not source or source.customer_id != customer.id:
            raise HTTPException(404, "历史词池不存在")
        if not customer.keyword_pack_path:
            raise HTTPException(400, "客户未配置 canonical 词池路径")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as handle:
            json.dump(source.data_json, handle, ensure_ascii=False, indent=2)
            tmp = Path(handle.name)
        try:
            result = install_keyword_pack(
                session,
                customer,
                tmp,
                source_kind=f"rollback:{source.id}",
                allow_downgrade=True,
            )
        finally:
            tmp.unlink(missing_ok=True)
        pack = result["pack"]
        return {"ok": True, "id": pack.id, "revision": pack.revision, "sha256": pack.content_sha256}
    finally:
        session.close()


@router.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    session = get_session()
    try:
        from engine.jobs.queue import ensure_default_template

        ensure_default_template(session)
        rows = session.scalars(select(Template)).all()
        if not rows:
            from engine.template.engine import BUILTIN_TEMPLATES

            return [t.model_dump() for t in BUILTIN_TEMPLATES.values()]
        return [{"name": t.name, "definition": t.definition_json, "active": t.active} for t in rows]
    finally:
        session.close()


@router.post("/dry-run")
def dry_run(body: DryRunRequest) -> dict[str, Any]:
    import random

    settings = load_settings()
    session = get_session()
    try:
        from engine.jobs.queue import ensure_default_template
        from engine.template.engine import (
            BUILTIN_TEMPLATES,
            DEFAULT_TEMPLATE,
            TemplateDefinition,
            template_for_theme,
        )
        from engine.template.rule_schema import apply_rules_to_template, resolve_job_inputs
        from engine.template.rule_store import (
            freeze_for_job,
            list_rotation_pool,
            pick_rule_for_job,
        )

        ensure_default_template(session)
        customer = resolve_job_customer(session, settings, body.customer_name)
        from engine.catalog.industry_pack import pack_id_for_customer
        from engine.jobs.job_categories import (
            normalize_job_categories,
            plan_category_for_cliplet_filter,
        )

        pack_id = pack_id_for_customer(customer.name, customer.profile_json)
        cats = normalize_job_categories(
            category=body.category,
            content_category=body.content_category,
            asset_category=body.asset_category,
        )
        content_category = cats["content_category"]
        asset_category = cats["asset_category"] or None

        if not body.use_active_rule:
            raise HTTPException(409, "预演必须使用当前启用规则")
        if body.rule_profile_id is not None:
            raise HTTPException(409, "预演禁止指定历史规则 ID")

        try:
            rule_row, picked_by = pick_rule_for_job(
                session,
                customer=customer,
                content_category=content_category,
                orientation=body.orientation,
                rotation=body.rule_rotation,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        pool_size = len(
            list_rotation_pool(
                session,
                customer_id=customer.id,
                orientation=body.orientation,
                content_category=content_category,
            )
        )

        from engine.catalog.keyword_pack import get_active_pack, resolve_rule_facet

        pack = get_active_pack(session, customer.name)
        try:
            facet_meta = resolve_rule_facet(
                pack,
                rule_row.effective_json if isinstance(rule_row.effective_json, dict) else {},
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        eff_rules = (
            rule_row.effective_json
            if rule_row and isinstance(rule_row.effective_json, dict)
            else None
        )
        want_strict = bool(body.strict_semantic_v1 or body.topic_intent is not None)
        if want_strict and not bool(getattr(settings, "strict_semantic_allowed", True)):
            raise HTTPException(409, "当前机型档位未开放严格语义生产（lite 请升配或补装视觉包）")
        resolved = resolve_job_inputs(
            theme=body.theme,
            category=content_category,
            template_name=body.template_name or "default-vertical",
            strict_semantic_v1=want_strict,
            rules=eff_rules,
        )
        tpl_name = resolved["template_name"]
        theme = resolved["theme"]
        category = content_category
        if tpl_name == "default-vertical":
            mapped = template_for_theme(theme, pack_id=pack_id)
            if mapped != tpl_name:
                tpl_name = mapped
        tpl_row = session.scalar(select(Template).where(Template.name == tpl_name))
        builtin = BUILTIN_TEMPLATES.get(tpl_name, DEFAULT_TEMPLATE)
        template = TemplateDefinition(
            **(tpl_row.definition_json if tpl_row else builtin.model_dump())
        )
        template = apply_rules_to_template(template, resolved["effective_rules"])
        if body.orientation == "landscape":
            template.output_width, template.output_height = 1920, 1080
        else:
            template.output_width, template.output_height = 1080, 1920
        seed = body.seed if body.seed is not None else random.randint(1, 2_000_000_000)
        frozen_topic = None
        if body.topic_intent is not None:
            from engine.catalog.semantic_ops import freeze_topic_intent

            try:
                frozen_topic = freeze_topic_intent(
                    session,
                    customer_id=customer.id,
                    **body.topic_intent.model_dump(),
                )
            except (ValueError, LookupError) as exc:
                raise HTTPException(409, str(exc)) from exc
        plan = build_plan(
            session,
            template,
            customer_name=customer.name,
            theme=theme,
            category=plan_category_for_cliplet_filter(content_category, asset_category or ""),
            seed=seed,
            strict_semantic_v1=bool(resolved["strict_semantic_v1"]),
            production_rules=resolved["effective_rules"],
            keyword_pack_id=pack.id if pack else None,
            topic_intent=frozen_topic,
            customer_id=customer.id,
            orientation=body.orientation,
            asset_category=asset_category,
        )
        out = plan_to_dict(plan)
        out["resolved_template"] = tpl_name
        out["content_category"] = content_category
        out["asset_category"] = asset_category
        out["production_rules"] = {
            "active": freeze_for_job(
                rule_row,
                picked_by=picked_by,
                rotation_pool_size=pool_size,
                facet_meta=facet_meta,
            ),
            "resolved": {
                "theme": theme,
                "category": category,
                "content_category": content_category,
                "asset_category": asset_category,
                "template_name": tpl_name,
                "orientation": body.orientation,
                "strict_semantic_v1": resolved["strict_semantic_v1"],
                "sources": resolved["sources"],
                "rejected": resolved["rejected"],
                "clamped": resolved["clamped"],
            },
        }
        out["topic_intent"] = frozen_topic
        return out
    finally:
        session.close()



