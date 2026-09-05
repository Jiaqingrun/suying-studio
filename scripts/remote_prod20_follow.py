#!/usr/bin/env python3
"""Follow job produce counts until target; respawn on circuit_open."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "http://127.0.0.1:8766"
BJ = timezone(timedelta(hours=8))
TARGET = 20
STATE = "/tmp/prod20_state.json"
REPORT = "/tmp/prod20_report.json"
LOG = "/tmp/prod20_follow.log"


def log(msg: str) -> None:
    line = f"{datetime.now(BJ).strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get(path: str):
    with urllib.request.urlopen(API + path, timeout=45) as r:
        return json.loads(r.read().decode())


def post(path: str, body: dict):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        API + path, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def job_row(jid: int):
    con = sqlite3.connect("file:/Users/xlf/Suying/data/montage.db?mode=ro", uri=True)
    row = con.execute(
        "select status,produced_count,target_count,consecutive_failures from jobs where id=?",
        (jid,),
    ).fetchone()
    con.close()
    return row


def main() -> int:
    open(LOG, "w").write("")
    # pick newest 20-count product job running/queued
    con = sqlite3.connect("file:/Users/xlf/Suying/data/montage.db?mode=ro", uri=True)
    row = con.execute(
        "select id from jobs where target_count=20 and theme='产品' order by id desc limit 1"
    ).fetchone()
    con.close()
    jid = int(row[0]) if row else 926
    produced_total = 0
    state = {
        "started_follow": datetime.now(BJ).isoformat(),
        "primary_job_id": jid,
        "job_ids": [jid],
        "target": TARGET,
        "corrections": [],
    }
    json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=2)
    log(f"follow job={jid}")
    last_print = -1
    while True:
        try:
            pipe = get("/jobs/pipeline")
            run = pipe.get("running") or {}
            row = job_row(jid)
            if not row:
                log(f"missing job {jid}")
                time.sleep(30)
                continue
            st, prod, tgt, cf = row[0], int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
            # best-effort cumulative from all state jobs
            cum = 0
            for jj in state["job_ids"]:
                rr = job_row(int(jj))
                if rr:
                    cum += int(rr[1] or 0)
            produced_total = cum
            if produced_total != last_print or st in ("completed", "circuit_open"):
                log(
                    f"cum={produced_total}/{TARGET} job={jid}:{st} {prod}/{tgt} "
                    f"cf={cf} pipe={run.get('id')}:{run.get('phase')} "
                    f"ev={(pipe.get('recent_events') or [{}])[0].get('message','')}"
                )
                last_print = produced_total
            if produced_total >= TARGET or (st == "completed" and prod >= tgt and produced_total >= TARGET):
                log(f"SUCCESS cum={produced_total}")
                state.update(
                    {
                        "result": "success",
                        "final_produced": produced_total,
                        "finished_at": datetime.now(BJ).isoformat(),
                    }
                )
                json.dump(state, open(REPORT, "w"), ensure_ascii=False, indent=2)
                json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=2)
                return 0
            if st == "completed" and produced_total < TARGET:
                rem = TARGET - produced_total
                log(f"completed partial, remain={rem}")
                res = post(
                    "/jobs",
                    {
                        "mode": "count",
                        "target_count": rem,
                        "template_name": "default-vertical",
                        "theme": "产品",
                        "category": "default",
                        "orientation": "portrait",
                        "use_active_rule": True,
                        "rush": True,
                        "customer_name": "北京始峰伟业",
                    },
                )
                jid = int(res["id"])
                state["job_ids"].append(jid)
                state["corrections"].append(
                    {"type": "fill_after_complete", "job": res, "at": datetime.now(BJ).isoformat()}
                )
                json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=2)
                log(f"RESPAWN {res}")
            if st == "circuit_open" and produced_total < TARGET:
                rem = TARGET - produced_total
                log(f"circuit_open rem={rem}")
                res = post(
                    "/jobs",
                    {
                        "mode": "count",
                        "target_count": rem,
                        "template_name": "default-vertical",
                        "theme": "产品",
                        "category": "default",
                        "orientation": "portrait",
                        "use_active_rule": True,
                        "rush": True,
                        "customer_name": "北京始峰伟业",
                    },
                )
                jid = int(res["id"])
                state["job_ids"].append(jid)
                state["corrections"].append(
                    {"type": "respawn_circuit", "job": res, "at": datetime.now(BJ).isoformat()}
                )
                json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=2)
                log(f"RESPAWN {res}")
            time.sleep(45)
        except Exception as exc:
            log(f"err {exc}")
            time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
