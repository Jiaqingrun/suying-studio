#!/usr/bin/env python3
"""Simulate App「立即生产 1 条」→ 自动过审，往复 N 轮并记录问题。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _req(base: str, method: str, path: str, body: dict | None = None, timeout: float = 60) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def _ui(step: str) -> None:
    print(f"[UI] {step}", flush=True)


def wait_job(base: str, job_id: int, timeout_sec: float = 900) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        jobs = _req(base, "GET", "/jobs")
        row = next((j for j in jobs if int(j["id"]) == job_id), None)
        if not row:
            raise RuntimeError(f"job {job_id} missing")
        status = str(row.get("status"))
        msg = f"{status} produced={row.get('produced_count')}/{row.get('target_count')} fails={row.get('consecutive_failures')}"
        if msg != last:
            print(f"  … job#{job_id} {msg}", flush=True)
            last = msg
        if status in {"completed", "circuit_open", "cancelled", "failed", "paused"}:
            return row
        pipe = _req(base, "GET", "/jobs/pipeline")
        ev = (pipe.get("recent_events") or [{}])[0]
        if ev.get("message"):
            print(f"  … event: {ev.get('message')}", flush=True)
        time.sleep(8)
    raise TimeoutError(f"job {job_id} timeout")


def check_auto_approve(base: str, since_id: int) -> dict[str, Any]:
    outputs = _req(base, "GET", "/outputs?state=ready")
    fresh = [o for o in outputs if int(o.get("id") or 0) > since_id]
    fresh.sort(key=lambda o: int(o["id"]), reverse=True)
    if not fresh:
        return {"ok": False, "error": "no new ready output"}
    out = fresh[0]
    oid = int(out["id"])
    reviews = _req(base, "GET", "/reviews?scope=history&limit=30")
    hit = next((r for r in reviews if int(r.get("render_output_id") or 0) == oid), None)
    logs = _req(base, "GET", "/operation-logs?category=quality&page=1&page_size=30")
    items = list(logs.get("items") or [])
    auto = next(
        (
            x
            for x in items
            if str(x.get("message") or "") == "审片自动通过"
            and str((x.get("details") or {}).get("output_id") or x.get("source_id") or "")
            in {str(oid), ""}
        ),
        None,
    )
    # fallback: any recent 审片自动通过
    if auto is None:
        auto = next((x for x in items if str(x.get("message") or "") == "审片自动通过"), None)
    return {
        "ok": bool(hit and hit.get("status") == "approved"),
        "output_id": oid,
        "review_status": (hit or {}).get("status"),
        "review_source": (hit or {}).get("decision_source"),
        "pack_status": out.get("pack_status"),
        "auto_log": bool(auto),
        "auto_log_message": (auto or {}).get("message"),
    }


def one_round(base: str, round_i: int, theme: str) -> dict[str, Any]:
    issues: list[str] = []
    _ui("打开「生产」页")
    _ui("查看流水线状态条 /jobs/pipeline")
    pipe0 = _req(base, "GET", "/jobs/pipeline")
    print(
        f"  pipeline running={pipe0.get('running')} queued={pipe0.get('queued_count')} worker={pipe0.get('worker_running')}",
        flush=True,
    )
    outs0 = _req(base, "GET", "/outputs?state=ready")
    max_out = max((int(o["id"]) for o in outs0), default=0)

    _ui("点击「立即生产 1 条」（rush=true, target_count=1）")
    try:
        created = _req(
            base,
            "POST",
            "/jobs",
            {
                "mode": "count",
                "target_count": 1,
                "theme": theme,
                "category": "default",
                "use_active_rule": True,
                "rush": True,
            },
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        issues.append(f"create_failed: {e.code} {detail[:300]}")
        return {"round": round_i, "ok": False, "issues": issues}

    job_id = int(created["id"])
    print(f"  created {created}", flush=True)
    if created.get("queued_ahead", 0) not in (0, None) and not created.get("rush"):
        issues.append(f"queued_ahead={created.get('queued_ahead')} without effective rush")

    _ui(f"等待任务 #{job_id} 完成并自动过审")
    try:
        final = wait_job(base, job_id)
    except Exception as e:  # noqa: BLE001
        issues.append(f"wait_failed: {e}")
        return {"round": round_i, "ok": False, "job_id": job_id, "issues": issues}

    if final.get("status") != "completed":
        issues.append(f"job_status={final.get('status')}")
    if int(final.get("produced_count") or 0) < 1:
        issues.append("produced_count<1")

    time.sleep(2)
    _ui("检查审片决策历史 / 日志 READY·质检「审片自动通过」")
    approve = check_auto_approve(base, since_id=max_out)
    if not approve.get("ok"):
        issues.append(f"auto_approve_missing: {approve}")
    if not approve.get("auto_log"):
        issues.append("quality_log_missing_审片自动通过")

    ok = not issues
    return {
        "round": round_i,
        "ok": ok,
        "job_id": job_id,
        "final": final,
        "approve": approve,
        "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8766")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--theme", default="仓配")
    ap.add_argument(
        "--disable-ollama-narration",
        action="store_true",
        help="测试时关闭旁白大模型，避免冷启动卡死",
    )
    args = ap.parse_args()
    health = _req(args.base, "GET", "/health")
    print(
        f"health={health.get('status')} customer={health.get('active_customer')} worker={health.get('worker_running')}",
        flush=True,
    )
    if args.disable_ollama_narration:
        _ui("设置：关闭 Ollama 旁白（避免 14B 冷启动卡住生产）")
        try:
            _req(args.base, "PUT", "/settings", {"ollama_narration_enabled": False})
        except Exception as e:  # noqa: BLE001
            print(f"  warn: cannot disable narration: {e}", flush=True)
    results = []
    for i in range(1, args.rounds + 1):
        print(f"\n===== ROUND {i}/{args.rounds} =====", flush=True)
        results.append(one_round(args.base, i, args.theme))
        print(json.dumps(results[-1], ensure_ascii=False, default=str), flush=True)
    summary = {
        "ok": all(r.get("ok") for r in results),
        "passed": sum(1 for r in results if r.get("ok")),
        "rounds": args.rounds,
        "results": results,
    }
    print("\n===== SUMMARY =====", flush=True)
    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
