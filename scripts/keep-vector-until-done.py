#!/usr/bin/env python3
"""Keep customer-machine vectorization running until portrait gap is empty.

Safe to re-run: only enables incremental mode, resumes PAUSED runs, and
starts a new portrait batch when the machine slot is free and pending > 0.
Exits 0 when pending_assets reaches 0 (orientation=portrait).

  python3 keep-vector-until-done.py
  python3 keep-vector-until-done.py --interval 40 --batch 80
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8766"


def _req(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw)


def status_portrait() -> dict:
    return _req("GET", "/vectorization/status?orientation=portrait")


def ensure_enabled() -> None:
    try:
        h = _req("GET", "/health")
    except Exception as exc:
        print(f"health fail: {exc}", flush=True)
        return
    if not h.get("vectorization_enabled"):
        try:
            r = _req("POST", "/vectorization/enable-switch?kick_reconcile=false")
            print(f"enabled: {r.get('message') or r.get('enabled')}", flush=True)
        except Exception as exc:
            print(f"enable fail: {exc}", flush=True)
    # Resume engine runtime if paused (vectorization may still be blocked).
    if str(h.get("runtime_state") or "") not in ("", "ACTIVE", "None"):
        print(f"runtime_state={h.get('runtime_state')}", flush=True)


def resume_if_needed(st: dict) -> None:
    s = str(st.get("status") or "")
    if s in ("PAUSED", "BLOCKED", "PAUSE_REQUESTED"):
        try:
            r = _req("POST", "/vectorization/resume")
            print(f"resume -> {r.get('status')}", flush=True)
        except Exception as exc:
            print(f"resume fail: {exc}", flush=True)


def start_batch(batch: int) -> None:
    try:
        r = _req(
            "POST",
            "/vectorization/runs",
            {"mode": "count", "count": max(1, min(batch, 100000)), "orientation": "portrait"},
        )
        print(
            f"started status={r.get('status')} frozen={r.get('frozen_assets')} "
            f"pending={r.get('pending_assets')} run={r.get('run_id')}",
            flush=True,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if "machine_slot_busy" in body:
            print("slot busy (batch already running)", flush=True)
        else:
            print(f"start fail HTTP {exc.code}: {body[:300]}", flush=True)
    except Exception as exc:
        print(f"start fail: {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=45, help="Seconds between checks")
    ap.add_argument("--batch", type=int, default=80, help="Assets per freeze when idle")
    ap.add_argument(
        "--max-hours",
        type=float,
        default=48.0,
        help="Safety stop (hours); 0 = no limit",
    )
    args = ap.parse_args()
    t0 = time.time()
    print(
        f"keep-vector-until-done start interval={args.interval}s batch={args.batch}",
        flush=True,
    )
    while True:
        if args.max_hours > 0 and (time.time() - t0) > args.max_hours * 3600:
            print("max-hours reached; exit", flush=True)
            return 2
        try:
            ensure_enabled()
            st = status_portrait()
        except Exception as exc:
            print(f"status fail: {exc}; sleep", flush=True)
            time.sleep(args.interval)
            continue

        pending = int(st.get("pending_assets") or 0)
        status = str(st.get("status") or "IDLE")
        completed = int(st.get("completed_assets") or 0)
        total = int(st.get("total_assets") or 0)
        run_id = st.get("run_id")
        print(
            f"tick portrait status={status} completed={completed}/{total} "
            f"pending={pending} run={run_id} asset={st.get('current_asset_id')}",
            flush=True,
        )

        if pending <= 0 and status in ("COMPLETED", "FAILED", "IDLE"):
            print("DONE pending=0", flush=True)
            return 0

        resume_if_needed(st)

        idle = status in ("COMPLETED", "FAILED", "IDLE") or not run_id
        # After a finished batch frozen>0 may still show COMPLETED — start next.
        if idle and pending > 0:
            start_batch(args.batch)

        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
