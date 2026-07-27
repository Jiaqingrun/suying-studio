"""Visual quality + hard blur/focus gates for assets & cliplets.

Authority: docs/QUALITY_LOCK.md · docs/HARD_LOCKS.md
FROZEN 2026-07-26 — user: 「虚焦模糊…写死」「以上规则全部写死」.
Do NOT lower thresholds or bypass gates without explicit user approval.
"""

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

# --- HARD LOCK (FROZEN — raising OK; lowering requires user approval) ---
_LOCK_FROZEN = True
_LOCK_AT = "2026-07-26"

# Combined 0–1 score floor used by planner + gates
MIN_QUALITY_SCORE = 0.35
# Laplacian variance on ~160px grayscale — below = 虚焦/严重发糊
MIN_LAPLACIAN_VAR = 48.0
# Asset-level: reject whole source if majority of sample frames fail blur gate
ASSET_BLUR_FAIL_RATIO = 0.6
# Legacy unscored marker (pre Sprint B)
UNSCORED_SCORE = 1.0

CLIPLET_STATUS_USABLE = "usable"
CLIPLET_STATUS_REJECTED_BLUR = "rejected_blur"
ASSET_STATUS_REJECTED_BLUR = "rejected_blur"


def assert_quality_lock_integrity() -> None:
    """Fail loud if someone softens frozen floors."""
    assert _LOCK_FROZEN is True
    assert MIN_QUALITY_SCORE >= 0.35, "MIN_QUALITY_SCORE must not be lowered below 0.35"
    assert MIN_LAPLACIAN_VAR >= 48.0, "MIN_LAPLACIAN_VAR must not be lowered below 48"
    assert ASSET_BLUR_FAIL_RATIO <= 0.6, "ASSET_BLUR_FAIL_RATIO must not be raised above 0.6"
    assert CLIPLET_STATUS_REJECTED_BLUR == "rejected_blur"
    assert ASSET_STATUS_REJECTED_BLUR == "rejected_blur"


assert_quality_lock_integrity()


def locked_quality_floors() -> dict[str, Any]:
    return {
        "frozen": _LOCK_FROZEN,
        "locked_at": _LOCK_AT,
        "min_quality_score": MIN_QUALITY_SCORE,
        "min_laplacian_var": MIN_LAPLACIAN_VAR,
        "asset_blur_fail_ratio": ASSET_BLUR_FAIL_RATIO,
        "forbid_blur_ingest": True,
        "forbid_blur_vectorize": True,
        "forbid_blur_in_plan": True,
        "forbid_unscored_vectorize": True,
    }


def laplacian_variance(img: Image.Image) -> float:
    """Focus metric: low variance ⇒ soft / out-of-focus."""
    gray = img.convert("L")
    tw = min(160, gray.width)
    th = max(3, int(gray.height * tw / max(1, gray.width)))
    gray = gray.resize((tw, th))
    px = list(gray.getdata())
    vals: list[float] = []
    for y in range(1, th - 1):
        row = y * tw
        for x in range(1, tw - 1):
            c = px[row + x]
            v = (
                px[row - tw + x]
                + px[row + tw + x]
                + px[row + x - 1]
                + px[row + x + 1]
                - 4 * c
            )
            vals.append(float(v))
    if len(vals) < 8:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def estimate_frame_quality(image_path: Path) -> float:
    """
    Return 0–1 quality from a still frame.
    HARD: if Laplacian variance < MIN_LAPLACIAN_VAR → score capped below gate.
    """
    try:
        img = Image.open(image_path).convert("L")
    except OSError:
        return 0.5

    lap = laplacian_variance(img)
    if lap < MIN_LAPLACIAN_VAR:
        # Map soft blur into [0.05, MIN_QUALITY_SCORE - 0.01] so gate always rejects
        soft = max(0.05, min(MIN_QUALITY_SCORE - 0.01, lap / max(MIN_LAPLACIAN_VAR, 1.0) * 0.3))
        return round(soft, 4)

    tw = min(160, img.width)
    th = max(1, int(img.height * tw / max(1, img.width)))
    img = img.resize((tw, th))
    pixels = list(img.getdata())
    n = len(pixels) or 1
    mean = sum(pixels) / n
    if mean < 20 or mean > 245:
        bright = 0.05
    elif mean < 40 or mean > 220:
        bright = 0.25
    elif 80 <= mean <= 180:
        bright = 1.0
    else:
        bright = 0.7

    energy = 0.0
    for y in range(th):
        row = pixels[y * tw : (y + 1) * tw]
        for x in range(1, tw):
            energy += abs(row[x] - row[x - 1])
    norm = energy / max(1.0, tw * th)
    if norm < 2.0:
        sharp = 0.15
    elif norm < 5.0:
        sharp = 0.45
    elif norm < 12.0:
        sharp = 0.75
    else:
        sharp = 1.0

    # Bonus for clearly sharp focus
    if lap >= MIN_LAPLACIAN_VAR * 2.5:
        sharp = min(1.0, sharp + 0.1)

    return round(max(0.0, min(0.99, 0.40 * bright + 0.60 * sharp)), 4)


