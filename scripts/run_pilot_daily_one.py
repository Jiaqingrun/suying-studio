#!/usr/bin/env python3
"""One-shot daily produce for universal-ad-copy pilot validation."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8766"
CUSTOMER = "北京始峰伟业"
RESTORE_PACK = "building-supply"
TEST_PACK = "universal-ad-copy"
ORIG_ACTIVE = "北京始峰伟业"
JOB_THEME = "配送"


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


def wait_job(job_id: int, timeout_sec: float = 1200) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        jobs = req("GET", "/jobs")
        row = next((j for j in jobs if int(j["id"]) == job_id), None)
        if not row:
            raise RuntimeError(f"job {job_id} missing")
        msg = (
            f"{row.get('status')} produced={row.get('produced_count')}/"
            f"{row.get('target_count')} fails={row.get('consecutive_failures')}"
        )
        if msg != last:
            print(f"  … job#{job_id} {msg}", flush=True)
            last = msg
        if row.get("status") in {"completed", "circuit_open", "cancelled", "failed", "paused"}:
            return row
        try:
            pipe = req("GET", "/jobs/pipeline")
            ev = (pipe.get("recent_events") or [{}])[0]
            if ev.get("message"):
                print(f"  … {ev.get('message')}", flush=True)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    raise TimeoutError(f"job {job_id} timeout")


def load_sidecar_meta(sidecar_path: str) -> dict[str, Any]:
    p = Path(sidecar_path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("meta") if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    health = req("GET", "/health")
    print(
        f"health={health.get('status')} worker={health.get('worker_running')} "
        f"ollama_narration={health.get('ollama_narration_enabled')}",
        flush=True,
    )

    customers = {c["name"]: c for c in req("GET", "/customers")}
    if CUSTOMER not in customers:
        print(f"客户不存在: {CUSTOMER}", file=sys.stderr)
        return 1
    cid = int(customers[CUSTOMER]["id"])
    old_pack = (customers[CUSTOMER].get("profile") or {}).get("industry_pack", RESTORE_PACK)
    print(f"客户 {CUSTOMER} id={cid} 原行业包={old_pack}", flush=True)

    req("POST", "/customers/activate", {"name": CUSTOMER})
    patched = req("PATCH", f"/customers/{cid}", {"industry_pack": TEST_PACK})
    print(f"已切换行业包 → {patched.get('profile', {}).get('industry_pack')}", flush=True)

    outs_before = req("GET", "/outputs?state=ready")
    max_out = max((int(o["id"]) for o in outs_before), default=0)

    print("创建日更任务 rush=1 …", flush=True)
    try:
        created = req(
            "POST",
            "/jobs",
            {
                "mode": "count",
                "target_count": 1,
                "theme": JOB_THEME,
                "category": "default",
                "use_active_rule": True,
                "rush": True,
            },
            timeout=180,
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"建任务失败: {e.code} {body[:500]}", file=sys.stderr)
        req("PATCH", f"/customers/{cid}", {"industry_pack": old_pack})
        req("POST", "/customers/activate", {"name": ORIG_ACTIVE})
        return 2

    job_id = int(created["id"])
    print(f"job#{job_id} created status={created.get('status')}", flush=True)

    try:
        final = wait_job(job_id)
    finally:
        req("PATCH", f"/customers/{cid}", {"industry_pack": old_pack})
        print(f"已恢复行业包 → {old_pack}", flush=True)
        req("POST", "/customers/activate", {"name": ORIG_ACTIVE})
        print(f"已切回客户 → {ORIG_ACTIVE}", flush=True)

    outputs = req("GET", "/outputs?state=ready")
    fresh = [o for o in outputs if int(o.get("id") or 0) > max_out]
    fresh.sort(key=lambda o: int(o["id"]), reverse=True)

    report: dict[str, Any] = {
        "job_id": job_id,
        "job_status": final.get("status"),
        "produced_count": final.get("produced_count"),
        "consecutive_failures": final.get("consecutive_failures"),
        "industry_pack_tested": TEST_PACK,
        "customer": CUSTOMER,
    }

    if fresh:
        out = next((o for o in fresh if int(o.get("job_id") or 0) == job_id), fresh[0])
        meta = load_sidecar_meta(str(out.get("sidecar_path") or ""))
        voice = meta.get("voice") if isinstance(meta.get("voice"), dict) else {}
        plan_fp = meta.get("content_fingerprint") if isinstance(meta.get("content_fingerprint"), dict) else {}
        script = str(meta.get("narration_script") or voice.get("script") or "")
        report["output"] = {
            "id": out.get("id"),
            "title": out.get("title") or meta.get("title"),
            "theme": out.get("theme"),
            "status": out.get("status"),
            "video_path": out.get("video_path"),
            "narration_script": script,
            "ollama_narration": voice.get("ollama_narration"),
            "ollama_base_lock": voice.get("ollama_base_lock"),
            "copy_diversity_warning": voice.get("copy_diversity_warning"),
            "copy_diversity_retry": voice.get("copy_diversity_retry"),
            "warnings": plan_fp.get("warnings") if isinstance(plan_fp.get("warnings"), list) else [],
        }
    else:
        report["output"] = None
        report["error"] = "no new ready output"

    print("\n===== 日更验收 =====", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    ok = final.get("status") == "completed" and int(final.get("produced_count") or 0) >= 1 and report.get("output")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
