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
from engine.ingest.proxy import make_proxy, proxy_dest_for_asset
from engine.ingest.pipeline import index_asset
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
    rotation = 0
    if video:
        for side in video.get("side_data_list", []):
            if side.get("rotation"):
                rotation = int(side["rotation"])
        # iOS / some Android store rotation in tags
        if not rotation:
            tags = video.get("tags") or {}
            rot = tags.get("rotate") or tags.get("rotation")
            if rot is not None:
                try:
                    rotation = int(float(rot))
                except (TypeError, ValueError):
                    rotation = 0
    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio is not None,
        "rotation": rotation,
    }


def display_size(width: int, height: int, rotation: int = 0) -> tuple[int, int]:
    """Return on-screen width/height after applying container rotation."""
    rot = abs(int(rotation or 0)) % 360
    if rot in (90, 270):
        return height, width
    return width, height


def is_landscape_video(width: int, height: int, rotation: int = 0) -> bool:
    """True when the video displays wider than tall (横屏)."""
    dw, dh = display_size(width, height, rotation)
    return dw > 0 and dh > 0 and dw > dh


def normalize_video(source: Path, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = parse_probe(ffprobe_metadata(source))
    vf = []
    if meta.get("rotation") in (-90, 270):
        vf.append("transpose=1")
    elif meta.get("rotation") in (90, -270):
        vf.append("transpose=2")
    elif meta.get("rotation") in (180, -180):
        vf.append("hflip,vflip")

    vf_filter = ",".join(vf) if vf else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
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
    use_vision: bool = True,
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
    # Reject landscape early (rotation-aware) — vertical montage library only.
    pre = parse_probe(ffprobe_metadata(file_path))
    if is_landscape_video(int(pre.get("width") or 0), int(pre.get("height") or 0), int(pre.get("rotation") or 0)):
        log.info(
            "skip landscape source %sx%s rot=%s path=%s",
            pre.get("width"),
            pre.get("height"),
            pre.get("rotation"),
            file_path,
        )
        return None

    existing = session.scalar(
        select(Asset).where(Asset.source_path == source_key, Asset.customer_id == customer_id)
    )
    if existing and existing.status == "ready":
        return None
    if existing and existing.status == "ingesting":
        # Resume a previously interrupted normalize
        asset = existing
        asset_uuid = asset.uuid
        storage_dir = Path(asset.storage_path).parent if asset.storage_path else (
            settings.paths.cache_root / "library" / asset_uuid
        )
        normalized = storage_dir / "normalized.mp4"
    else:
        asset_uuid = str(uuid.uuid4())
        root = library_root or settings.paths.library_root
        category = category_from_path(root, file_path)
        storage_dir = settings.paths.cache_root / "library" / asset_uuid
        storage_dir.mkdir(parents=True, exist_ok=True)
        normalized = storage_dir / "normalized.mp4"
        asset = Asset(
            uuid=asset_uuid,
            customer_id=customer_id,
            source_path=source_key,
            storage_path=str(normalized),
            category=category,
            status="ingesting",
            metadata_json={"library_root": str(root)},
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

    proxy = proxy_dest_for_asset(storage_dir)
    proxy_ok = make_proxy(normalized, proxy)
    proxy_path = str(proxy) if proxy_ok else None

    probe = parse_probe(ffprobe_metadata(normalized))
    # Safety net: normalized dims should already be display-oriented.
    if is_landscape_video(int(probe.get("width") or 0), int(probe.get("height") or 0), int(probe.get("rotation") or 0)):
        log.info(
            "reject landscape after normalize %sx%s asset=%s path=%s",
            probe.get("width"),
            probe.get("height"),
            asset.id,
            file_path,
        )
        asset.status = "rejected_landscape"
        meta = dict(asset.metadata_json or {})
        meta.update({**probe, "library_root": str(root), "reject_reason": "landscape"})
        asset.metadata_json = meta
        asset.width = probe.get("width")
        asset.height = probe.get("height")
        session.commit()
        # clean normalized outputs so they are not selected
        try:
            if normalized.exists():
                normalized.unlink()
            if proxy.exists():
                proxy.unlink()
        except OSError:
            pass
        return None

    meta = {**probe, "library_root": str(root), "has_proxy": bool(proxy_ok)}
    if defer_index:
        meta["index_pending"] = True
    asset.storage_path = str(normalized)
    asset.proxy_path = proxy_path
    asset.status = "ready"
    asset.duration_sec = probe.get("duration_sec")
    asset.width = probe.get("width")
    asset.height = probe.get("height")
    asset.fps = probe.get("fps")
    asset.has_audio = probe.get("has_audio", False)
    asset.metadata_json = meta
    session.commit()
    session.refresh(asset)

    # Auto: cliplets + embeddings only when vectorization is enabled
    from engine.config.settings import load_settings as _load

    vec_on = _load().vectorization_enabled
    if defer_index or not vec_on:
        if not vec_on:
            meta = dict(asset.metadata_json or {})
            meta["index_pending"] = True
            meta["vectorization_skipped"] = True
            asset.metadata_json = meta
            session.commit()
            log.info("ingest skip index (vectorization off) asset=%s path=%s", asset.id, file_path)
        else:
            log.info("ingest deferred index asset=%s path=%s", asset.id, file_path)
        return asset

    try:
        index_asset(session, asset, use_vision=use_vision)
    except Exception:
        log.exception("post-ingest index failed for %s", file_path)
        try:
            meta = dict(asset.metadata_json or {})
            meta["index_pending"] = True
            asset.metadata_json = meta
            session.commit()
        except Exception:
            pass
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