def is_usable_quality(score: float | None, *, lap_var: float | None = None) -> bool:
    """HARD gate: usable for planning + vectorization."""
    if lap_var is not None and lap_var < MIN_LAPLACIAN_VAR:
        return False
    q = float(score if score is not None else 0.0)
    if q <= 0:
        return False
    # Legacy unscored 1.0 is NOT trusted as sharp — treat as unknown → fail closed for vectorize
    # but planner historically allowed 1.0; for NEW gates we require rescored values.
    if abs(q - UNSCORED_SCORE) < 1e-9:
        return False
    return q >= MIN_QUALITY_SCORE


def _extract_frame(video_path: Path, at_sec: float, out: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, at_sec):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(out),
    ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0 and out.is_file()


def score_clip_window(video_path: Path, start_sec: float, end_sec: float) -> float:
    """Extract mid-frame and score; returns 0–1 (0.5 on failure — fail-closed via gate)."""
    mid = max(0.0, (start_sec + end_sec) / 2.0)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "q.jpg"
        if not _extract_frame(video_path, mid, out):
            return 0.2  # fail closed (was 0.5 — too permissive for blur)
        return estimate_frame_quality(out)


def score_frame_detail(video_path: Path, at_sec: float) -> dict[str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "q.jpg"
        if not _extract_frame(video_path, at_sec, out):
            return {"score": 0.2, "laplacian": 0.0}
        try:
            img = Image.open(out)
            lap = laplacian_variance(img)
        except OSError:
            return {"score": 0.2, "laplacian": 0.0}
        return {"score": estimate_frame_quality(out), "laplacian": round(lap, 2)}


def score_asset_focus(
    video_path: Path,
    *,
    duration_sec: float | None = None,
) -> dict[str, Any]:
    """Sample several frames; decide if whole asset is too blurry to keep."""
    dur = float(duration_sec or 0.0)
    if dur <= 0.5:
        # probe duration via ffmpeg if unknown
        dur = 6.0
    samples = [0.15, 0.5, 0.85]
    details: list[dict[str, float]] = []
    for frac in samples:
        at = max(0.05, min(dur - 0.05, dur * frac)) if dur > 0.2 else 0.1
        details.append(score_frame_detail(video_path, at))
    scores = [d["score"] for d in details]
    laps = [d["laplacian"] for d in details]
    fail_n = sum(
        1
        for d in details
        if d["laplacian"] < MIN_LAPLACIAN_VAR or d["score"] < MIN_QUALITY_SCORE
    )
    ratio = fail_n / max(1, len(details))
    rejected = ratio >= ASSET_BLUR_FAIL_RATIO
    return {
        "rejected_blur": rejected,
        "fail_ratio": round(ratio, 3),
        "mean_score": round(sum(scores) / max(1, len(scores)), 4),
        "mean_laplacian": round(sum(laps) / max(1, len(laps)), 2),
        "samples": details,
        "min_quality_score": MIN_QUALITY_SCORE,
        "min_laplacian_var": MIN_LAPLACIAN_VAR,
    }


def backfill_cliplet_quality(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = 2000,
    force: bool = False,
    only_unscored: bool = True,
) -> dict[str, Any]:
    """Recompute visual quality scores for existing cliplets."""
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
        if not is_usable_quality(q):
            below_threshold += 1
        if (i + 1) % 50 == 0:
            session.commit()

    session.commit()
    return {
        "scanned": len(rows),
        "updated": updated,
        "skipped": skipped,
        "missing_video": missing,
        "below_min_quality": below_threshold,
        "min_quality_score": MIN_QUALITY_SCORE,
        "score_hist": dict(sorted(hist.items())),
    }


def purge_blur_from_catalog(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = 5000,
    rescore: bool = True,
    purge_assets: bool = True,
) -> dict[str, Any]:
    """Rescore → reject blur cliplets (clear embeddings) → optional asset reject.

    Does not delete source files on disk; marks DB so they never vectorize / plan.
    """
    from engine.ingest.proxy import analysis_video_path

    if rescore:
        backfill_cliplet_quality(
            session,
            customer_id=customer_id,
            limit=limit,
            force=True,
            only_unscored=False,
        )

    stmt = select(Cliplet).order_by(Cliplet.id.asc()).limit(limit)
    if customer_id is not None:
        stmt = (
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(Asset.customer_id == customer_id)
            .order_by(Cliplet.id.asc())
            .limit(limit)
        )
    rows = list(session.scalars(stmt).all())

    rejected_clips = 0
    cleared_emb = 0
    kept = 0
    for row in rows:
        q = float(row.score or 0.0)
        if is_usable_quality(q):
            if getattr(row, "status", None) == CLIPLET_STATUS_REJECTED_BLUR:
                row.status = CLIPLET_STATUS_USABLE
            kept += 1
            continue
        row.status = CLIPLET_STATUS_REJECTED_BLUR
        # Always clear vector — even if previously embedded
        if row.embedding_json is not None:
            cleared_emb += 1
        row.embedding_json = None
        row.indexed_at = None
        rejected_clips += 1
    session.commit()
    # SQLite may store JSON `null` as non-NULL blob; force SQL NULL for rejected rows
    from sqlalchemy import text

    session.execute(
        text(
            "UPDATE cliplets SET embedding_json=NULL, indexed_at=NULL "
            "WHERE status = :st OR (score < :mn AND score != 1.0)"
        ),
        {"st": CLIPLET_STATUS_REJECTED_BLUR, "mn": MIN_QUALITY_SCORE},
    )
    session.commit()
    raw_left = int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM cliplets WHERE status = :st AND embedding_json IS NOT NULL"
            ),
            {"st": CLIPLET_STATUS_REJECTED_BLUR},
        ).scalar()
        or 0
    )
    assert raw_left == 0, f"rejected_blur still embedded: {raw_left}"

    assets_rejected = 0
    if purge_assets:
        asset_stmt = select(Asset).where(Asset.status == "ready")
        if customer_id is not None:
            asset_stmt = asset_stmt.where(Asset.customer_id == customer_id)
        assets = list(session.scalars(asset_stmt.limit(limit)).all())
        for asset in assets:
            clips = list(session.scalars(select(Cliplet).where(Cliplet.asset_id == asset.id)).all())
            # Fast path: all cliplets already rejected_blur / unusable → reject asset
            if clips and all(
                (c.status == CLIPLET_STATUS_REJECTED_BLUR) or (not is_usable_quality(float(c.score or 0)))
                for c in clips
            ):
                focus = {
                    "rejected_blur": True,
                    "reason": "all_cliplets_rejected_blur",
                    "cliplet_count": len(clips),
                }
            else:
                video = analysis_video_path(asset)
                if not video.exists():
                    continue
                # Only re-sample when mixed quality — catch whole-file soft focus
                usable_n = sum(1 for c in clips if is_usable_quality(float(c.score or 0)))
                if clips and usable_n >= max(1, len(clips) // 2):
                    continue
                focus = score_asset_focus(video, duration_sec=float(asset.duration_sec or 0) or None)
                if not focus["rejected_blur"]:
                    continue
            asset.status = ASSET_STATUS_REJECTED_BLUR
            meta = dict(asset.metadata_json or {})
            meta["reject_reason"] = "blur"
            meta["focus_qa"] = focus
            asset.metadata_json = meta
            for c in clips:
                c.status = CLIPLET_STATUS_REJECTED_BLUR
                if c.embedding_json is not None:
                    c.embedding_json = None
                    c.indexed_at = None
                    cleared_emb += 1
            assets_rejected += 1
            if assets_rejected % 20 == 0:
                session.commit()
        session.commit()

    return {
        "cliplets_scanned": len(rows),
        "cliplets_rejected_blur": rejected_clips,
        "cliplets_kept": kept,
        "embeddings_cleared": cleared_emb,
        "assets_rejected_blur": assets_rejected,
        "min_quality_score": MIN_QUALITY_SCORE,
        "min_laplacian_var": MIN_LAPLACIAN_VAR,
    }
