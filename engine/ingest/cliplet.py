from __future__ import annotations

import re
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, Customer
from engine.catalog.semantic_tags import annotate_cliplet
from engine.catalog.industry_pack import pack_id_for_customer
from engine.ingest.vision_caption import describe_cliplet_vision
from engine.config.settings import load_settings
from engine.ingest.proxy import analysis_video_path


def detect_scenes(video_path: Path, threshold: float = 0.35) -> list[float]:
    """Return scene-change timestamps (seconds) using ffmpeg scene filter."""
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-filter:v",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    times: list[float] = []
    for line in (result.stderr or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def build_clip_ranges(duration: float, cuts: list[float], min_len: float = 2.0, max_len: float = 8.0) -> list[tuple[float, float]]:
    points = [0.0, *[t for t in cuts if 0 < t < duration], duration]
    points = sorted(set(round(p, 3) for p in points))
    ranges: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        start, end = points[i], points[i + 1]
        if end - start < min_len:
            continue
        # split long scenes into max_len chunks
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + max_len, end)
            if chunk_end - cursor >= min_len:
                ranges.append((round(cursor, 3), round(chunk_end, 3)))
            cursor = chunk_end
    if not ranges and duration > 0.05:
        # Keep ultra-short clips usable (otherwise reconcile loops forever on them)
        ranges.append((0.0, min(duration, max_len)))
    return ranges


def describe_cliplet(asset: Asset, start: float, end: float) -> str:
    """Heuristic text description (kept for callers; vision path preferred)."""
    from engine.ingest.vision_caption import heuristic_describe

    return heuristic_describe(asset, start, end)


def create_cliplets_for_asset(
    session: Session,
    asset: Asset,
    *,
    force: bool = False,
    use_vision: bool = True,
) -> list[Cliplet]:
    existing = list(session.scalars(select(Cliplet).where(Cliplet.asset_uuid == asset.uuid)).all())
    if existing and not force:
        return existing
    if existing and force:
        for row in existing:
            session.delete(row)
        session.commit()

    duration = float(asset.duration_sec or 0)
    if duration <= 0:
        return []
    video = analysis_video_path(asset)
    if not video.exists():
        return []
    cuts = detect_scenes(video)
    ranges = build_clip_ranges(duration, cuts)
    settings = load_settings()
    cache_dir = settings.paths.frames_root()
    from engine.ingest.quality import score_clip_window

    pack_id = "_blank"
    if asset.customer_id:
        cust = session.get(Customer, asset.customer_id)
        if cust:
            pack_id = pack_id_for_customer(cust.name, cust.profile_json)

    rows: list[Cliplet] = []
    for start, end in ranges:
        desc, _backend = describe_cliplet_vision(
            asset, start, end, cache_dir=cache_dir, use_vision=use_vision
        )
        q = score_clip_window(video, start, end)
        row = Cliplet(
            asset_id=asset.id,
            asset_uuid=asset.uuid,
            start_sec=start,
            end_sec=end,
            duration_sec=round(end - start, 3),
            description=desc,
            category=asset.category,
            score=q,
        )
        annotate_cliplet(row, asset, pack_id=pack_id)
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def recaption_existing_cliplets(
    session: Session,
    *,
    limit: int = 500,
    use_vision: bool = True,
) -> dict[str, int]:
    """Refresh descriptions (+ clear embeddings) for existing cliplets."""
    from engine.catalog.vector_index import index_cliplet

    settings = load_settings()
    cache_dir = settings.paths.frames_root()
    rows = list(session.scalars(select(Cliplet).order_by(Cliplet.id.asc()).limit(limit)).all())
    vision_n = 0
    heur_n = 0
    for row in rows:
        asset = session.get(Asset, row.asset_id)
        if not asset:
            continue
        desc, backend = describe_cliplet_vision(
            asset, row.start_sec, row.end_sec, cache_dir=cache_dir, use_vision=use_vision
        )
        row.description = desc
        row.embedding_json = None
        row.indexed_at = None
        annotate_cliplet(row, asset)
        if backend == "vision":
            vision_n += 1
        else:
            heur_n += 1
        session.commit()
        index_cliplet(session, row)
    return {"updated": len(rows), "vision": vision_n, "heuristic": heur_n}
