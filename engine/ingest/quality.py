"""Lightweight visual quality scoring for cliplets (Sprint B)."""

from __future__ import annotations

import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet


def estimate_frame_quality(image_path: Path) -> float:
    """
    Return 0–1 quality from a still frame.
    Penalize too-dark / too-bright / very soft (low edge energy) frames.
    """
    try:
        img = Image.open(image_path).convert("L")
    except OSError:
        return 0.5
    # Downsample for speed
    tw = min(160, img.width)
    th = max(1, int(img.height * tw / max(1, img.width)))
    img = img.resize((tw, th))
    pixels = list(img.getdata())
    n = len(pixels) or 1
    mean = sum(pixels) / n
    # brightness score: peak at ~110–160
    if mean < 20 or mean > 245:
        bright = 0.05
    elif mean < 40 or mean > 220:
        bright = 0.25
    elif 80 <= mean <= 180:
        bright = 1.0
    else:
        bright = 0.7

    # edge energy proxy (horizontal diffs)
    energy = 0.0
    for y in range(th):
        row = pixels[y * tw : (y + 1) * tw]
        for x in range(1, tw):
            energy += abs(row[x] - row[x - 1])
    # normalize roughly
    norm = energy / max(1.0, tw * th)
    if norm < 2.0:
        sharp = 0.15
    elif norm < 5.0:
        sharp = 0.45
    elif norm < 12.0:
        sharp = 0.75
    else:
        sharp = 1.0

    return round(max(0.0, min(0.99, 0.45 * bright + 0.55 * sharp)), 4)


def score_clip_window(video_path: Path, start_sec: float, end_sec: float) -> float:
    """Extract mid-frame and score; returns 0–1 (0.5 on failure)."""
    mid = max(0.0, (start_sec + end_sec) / 2.0)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "q.jpg"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{mid:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out),
        ]
        if subprocess.run(cmd, capture_output=True, check=False).returncode != 0 or not out.exists():
            return 0.5
        return estimate_frame_quality(out)


# Legacy default before Sprint B scoring — treat as "unscored"
UNSCORED_SCORE = 1.0


def backfill_cliplet_quality(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = 2000,
    force: bool = False,
    only_unscored: bool = True,
) -> dict[str, Any]:
    """Recompute visual quality scores for existing cliplets (B3)."""
    from engine.ingest.proxy import analysis_video_path

    stmt = select(Cliplet).order_by(Cliplet.id.asc())
    if customer_id is not None:
        stmt = (
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(Asset.customer_id == customer_id)
            .order_by(Cliplet.id.asc())
        )
    if only_unscored and not force:
        stmt = stmt.where(Cliplet.score == UNSCORED_SCORE)
    stmt = stmt.limit(limit)
    rows = list(session.scalars(stmt).all())

    updated = 0
    skipped = 0
    missing = 0
    below_threshold = 0
    hist: Counter[str] = Counter()
    asset_cache: dict[int, Asset | None] = {}
    video_cache: dict[int, Path | None] = {}

    for i, row in enumerate(rows):
        if not force and only_unscored and float(row.score or UNSCORED_SCORE) != UNSCORED_SCORE:
            skipped += 1
            continue
        aid = int(row.asset_id)
        if aid not in asset_cache:
            asset_cache[aid] = session.get(Asset, aid)
        asset = asset_cache[aid]
        if not asset:
            missing += 1
            continue
        if aid not in video_cache:
            p = analysis_video_path(asset)
            video_cache[aid] = p if p.exists() else None
        video = video_cache[aid]
        if video is None:
            missing += 1
            continue
        q = score_clip_window(video, float(row.start_sec), float(row.end_sec))
        row.score = q
        updated += 1
        bucket = f"{int(q * 10) / 10:.1f}"
        hist[bucket] += 1
        if q < 0.28:
            below_threshold += 1
        if (i + 1) % 50 == 0:
            session.commit()

    session.commit()
    return {
        "scanned": len(rows),
        "updated": updated,
        "skipped": skipped,
        "missing_video": missing,
        "below_min_0_28": below_threshold,
        "score_hist": dict(sorted(hist.items())),
    }
