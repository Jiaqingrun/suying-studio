"""G6 Ops helpers: publish trail on sidecar + voice coverage stats."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem, RenderOutput


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_sidecar(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_sidecar(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_publish_trail(
    out: RenderOutput,
    *,
    platform: str,
    reach_item_id: int | None = None,
    note: str = "",
) -> list[dict[str, Any]]:
    """Append a published event into sidecar meta.publish_trail (best-effort)."""
    if not out.sidecar_path:
        return []
    data = read_sidecar(out.sidecar_path)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    trail = list(meta.get("publish_trail") or [])
    entry = {
        "platform": platform,
        "published_at": _now_iso(),
        "reach_item_id": reach_item_id,
        "note": note or "",
    }
    # de-dupe same platform same day
    day = entry["published_at"][:10]
    trail = [
        t
        for t in trail
        if not (str(t.get("platform")) == platform and str(t.get("published_at", ""))[:10] == day)
    ]
    trail.append(entry)
    meta["publish_trail"] = trail
    data["meta"] = meta
    try:
        write_sidecar(out.sidecar_path, data)
    except OSError:
        return trail
    return trail


def trail_from_sidecar(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(meta, dict):
        return []
    raw = meta.get("publish_trail") or []
    return [t for t in raw if isinstance(t, dict)]


def trail_from_reach(session: Session, output_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ReachQueueItem)
        .where(ReachQueueItem.output_id == output_id, ReachQueueItem.status == "published")
        .order_by(ReachQueueItem.updated_at.desc())
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "platform": r.platform,
                "published_at": (r.updated_at or r.created_at).isoformat()
                if (r.updated_at or r.created_at)
                else "",
                "reach_item_id": r.id,
                "note": r.note or "",
            }
        )
    return out


def merge_publish_trail(
    session: Session,
    output_id: int,
    meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Prefer sidecar trail; fill gaps from reach_queue published rows."""
    side = trail_from_sidecar(meta)
    reach = trail_from_reach(session, output_id)
    if not side:
        return reach
    seen = {(str(t.get("platform")), str(t.get("published_at", ""))[:10]) for t in side}
    merged = list(side)
    for t in reach:
        key = (str(t.get("platform")), str(t.get("published_at", ""))[:10])
        if key not in seen:
            merged.append(t)
            seen.add(key)
    return merged


def voice_flags_from_meta(meta: dict[str, Any] | None) -> tuple[bool, bool]:
    m = meta if isinstance(meta, dict) else {}
    return bool(m.get("narration_path")), bool(m.get("subtitle_burned"))


# VIDEO_LOCK Edge 晓晓: macOS say / mock = hard fail (QC 绿灯也算违规)
NONCOMPLIANT_TTS_SAY = "tts_fell_back_to_macos_say"
NONCOMPLIANT_TTS_MOCK = "tts_used_mock_under_lock"
NONCOMPLIANT_TTS_CODES = frozenset({NONCOMPLIANT_TTS_SAY, NONCOMPLIANT_TTS_MOCK})


def lock_requires_edge(video_lock: dict[str, Any] | None) -> bool:
    if not isinstance(video_lock, dict) or not video_lock.get("locked"):
        return False
    voice = video_lock.get("voice") if isinstance(video_lock.get("voice"), dict) else {}
    return str(voice.get("provider") or "").lower() in ("edge", "xiaoxiao")


def lock_requires_clone(video_lock: dict[str, Any] | None) -> bool:
    if not isinstance(video_lock, dict) or not video_lock.get("locked"):
        return False
    from engine.pack.voice_clone import is_clone_provider

    voice = video_lock.get("voice") if isinstance(video_lock.get("voice"), dict) else {}
    return is_clone_provider(str(voice.get("provider") or ""))


def tts_provider_from_meta(meta: dict[str, Any] | None) -> str:
    m = meta if isinstance(meta, dict) else {}
    raw = str(m.get("tts_provider") or m.get("provider") or "").strip().lower()
    return raw


def tts_lock_violation(
    meta: dict[str, Any] | None,
    video_lock: dict[str, Any] | None = None,
) -> str | None:
    """Return noncompliant code for truly bad VO providers (say/mock).

    Legal Edge/clone narration already on the sidecar is never re-flagged when
    VIDEO_LOCK later changes (avoids false tts_bad after voice switch).
    """
    from engine.pack.voice_clone import is_clone_provider

    m = meta if isinstance(meta, dict) else {}
    has_voice = bool(m.get("narration_path"))
    if not has_voice:
        return None

    provider = tts_provider_from_meta(m)
    nc = str(m.get("noncompliant") or "")

    # Production-legal providers: keep compliant even if current lock differs.
    if provider == "edge" or is_clone_provider(provider):
        return None

    if provider == "say" or nc == NONCOMPLIANT_TTS_SAY:
        return NONCOMPLIANT_TTS_SAY
    if provider == "mock" or nc == NONCOMPLIANT_TTS_MOCK:
        return NONCOMPLIANT_TTS_MOCK
    if nc in NONCOMPLIANT_TTS_CODES:
        return nc
    if provider:
        return f"tts_provider_{provider}"

    # Missing provider with narration: only flag when a lock exists and expects VO.
    if lock_requires_clone(video_lock) or lock_requires_edge(video_lock):
        return "tts_provider_missing"
    return None


def stamp_tts_noncompliant(
    sidecar_path: str | Path | None,
    code: str = NONCOMPLIANT_TTS_SAY,
) -> bool:
    """Write noncompliant + needs_review into sidecar. Returns True if written."""
    if not sidecar_path:
        return False
    data = read_sidecar(sidecar_path)
    if not data:
        return False
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["noncompliant"] = code
    meta["needs_review"] = True
    data["meta"] = meta
    data["needs_review"] = True
    try:
        write_sidecar(sidecar_path, data)
        return True
    except OSError:
        return False
