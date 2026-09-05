#!/usr/bin/env python3
"""Repair assets mislabeled landscape due to double-rotation normalize.

DJI / iPhone Camera 竖拍 often stores coded 1920×1080 + Display Matrix 90°.
Older normalize_video applied transpose *on top of* ffmpeg autorotate, baking
sideways landscape pixels and writing orientation=landscape.

Usage (repo root, engine env):
  PYTHONPATH=. python3 scripts/repair_rotation_orientation.py
  PYTHONPATH=. python3 scripts/repair_rotation_orientation.py --limit 20 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select

from engine.catalog.db import Asset, Cliplet, VectorizationQueue, VectorizationRun, get_session
from engine.catalog.vectorization_runtime import enqueue_asset
from engine.config.settings import load_settings
from engine.ingest.metadata import (
    classify_orientation,
    display_size,
    ffprobe_metadata,
    normalize_video,
    parse_probe,
)
from engine.ingest.proxy import make_proxy, proxy_dest_for_asset

log = logging.getLogger("repair_rotation_orientation")


def _source_orientation(path: Path) -> tuple[str, dict]:
    meta = parse_probe(ffprobe_metadata(path))
    orient = classify_orientation(
        int(meta.get("width") or 0),
        int(meta.get("height") or 0),
        int(meta.get("rotation") or 0),
    )
    return orient, meta


def repair_one(session, settings, asset: Asset, *, force_renorm: bool) -> str:
    source = Path(asset.source_path or "")
    if not source.is_file():
        return "missing_source"

    src_orient, pre = _source_orientation(source)
    if src_orient not in {"portrait", "landscape"}:
        return f"skip_src_{src_orient}"

    stored_orient = str(asset.orientation or "").lower()
    need_label = stored_orient != src_orient
    need_pixels = False
    storage = Path(asset.storage_path) if asset.storage_path else None
    if storage and storage.is_file() and asset.status == "ready":
        nmeta = parse_probe(ffprobe_metadata(storage))
        nw, nh = display_size(
            int(nmeta.get("width") or 0),
            int(nmeta.get("height") or 0),
            int(nmeta.get("rotation") or 0),
        )
        n_orient = classify_orientation(nw, nh, 0)
        if n_orient != src_orient:
            need_pixels = True
    elif asset.status in {"pending", "ingesting", "failed"} and need_label:
        need_pixels = False
    elif asset.status == "ready" and need_label:
        need_pixels = True

    if not need_label and not need_pixels:
        return "ok"

    # Pending / incomplete: orient only; next full ingest renorms under fixed code.
    if asset.status != "ready" or not force_renorm and not need_pixels:
        asset.orientation = src_orient
        dw, dh = display_size(
            int(pre.get("width") or 0),
            int(pre.get("height") or 0),
            int(pre.get("rotation") or 0),
        )
        if dw and dh:
            asset.width = dw
            asset.height = dh
        meta = dict(asset.metadata_json or {})
        meta.update(
            {
                **pre,
                "orientation": src_orient,
                "source_orientation": src_orient,
                "source_rotation": pre.get("rotation"),
                "repair_rotation_orientation": True,
            }
        )
        asset.metadata_json = meta
        session.commit()
        return "label_only"

    # Ready: force re-normalize to upright pixels.
    if not storage:
        storage_dir = settings.paths.cache_root / "library" / src_orient / asset.uuid
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage = storage_dir / "normalized.mp4"
        asset.storage_path = str(storage)
    else:
        storage_dir = storage.parent

    if not normalize_video(source, storage):
        return "normalize_failed"

    proxy = proxy_dest_for_asset(storage_dir)
    proxy_ok = make_proxy(storage, proxy)
    nmeta = parse_probe(ffprobe_metadata(storage))
    store_w, store_h = display_size(
        int(nmeta.get("width") or 0),
        int(nmeta.get("height") or 0),
        int(nmeta.get("rotation") or 0),
    )
    post_orient = classify_orientation(store_w, store_h, 0)
    if post_orient != src_orient:
        # Still wrong pixels: keep source label but flag (should be rare with fixed normalize)
        log.warning(
            "asset %s post-norm %s != src %s (%sx%s)",
            asset.id,
            post_orient,
            src_orient,
            store_w,
            store_h,
        )

    asset.orientation = src_orient
    asset.width = store_w or asset.width
    asset.height = store_h or asset.height
    asset.proxy_path = str(proxy) if proxy_ok else asset.proxy_path
    asset.duration_sec = nmeta.get("duration_sec") or asset.duration_sec
    asset.fps = nmeta.get("fps") or asset.fps
    asset.has_audio = bool(nmeta.get("has_audio", asset.has_audio))
    meta = dict(asset.metadata_json or {})
    meta.update(
        {
            **nmeta,
            "orientation": src_orient,
            "source_orientation": src_orient,
            "source_rotation": pre.get("rotation"),
            "has_proxy": bool(proxy_ok),
            "repair_rotation_orientation": True,
            "index_pending": True,
        }
    )
    asset.metadata_json = meta

    # Drop cliplets built from sideways frames; vector loop will re-index gaps.
    session.execute(delete(Cliplet).where(Cliplet.asset_id == int(asset.id)))
    session.commit()

    q = session.scalar(
        select(VectorizationQueue).where(
            VectorizationQueue.customer_id == int(asset.customer_id or 0),
            VectorizationQueue.asset_id == int(asset.id),
        )
    )
    if q is not None:
        q.orientation = src_orient
        q.status = "QUEUED"
        q.run_id = None
        q.claim_token = None
        q.claimed_at = None
        q.error = None
        q.completed_at = None
        session.commit()
    else:
        enqueue_asset(session, asset)

    return "renormed"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Max assets to touch (0=all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--label-only",
        action="store_true",
        help="Only correct DB orientation; do not re-normalize",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Scan every asset (slow). Default: only orientation=landscape rows.",
    )
    args = ap.parse_args()

    settings = load_settings()
    session = get_session()
    # Focus on landscape-labeled rows (double-rotate bug bucket); optional probe ready portrait later via --all
    stmt = (
        select(Asset)
        .where(Asset.status.in_(("ready", "pending", "ingesting", "failed")))
        .order_by(Asset.id.asc())
    )
    if not args.all:
        stmt = stmt.where(Asset.orientation == "landscape")
    assets = list(session.scalars(stmt).all())
    touched = {"label_only": 0, "renormed": 0, "ok": 0, "other": 0}
    n = 0
    for asset in assets:
        source = Path(asset.source_path or "")
        if not source.is_file():
            continue
        src_orient, _ = _source_orientation(source)
        stored = str(asset.orientation or "").lower()
        if src_orient == stored and asset.status != "ready":
            touched["ok"] += 1
            continue
        if src_orient == stored and asset.status == "ready" and asset.storage_path:
            sto = Path(asset.storage_path)
            if sto.is_file():
                nmeta = parse_probe(ffprobe_metadata(sto))
                nw, nh = display_size(
                    int(nmeta.get("width") or 0),
                    int(nmeta.get("height") or 0),
                    int(nmeta.get("rotation") or 0),
                )
                if classify_orientation(nw, nh, 0) == src_orient:
                    touched["ok"] += 1
                    continue

        if args.limit and n >= args.limit:
            break
        n += 1
        if args.dry_run:
            log.info(
                "dry-run asset=%s status=%s stored=%s src=%s path=%s",
                asset.id,
                asset.status,
                stored,
                src_orient,
                source.name,
            )
            touched["other"] += 1
            continue

        force = not args.label_only
        result = repair_one(session, settings, asset, force_renorm=force)
        if result in touched:
            touched[result] += 1
        else:
            touched["other"] += 1
            log.info("asset %s -> %s", asset.id, result)
        if n % 10 == 0:
            log.info("progress touched=%s last_id=%s result=%s", n, asset.id, result)

    # Stop leftover landscape runs so auto tick focuses on portrait.
    if not args.dry_run:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        active = list(
            session.scalars(
                select(VectorizationRun).where(
                    VectorizationRun.status.in_(
                        ("IDLE", "RUNNING", "PAUSE_REQUESTED")
                    ),
                    VectorizationRun.orientation == "landscape",
                )
            ).all()
        )
        for run in active:
            run.status = "COMPLETED"
            run.finished_at = now
            run.updated_at = now
            log.info("closed landscape run %s", run.run_id or run.id)
        session.commit()

    print({"touched": n, **touched})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
