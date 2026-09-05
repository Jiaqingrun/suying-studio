#!/usr/bin/env python3
"""Production-safe semantic heal: merge, demote fake closeups, idle on-demand verify, re-embed.

Does NOT enable full-library VLM backfill (SEMANTIC_PIPELINE hard default).
Does NOT replace montage.db. Matches peer rows by basename + time window.

Typical dual-host flow:
  1) local:  python3 scripts/semantic_idle_heal.py export
  2) local:  python3 scripts/semantic_idle_heal.py once
  3) scp export to remote; remote once; both start: loop

Idle gate: no held produce job, produce/tts/render free, ollama_heavy free.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_DEFAULT = os.environ.get("SUYING_API", "http://127.0.0.1:8766")
DATA_DEFAULT = Path(os.environ.get("SUYING_DATA", str(Path.home() / "Suying/data")))
CUSTOMER_ID = int(os.environ.get("SUYING_HEAL_CUSTOMER_ID", "1"))

OPS_MARKERS = (
    "装车",
    "卸货",
    "仓库",
    "货架",
    "货车",
    "卡车",
    "堆场",
    "叉车",
    "仓配",
    "出库",
    "码放",
    "分拣",
    "仓内",
    "发货",
    "车斗",
    "户外场景",
    "混凝土路面",
    "草地",
    "户外",
    "漏斗",
    "料斗",
    "搅拌",
    "工业设备",
    "搅拌罐",
    "进料斗",
    "工地",
    "叉运",
    "装卸",
)
TABLETOP_MARKERS = (
    "桌面",
    "包装盒",
    "卷尺",
    "台面",
    "钳子",
    "水龙头",
    "充电器",
    "电池",
    "电钻",
    "套筒",
    "焊条",
    "白板",
    "货架上整齐摆放且特写",  # rare
    "精工",
    "产品名",
)
PLACEHOLDER_MARKERS = ("实拍业务素材", "竖屏；实拍")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)


def api_json(path: str, method: str = "GET", body: dict | None = None, timeout: float = 60) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API_DEFAULT.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def production_idle(api: str = API_DEFAULT, *, allow_ollama_busy: bool = False) -> tuple[bool, str]:
    """Return (ok, reason).

    allow_ollama_busy=True: only require no produce workload (safe for re-embed batches).
    Default: also require ollama_heavy free (for vision verify).
    """
    global API_DEFAULT
    API_DEFAULT = api
    try:
        health = api_json("/health", timeout=8)
    except Exception as exc:
        return False, f"health_err:{exc}"
    if health.get("status") != "ok":
        return False, f"health={health.get('status')}"
    if health.get("runtime_state") not in (None, "ACTIVE", "active"):
        rs = str(health.get("runtime_state") or "")
        if rs and rs.upper() != "ACTIVE":
            return False, f"runtime={rs}"
    try:
        pipe = api_json("/jobs/pipeline", timeout=8)
    except Exception as exc:
        return False, f"pipeline_err:{exc}"
    running = pipe.get("running")
    if running:
        return False, f"job_running:{running.get('id')}:{running.get('phase')}"
    if pipe.get("queued_count"):
        return False, f"job_queued:{pipe.get('queued_count')}"
    gate = pipe.get("resource_gate") or {}
    pools = gate.get("pools") or {}
    for name in ("produce", "render", "tts"):
        p = pools.get(name) or {}
        if int(p.get("used") or 0) > 0:
            return False, f"pool_{name}_busy:{p.get('holders')}"
    if not allow_ollama_busy:
        oh = pools.get("ollama_heavy") or {}
        if int(oh.get("used") or 0) > 0:
            return False, f"ollama_heavy:{oh.get('holders')}"
    return True, "idle" if not allow_ollama_busy else "soft_idle"


def open_db(db_path: Path, *, timeout: float = 60.0) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    return con


def clip_key(basename: str, start: float, end: float) -> str:
    return f"{basename}|{round(float(start or 0), 3)}|{round(float(end or 0), 3)}"


def demote_scene(scene: str | None, description: str | None) -> tuple[str, bool]:
    sc = (scene or "").strip() or "default"
    desc = description or ""
    # Chinese alias → english catalog label for product prefer path
    if sc in ("产品特写",):
        sc = "product_closeup"
    if sc != "product_closeup":
        return sc, False
    ops = any(m in desc for m in OPS_MARKERS)
    table = any(m in desc for m in TABLETOP_MARKERS)
    if ops and not table:
        if any(m in desc for m in ("装车", "卸货", "货车", "卡车", "车斗", "装卸")):
            return "loading", True
        if any(m in desc for m in ("仓库", "货架", "仓内", "仓配", "码放", "分拣")):
            return "warehouse", True
        if any(
            m in desc
            for m in (
                "堆场",
                "户外",
                "草地",
                "混凝土",
                "漏斗",
                "料斗",
                "搅拌",
                "工地",
                "工业设备",
            )
        ):
            return "other_visible", True
        return "default", True
    return sc, False


def export_source(con: sqlite3.Connection, customer_id: int, out_path: Path) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT c.id, c.start_sec, c.end_sec, c.duration_sec, c.scene, c.description,
               c.objects_json, c.actions_json, c.theme, c.theme_score, c.score, c.status,
               c.semantic_schema_version, c.semantic_json, c.semantic_gate_json,
               a.uuid AS asset_uuid, a.source_path
        FROM cliplets c
        JOIN assets a ON a.uuid = c.asset_uuid
        WHERE a.customer_id = ?
          AND c.status = 'usable'
          AND (
            c.semantic_schema_version = 'suying.cliplet.semantic.v1'
            OR (
              length(coalesce(c.description,'')) >= 50
              AND c.description NOT LIKE '%实拍业务素材%'
            )
          )
        """,
        (customer_id,),
    ).fetchall()
    items = []
    for r in rows:
        b = os.path.basename(r["source_path"] or "")
        if not b:
            continue
        items.append(
            {
                "key": clip_key(b, r["start_sec"], r["end_sec"]),
                "basename": b,
                "start_sec": float(r["start_sec"] or 0),
                "end_sec": float(r["end_sec"] or 0),
                "duration_sec": float(r["duration_sec"] or 0),
                "scene": r["scene"],
                "description": r["description"],
                "objects_json": r["objects_json"],
                "actions_json": r["actions_json"],
                "theme": r["theme"],
                "theme_score": r["theme_score"],
                "score": r["score"],
                "semantic_schema_version": r["semantic_schema_version"],
                "semantic_json": r["semantic_json"],
                "semantic_gate_json": r["semantic_gate_json"],
                "source_cliplet_id": r["id"],
                "source_asset_uuid": r["asset_uuid"],
            }
        )
    payload = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "customer_id": customer_id,
        "count": len(items),
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(out_path), "count": len(items)}


