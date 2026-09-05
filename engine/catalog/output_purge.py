"""Purge on-disk media for failed / rejected outputs (keep DB audit rows)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import RenderOutput

log = logging.getLogger("montage.output_purge")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_unlink(path: Path) -> int:
    if not path.is_file() or path.is_symlink():
        return 0
    try:
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        return size
    except OSError as exc:
        log.warning("unlink failed path=%s err=%s", path, exc)
        return 0


def _safe_rmtree(path: Path) -> int:
    if not path.is_dir() or path.is_symlink():
        return 0
    freed = 0
    try:
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    freed += child.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        log.warning("rmtree failed path=%s err=%s", path, exc)
    return freed


def collect_output_artifact_paths(output: RenderOutput) -> list[Path]:
    """Resolve known artifact paths for one render output (files and dirs)."""
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(raw: str | Path | None) -> None:
        if not raw:
            return
        try:
            path = Path(str(raw)).expanduser()
        except (TypeError, ValueError):
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    _add(output.output_path)
    _add(output.sidecar_path)
    _add(output.pack_dir)

    main = Path(output.output_path).expanduser() if output.output_path else None
    if main is not None:
        stem = main.with_suffix("")
        parent = main.parent
        name = main.stem
        _add(main.with_suffix(".json"))
        _add(main.with_suffix(".voice.wav"))
        _add(parent / f"{name}.publish_pack")
        _add(parent / f"{name}_covers")
        for sibling in parent.glob(f"{name}*.srt"):
            _add(sibling)
        for sibling in parent.glob(f"{name}*.vtt"):
            _add(sibling)

    qc = output.qc_json if isinstance(output.qc_json, dict) else {}
    for key in ("publish_pack_dir", "covers_dir", "pack_dir"):
        _add(qc.get(key))
    covers = qc.get("covers")
    if isinstance(covers, list):
        for item in covers:
            if isinstance(item, str):
                _add(item)

    return paths


def purge_output_media(
    session: Session,
    output: RenderOutput,
    *,
    reason: str = "rejected",
    actor: str = "review",
) -> dict[str, Any]:
    """Delete deliverable media for an output; keep the DB row as failed audit.

    Safe to call multiple times (idempotent). Never touches ready/published trees
    outside the paths recorded on this output.
    """
    qc = dict(output.qc_json or {})
    if qc.get("media_purged_at") and not any(
        p.exists() for p in collect_output_artifact_paths(output)
    ):
        return {
            "ok": True,
            "output_id": output.id,
            "already_purged": True,
            "removed": 0,
            "freed_bytes": 0,
            "paths": [],
        }

    removed_paths: list[str] = []
    freed = 0
    for path in collect_output_artifact_paths(output):
        if path.is_file():
            size = _safe_unlink(path)
            if size or not path.exists():
                freed += size
                removed_paths.append(str(path))
        elif path.is_dir():
            size = _safe_rmtree(path)
            freed += size
            removed_paths.append(str(path))

    # Empty day folders under failed/review after purge (best-effort)
    if output.output_path:
        try:
            parent = Path(output.output_path).expanduser().parent
            if parent.is_dir() and parent.name.count("-") == 2:  # YYYY-MM-DD
                try:
                    next(parent.iterdir())
                except StopIteration:
                    parent.rmdir()
                except OSError:
                    pass
        except OSError:
            pass

    now = _now()
    qc.update(
        {
            "media_purged_at": now.isoformat(),
            "media_purge_reason": reason,
            "media_purge_actor": actor,
            "media_purge_freed_bytes": int(freed),
            "media_purge_paths": removed_paths[:40],
            "media_original_path": str(output.output_path or qc.get("media_original_path") or ""),
        }
    )
    output.qc_json = qc
    output.state = "failed"
    output.pack_status = "pending"
    output.pack_dir = None
    output.pack_error = (output.pack_error or "")[:0]
    session.flush()
    return {
        "ok": True,
        "output_id": output.id,
        "already_purged": False,
        "removed": len(removed_paths),
        "freed_bytes": freed,
        "paths": removed_paths,
    }
