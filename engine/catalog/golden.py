"""L0 golden sample scores — persist signed ruler rows in the authority DB."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import GoldenSample
from engine.config.settings import AppSettings

# Product core ships no customer media references. Existing authority-DB rows
# remain available; new golden baselines must come from the customer's ops flow.
FROZEN_BASELINE: list[dict[str, Any]] = []


def _probe_basic(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
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
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if raw.returncode != 0:
            return {"exists": True, "probe_ok": False}
        data = json.loads(raw.stdout or "{}")
    except (OSError, json.JSONDecodeError):
        return {"exists": True, "probe_ok": False}
    duration = float((data.get("format") or {}).get("duration") or 0) or None
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams") or [])
    return {"exists": True, "probe_ok": True, "duration_sec": duration, "has_audio": has_audio}


def upsert_frozen_golden(
    session: Session,
    settings: AppSettings,
    *,
    customer_id: int | None,
) -> list[dict[str, Any]]:
    """Write frozen G1/G2(/G3) rows; verify files on disk when present."""
    out_root = Path(settings.paths.output_root)
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for row in FROZEN_BASELINE:
        abs_path = out_root / row["rel_path"]
        probe = _probe_basic(abs_path)
        existing = session.scalar(
            select(GoldenSample).where(
                GoldenSample.customer_id == customer_id,
                GoldenSample.code == row["code"],
            )
        )
        duration = row.get("duration_sec")
        has_audio = bool(row.get("has_audio"))
        if probe.get("probe_ok"):
            if probe.get("duration_sec"):
                duration = round(float(probe["duration_sec"]), 2)
            has_audio = bool(probe.get("has_audio"))
        payload = {
            "filename": row["filename"],
            "rel_path": row["rel_path"],
            "absolute_path": str(abs_path),
            "duration_sec": duration,
            "title_text": row.get("title_text") or "",
            "has_audio": has_audio,
            "look_score": row.get("look_score"),
            "shippable": row.get("shippable"),
            "signed": bool(row.get("signed")),
            "signed_at": row.get("signed_at"),
            "note": row.get("note") or "",
            "meta_json": {
                **(row.get("meta") or {}),
                "file_exists": bool(probe.get("exists")),
                "probe_ok": bool(probe.get("probe_ok")),
            },
            "updated_at": now,
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
            obj = existing
        else:
            obj = GoldenSample(customer_id=customer_id, code=row["code"], **payload)
            session.add(obj)
        results.append(
            {
                "code": row["code"],
                "path": str(abs_path),
                "exists": bool(probe.get("exists")),
                "look_score": payload["look_score"],
                "shippable": payload["shippable"],
                "signed": payload["signed"],
            }
        )
    session.commit()

    # Mirror JSON under data_root for human inspection
    mirror = Path(settings.paths.data_root) / "golden_scores.json"
    mirror.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "customer_id": customer_id,
                "samples": [
                    {
                        "code": r.code,
                        "filename": r.filename,
                        "rel_path": r.rel_path,
                        "look_score": r.look_score,
                        "shippable": r.shippable,
                        "signed": r.signed,
                        "signed_at": r.signed_at,
                        "has_audio": r.has_audio,
                        "duration_sec": r.duration_sec,
                        "note": r.note,
                    }
                    for r in session.scalars(
                        select(GoldenSample)
                        .where(GoldenSample.customer_id == customer_id)
                        .order_by(GoldenSample.code)
                    ).all()
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return results


def list_golden(session: Session, customer_id: int | None) -> list[dict[str, Any]]:
    q = select(GoldenSample).order_by(GoldenSample.code)
    if customer_id is not None:
        q = q.where(GoldenSample.customer_id == customer_id)
    rows = list(session.scalars(q).all())
    return [
        {
            "id": r.id,
            "customer_id": r.customer_id,
            "code": r.code,
            "filename": r.filename,
            "rel_path": r.rel_path,
            "absolute_path": r.absolute_path,
            "duration_sec": r.duration_sec,
            "title_text": r.title_text,
            "has_audio": r.has_audio,
            "look_score": r.look_score,
            "shippable": r.shippable,
            "signed": r.signed,
            "signed_at": r.signed_at,
            "note": r.note,
            "meta": r.meta_json or {},
        }
        for r in rows
    ]