def _loads_maybe(v: Any) -> Any:
    if isinstance(v, (dict, list)) or v is None:
        return v
    if isinstance(v, str) and v.strip().startswith(("{", "[")):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _dump_maybe(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def apply_demote(con: sqlite3.Connection, customer_id: int, *, dry_run: bool) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT c.id, c.scene, c.description
        FROM cliplets c
        JOIN assets a ON a.uuid = c.asset_uuid
        WHERE a.customer_id = ?
          AND c.status = 'usable'
          AND (c.scene = 'product_closeup' OR c.scene = '产品特写')
        """,
        (customer_id,),
    ).fetchall()
    changed: list[dict[str, Any]] = []
    for r in rows:
        new_sc, did = demote_scene(r["scene"], r["description"])
        if not did:
            continue
        changed.append({"id": r["id"], "from": r["scene"], "to": new_sc})
        if not dry_run:
            con.execute(
                """
                UPDATE cliplets
                SET scene = ?,
                    embedding_json = NULL,
                    embedding_backend = NULL,
                    embedding_model = NULL,
                    embedding_schema_version = NULL,
                    indexed_at = NULL
                WHERE id = ?
                """,
                (new_sc, r["id"]),
            )
    if not dry_run and changed:
        con.commit()
    return {"demoted": len(changed), "samples": changed[:20], "ids": [c["id"] for c in changed]}


def apply_merge(
    con: sqlite3.Connection,
    customer_id: int,
    export_path: Path,
    *,
    dry_run: bool,
    fuzzy_sec: float = 0.5,
) -> dict[str, Any]:
    if not export_path.exists():
        return {"ok": False, "error": f"export_missing:{export_path}"}
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    by_base: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute(
        """
        SELECT c.id, c.start_sec, c.end_sec, c.scene, c.description,
               c.semantic_schema_version, c.semantic_json,
               length(coalesce(c.description,'')) AS dlen,
               a.source_path
        FROM cliplets c
        JOIN assets a ON a.uuid = c.asset_uuid
        WHERE a.customer_id = ? AND c.status = 'usable'
        """,
        (customer_id,),
    ):
        b = os.path.basename(r["source_path"] or "")
        by_base.setdefault(b, []).append(r)

    merged = 0
    skipped = 0
    dirty_ids: list[int] = []
    samples: list[dict[str, Any]] = []

    def find_target(it: dict[str, Any]) -> sqlite3.Row | None:
        cands = by_base.get(it["basename"]) or []
        exact = None
        best = None
        best_score = 1e9
        for c in cands:
            ds = abs(float(c["start_sec"] or 0) - float(it["start_sec"]))
            de = abs(float(c["end_sec"] or 0) - float(it["end_sec"]))
            if ds < 1e-6 and de < 1e-6:
                exact = c
                break
            score = ds + de
            if score < best_score and ds <= fuzzy_sec and de <= fuzzy_sec:
                best_score = score
                best = c
        return exact or best

    for it in items:
        tgt = find_target(it)
        if tgt is None:
            skipped += 1
            continue
        src_desc = (it.get("description") or "").strip()
        tgt_desc = (tgt["description"] or "").strip()
        src_schema = it.get("semantic_schema_version") or ""
        tgt_schema = tgt["semantic_schema_version"] or ""
        src_sem_ok = src_schema == "suying.cliplet.semantic.v1" and bool(
            _loads_maybe(it.get("semantic_json"))
        )
        tgt_sem_ok = tgt_schema == "suying.cliplet.semantic.v1" and bool(
            _loads_maybe(tgt["semantic_json"])
        )
        placeholder = any(m in tgt_desc for m in PLACEHOLDER_MARKERS) or len(tgt_desc) < 40
        richer = len(src_desc) >= len(tgt_desc) + 20 or (src_sem_ok and not tgt_sem_ok)
        if not (placeholder or richer or src_sem_ok and not tgt_sem_ok):
            skipped += 1
            continue
        if tgt_sem_ok and not src_sem_ok:
            skipped += 1
            continue

        # Prefer exporter scene unless it's fake closeup after demote rules on source text.
        scene, _ = demote_scene(it.get("scene"), src_desc)
        if scene == "product_closeup" and demote_scene("product_closeup", src_desc)[1]:
            scene = demote_scene("product_closeup", src_desc)[0]

        new_desc = src_desc or tgt_desc
        new_scene = scene or tgt["scene"]
        new_schema = src_schema or tgt_schema
        new_sem = _dump_maybe(_loads_maybe(it.get("semantic_json")))
        # No-op if already equal (avoid wiping embeddings every cycle).
        if (
            (tgt_desc or "") == (new_desc or "")
            and (tgt["scene"] or "") == (new_scene or "")
            and (tgt_schema or "") == (new_schema or "")
            and tgt_sem_ok
            == (new_schema == "suying.cliplet.semantic.v1" and bool(_loads_maybe(it.get("semantic_json"))))
        ):
            # Still may differ on objects; skip dirty if description/scene/schema stable
            if tgt_schema == new_schema and tgt_sem_ok:
                skipped += 1
                continue

        values = {
            "description": new_desc,
            "scene": new_scene,
            "objects_json": _dump_maybe(_loads_maybe(it.get("objects_json"))),
            "actions_json": _dump_maybe(_loads_maybe(it.get("actions_json"))),
            "theme": it.get("theme") or None,
            "theme_score": it.get("theme_score"),
            "semantic_schema_version": new_schema,
            "semantic_json": new_sem,
            "semantic_gate_json": _dump_maybe(_loads_maybe(it.get("semantic_gate_json"))),
        }
        changed_text = (tgt_desc or "") != (new_desc or "") or (tgt["scene"] or "") != (new_scene or "")
        merged += 1
        if changed_text or not tgt_sem_ok:
            dirty_ids.append(int(tgt["id"]))
        if len(samples) < 12:
            samples.append(
                {
                    "id": int(tgt["id"]),
                    "basename": it["basename"],
                    "scene": values["scene"],
                    "dlen": len(values["description"]),
                    "schema": values["semantic_schema_version"],
                }
            )
        if dry_run:
            continue
        if changed_text or not tgt_sem_ok:
            con.execute(
                """
                UPDATE cliplets SET
                  description = ?,
                  scene = ?,
                  objects_json = ?,
                  actions_json = ?,
                  theme = COALESCE(?, theme),
                  theme_score = COALESCE(?, theme_score),
                  semantic_schema_version = ?,
                  semantic_json = ?,
                  semantic_gate_json = ?,
                  embedding_json = NULL,
                  embedding_backend = NULL,
                  embedding_model = NULL,
                  embedding_schema_version = NULL,
                  indexed_at = NULL
                WHERE id = ?
                """,
                (
                    values["description"],
                    values["scene"],
                    values["objects_json"],
                    values["actions_json"],
                    values["theme"],
                    values["theme_score"],
                    values["semantic_schema_version"],
                    values["semantic_json"],
                    values["semantic_gate_json"],
                    int(tgt["id"]),
                ),
            )
        else:
            con.execute(
                """
                UPDATE cliplets SET
                  objects_json = COALESCE(?, objects_json),
                  actions_json = COALESCE(?, actions_json),
                  theme = COALESCE(?, theme),
                  theme_score = COALESCE(?, theme_score)
                WHERE id = ?
                """,
                (
                    values["objects_json"],
                    values["actions_json"],
                    values["theme"],
                    values["theme_score"],
                    int(tgt["id"]),
                ),
            )
    if not dry_run and dirty_ids:
        con.commit()
    return {
        "ok": True,
        "merged": merged,
        "skipped": skipped,
        "dirty_ids": dirty_ids,
        "samples": samples,
    }


def pick_verify_ids(con: sqlite3.Connection, customer_id: int, limit: int = 5) -> list[int]:
    """Prefer tabletopish / unknown-product placeholders not yet strict v1 terminal."""
    rows = con.execute(
        """
        SELECT c.id, c.description, c.scene, c.semantic_schema_version,
               c.semantic_gate_json
        FROM cliplets c
        JOIN assets a ON a.uuid = c.asset_uuid
        WHERE a.customer_id = ?
          AND c.status = 'usable'
          AND (c.semantic_schema_version IS NULL
               OR c.semantic_schema_version != 'suying.cliplet.semantic.v1'
               OR c.semantic_json IS NULL
               OR length(coalesce(c.semantic_json,'')) < 20)
        ORDER BY c.id ASC
        LIMIT 800
        """,
        (customer_id,),
    ).fetchall()
    scored: list[tuple[int, int]] = []
    for r in rows:
        gate = r["semantic_gate_json"]
        if isinstance(gate, str) and gate.startswith("{"):
            try:
                gate = json.loads(gate)
            except Exception:
                gate = {}
        if isinstance(gate, dict) and gate.get("strict_verification_terminal"):
            continue
        desc = r["description"] or ""
        score = 0
        if any(m in desc for m in PLACEHOLDER_MARKERS):
            score += 5
        if any(m in desc for m in TABLETOP_MARKERS):
            score += 8
        if r["scene"] in ("product_closeup", "product", "default", "", None):
            score += 2
        if any(m in desc for m in OPS_MARKERS):
            score -= 6  # leave ops for later / demote handles scene
        if score > 0:
            scored.append((score, int(r["id"])))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:limit]]


def _ollama_embed(text: str, model: str = "nomic-embed-text") -> list[float] | None:
    try:
        payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        emb = data.get("embedding")
        if isinstance(emb, list) and emb:
            return [float(x) for x in emb]
    except Exception:
        return None
    return None


def _compose_embed_from_row(row: sqlite3.Row) -> str:
    parts: list[str] = []
    desc = (row["description"] or "").strip()
    if desc:
        parts.append(desc)
    scene = (row["scene"] or "").strip()
    if scene and scene != "default":
        parts.append(f"scene:{scene}")
    for col in ("objects_json", "actions_json"):
        try:
            raw = row[col]
            arr = _loads_maybe(raw) if raw else []
            if isinstance(arr, list) and arr:
                parts.append(" ".join(str(x) for x in arr[:8]))
        except Exception:
            pass
    sem = _loads_maybe(row["semantic_json"]) if "semantic_json" in row.keys() else None
    if isinstance(sem, dict):
        for prod in (sem.get("products") or [])[:4]:
            if isinstance(prod, dict):
                bits = [prod.get("name"), prod.get("category"), prod.get("use")]
                parts.append(" / ".join(str(b) for b in bits if b))
    return " | ".join(p for p in parts if p).strip()


def reembed_dirty(
    db_path: Path,
    dirty_ids: list[int],
    *,
    limit: int = 40,
    studio_root: Path | None = None,
) -> dict[str, Any]:
    """Re-index embeddings for changed cliplets (Ollama direct, engine optional)."""
    if not dirty_ids:
        con = open_db(db_path)
        dirty_ids = [
            int(r[0])
            for r in con.execute(
                """
                SELECT c.id FROM cliplets c
                JOIN assets a ON a.uuid = c.asset_uuid
                WHERE a.customer_id = ? AND c.status = 'usable'
                  AND (c.embedding_json IS NULL OR length(coalesce(c.embedding_json,'')) < 10)
                ORDER BY c.id ASC LIMIT ?
                """,
                (CUSTOMER_ID, limit),
            )
        ]
        con.close()
    dirty_ids = dirty_ids[:limit]
    if not dirty_ids:
        return {"ok": True, "reembedded": 0, "ids": []}

    # Prefer lightweight Ollama re-embed (does not start studio import/sqlalchemy fights).
    con = open_db(db_path)
    done = 0
    errors: list[str] = []
    for cid in dirty_ids:
        try:
            row = con.execute(
                "SELECT id, description, scene, objects_json, actions_json, semantic_json, status, "
                "length(coalesce(embedding_json,'')) AS emb_len "
                "FROM cliplets WHERE id=?",
                (cid,),
            ).fetchone()
            if not row or (row["status"] or "") != "usable":
                continue
            # Already embedded (from prior idle ticks) — do not re-spend ollama
            if int(row["emb_len"] or 0) > 10:
                done += 1  # count as satisfied for backlog drain
                continue
            text = _compose_embed_from_row(row)
            if not text:
                errors.append(f"{cid}:empty_text")
                continue
            emb = _ollama_embed(text)
            if not emb:
                errors.append(f"{cid}:embed_fail")
                continue
            con.execute(
                """
                UPDATE cliplets SET
                  embedding_json = ?,
                  embedding_backend = 'ollama',
                  embedding_model = 'nomic-embed-text',
                  embedding_schema_version = 'suying.cliplet.embedding.v1',
                  indexed_at = ?
                WHERE id = ?
                """,
                (json.dumps(emb), datetime.now(timezone.utc).isoformat(), cid),
            )
            done += 1
            if done % 5 == 0:
                con.commit()
        except Exception as exc:
            errors.append(f"{cid}:{type(exc).__name__}:{exc}")
    con.commit()
    con.close()
    return {
        "ok": True,
        "reembedded": done,
        "ids": dirty_ids,
        "errors": errors[:12],
        "via": "ollama_direct",
    }


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "verify_done": [],
        "verify_failed": [],
        "last_run": None,
        "merged_total": 0,
        "demoted_total": 0,
        "reembed_total": 0,
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_once(args: argparse.Namespace) -> int:
    data = Path(args.data_root)
    db = data / "montage.db"
    ops = data / "ops"
    export_path = Path(args.export) if args.export else ops / "semantic_peer_export.json"
    state_path = ops / "semantic_idle_heal_state.json"
    state = load_state(state_path)

    idle, reason = production_idle(args.api, allow_ollama_busy=True)
    hard_idle, hard_reason = production_idle(args.api, allow_ollama_busy=False)
    if not idle and not args.force_offline:
        log(f"skip cycle: not idle ({reason})")
        return 0
    log(f"idle ok soft=({reason}) hard=({hard_reason})")

    con = open_db(db)
    demote = apply_demote(con, CUSTOMER_ID, dry_run=args.dry_run)
    log(f"demote fake closeup: {demote['demoted']} sample={demote.get('samples')}")
    dirty: list[int] = list(demote.get("ids") or [])
    dirty.extend(int(x) for x in (state.get("pending_reembed") or []))

    merge = {"merged": 0, "skipped": 0, "dirty_ids": []}
    if export_path.exists():
        merge = apply_merge(con, CUSTOMER_ID, export_path, dry_run=args.dry_run)
        log(f"merge from {export_path.name}: merged={merge['merged']} skipped={merge['skipped']}")
        dirty.extend(merge.get("dirty_ids") or [])
    else:
        log(f"no export at {export_path}, merge skipped")

    # Also queue rows with cleared embeddings
    if not args.dry_run:
        for (cid,) in con.execute(
            """
            SELECT c.id FROM cliplets c
            JOIN assets a ON a.uuid=c.asset_uuid
            WHERE a.customer_id=? AND c.status='usable'
              AND (c.embedding_json IS NULL OR length(coalesce(c.embedding_json,''))<10)
            ORDER BY c.id ASC LIMIT 500
            """,
            (CUSTOMER_ID,),
        ):
            dirty.append(int(cid))

    # re-embed when idle (or offline force)
    dirty = list(dict.fromkeys(dirty))
    reemb: dict[str, Any] = {"reembedded": 0}
    if dirty and not args.dry_run and (idle or args.force_offline):
        # prefer small batches so daily jobs can inject
        reemb = reembed_dirty(db, dirty, limit=int(args.reembed_limit))
        log(f"reembed: {reemb}")
    elif dirty:
        log(f"dirty={len(dirty)} (reembed deferred)")

    # on-demand verify batch — only when ollama fully free
    verify_res = None
    if args.verify and not args.dry_run and hard_idle:
        done = set(int(x) for x in state.get("verify_done") or [])
        failed = set(int(x) for x in state.get("verify_failed") or [])
        candidates = [i for i in pick_verify_ids(con, CUSTOMER_ID, limit=80) if i not in done and i not in failed]
        batch = candidates[: max(1, min(int(args.verify_batch), 5))]
        if batch:
            log(f"verify on-demand batch={batch}")
            try:
                verify_res = api_json(
                    "/index/captions/verify",
                    method="POST",
                    body={"cliplet_ids": batch},
                    timeout=600,
                )
                # record
                for i in batch:
                    if i in (verify_res.get("verified") or verify_res.get("passed_ids") or []):
                        done.add(i)
                # API returns passed/failed counts and maybe lists
                passed_n = int(verify_res.get("passed") or 0)
                failed_n = int(verify_res.get("failed") or 0)
                # fallback: mark all tried as done to progress (failed still not v1)
                if "verified" not in verify_res and "not_verified" not in verify_res:
                    if passed_n or failed_n:
                        # partial knowledge: keep ids in a tried set
                        for i in batch:
                            done.add(i)
                    else:
                        for i in batch:
                            done.add(i)
                else:
                    for i in verify_res.get("verified") or []:
                        done.add(int(i))
                    for i in verify_res.get("not_verified") or []:
                        failed.add(int(i))
                        done.add(int(i))
                # after verify, re-embed verified rows (description/semantic may change)
                re2 = reembed_dirty(db, batch, limit=len(batch))
                log(f"verify result: {json.dumps(verify_res, ensure_ascii=False)[:500]}")
                log(f"post-verify reembed: {re2}")
                state["verify_done"] = sorted(done)[-2000:]
                state["verify_failed"] = sorted(failed)[-2000:]
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                log(f"verify HTTP {exc.code}: {body}")
            except Exception as exc:
                log(f"verify err: {exc}")
        else:
            log("verify: no candidates")
    con.close()

    # Persist residual dirty for next idle tick (null embeddings)
    pending = list(dict.fromkeys((state.get("pending_reembed") or []) + dirty))
    if reemb.get("reembedded"):
        done_set = set(reemb.get("ids") or []) if int(reemb.get("reembedded") or 0) else set()
        # keep only those still null next cycle - simplified: drop first reembed_limit
        pending = pending[int(args.reembed_limit) :]
    state["pending_reembed"] = pending[:5000]
    state["last_run"] = _now()
    state["merged_total"] = int(state.get("merged_total") or 0) + int(merge.get("merged") or 0)
    state["demoted_total"] = int(state.get("demoted_total") or 0) + int(demote.get("demoted") or 0)
    state["reembed_total"] = int(state.get("reembed_total") or 0) + int(reemb.get("reembedded") or 0)
    if not args.dry_run:
        save_state(state_path, state)
    log(
        f"cycle done demoted={demote.get('demoted')} merged={merge.get('merged')} "
        f"reembed={reemb.get('reembedded')} state={state_path}"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    data = Path(args.data_root)
    db = data / "montage.db"
    out = Path(args.export) if args.export else data / "ops" / "semantic_peer_export.json"
    con = open_db(db)
    res = export_source(con, CUSTOMER_ID, out)
    con.close()
    log(f"export {res}")
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    sleep_s = max(30, int(args.interval))
    log(f"loop start interval={sleep_s}s api={args.api} data={args.data_root}")
    while True:
        try:
            cmd_once(args)
        except KeyboardInterrupt:
            log("loop stop")
            return 0
        except Exception as exc:
            log(f"loop error: {exc}")
        time.sleep(sleep_s)


def cmd_status(args: argparse.Namespace) -> int:
    data = Path(args.data_root)
    db = data / "montage.db"
    con = open_db(db)
    pc = con.execute(
        "SELECT count(*) FROM cliplets c JOIN assets a ON a.uuid=c.asset_uuid "
        "WHERE a.customer_id=? AND c.status='usable' AND c.scene='product_closeup'",
        (CUSTOMER_ID,),
    ).fetchone()[0]
    sem = con.execute(
        "SELECT count(*) FROM cliplets c JOIN assets a ON a.uuid=c.asset_uuid "
        "WHERE a.customer_id=? AND c.semantic_schema_version='suying.cliplet.semantic.v1' "
        "AND length(coalesce(c.semantic_json,''))>20",
        (CUSTOMER_ID,),
    ).fetchone()[0]
    emb_null = con.execute(
        "SELECT count(*) FROM cliplets c JOIN assets a ON a.uuid=c.asset_uuid "
        "WHERE a.customer_id=? AND c.status='usable' "
        "AND (c.embedding_json IS NULL OR length(coalesce(c.embedding_json,''))<10)",
        (CUSTOMER_ID,),
    ).fetchone()[0]
    placeholder = con.execute(
        "SELECT count(*) FROM cliplets c JOIN assets a ON a.uuid=c.asset_uuid "
        "WHERE a.customer_id=? AND c.status='usable' AND c.description LIKE '%实拍业务素材%'",
        (CUSTOMER_ID,),
    ).fetchone()[0]
    con.close()
    idle, reason = production_idle(args.api)
    state_path = data / "ops" / "semantic_idle_heal_state.json"
    state = load_state(state_path) if state_path.exists() else {}
    print(
        json.dumps(
            {
                "idle": idle,
                "reason": reason,
                "product_closeup": pc,
                "semantic_v1_full": sem,
                "embedding_missing": emb_null,
                "placeholder_desc": placeholder,
                "state": {
                    "merged_total": state.get("merged_total"),
                    "demoted_total": state.get("demoted_total"),
                    "reembed_total": state.get("reembed_total"),
                    "verify_done": len(state.get("verify_done") or []),
                    "last_run": state.get("last_run"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Idle semantic heal (merge/demote/verify/reembed)")
    p.add_argument("--api", default=API_DEFAULT)
    p.add_argument("--data-root", default=str(DATA_DEFAULT))
    p.add_argument("--export", default="", help="path to peer export JSON")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-offline", action="store_true", help="run demote/merge without idle")
    p.add_argument("--verify", action="store_true", default=True)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--verify-batch", type=int, default=3)
    p.add_argument("--reembed-limit", type=int, default=40)
    p.add_argument("--interval", type=int, default=90, help="loop sleep seconds")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export", help="export high-quality usable rows for peer merge")
    sub.add_parser("once", help="one heal cycle if idle")
    sub.add_parser("loop", help="idle loop forever")
    sub.add_parser("status", help="show heal metrics")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_verify:
        args.verify = False
    global API_DEFAULT, DATA_DEFAULT
    API_DEFAULT = args.api
    DATA_DEFAULT = Path(args.data_root)
    if args.cmd == "export":
        return cmd_export(args)
    if args.cmd == "once":
        return cmd_once(args)
    if args.cmd == "loop":
        return cmd_loop(args)
    if args.cmd == "status":
        return cmd_status(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
