from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset
from engine.config.settings import AppSettings
from engine.catalog.customer_scope import require_active_customer
from engine.ingest.orientation import (
    ASSET_STATUS_REJECTED_ORIENTATION,
    ORIENTATION_RATIO_LANDSCAPE_MIN,
    ORIENTATION_RATIO_PORTRAIT_MAX,
    classify_orientation,
    display_size,
    evaluate_orientation_audit,
    extract_rotation,
)
from engine.ingest.proxy import make_proxy, proxy_dest_for_asset
import logging

log = logging.getLogger("montage.ingest")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def ffprobe_metadata(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout or "{}")


def parse_probe(probe: dict[str, Any]) -> dict[str, Any]:
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    width = int(video.get("width", 0)) if video else 0
    height = int(video.get("height", 0)) if video else 0
    fps = 0.0
    if video and video.get("avg_frame_rate"):
        num, _, den = video["avg_frame_rate"].partition("/")
        if den and float(den):
            fps = float(num) / float(den)
    # L16: single rotation extractor (Display Matrix / tags / matrix dump).
    rotation = extract_rotation(probe) if probe else 0
    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio is not None,
        "rotation": rotation,
    }


def is_landscape_video(width: int, height: int, rotation: int = 0) -> bool:
    """True when the video displays wider than tall (横屏)."""
    dw, dh = display_size(width, height, rotation)
    return dw > 0 and dh > 0 and dw > dh


# Re-export for callers/tests/smoke (L16 integrity)
__all__ = [
    "ORIENTATION_RATIO_LANDSCAPE_MIN",
    "ORIENTATION_RATIO_PORTRAIT_MAX",
    "VIDEO_EXTENSIONS",
    "classify_orientation",
    "display_size",
    "ffprobe_metadata",
    "ingest_file",
    "is_landscape_video",
    "normalize_video",
    "parse_probe",
]


