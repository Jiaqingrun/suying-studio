from __future__ import annotations

import hashlib
import math
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet


EMBED_MODEL = "nomic-embed-text"


class EmbeddingCancelled(RuntimeError):
    pass


def _active_embed_model() -> str:
    try:
        from engine.catalog.ollama_status import active_models_from_settings

        embed, _ = active_models_from_settings()
        return embed or EMBED_MODEL
    except Exception:
        return EMBED_MODEL


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic fallback embedding when Ollama is unavailable."""
    vec = [0.0] * dim
    tokens = text.lower().replace("；", " ").replace(":", " ").split()
    if not tokens:
        tokens = [text]
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, min(len(h), dim)):
            vec[i % dim] += (h[i] - 128) / 128.0
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_text(
    text: str,
    model: str | None = None,
    *,
    strict: bool = False,
    cancel_event: threading.Event | None = None,
    client_callback: Callable[[httpx.Client | None], None] | None = None,
    timeout_sec: float | None = None,
) -> tuple[list[float], str]:
    """Embed via the unified Ollama gateway (shared ollama_heavy slot, cancelable subprocess).

    ``client_callback`` is retained for backward compatibility with callers that
    still register/unregister an httpx.Client for cooperative cancellation; the
    actual network call now runs out-of-process, so the callback is invoked with
    ``None`` (no live client to hand back) purely to preserve register/clear pairing.
    """
    from engine.catalog.ollama_runtime import (
        embed_circuit_allows_request,
        embeddings,
        record_embed_failure,
        record_embed_success,
    )

    model = model or _active_embed_model()
    error = "ollama_embedding_unavailable"
    if cancel_event is not None and cancel_event.is_set():
        raise EmbeddingCancelled("cancelled_before_request")
    if not embed_circuit_allows_request():
        error = "ollama_embed_circuit_open"
        if strict:
            raise RuntimeError(error)
        return _hash_embed(text), "hash_fallback"
    # Planning tolerates hash fallback; keep timeouts short so a wedged Ollama
    # cannot pin the render slot for minutes. Strict paths keep a longer budget.
    if timeout_sec is None:
        timeout_sec = 60.0 if strict else 8.0
    if client_callback:
        client_callback(None)
    try:
        result = embeddings(
            model=model,
            prompt=text,
            timeout_sec=float(timeout_sec),
            cancel_event=cancel_event,
        )
        if result.get("ok"):
            body = result.get("body") or {}
            emb = (body.get("embedding") or []) if isinstance(body, dict) else []
            if emb:
                if cancel_event is not None and cancel_event.is_set():
                    raise EmbeddingCancelled("cancelled_after_response")
                record_embed_success()
                return emb, "ollama"
            error = "ollama_embedding_empty_vector"
            record_embed_failure(error)
        else:
            error = str(result.get("error") or "ollama_embedding_failed")
            if str(result.get("error_kind") or "") == "cancelled":
                raise EmbeddingCancelled("cancelled_during_request")
            record_embed_failure(error)
    except EmbeddingCancelled:
        raise
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise EmbeddingCancelled("cancelled_during_request") from exc
        error = f"ollama_embedding_{type(exc).__name__}"
        record_embed_failure(error)
    finally:
        if client_callback:
            client_callback(None)
    if strict:
        raise RuntimeError(error)
    return _hash_embed(text), "hash_fallback"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def index_cliplet(
    session: Session,
    cliplet: Cliplet,
    *,
    cancel_event: threading.Event | None = None,
    client_callback: Callable[[httpx.Client | None], None] | None = None,
    require_model: bool = False,
) -> Cliplet:
    from sqlalchemy import text

    from engine.catalog.semantic_tags import compose_embed_text
    from engine.ingest.semantic_gate import (
        STRICT_EMBEDDING_MODEL,
        STRICT_EMBEDDING_SCHEMA_VERSION,
        semantic_analysis_passed,
        semantic_index_admissible,
    )
    from engine.ingest.quality import CLIPLET_STATUS_REJECTED_BLUR, is_usable_quality

    # HARD: never vectorize blur / rejected slices
    if (cliplet.status or "") == CLIPLET_STATUS_REJECTED_BLUR or not is_usable_quality(
        float(cliplet.score or 0.0)
    ):
        cliplet.status = CLIPLET_STATUS_REJECTED_BLUR
        session.flush()
        session.execute(
            text(
                "UPDATE cliplets SET embedding_json=NULL, embedding_backend=NULL, "
                "embedding_model=NULL, embedding_schema_version=NULL, indexed_at=NULL, "
                "status=:st WHERE id=:id"
            ),
            {"st": CLIPLET_STATUS_REJECTED_BLUR, "id": int(cliplet.id)},
        )
        session.commit()
        session.refresh(cliplet)
        return cliplet

    # General production accepts an audited coarse record; strict production
    # still checks semantic v1 independently and never consumes coarse rows.
    if not semantic_index_admissible(cliplet):
        cliplet.status = "rejected_semantic"
        session.flush()
        session.execute(
            text(
                "UPDATE cliplets SET embedding_json=NULL, embedding_backend=NULL, "
                "embedding_model=NULL, embedding_schema_version=NULL, indexed_at=NULL, "
                "status='rejected_semantic' WHERE id=:id"
            ),
            {"id": int(cliplet.id)},
        )
        session.commit()
        session.refresh(cliplet)
        return cliplet

    embed_input = compose_embed_text(cliplet)
    if not embed_input.strip():
        cliplet.status = "rejected_semantic"
        session.flush()
        session.execute(
            text(
                "UPDATE cliplets SET embedding_json=NULL, embedding_backend=NULL, "
                "embedding_model=NULL, embedding_schema_version=NULL, indexed_at=NULL, "
                "status='rejected_semantic' WHERE id=:id"
            ),
            {"id": int(cliplet.id)},
        )
        session.commit()
        session.refresh(cliplet)
        return cliplet
    strict_semantic = semantic_analysis_passed(cliplet)
    model = STRICT_EMBEDDING_MODEL if strict_semantic else _active_embed_model()
    emb, backend = embed_text(
        embed_input,
        model=model,
        strict=strict_semantic or require_model,
        cancel_event=cancel_event,
        client_callback=client_callback,
    )
    if cancel_event is not None and cancel_event.is_set():
        raise EmbeddingCancelled("cancelled_before_commit")
    if strict_semantic and backend != "ollama":
        raise RuntimeError("strict_embedding_backend_invalid")
    cliplet.embedding_json = emb
    cliplet.embedding_backend = backend
    cliplet.embedding_model = model
    cliplet.embedding_schema_version = STRICT_EMBEDDING_SCHEMA_VERSION
    cliplet.indexed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(cliplet)
    return cliplet


def index_asset_cliplets(
    session: Session,
    asset: Asset,
    *,
    cancel_event: threading.Event | None = None,
    client_callback: Callable[[httpx.Client | None], None] | None = None,
) -> dict[str, int]:
    """Idempotently slice one asset and commit each missing cliplet separately."""
    from engine.ingest.cliplet import create_cliplets_for_asset

    rows = create_cliplets_for_asset(session, asset, force=False, use_vision=False)
    completed = 0
    kept = 0
    for row in sorted(rows, key=lambda value: int(value.id)):
        if cancel_event is not None and cancel_event.is_set():
            raise EmbeddingCancelled("cancelled_between_cliplets")
        if row.embedding_json is not None:
            kept += 1
            continue
        index_cliplet(
            session,
            row,
            cancel_event=cancel_event,
            client_callback=client_callback,
            require_model=True,
        )
        if row.embedding_json is not None:
            completed += 1
    return {
        "asset_id": int(asset.id),
        "completed_cliplets": completed,
        "kept_cliplets": kept,
        "total_cliplets": len(rows),
    }


def index_pending(session: Session, limit: int = 200, customer_id: int | None = None) -> dict[str, Any]:
    from engine.ingest.quality import effective_min_quality_score

    floor = float(effective_min_quality_score())
    try:
        from engine.config.settings import load_settings

        batch = int(getattr(load_settings(), "vector_batch_size", 0) or 0)
    except Exception:  # noqa: BLE001
        batch = 0
    stmt = (
        select(Cliplet)
        .where(Cliplet.embedding_json.is_(None))
        .where(Cliplet.status == "usable")
        .where(Cliplet.score >= floor)
        .where(Cliplet.score != 1.0)  # skip legacy unscored until rescored
    )
    if customer_id is not None:
        stmt = (
            stmt.join(Asset, Cliplet.asset_id == Asset.id)
            .where(Asset.customer_id == customer_id)
            .where(Asset.status == "ready")
        )
    requested = int(limit)
    if batch > 0 and requested >= 200:
        requested = batch
    safe_limit = max(0, min(requested, 200))
    rows = list(session.scalars(stmt.order_by(Cliplet.asset_id.asc(), Cliplet.id.asc()).limit(safe_limit)).all())
    indexed = 0
    skipped = 0
    for row in rows:
        before = row.embedding_json
        index_cliplet(session, row)
        if row.embedding_json is not None and before is None:
            indexed += 1
        else:
            skipped += 1
    return {"indexed": indexed, "skipped_quality_or_semantic": skipped, "candidates": len(rows)}


def search_cliplets(
    session: Session,
    query: str,
    *,
    category: str | None = None,
    theme: str | None = None,
    scene: str | None = None,
    object_tag: str | None = None,
    top_k: int = 20,
    min_duration: float = 2.0,
    customer_id: int | None = None,
    orientation: str | None = None,
    strict_semantic_v1: bool = False,
) -> list[tuple[Cliplet, float]]:
    from engine.ingest.quality import effective_min_quality_score

    floor = float(effective_min_quality_score())
    if strict_semantic_v1:
        from engine.ingest.semantic_gate import STRICT_EMBEDDING_MODEL

        q_emb, q_backend = embed_text(query, model=STRICT_EMBEDDING_MODEL, strict=True)
    else:
        q_emb, q_backend = embed_text(query)
    stmt = select(Cliplet).where(
        Cliplet.embedding_json.is_not(None),
        Cliplet.duration_sec >= min_duration,
        Cliplet.status == "usable",
        Cliplet.score >= floor,
    )
    if customer_id is not None or orientation in {"portrait", "landscape"}:
        stmt = stmt.join(Asset, Cliplet.asset_id == Asset.id).where(
            Asset.status == "ready"
        )
    if customer_id is not None:
        stmt = stmt.where(Asset.customer_id == customer_id)
    if orientation in {"portrait", "landscape"}:
        stmt = stmt.where(Asset.orientation == orientation)
    if category and category != "default":
        stmt = stmt.where(Cliplet.category == category)
    if theme and theme != "default":
        stmt = stmt.where(Cliplet.theme == theme)
    if scene and scene != "default":
        stmt = stmt.where(Cliplet.scene == scene)
    rows = list(session.scalars(stmt).all())
    if strict_semantic_v1:
        from engine.ingest.semantic_gate import semantic_gate_passed

        rows = [row for row in rows if semantic_gate_passed(row)]
    # objects_json is a list; hard-filter in Python (portable across SQLite/JSON backends)
    if object_tag and object_tag != "default":
        rows = [r for r in rows if object_tag in (r.objects_json or [])]
    q_tokens = [t for t in (query or "").replace("，", " ").replace(",", " ").split() if t.strip()]
    scored: list[tuple[Cliplet, float]] = []
    for row in rows:
        row_backend = (row.embedding_backend or "ollama").strip()
        # Hash and Ollama vectors live in different spaces — never mix cosine.
        if q_backend == "hash_fallback" and row_backend == "ollama":
            base = 0.0
        elif q_backend == "ollama" and row_backend == "hash_fallback":
            base = 0.0
        else:
            base = cosine(q_emb, row.embedding_json or [])
        # Soft keyword boost so tag-bearing clips surface for queries like「装车 货车」
        blob = " ".join(
            [
                row.theme or "",
                row.scene or "",
                " ".join(row.objects_json or []),
                " ".join(row.actions_json or []),
                row.description or "",
            ]
        )
        boost = 0.0
        for tok in q_tokens:
            if tok and tok in blob:
                boost += 0.04
        scored.append((row, base + boost))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
