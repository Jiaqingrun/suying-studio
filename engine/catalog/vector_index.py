from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet


OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"


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


def embed_text(text: str, model: str = EMBED_MODEL) -> tuple[list[float], str]:
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            if resp.status_code == 200:
                data = resp.json()
                emb = data.get("embedding") or []
                if emb:
                    return emb, "ollama"
    except Exception:
        pass
    return _hash_embed(text), "hash_fallback"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def index_cliplet(session: Session, cliplet: Cliplet) -> Cliplet:
    from engine.catalog.semantic_tags import compose_embed_text

    emb, _backend = embed_text(compose_embed_text(cliplet))
    cliplet.embedding_json = emb
    cliplet.indexed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(cliplet)
    return cliplet


def index_pending(session: Session, limit: int = 200) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(Cliplet).where(Cliplet.embedding_json.is_(None)).limit(limit)
        ).all()
    )
    for row in rows:
        index_cliplet(session, row)
    return {"indexed": len(rows)}


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
) -> list[tuple[Cliplet, float]]:
    q_emb, _ = embed_text(query)
    stmt = select(Cliplet).where(Cliplet.embedding_json.is_not(None), Cliplet.duration_sec >= min_duration)
    if customer_id is not None:
        stmt = stmt.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
    if category and category != "default":
        stmt = stmt.where(Cliplet.category == category)
    if theme and theme != "default":
        stmt = stmt.where(Cliplet.theme == theme)
    if scene and scene != "default":
        stmt = stmt.where(Cliplet.scene == scene)
    rows = list(session.scalars(stmt).all())
    # objects_json is a list; hard-filter in Python (portable across SQLite/JSON backends)
    if object_tag and object_tag != "default":
        rows = [r for r in rows if object_tag in (r.objects_json or [])]
    q_tokens = [t for t in (query or "").replace("，", " ").replace(",", " ").split() if t.strip()]
    scored: list[tuple[Cliplet, float]] = []
    for row in rows:
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
