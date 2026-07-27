#!/usr/bin/env python3
"""Batch: produce N ready montages one-by-one with strict QA between each.

State + report under 速影工作区/cache/batch10_qa/
On QA fail: exit 1 and print AGENT_BATCH_FAIL (agent fixes rules, then --resume).
On all pass: write final report and print AGENT_BATCH_DONE.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

_qa_path = ROOT / "scripts" / "qa_ready_montage.py"
_spec = importlib.util.spec_from_file_location("qa_ready_montage", _qa_path)
assert _spec and _spec.loader
_qa_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_qa_mod)
qa_montage = _qa_mod.qa_montage

ENGINE = "http://127.0.0.1:8766"
CACHE = Path("/Users/qr/QR-Volume/速影工作区/cache/batch10_ready_gate_qa")
STATE = CACHE / "state.json"
REPORT = CACHE / "report.md"
READY_ROOT = Path(
    "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片/ready"
)
THEMES = ["批发", "仓配", "配送", "现货", "工地"]
RULES_LOG = CACHE / "rules_learned.md"


def _emit(line: str) -> None:
    print(line, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _get(path: str, timeout: float = 8) -> Any:
    with urllib.request.urlopen(f"{ENGINE}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(path: str, body: dict, timeout: float = 30) -> Any:
    req = urllib.request.Request(
        f"{ENGINE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _load_state() -> dict[str, Any]:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {
        "target": 10,
        "items": [],
        "started_at": _now(),
        "status": "running",
    }


def _save_state(state: dict[str, Any]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _job_by_id(job_id: int) -> dict | None:
    jobs = _get("/jobs?limit=50")
    if not isinstance(jobs, list):
        return None
    for j in jobs:
        if isinstance(j, dict) and int(j.get("id") or 0) == int(job_id):
            return j
    return None


def _wait_job(job_id: int, *, timeout_sec: float = 900) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        j = _job_by_id(job_id)
        if j and j.get("status") in ("completed", "failed", "circuit_open"):
            return j
        time.sleep(5)
    raise TimeoutError(f"job {job_id} timeout")


def _find_ready_for_job(job_id: int) -> Path | None:
    outs = _get("/outputs?state=ready")
    if isinstance(outs, list):
        for o in outs:
            if isinstance(o, dict) and int(o.get("job_id") or 0) == int(job_id):
                p = o.get("output_path")
                if p and Path(p).is_file():
                    return Path(p)
    # Fallback: scan today's ready dir for montage_{job}_*
    today = datetime.now().strftime("%Y-%m-%d")
    day = READY_ROOT / today
    if day.is_dir():
        hits = sorted(day.glob(f"montage_{job_id}_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    return None


def _write_report(state: dict[str, Any]) -> None:
    items = state.get("items") or []
    passed = [i for i in items if i.get("qa_ok")]
    failed = [i for i in items if i.get("qa_ok") is False]
    lines = [
        f"# 成品测试批次报告",
        "",
        f"- 开始：{state.get('started_at')}",
        f"- 更新：{_now()}",
        f"- 目标：{state.get('target')} 条",
        f"- 已测：{len(items)}",
        f"- 通过：{len(passed)}",
        f"- 失败：{len(failed)}",
        f"- 状态：{state.get('status')}",
        "",
        "## 明细",
        "",
        "| # | Job | 文件 | QA | 失败项 |",
        "|---|-----|------|----|--------|",
    ]
    for i, it in enumerate(items, 1):
        fails = it.get("fails") or []
        fail_s = "; ".join(fails[:4]) if fails else "—"
        if len(fails) > 4:
            fail_s += f" (+{len(fails) - 4})"
        mark = "✅" if it.get("qa_ok") else ("❌" if it.get("qa_ok") is False else "…")
        lines.append(
            f"| {i} | {it.get('job_id')} | `{it.get('file') or '—'}` | {mark} | {fail_s} |"
        )
    lines.extend(["", "## 规则沉淀（本批次）", ""])
    for note in state.get("rule_notes") or []:
        lines.append(f"- {note}")
    if not state.get("rule_notes"):
        lines.append("- （尚无新规则；沿用 HARD_LOCKS / EMOJI_STICKER_LOCK / NARRATION_SUBTITLE_LOCK）")
    passed_paths = [i for i in items if i.get("qa_ok") and i.get("path")]
    if passed_paths:
        lines.extend(["", "## 通过成片", ""])
        for i in passed_paths:
            lines.append(f"- Job{i.get('job_id')}: `{i.get('file')}`")
    lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    # Mirror rule notes into learned log
    if state.get("rule_notes"):
        RULES_LOG.parent.mkdir(parents=True, exist_ok=True)
        RULES_LOG.write_text(
            "# 本批次自学规则\n\n" + "\n".join(f"- {n}" for n in state["rule_notes"]) + "\n",
            encoding="utf-8",
        )


def run_one(state: dict[str, Any], index: int) -> dict[str, Any]:
    theme = THEMES[(index - 1) % len(THEMES)]
    created = _post("/jobs", {"target_count": 1, "theme": theme, "mode": "count"})
    job_id = int(created["id"])
    print(f"AGENT_BATCH_ITEM {{\"n\":{index},\"job_id\":{job_id},\"theme\":{json.dumps(theme)},\"phase\":\"queued\"}}")
    job = _wait_job(job_id)
    if job.get("status") != "completed" or int(job.get("produced_count") or 0) < 1:
        item = {
            "n": index,
            "job_id": job_id,
            "theme": theme,
            "job_status": job.get("status"),
            "qa_ok": False,
            "fails": [f"job status={job.get('status')} produced={job.get('produced_count')}"],
            "at": _now(),
        }
        return item
    mp4 = _find_ready_for_job(job_id)
    if not mp4:
        return {
            "n": index,
            "job_id": job_id,
            "theme": theme,
            "qa_ok": False,
            "fails": ["ready mp4 not found"],
            "at": _now(),
        }
    # wait a moment for sidecar flush
    time.sleep(1)
    qa = qa_montage(mp4)
    item = {
        "n": index,
        "job_id": job_id,
        "theme": theme,
        "file": mp4.name,
        "path": str(mp4),
        "qa_ok": bool(qa.get("ok")),
        "fails": list(qa.get("fails") or []),
        "checks": qa.get("checks"),
        "title": qa.get("title"),
        "at": _now(),
    }
    phase = "pass" if item["qa_ok"] else "fail"
    print(
        "AGENT_BATCH_ITEM "
        + json.dumps(
            {"n": index, "job_id": job_id, "file": mp4.name, "phase": phase, "fails": item["fails"]},
            ensure_ascii=False,
        )
    )
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=10)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--stop-on-fail", action="store_true", default=True)
    ap.add_argument("--continue-on-fail", action="store_true", help="do not stop; keep going")
    args = ap.parse_args()
    stop_on_fail = not args.continue_on_fail

    # health
    try:
        h = _get("/health")
    except Exception as e:  # noqa: BLE001
        print(f"ENGINE_DOWN {e}", file=sys.stderr)
        return 2
    if not h:
        print("ENGINE_DOWN", file=sys.stderr)
        return 2

    if args.reset and STATE.is_file():
        STATE.unlink()

    state = _load_state() if args.resume or STATE.is_file() else {
        "target": args.target,
        "items": [],
        "started_at": _now(),
        "status": "running",
        "rule_notes": [],
    }
    state["target"] = args.target
    _save_state(state)
    _write_report(state)

    done = len([i for i in state["items"] if i.get("qa_ok")])
    # Count attempts: for resume, continue from len(items)+1 but only count passes toward target
    # User wants 10 passing videos. Failed ones need fix+retry (new job).
    while done < args.target:
        n = len(state["items"]) + 1
        print(f"AGENT_BATCH_TICK {{\"done_pass\":{done},\"target\":{args.target},\"attempt\":{n}}}")
        try:
            item = run_one(state, n)
        except Exception as e:  # noqa: BLE001
            item = {
                "n": n,
                "qa_ok": False,
                "fails": [f"exception: {e}"],
                "at": _now(),
            }
            print(f"AGENT_BATCH_FAIL {json.dumps(item, ensure_ascii=False)}")
        state["items"].append(item)
        _save_state(state)
        _write_report(state)

        if item.get("qa_ok"):
            done += 1
            print(f"AGENT_BATCH_PASS {{\"done_pass\":{done},\"target\":{args.target},\"file\":{json.dumps(item.get('file'))}}}")
            continue

        state["status"] = "blocked"
        _save_state(state)
        _write_report(state)
        print(
            "AGENT_BATCH_FAIL "
            + json.dumps(
                {
                    "done_pass": done,
                    "target": args.target,
                    "job_id": item.get("job_id"),
                    "file": item.get("file"),
                    "fails": item.get("fails"),
                    "report": str(REPORT),
                },
                ensure_ascii=False,
            )
        )
        if stop_on_fail:
            return 1
        # continue-on-fail: do not increment done; try another
        state["status"] = "running"
        _save_state(state)

    state["status"] = "done"
    state["finished_at"] = _now()
    _save_state(state)
    _write_report(state)
    print(
        "AGENT_BATCH_DONE "
        + json.dumps(
            {
                "passed": done,
                "target": args.target,
                "attempts": len(state["items"]),
                "report": str(REPORT),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
