"""Near-window copy diversity gate (titles / openers / CTA / script similarity)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, KeywordUsage, RenderOutput
from engine.pack.narration_script import first_sentence, normalize_spoken_key, opener_key

ROLLING_WINDOW = 50
SCRIPT_JACCARD_THRESHOLD = 0.72


def _normalize_title(title: str) -> str:
    return re.sub(r"[\s\r\n　]+", "", str(title or "").strip())


def _bigrams(text: str) -> set[str]:
    compact = normalize_spoken_key(text, max_chars=9999)
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def script_bigram_jaccard(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def last_sentence(script: str) -> str:
    raw = re.sub(r"\s+", "", str(script or "").strip())
    if not raw:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?"):
        parts = [p for p in raw.split(sep) if p.strip()]
        if len(parts) > 1:
            return parts[-1].strip()
    return raw[-18:]


def cta_key(script: str) -> str:
    return normalize_spoken_key(last_sentence(script), max_chars=16)


@dataclass
class CopyDiversityRecord:
    title: str = ""
    opener: str = ""
    cta: str = ""
    script: str = ""


def load_recent_copy_records(
    session: Session | None,
    customer_id: int | None,
    *,
    limit: int = ROLLING_WINDOW,
) -> list[CopyDiversityRecord]:
    """Load recent titles + narration openers/cta for a customer (fail-open)."""
    if session is None or customer_id is None or limit <= 0:
        return []
    records: list[CopyDiversityRecord] = []
    seen_title: set[str] = set()

    try:
        title_rows = session.scalars(
            select(KeywordUsage.keyword)
            .where(KeywordUsage.customer_id == int(customer_id))
            .order_by(KeywordUsage.id.desc())
            .limit(limit * 2)
        ).all()
        for t in title_rows:
            nt = _normalize_title(str(t))
            if not nt or nt in seen_title:
                continue
            seen_title.add(nt)
            records.append(CopyDiversityRecord(title=nt))
            if len(records) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass

    try:
        out_rows = session.scalars(
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(Job.customer_id == int(customer_id))
            .order_by(RenderOutput.id.desc())
            .limit(limit * 2)
        ).all()
    except Exception:  # noqa: BLE001
        out_rows = []

    for row in out_rows:
        script = ""
        side = str(getattr(row, "sidecar_path", "") or "").strip()
        if side:
            try:
                import json
                from pathlib import Path

                data = json.loads(Path(side).read_text(encoding="utf-8"))
                meta = data.get("meta") if isinstance(data, dict) else None
                if isinstance(meta, dict):
                    script = str(meta.get("narration_script") or "")
            except Exception:  # noqa: BLE001
                script = ""
        if not script and isinstance(getattr(row, "qc_json", None), dict):
            script = str((row.qc_json or {}).get("narration_script") or "")
        if not script:
            continue
        rec = CopyDiversityRecord(
            opener=opener_key(script),
            cta=cta_key(script),
            script=normalize_spoken_key(script, max_chars=9999),
        )
        records.append(rec)
        if len(records) >= limit * 2:
            break
    return records[: limit * 2]


def check_title(title: str, recent: list[CopyDiversityRecord]) -> str | None:
    nt = _normalize_title(title)
    if not nt:
        return "empty_title"
    for rec in recent:
        if rec.title and rec.title == nt:
            return "title_collision"
    return None


def check_narration(script: str, recent: list[CopyDiversityRecord]) -> str | None:
    if not str(script or "").strip():
        return "empty_script"
    ok = opener_key(script)
    ck = cta_key(script)
    compact = normalize_spoken_key(script, max_chars=9999)
    for rec in recent:
        if rec.opener and ok and rec.opener == ok:
            return "opener_collision"
        if rec.cta and ck and rec.cta == ck:
            return "cta_collision"
        if rec.script and compact and script_bigram_jaccard(compact, rec.script) >= SCRIPT_JACCARD_THRESHOLD:
            return "script_similarity"
    return None


def diversity_report(
    *,
    title: str = "",
    script: str = "",
    recent: list[CopyDiversityRecord] | None = None,
) -> dict[str, Any]:
    recent = recent or []
    reasons: list[str] = []
    tr = check_title(title, recent)
    if tr:
        reasons.append(tr)
    sr = check_narration(script, recent)
    if sr:
        reasons.append(sr)
    return {
        "passed": not reasons,
        "reasons": reasons,
        "window": ROLLING_WINDOW,
        "recent_count": len(recent),
    }
