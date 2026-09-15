"""Shared RenderOutput scoping helpers for API routers."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from engine.catalog.db import Customer, Job, RenderOutput


def scoped_output(session, output_id: int, customer_id: int) -> RenderOutput:
    out = session.get(RenderOutput, output_id)
    if not out:
        raise HTTPException(404, "output not found")
    if out.job_id:
        job = session.get(Job, out.job_id)
        if job and job.customer_id and job.customer_id != customer_id:
            raise HTTPException(403, "output 不属于当前客户")
    return out


def output_cover_paths(out: RenderOutput) -> list[str]:
    covers: list[str] = []
    if isinstance(out.qc_json, dict):
        covers = [str(c) for c in (out.qc_json.get("covers") or []) if c]
    if not covers and out.sidecar_path:
        try:
            import json

            data = json.loads(Path(out.sidecar_path).read_text(encoding="utf-8"))
            covers = [str(c) for c in (data.get("covers") or []) if c]
        except Exception:
            covers = []
    return covers


def media_allow_roots(session: Session | None = None) -> list[Path]:
    """Roots that in-app media streaming may read (fail-closed outside)."""
    from engine.config.settings import load_settings

    settings = load_settings()
    roots: list[Path] = []
    for raw in (
        settings.paths.output_root,
        settings.paths.render_root,
        settings.paths.cache_root,
    ):
        if raw:
            roots.append(Path(str(raw)).expanduser())
    if session is not None:
        from sqlalchemy import select

        for row in session.scalars(select(Customer)).all():
            if row.output_root:
                roots.append(Path(str(row.output_root)).expanduser())
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def assert_streamable_media_path(path: Path, roots: list[Path]) -> Path:
    """Resolve path and require it under an allowlisted media root (no symlink leaf)."""
    if path.is_symlink():
        raise HTTPException(403, "拒绝通过符号链接读取成片/封面")
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError as exc:
        raise HTTPException(404, f"成片文件不可用: {path}") from exc
    for root in roots:
        try:
            root_r = root.expanduser().resolve(strict=False)
        except OSError:
            continue
        try:
            if resolved == root_r or resolved.is_relative_to(root_r):
                if not resolved.is_file():
                    raise HTTPException(404, f"成片文件不存在: {resolved}")
                return resolved
        except (OSError, ValueError):
            continue
    raise HTTPException(403, "成片/封面路径越界，已拒绝读取")
