#!/usr/bin/env python3
"""Run N rush jobs for 臻享丽人 B档改后抽样，写入日志目录 JSON。"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8766"
CUSTOMER = "臻享丽人"
RESTORE = "北京始峰伟业"
LOG_DIR = Path.home() / "Suying/logs/daily-diversity-zhenxiang-20260830"
OUT_JSON = LOG_DIR / "after-sample-5.json"


def req(method: str, path: str, body: dict | None = None, timeout: float = 120) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def wait_job(job_id: int, timeout_sec: float = 900) -> dict[str, Any] | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        jobs = req("GET", "/jobs")
        row = next((j for j in jobs if int(j["id"]) == job_id), None)
        if not row:
            time.sleep(3)
            continue
        st = row.get("status")
        if st in {"completed", "circuit_open", "cancelled", "failed", "paused"}:
            return row
        time.sleep(12)
    return None


def sidecar_meta(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def extract_row(out: dict[str, Any]) -> dict[str, Any]:
    meta = sidecar_meta(str(out.get("sidecar_path") or ""))
    cw = meta.get("copywriting") if isinstance(meta.get("copywriting"), dict) else {}
    fp = meta.get("content_fingerprint") if isinstance(meta.get("content_fingerprint"), dict) else {}
    shots = fp.get("shot_script") if isinstance(fp.get("shot_script"), list) else []
    buckets = [
        str(s.get("bucket") or s.get("role") or "")
        for s in shots[:4]
        if isinstance(s, dict)
    ]
    clips = meta.get("clips") if isinstance(meta.get("clips"), list) else []
    clip_roles = [str(c.get("role") or c.get("name") or "") for c in clips[:4] if isinstance(c, dict)]
    desc = str(cw.get("description") or "")
    hook = str(cw.get("hook") or cw.get("opener") or "")
    if not hook and desc:
        hook = desc.split("\n")[0][:80]
    return {
        "output_id": out.get("id"),
        "job_id": out.get("job_id"),
        "title": (out.get("title") or meta.get("title") or "").replace("\n", " / "),
        "theme": out.get("theme") or meta.get("theme"),
        "category": out.get("category") or meta.get("category"),
        "facet": (meta.get("meta") or {}).get("content_facet") if isinstance(meta.get("meta"), dict) else None,
        "hook": hook[:120],
        "main_buckets": buckets or clip_roles,
        "component_ids": cw.get("component_ids"),
    }


def twin_check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    titles = [r.get("title") or "" for r in rows]
    hooks = [r.get("hook") or "" for r in rows]
    mains = ["|".join(r.get("main_buckets") or []) for r in rows]
    uniq = lambda xs: len({x for x in xs if x})
    return {
        "n": len(rows),
        "unique_titles": uniq(titles),
        "unique_hooks": uniq(hooks),
        "unique_main_segments": uniq(mains),
        "title_twin": uniq(titles) < len(rows) and len(rows) >= 2,
        "hook_twin": uniq(hooks) < len(rows) and len(rows) >= 2,
        "main_twin": uniq(mains) < len(rows) and len(rows) >= 2,
        "pass_b_weekly": uniq(titles) >= min(5, len(rows)) and uniq(hooks) >= min(5, len(rows)),
    }


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"customer": CUSTOMER, "target": n, "jobs": [], "outputs": []}
    try:
        req("POST", "/customers/activate", {"name": CUSTOMER})
        max_out = max((int(o["id"]) for o in req("GET", "/outputs?state=ready")), default=0)
        for i in range(n):
            print(f"=== rush {i+1}/{n} ===", flush=True)
            created = req(
                "POST",
                "/jobs",
                {
                    "mode": "count",
                    "target_count": 1,
                    "category": "default",
                    "use_active_rule": True,
                    "rush": True,
                },
                timeout=180,
            )
            job_id = int(created["id"])
            final = wait_job(job_id)
            if not final:
                report["jobs"].append({"job_id": job_id, "status": "timeout", "produced_count": 0})
                continue
            report["jobs"].append(
                {
                    "job_id": job_id,
                    "status": final.get("status"),
                    "produced_count": final.get("produced_count"),
                }
            )
            print(f"  job#{job_id} {final.get('status')} prod={final.get('produced_count')}", flush=True)
            if final.get("status") != "completed":
                time.sleep(5)
                continue
            outs = req("GET", "/outputs?state=ready")
            fresh = [o for o in outs if int(o.get("id") or 0) > max_out]
            if fresh:
                fresh.sort(key=lambda o: int(o["id"]))
                out = fresh[-1]
                max_out = int(out["id"])
                report["outputs"].append(extract_row(out))
        report["twin_check"] = twin_check(report["outputs"])
    except urllib.error.HTTPError as e:
        report["error"] = e.read().decode("utf-8", errors="replace")[:500]
        print(report["error"], file=sys.stderr)
    finally:
        req("POST", "/customers/activate", {"name": RESTORE})
        report["restored_active"] = RESTORE

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    ok = len(report.get("outputs") or []) >= n
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
