"""Shared RenderOutput scoping helpers for API routers."""

from __future__ import annotations

from fastapi import HTTPException

from engine.catalog.db import Job, RenderOutput


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
            from pathlib import Path

            data = json.loads(Path(out.sidecar_path).read_text(encoding="utf-8"))
            covers = [str(c) for c in (data.get("covers") or []) if c]
        except Exception:
            covers = []
    return covers