def normalize_video(source: Path, dest: Path) -> bool:
    """Bake source to upright pixels.

    Modern ffmpeg applies container Display Matrix on decode (autorotate).
    Manual transpose on top of that *double-rotates* DJI/iPhone Camera clips
    (coded 1920×1080 + rotation 90 → upright 1080×1920, then transpose → wrong
    1920×1080 landscape). Rely on autorotate only; encode drops the matrix.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # scale even dims only; decoder already presents upright frames.
    vf_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        vf_filter,
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0 and dest.exists()


def category_from_path(library_root: Path, file_path: Path) -> str:
    try:
        rel = file_path.relative_to(library_root)
        parts = rel.parts
        # Prefer meaningful leaf folders like Camera / WeiXin over HWABR-Q wrapper
        meaningful = [p for p in parts[:-1] if p not in {"HWABR-Q", "HWABR-Q相册备份"}]
        if meaningful:
            return meaningful[-1]
        if len(parts) > 1:
            return parts[0]
    except ValueError:
        pass
    return "uncategorized"


def ingest_file(
    session: Session,
    settings: AppSettings,
    file_path: Path,
    library_root: Path | None = None,
    *,
    customer_id: int | None = None,
    defer_index: bool = False,
    use_vision: bool = False,
) -> Asset | None:
    """Ingest one video. Returns the new/updated Asset, or None if skipped/failed.

    Claims the source_path in DB as status=ingesting *before* ffmpeg runs, so
    concurrent scans/restarts cannot spawn duplicate normalize jobs.
    """
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    if not file_path.is_file():
        return None

    if customer_id is None:
        customer_id = require_active_customer(session, settings).id

    source_key = str(file_path.resolve())
    pre = parse_probe(ffprobe_metadata(file_path))
    orientation = classify_orientation(
        int(pre.get("width") or 0),
        int(pre.get("height") or 0),
        int(pre.get("rotation") or 0),
    )

    existing = session.scalar(
        select(Asset).where(Asset.source_path == source_key, Asset.customer_id == customer_id)
    )
    if existing and existing.status == "ready":
        return None
    if existing:
        # Resume interrupted and formerly landscape-rejected sources in place.
        asset = existing
        asset_uuid = asset.uuid
        storage_dir = Path(asset.storage_path).parent if asset.storage_path else (
            settings.paths.cache_root / "library" / orientation / asset_uuid
        )
        normalized = storage_dir / "normalized.mp4"
        asset.storage_path = str(normalized)
        asset.status = "ingesting"
        asset.orientation = orientation
        asset.width = pre.get("width")
        asset.height = pre.get("height")
        asset.metadata_json = {
            **dict(asset.metadata_json or {}),
            **pre,
            "orientation": orientation,
            "library_root": str(library_root or settings.paths.library_root),
        }
        session.commit()
    else:
        asset_uuid = str(uuid.uuid4())
        root = library_root or settings.paths.library_root
        category = category_from_path(root, file_path)
        storage_dir = settings.paths.cache_root / "library" / orientation / asset_uuid
        storage_dir.mkdir(parents=True, exist_ok=True)
        normalized = storage_dir / "normalized.mp4"
        asset = Asset(
            uuid=asset_uuid,
            customer_id=customer_id,
            source_path=source_key,
            storage_path=str(normalized),
            category=category,
            status="ingesting",
            orientation=orientation,
            metadata_json={"library_root": str(root), "orientation": orientation},
        )
        session.add(asset)
        try:
            session.commit()
            session.refresh(asset)
        except Exception:
            session.rollback()
            # Race: another worker claimed it
            existing2 = session.scalar(
                select(Asset).where(Asset.source_path == source_key, Asset.customer_id == customer_id)
            )
            if existing2 and existing2.status == "ready":
                return None
            log.exception("failed to claim ingest for %s", file_path)
            return None

    root = library_root or settings.paths.library_root
    storage_dir = Path(normalized).parent
    storage_dir.mkdir(parents=True, exist_ok=True)

    if not (normalized.exists() and normalized.stat().st_size > 0):
        ok = normalize_video(file_path, normalized)
        if not ok:
            asset.status = "failed"
            meta = dict(asset.metadata_json or {})
            meta["error"] = "normalize_failed"
            asset.metadata_json = meta
            session.commit()
            return None
    else:
        # Re-bake when an earlier double-rotate / missing matrix left sideways pixels.
        n_probe = parse_probe(ffprobe_metadata(normalized))
        n_orient = classify_orientation(
            *display_size(
                int(n_probe.get("width") or 0),
                int(n_probe.get("height") or 0),
                int(n_probe.get("rotation") or 0),
            ),
            0,
        )
        pre_orient_check = classify_orientation(
            int(pre.get("width") or 0),
            int(pre.get("height") or 0),
            int(pre.get("rotation") or 0),
        )
        if pre_orient_check in {"portrait", "landscape"} and n_orient != pre_orient_check:
            log.info(
                "re-normalize upright bake path=%s was=%s want=%s",
                file_path.name,
                n_orient,
                pre_orient_check,
            )
            ok = normalize_video(file_path, normalized)
            if not ok:
                asset.status = "failed"
                meta = dict(asset.metadata_json or {})
                meta["error"] = "normalize_failed"
                asset.metadata_json = meta
                session.commit()
                return None

    proxy = proxy_dest_for_asset(storage_dir)
    proxy_ok = make_proxy(normalized, proxy)
    proxy_path = str(proxy) if proxy_ok else None

    probe = parse_probe(ffprobe_metadata(normalized))
    # L16 HARD: dual consensus — source display orient + baked pixels (fail closed).
    store_w, store_h = display_size(
        int(probe.get("width") or 0),
        int(probe.get("height") or 0),
        int(probe.get("rotation") or 0),
    )
    audit = evaluate_orientation_audit(source_meta=pre, baked_meta=probe)
    if not audit.get("passed"):
        # One forced re-bake then re-audit (clears double-rotate / stale cache).
        log.warning(
            "orientation audit fail path=%s violations=%s; re-normalize once",
            file_path.name,
            audit.get("violations"),
        )
        if normalize_video(file_path, normalized):
            proxy_ok = make_proxy(normalized, proxy)
            proxy_path = str(proxy) if proxy_ok else proxy_path
            probe = parse_probe(ffprobe_metadata(normalized))
            store_w, store_h = display_size(
                int(probe.get("width") or 0),
                int(probe.get("height") or 0),
                int(probe.get("rotation") or 0),
            )
            audit = evaluate_orientation_audit(source_meta=pre, baked_meta=probe)

    orientation = str(audit.get("orientation") or "unknown")
    if not audit.get("passed") or orientation not in {"portrait", "landscape"}:
        log.info(
            "reject orientation asset_src=%s audit=%s path=%s",
            file_path.name,
            audit.get("violations"),
            file_path,
        )
        asset.status = ASSET_STATUS_REJECTED_ORIENTATION
        meta = dict(asset.metadata_json or {})
        meta.update(
            {
                **probe,
                "orientation": orientation,
                "library_root": str(root),
                "reject_reason": "orientation",
                "orientation_audit": audit,
                "source_rotation": pre.get("rotation"),
                "source_orientation": (audit.get("source") or {}).get("orientation"),
                "has_proxy": bool(proxy_ok),
            }
        )
        asset.metadata_json = meta
        asset.storage_path = str(normalized)
        asset.proxy_path = proxy_path
        asset.duration_sec = probe.get("duration_sec")
        asset.width = store_w
        asset.height = store_h
        asset.orientation = orientation if orientation in {"portrait", "landscape"} else "unknown"
        asset.fps = probe.get("fps")
        asset.has_audio = probe.get("has_audio", False)
        session.commit()
        return None

    # Authoritative display size + label from audit
    bake_ev = audit.get("baked") or {}
    store_w = int(bake_ev.get("display_width") or store_w or 0)
    store_h = int(bake_ev.get("display_height") or store_h or 0)

    # HARD: reject whole source if mostly out-of-focus / soft (QUALITY_LOCK)
    from engine.ingest.quality import ASSET_STATUS_REJECTED_BLUR, score_asset_focus

    focus = score_asset_focus(normalized, duration_sec=float(probe.get("duration_sec") or 0) or None)
    if focus.get("rejected_blur"):
        log.info(
            "reject blur asset=%s mean_lap=%s fail_ratio=%s path=%s",
            asset.id,
            focus.get("mean_laplacian"),
            focus.get("fail_ratio"),
            file_path,
        )
        asset.status = ASSET_STATUS_REJECTED_BLUR
        meta = dict(asset.metadata_json or {})
        meta.update(
            {
                **probe,
                "orientation": orientation,
                "library_root": str(root),
                "reject_reason": "blur",
                "focus_qa": focus,
                "orientation_audit": audit,
                "has_proxy": bool(proxy_ok),
            }
        )
        asset.metadata_json = meta
        asset.storage_path = str(normalized)
        asset.proxy_path = proxy_path
        asset.duration_sec = probe.get("duration_sec")
        asset.width = store_w
        asset.height = store_h
        asset.orientation = orientation
        asset.fps = probe.get("fps")
        asset.has_audio = probe.get("has_audio", False)
        session.commit()
        return None

    meta = {
        **probe,
        "orientation": orientation,
        "library_root": str(root),
        "has_proxy": bool(proxy_ok),
        "focus_qa": focus,
        "source_rotation": pre.get("rotation"),
        "source_orientation": (audit.get("source") or {}).get("orientation"),
        "orientation_audit": audit,
    }
    if defer_index:
        meta["index_pending"] = True
    asset.storage_path = str(normalized)
    asset.proxy_path = proxy_path
    asset.status = "ready"
    asset.duration_sec = probe.get("duration_sec")
    asset.width = store_w
    asset.height = store_h
    asset.orientation = orientation
    asset.fps = probe.get("fps")
    asset.has_audio = probe.get("has_audio", False)
    asset.metadata_json = meta
    session.commit()
    session.refresh(asset)

    # HARD GSemanticOps: ingest/watchers only persist queue eligibility. Model
    # work belongs exclusively to the machine-wide vectorization executor.
    from engine.catalog.vectorization_runtime import enqueue_asset

    enqueue_asset(session, asset)
    log.info("ingest enqueued vectorization asset=%s path=%s", asset.id, file_path)
    return asset


def backfill_proxies(
    session: Session,
    settings: AppSettings,
    *,
    limit: int = 500,
    customer_id: int | None = None,
) -> dict[str, int]:
    """Generate missing proxies for existing ready assets."""
    stmt = select(Asset).where(Asset.status == "ready")
    if customer_id is not None:
        stmt = stmt.where(Asset.customer_id == customer_id)
    rows = list(session.scalars(stmt.limit(limit)).all())
    created = 0
    skipped = 0
    failed = 0
    for asset in rows:
        if asset.proxy_path and Path(asset.proxy_path).exists():
            skipped += 1
            continue
        storage = Path(asset.storage_path)
        if not storage.exists():
            failed += 1
            continue
        dest = proxy_dest_for_asset(storage.parent)
        if make_proxy(storage, dest):
            asset.proxy_path = str(dest)
            meta = dict(asset.metadata_json or {})
            meta["has_proxy"] = True
            asset.metadata_json = meta
            session.commit()
            created += 1
        else:
            failed += 1
    return {"created": created, "skipped": skipped, "failed": failed, "scanned": len(rows)}
