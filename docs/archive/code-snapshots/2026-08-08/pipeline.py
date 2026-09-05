from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, log_event
from engine.catalog.vector_index import index_cliplet
from engine.config.settings import AppSettings, load_settings
from engine.ingest.cliplet import create_cliplets_for_asset
from engine.ingest.proxy import make_proxy, proxy_dest_for_asset
from pathlib import Path

log = logging.getLogger("montage.ingest")


def ensure_proxy(session: Session, asset: Asset) -> bool:
    if asset.proxy_path and Path(asset.proxy_path).exists():
        return True
    storage = Path(asset.storage_path)
    if not storage.exists():
        return False
    dest = proxy_dest_for_asset(storage.parent)
    if make_proxy(storage, dest):
        asset.proxy_path = str(dest)
        meta = dict(asset.metadata_json or {})
        meta["has_proxy"] = True
        asset.metadata_json = meta
        session.commit()
        return True
    return False


def index_asset(
    session: Session,
    asset: Asset,
    *,
    use_vision: bool = False,
    force_cliplets: bool = False,
) -> dict[str, Any]:
    """Create cliplets + embeddings for one ready asset. Returns stats.

    Incremental by default: existing cliplets are kept (`force_cliplets=False`);
    only cliplets without embedding_json are embedded.
    """
    ensure_proxy(session, asset)
    cliplets = create_cliplets_for_asset(session, asset, force=force_cliplets, use_vision=use_vision)
    indexed = 0
    skipped_emb = 0
    for c in cliplets:
        if (getattr(c, "status", None) or "") == "rejected_blur":
            skipped_emb += 1
            continue
        if c.embedding_json is None:
            index_cliplet(session, c)
            if c.embedding_json is not None:
                indexed += 1
            else:
                skipped_emb += 1
        else:
            skipped_emb += 1
    return {
        "asset_id": asset.id,
        "cliplets": len(cliplets),
        "indexed": indexed,
        "embeddings_kept": skipped_emb,
        "mode": "rebuild" if force_cliplets else "incremental",
    }


def vectorization_gap_report(
    session: Session,
    *,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Count what incremental vectorization still needs to do (never implies full rebuild)."""
    ready_q = select(func.count()).select_from(Asset).where(Asset.status == "ready")
    if customer_id is not None:
        ready_q = ready_q.where(Asset.customer_id == customer_id)
    ready = int(session.scalar(ready_q) or 0)

    clip_count_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(Cliplet.asset_id == Asset.id)
        .correlate(Asset)
        .scalar_subquery()
    )
    pending_emb_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(
            Cliplet.asset_id == Asset.id,
            Cliplet.status == "usable",
            Cliplet.embedding_json.is_(None),
        )
        .correlate(Asset)
        .scalar_subquery()
    )
    gap_stmt = (
        select(func.count())
        .select_from(Asset)
        .where(Asset.status == "ready")
        .where((clip_count_sq == 0) | (pending_emb_sq > 0))
    )
    if customer_id is not None:
        gap_stmt = gap_stmt.where(Asset.customer_id == customer_id)
    gap_assets = int(session.scalar(gap_stmt) or 0)

    clip_q = select(func.count()).select_from(Cliplet).where(Cliplet.status == "usable")
    if customer_id is not None:
        clip_q = clip_q.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
    cliplets_total = int(session.scalar(clip_q) or 0)

    emb_q = select(func.count()).select_from(Cliplet).where(
        Cliplet.status == "usable", Cliplet.embedding_json.is_not(None)
    )
    if customer_id is not None:
        emb_q = emb_q.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
    cliplets_embedded = int(session.scalar(emb_q) or 0)

    pending_emb = max(0, cliplets_total - cliplets_embedded)
    complete_assets = max(0, ready - gap_assets)

    return {
        "mode": "incremental",
        "ready_assets": ready,
        "complete_assets": complete_assets,
        "gap_assets": gap_assets,
        "cliplets_total": cliplets_total,
        "cliplets_embedded": cliplets_embedded,
        "pending_embeddings": pending_emb,
        # New empty library → gap≈ready (natural full). Old library → gap≪ready.
        "profile": "new_library" if ready > 0 and complete_assets == 0 else (
            "caught_up" if gap_assets == 0 else "incremental_backfill"
        ),
    }


def reconcile_pending_assets(
    session: Session,
    settings: AppSettings | None = None,
    *,
    customer_id: int | None = None,
    limit: int = 20,
    use_vision: bool = False,
    mode: str = "incremental",
) -> dict[str, Any]:
    """
    Incremental vectorization only: process ready assets missing cliplets
    or missing embeddings. Never deletes / rebuilds existing vectors unless
    mode='rebuild' (explicit, not used by first-enable).
    """
    settings = settings or load_settings()
    force = mode == "rebuild"
    if mode not in ("incremental", "rebuild"):
        mode = "incremental"
        force = False

    gaps_before = vectorization_gap_report(session, customer_id=customer_id)

    # Prefer true gaps via SQL so we don't stall on "newest N already done"
    clip_count_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(Cliplet.asset_id == Asset.id)
        .correlate(Asset)
        .scalar_subquery()
    )
    pending_emb_sq = (
        select(func.count())
        .select_from(Cliplet)
        .where(
            Cliplet.asset_id == Asset.id,
            Cliplet.status == "usable",
            Cliplet.embedding_json.is_(None),
        )
        .correlate(Asset)
        .scalar_subquery()
    )
    stmt = (
        select(Asset)
        .where(Asset.status == "ready")
        .where((clip_count_sq == 0) | (pending_emb_sq > 0))
        .order_by(Asset.id.asc())  # oldest gaps first — drain the backlog
        .limit(limit)
    )
    if customer_id is not None:
        stmt = stmt.where(Asset.customer_id == customer_id)
    assets = list(session.scalars(stmt).all())

    processed = 0
    created = 0
    indexed = 0
    kept = 0
    failed = 0
    errors: list[str] = []

    for asset in assets:
        clip_count = session.scalar(
            select(func.count()).select_from(Cliplet).where(Cliplet.asset_id == asset.id)
        ) or 0
        try:
            result = index_asset(
                session, asset, use_vision=use_vision, force_cliplets=force
            )
            meta = dict(asset.metadata_json or {})
            meta.pop("index_pending", None)
            meta.pop("index_error", None)
            asset.metadata_json = meta
            session.commit()

            created += int(result.get("cliplets", 0)) if clip_count == 0 else 0
            indexed += int(result.get("indexed", 0))
            kept += int(result.get("embeddings_kept", 0))
            processed += 1
            log.info(
                "reconcile[%s] asset=%s cliplets=%s indexed=%s kept=%s",
                mode,
                asset.id,
                result.get("cliplets"),
                result.get("indexed"),
                result.get("embeddings_kept"),
            )
        except Exception as e:
            failed += 1
            processed += 1
            msg = f"asset {asset.id}: {type(e).__name__}: {e}"
            errors.append(msg)
            log.exception("reconcile failed for asset %s", asset.id)
            meta = dict(asset.metadata_json or {})
            meta["index_error"] = str(e)[:500]
            meta["index_pending"] = True
            asset.metadata_json = meta
            session.commit()

    gaps_after = vectorization_gap_report(session, customer_id=customer_id)
    return {
        "mode": mode,
        "processed": processed,
        "created_cliplets": created,
        "indexed": indexed,
        "embeddings_kept": kept,
        "failed": failed,
        "skipped": 0,
        "errors": errors[:10],
        "queued": len(assets),
        "gaps_before": gaps_before,
        "gaps_after": gaps_after,
    }
