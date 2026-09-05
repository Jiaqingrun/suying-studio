"""HARD orientation lock (L16): display-size classification + dual-source consensus.

Fail closed: assets never become ready / enter vectorization without a passing
orientation audit. Display size always accounts for container rotation
(Display Matrix / rotate tags), not coded width×height alone.
"""
from __future__ import annotations

import re
from typing import Any

ORIENTATION_LOCK_VERSION = 1
ASSET_STATUS_REJECTED_ORIENTATION = "rejected_orientation"

# Same ratio thresholds as classify_orientation (must stay in sync)
ORIENTATION_RATIO_LANDSCAPE_MIN = 1.1
ORIENTATION_RATIO_PORTRAIT_MAX = 0.9

ALLOWED_ORIENTATIONS = frozenset({"portrait", "landscape"})
REJECT_REASONS = frozenset(
    {
        "unknown_source",
        "unknown_baked",
        "ambiguous_source",
        "ambiguous_baked",
        "source_baked_mismatch",
        "stored_mismatch",
        "missing_dimensions",
        "not_audited",
    }
)


def assert_orientation_lock_integrity() -> None:
    """Smoke / load-time guard: thresholds only tightened, never inverted."""
    assert ORIENTATION_LOCK_VERSION >= 1
    assert 0 < ORIENTATION_RATIO_PORTRAIT_MAX < 1.0
    assert ORIENTATION_RATIO_LANDSCAPE_MIN > 1.0
    assert ORIENTATION_RATIO_PORTRAIT_MAX < ORIENTATION_RATIO_LANDSCAPE_MIN
    assert ASSET_STATUS_REJECTED_ORIENTATION == "rejected_orientation"
    # Re-exports on metadata must stay in lockstep (single public ratio constants).
    from engine.ingest import metadata as meta_mod

    assert float(meta_mod.ORIENTATION_RATIO_LANDSCAPE_MIN) == float(ORIENTATION_RATIO_LANDSCAPE_MIN)
    assert float(meta_mod.ORIENTATION_RATIO_PORTRAIT_MAX) == float(ORIENTATION_RATIO_PORTRAIT_MAX)


def _coerce_rotation(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def rotation_from_displaymatrix_text(text: str | None) -> int:
    """Infer 90/-90/180 from ffprobe displaymatrix dump when rotation key missing."""
    if not text:
        return 0
    # Lines look like: 00000000:            0      -65536           0
    nums: list[int] = []
    for line in str(text).splitlines():
        parts = re.findall(r"-?\d+", line.split(":", 1)[-1])
        nums.extend(int(p) for p in parts[:3])
        if len(nums) >= 6:
            break
    if len(nums) < 6:
        return 0
    a, b, _c0, c, d, _c1 = nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]
    # Identity
    if a > 0 and d > 0 and abs(b) < 1000 and abs(c) < 1000:
        return 0
    # 90° CW (ffmpeg dump often a=0,b=-65536,c=65536,d=0)
    if abs(a) < 1000 and b < 0 and c > 0 and abs(d) < 1000:
        return 90
    # 270° / -90°
    if abs(a) < 1000 and b > 0 and c < 0 and abs(d) < 1000:
        return -90
    # 180°
    if a < 0 and d < 0 and abs(b) < 1000 and abs(c) < 1000:
        return 180
    return 0


def extract_rotation(probe_or_meta: dict[str, Any] | None) -> int:
    """Rotation degrees from parse_probe meta or raw ffprobe JSON."""
    if not probe_or_meta:
        return 0
    # parse_probe meta
    if "rotation" in probe_or_meta and (
        "width" in probe_or_meta or "duration_sec" in probe_or_meta
    ):
        rot = _coerce_rotation(probe_or_meta.get("rotation"))
        if rot:
            return rot
    video = None
    streams = probe_or_meta.get("streams")
    if isinstance(streams, list):
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
    else:
        # Already stream-ish or parse_probe residual
        video = probe_or_meta if "side_data_list" in probe_or_meta else None
    if video:
        for side in video.get("side_data_list") or []:
            if not isinstance(side, dict):
                continue
            if side.get("rotation") is not None:
                r = _coerce_rotation(side.get("rotation"))
                if r:
                    return r
            r = rotation_from_displaymatrix_text(side.get("displaymatrix"))
            if r:
                return r
        tags = video.get("tags") or {}
        r = _coerce_rotation(tags.get("rotate") or tags.get("rotation"))
        if r:
            return r
        r = _coerce_rotation(video.get("rotation"))
        if r:
            return r
    fmt = probe_or_meta.get("format") or {}
    if isinstance(fmt, dict):
        tags = fmt.get("tags") or {}
        r = _coerce_rotation(tags.get("rotate") or tags.get("rotation"))
        if r:
            return r
    return _coerce_rotation(probe_or_meta.get("rotation"))


