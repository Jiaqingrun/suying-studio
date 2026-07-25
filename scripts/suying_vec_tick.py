#!/usr/bin/env python3
"""One tick of 速影 vectorization + backfill pipeline (idempotent)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8766"


def get(path: str, timeout: float = 30) -> Any:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path: str, timeout: float = 180) -> Any:
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> None:
    out: dict[str, Any] = {"ok": True}
    try:
        health = get("/health", timeout=5)
        out["customer"] = health.get("active_customer")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"engine_down: {e}"}, ensure_ascii=False))
        return

    scan = get("/assets/scan/status")
    out["scan"] = {
        "running": scan.get("running"),
        "ingested": scan.get("ingested"),
        "attempted": scan.get("attempted"),
        "limit": scan.get("limit"),
        "error": scan.get("error"),
    }

    # Keep scanning in bounded batches (full scan can hang on one huge normalize).
    if not scan.get("running"):
        try:
            st = get("/assets/vectorization-status")
            ready = int(st.get("ready_assets") or 0)
            if ready < 500:
                kicked = post("/assets/scan?limit=30&background=true", timeout=15)
                out["scan_kick"] = kicked
            else:
                out["scan_kick"] = "enough_assets"
        except Exception as e:
            out["scan_kick_error"] = str(e)

    # Reconcile gaps (cliplet + embed)
    try:
        st = get("/assets/vectorization-status")
        out["vec_before"] = {
            "ready": st.get("ready_assets"),
            "gap": st.get("gap_assets"),
            "cliplets": st.get("cliplets_total"),
            "embedded": st.get("cliplets_embedded"),
            "pending_emb": st.get("pending_embeddings"),
            "enabled": st.get("vectorization_enabled"),
        }
        if int(st.get("gap_assets") or 0) > 0 or int(st.get("pending_embeddings") or 0) > 0:
            # Prefer throughput: vision is optional; Ollama 502 falls back anyway but is slow.
            out["reconcile"] = post(
                "/assets/reconcile?limit=15&use_vision=false&mode=incremental",
                timeout=120,
            )
        else:
            out["reconcile"] = "no_gap"
    except Exception as e:
        out["reconcile_error"] = str(e)

    # Semantic tags
    try:
        out["themes"] = post("/cliplets/themes/backfill?limit=200", timeout=60)
    except Exception as e:
        out["themes_error"] = str(e)

    # Quality scores (cheap batch)
    try:
        out["quality"] = post("/cliplets/quality/backfill?limit=80", timeout=120)
    except Exception as e:
        out["quality_error"] = str(e)

    try:
        out["vec_after"] = get("/assets/vectorization-status")
    except Exception as e:
        out["vec_after_error"] = str(e)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
