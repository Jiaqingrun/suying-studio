#!/usr/bin/env python3
"""Customer host: produce N with active video rule; monitor + auto-correct; write report."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

API = "http://127.0.0.1:8766"
BJ = timezone(timedelta(hours=8))
TARGET = int(os.environ.get("PROD_TARGET", "20"))
LOG = os.environ.get("PROD_LOG", "/tmp/prod20_monitor.log")
STATE = os.environ.get("PROD_STATE", "/tmp/prod20_state.json")
REPORT = os.environ.get("PROD_REPORT", "/tmp/prod20_report.json")
DB = os.environ.get("SUYING_DB", "/Users/xlf/Suying/data/montage.db")
MAX_HOURS = float(os.environ.get("PROD_MAX_HOURS", "8"))
STAGNANT_SEC = float(os.environ.get("PROD_STAGNANT_SEC", "900"))
POLL_SEC = float(os.environ.get("PROD_POLL_SEC", "45"))


def log(msg: str) -> None:
    line = f"{datetime.now(BJ).strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http(method: str, path: str, body: dict | None = None, timeout: float = 90.0) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"{method} {path} {e.code}: {detail[:500]}") from e


def get(path: str, **kw: Any) -> Any:
    return http("GET", path, **kw)


def post(path: str, body: dict | None = None, **kw: Any) -> Any:
    return http("POST", path, {} if body is None else body, **kw)


def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def job_by_id(jid: int) -> dict | None:
    jobs = get("/jobs?limit=200")
    for j in jobs:
        if int(j.get("id") or 0) == int(jid):
            return j
    return None


def last_events(job_id: int, limit: int = 15) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select level, message, created_at from job_events where job_id=? order by id desc limit ?",
        (job_id, limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def produce_sum(job_ids: list[int]) -> int:
    total = 0
    for jid in job_ids:
        j = job_by_id(jid)
        if j:
            total += int(j.get("produced_count") or 0)
    return total


def create_batch(n: int, label: str) -> dict:
    body = {
        "mode": "count",
        "target_count": int(n),
        "template_name": "default-vertical",
        "theme": "default",
        "category": "default",
        "orientation": "portrait",
        "use_active_rule": True,
        "rush": True,
        "customer_name": "北京始峰伟业",
    }
    res = post("/jobs", body)
    log(
        f"CREATE {label} n={n} id={res.get('id')} status={res.get('status')} "
        f"msg={res.get('message')} running={res.get('running_job_id')}"
    )
    return res


def pause_job(jid: int) -> None:
    try:
        r = post(f"/jobs/{jid}/pause")
        log(f"PAUSE {jid} -> {r}")
    except Exception as exc:
        log(f"PAUSE fail {jid}: {exc}")


def resume_job(jid: int) -> None:
    try:
        r = post(f"/jobs/{jid}/resume")
        log(f"RESUME {jid} -> {r}")
    except Exception as exc:
        log(f"RESUME fail {jid}: {exc}")


def reap_stale(sec: float = 480.0) -> None:
    try:
        r = post(f"/jobs/reap-stale?stale_after_sec={sec}")
        if int(r.get("count") or 0) > 0:
            log(f"REAP {r}")
    except Exception as exc:
        log(f"REAP fail: {exc}")


def main() -> int:
    open(LOG, "w", encoding="utf-8").write("")
    log(f"=== START produce x{TARGET} with active rule ===")
    health = get("/health")
    log(f"engine {health.get('engine_version')} status={health.get('status')}")
    rule = (get("/production-rules/active") or {}).get("rule") or {}
    log(
        f"active_rule id={rule.get('id')} name={rule.get('name')} "
        f"cat={rule.get('content_category')} orient={rule.get('orientation')}"
    )

    pipe = get("/jobs/pipeline")
    running = pipe.get("running") or {}
    # Pause small competing automation jobs so our 20 can take the worker.
    if running:
        tid = int(running.get("id") or 0)
        tcount = int(running.get("target_count") or 0)
        # Always pause non-our long-running competitor under target 3.
        if tid and tcount <= 3:
            log(f"pause competing job {tid} target={tcount}")
            pause_job(tid)
            time.sleep(2)

    first = create_batch(TARGET, "main")
    jid = int(first["id"])
    job_ids = [jid]
    state: dict[str, Any] = {
        "started_at": datetime.now(BJ).isoformat(),
        "target": TARGET,
        "job_ids": job_ids,
        "corrections": [],
        "active_rule_id": rule.get("id"),
        "active_rule_name": rule.get("name"),
        "engine_version": health.get("engine_version"),
    }
    save_json(STATE, state)

    last_prog: tuple[Any, ...] = ()
    stagnant_since = time.time()
    deadline = time.time() + MAX_HOURS * 3600
    iteration = 0
    spawned_after_circuit = 0

    while time.time() < deadline:
        iteration += 1
        try:
            pipe = get("/jobs/pipeline")
            if not pipe.get("worker_running"):
                log("WARN worker_running=false")
            run = pipe.get("running") or {}
            total_prod = produce_sum(job_ids)
            remain = max(0, TARGET - total_prod)

            statuses = []
            any_active = False
            for jid_i in job_ids:
                j = job_by_id(jid_i) or {}
                st = j.get("status") or "?"
                pc = int(j.get("produced_count") or 0)
                cf = int(j.get("consecutive_failures") or 0)
                statuses.append(f"{jid_i}:{st}:{pc}/{j.get('target_count')} cf={cf}")
                if st in ("running", "queued", "pending", "paused"):
                    any_active = True

            evt0 = (pipe.get("recent_events") or [{}])[0]
            last_msg = str(evt0.get("message") or "")[:80]
            log(
                f"tick#{iteration} produced={total_prod}/{TARGET} remain={remain} "
                f"jobs=[{'; '.join(statuses)}] "
                f"pipe={run.get('id')}:{run.get('phase')} evt={last_msg}"
            )

            prog = (
                total_prod,
                run.get("id"),
                run.get("phase"),
                run.get("produced_count"),
                last_msg,
            )
            if prog != last_prog:
                last_prog = prog
                stagnant_since = time.time()
            stagnant = time.time() - stagnant_since

            # Success
            if total_prod >= TARGET:
                log(f"SUCCESS produced={total_prod}/{TARGET}")
                report = {
                    **state,
                    "finished_at": datetime.now(BJ).isoformat(),
                    "final_produced": total_prod,
                    "result": "success",
                    "job_ids": job_ids,
                    "last_jobs": [job_by_id(i) for i in job_ids],
                }
                save_json(STATE, report)
                save_json(REPORT, report)
                return 0

            # Correct circuit_open: spawn remaining, limit loop blowup
            for jid_i in list(job_ids):
                j = job_by_id(jid_i) or {}
                if j.get("status") != "circuit_open":
                    continue
                pc = int(j.get("produced_count") or 0)
                rem = max(0, TARGET - produce_sum(job_ids))
                if rem <= 0:
                    break
                if spawned_after_circuit >= 12:
                    log("CORRECT stop: too many circuit respawns")
                    break
                log(f"CORRECT circuit_open job={jid_i} produced={pc} spawn rem={rem}")
                for ev in last_events(jid_i, 10):
                    log(f"  evt {ev.get('level')} {ev.get('message')}")
                new = create_batch(rem, f"after_circuit_{jid_i}")
                job_ids.append(int(new["id"]))
                spawned_after_circuit += 1
                state["job_ids"] = job_ids
                state["corrections"].append(
                    {
                        "at": datetime.now(BJ).isoformat(),
                        "type": "circuit_open_respawn",
                        "from_job": jid_i,
                        "spawn": rem,
                        "new_job": new.get("id"),
                    }
                )
                save_json(STATE, state)
                stagnant_since = time.time()
                break

            # Resume paused own job when worker free
            run = (get("/jobs/pipeline").get("running")) or {}
            for jid_i in job_ids:
                j = job_by_id(jid_i) or {}
                if j.get("status") == "paused" and not run:
                    log(f"CORRECT resume own paused {jid_i}")
                    resume_job(jid_i)
                    state["corrections"].append(
                        {
                            "at": datetime.now(BJ).isoformat(),
                            "type": "resume",
                            "job_id": jid_i,
                        }
                    )
                    save_json(STATE, state)

            # Stagnant long running
            if run and stagnant >= STAGNANT_SEC:
                log(f"CORRECT stagnant {stagnant:.0f}s job={run.get('id')} → reap")
                reap_stale(max(300.0, STAGNANT_SEC * 0.6))
                state["corrections"].append(
                    {
                        "at": datetime.now(BJ).isoformat(),
                        "type": "reap_stale",
                        "job_id": run.get("id"),
                        "stagnant_sec": round(stagnant),
                    }
                )
                save_json(STATE, state)
                stagnant_since = time.time()

            # All terminal short of target
            total_prod = produce_sum(job_ids)
            remain = max(0, TARGET - total_prod)
            any_active = False
            for jid_i in job_ids:
                j = job_by_id(jid_i) or {}
                if j.get("status") in ("running", "queued", "pending", "paused"):
                    any_active = True
            if not any_active and remain > 0 and spawned_after_circuit < 12:
                log(f"CORRECT all-terminal remain={remain}")
                new = create_batch(remain, "fill_remain")
                job_ids.append(int(new["id"]))
                spawned_after_circuit += 1
                state["job_ids"] = job_ids
                state["corrections"].append(
                    {
                        "at": datetime.now(BJ).isoformat(),
                        "type": "fill_remain",
                        "spawn": remain,
                        "new_job": new.get("id"),
                    }
                )
                save_json(STATE, state)

            time.sleep(POLL_SEC)
        except Exception as exc:
            log(f"ERROR tick: {exc}")
            log(traceback.format_exc()[-400:])
            time.sleep(20)

    final = produce_sum(job_ids)
    log(f"TIMEOUT produced={final}/{TARGET}")
    report = {
        **state,
        "finished_at": datetime.now(BJ).isoformat(),
        "final_produced": final,
        "result": "timeout",
        "job_ids": job_ids,
        "last_jobs": [job_by_id(i) for i in job_ids],
    }
    save_json(STATE, report)
    save_json(REPORT, report)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