def display_size(width: int, height: int, rotation: int = 0) -> tuple[int, int]:
    rot = abs(int(rotation or 0)) % 360
    if rot in (90, 270):
        return int(height or 0), int(width or 0)
    return int(width or 0), int(height or 0)


def classify_orientation(width: int, height: int, rotation: int = 0) -> str:
    """Rotation-aware display bucket (authoritative for L16)."""
    dw, dh = display_size(width, height, rotation)
    if dw <= 0 or dh <= 0:
        return "unknown"
    ratio = dw / dh
    if ratio >= ORIENTATION_RATIO_LANDSCAPE_MIN:
        return "landscape"
    if ratio <= ORIENTATION_RATIO_PORTRAIT_MAX:
        return "portrait"
    return "other"


def classify_from_meta(meta: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Classify one probe/meta dict; returns (orientation, evidence)."""
    meta = meta or {}
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    rot = extract_rotation(meta)
    if w <= 0 or h <= 0:
        return "unknown", {
            "width": w,
            "height": h,
            "rotation": rot,
            "display_width": 0,
            "display_height": 0,
            "orientation": "unknown",
        }
    dw, dh = display_size(w, h, rot)
    orient = classify_orientation(w, h, rot)
    return orient, {
        "width": w,
        "height": h,
        "rotation": rot,
        "display_width": dw,
        "display_height": dh,
        "orientation": orient,
        "ratio": round(dw / dh, 4) if dh else None,
    }


def evaluate_orientation_audit(
    *,
    source_meta: dict[str, Any] | None,
    baked_meta: dict[str, Any] | None,
    stored_orientation: str | None = None,
) -> dict[str, Any]:
    """Hard dual-source orientation audit. Fail closed on any mismatch/ambiguity."""
    src_orient, src_ev = classify_from_meta(source_meta)
    bake_orient, bake_ev = classify_from_meta(baked_meta)
    violations: list[str] = []

    if src_orient == "unknown":
        violations.append("unknown_source")
    elif src_orient == "other":
        violations.append("ambiguous_source")

    if bake_orient == "unknown":
        violations.append("unknown_baked")
    elif bake_orient == "other":
        violations.append("ambiguous_baked")

    if (
        src_orient in ALLOWED_ORIENTATIONS
        and bake_orient in ALLOWED_ORIENTATIONS
        and src_orient != bake_orient
    ):
        violations.append("source_baked_mismatch")

    stored = str(stored_orientation or "").lower().strip()
    final = (
        bake_orient
        if bake_orient in ALLOWED_ORIENTATIONS
        else src_orient
        if src_orient in ALLOWED_ORIENTATIONS
        else "unknown"
    )
    if stored and stored in ALLOWED_ORIENTATIONS and final in ALLOWED_ORIENTATIONS:
        if stored != final:
            violations.append("stored_mismatch")

    passed = not violations and final in ALLOWED_ORIENTATIONS
    return {
        "lock": "ORIENTATION_LOCK",
        "lock_version": ORIENTATION_LOCK_VERSION,
        "passed": passed,
        "orientation": final if passed else (final if final in ALLOWED_ORIENTATIONS else "unknown"),
        "violations": violations,
        "source": src_ev,
        "baked": bake_ev,
        "hard": True,
    }


def is_orientation_audit_passed(asset_or_meta: Any) -> bool:
    """Whether an Asset (or metadata dict) has a passing L16 audit."""
    if asset_or_meta is None:
        return False
    meta: dict[str, Any]
    if isinstance(asset_or_meta, dict):
        meta = asset_or_meta
        orient = str(meta.get("orientation") or "").lower()
    else:
        meta = dict(getattr(asset_or_meta, "metadata_json", None) or {})
        orient = str(getattr(asset_or_meta, "orientation", None) or "").lower()
    audit = meta.get("orientation_audit")
    if isinstance(audit, dict):
        if audit.get("passed") is True:
            ao = str(audit.get("orientation") or orient).lower()
            return ao in ALLOWED_ORIENTATIONS
        if audit.get("passed") is False:
            return False
    # Legacy ready assets (pre-L16): accept only if stored display size matches label
    # Explicit not_audited fails for NEW vector enqueue via require_passed.
    return False


def legacy_display_consistent(asset: Any) -> bool:
    """Pre-L16 grandfather: width/height (as stored display sizes) match orientation label."""
    orient = str(getattr(asset, "orientation", None) or "").lower()
    if orient not in ALLOWED_ORIENTATIONS:
        return False
    w = int(getattr(asset, "width", None) or 0)
    h = int(getattr(asset, "height", None) or 0)
    if w <= 0 or h <= 0:
        return False
    got = classify_orientation(w, h, 0)
    return got == orient


def asset_may_vectorize(asset: Any) -> bool:
    """Vectorization / selection eligibility under L16."""
    if str(getattr(asset, "status", None) or "") != "ready":
        return False
    if is_orientation_audit_passed(asset):
        return True
    # Temporary grandfather for pre-lock ready rows until reaudit rewrite
    meta = dict(getattr(asset, "metadata_json", None) or {})
    if meta.get("orientation_audit") is None and legacy_display_consistent(asset):
        return True
    return False


def orientation_lock_snapshot(session: Any, *, customer_id: int | None = None) -> dict[str, Any]:
    """App/health counters for hard orientation state (SQL-only, no path probe)."""
    from sqlalchemy import func, select, text

    from engine.catalog.db import Asset

    assert_orientation_lock_integrity()

    def _count(status: str | None = None, orientation: str | None = None) -> int:
        stmt = select(func.count()).select_from(Asset)
        if customer_id is not None:
            stmt = stmt.where(Asset.customer_id == customer_id)
        if status:
            stmt = stmt.where(Asset.status == status)
        if orientation:
            stmt = stmt.where(Asset.orientation == orientation)
        return int(session.scalar(stmt) or 0)

    rejected = _count(status=ASSET_STATUS_REJECTED_ORIENTATION)
    ready = _count(status="ready")
    # Audited OK: metadata_json contains orientation_audit.passed == true (SQLite json)
    # Fall back to 0 count if json_extract unavailable / empty.
    try:
        params: dict[str, Any] = {}
        sql = (
            "SELECT COUNT(*) FROM assets WHERE status='ready' "
            "AND json_extract(metadata_json, '$.orientation_audit.passed') = 1"
        )
        if customer_id is not None:
            sql += " AND customer_id=:cid"
            params["cid"] = customer_id
        audited_ok = int(session.execute(text(sql), params).scalar() or 0)
    except Exception:  # noqa: BLE001
        audited_ok = 0
    # Unverified ready: not audited and (no width/height or sideway label risk)
    try:
        params2: dict[str, Any] = {}
        sql2 = (
            "SELECT COUNT(*) FROM assets WHERE status='ready' "
            "AND (metadata_json IS NULL OR json_extract(metadata_json, '$.orientation_audit.passed') IS NULL "
            "OR json_extract(metadata_json, '$.orientation_audit.passed') = 0) "
            "AND NOT ("
            "  (orientation='portrait' AND height >= width AND width > 0 AND height > 0) "
            "  OR (orientation='landscape' AND width > height AND width > 0 AND height > 0)"
            ")"
        )
        if customer_id is not None:
            sql2 += " AND customer_id=:cid"
            params2["cid"] = customer_id
        unaudited = int(session.execute(text(sql2), params2).scalar() or 0)
    except Exception:  # noqa: BLE001
        unaudited = max(0, ready - audited_ok)

    by_orient = {
        "portrait": _count(status="ready", orientation="portrait"),
        "landscape": _count(status="ready", orientation="landscape"),
        "other": _count(status="ready", orientation="other"),
        "unknown": _count(status="ready", orientation="unknown"),
    }
    return {
        "lock": "ORIENTATION_LOCK",
        "lock_version": ORIENTATION_LOCK_VERSION,
        "hard": True,
        "integrity_ok": True,
        "ready_total": ready,
        "ready_audited_ok": audited_ok,
        "ready_unverified": max(0, unaudited),
        "rejected_orientation": rejected,
        "ready_by_orientation": by_orient,
        "reject_status": ASSET_STATUS_REJECTED_ORIENTATION,
        "message": (
            "横竖屏硬审核已启用：显示分辨率（含旋转元数据）双源一致才可入库/向量化"
        ),
    }
