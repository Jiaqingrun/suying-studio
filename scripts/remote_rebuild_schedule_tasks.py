#!/usr/bin/env python3
"""xlf ops: clear all timed tasks, probe real logins (no channels), create 3 windows.

Run on customer host (engine localhost:8766 GUI session) or via:
  ssh xlf-remote 'python3 -' < scripts/remote_rebuild_schedule_tasks.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

API = "http://127.0.0.1:8766"
DB = "/Users/xlf/Suying/data/montage.db"
BJ = timezone(timedelta(hours=8))
EXCLUDE_PLATFORMS = {"channels", "video_wechat", "shipinhao"}  # 视频号
WEEKDAYS = list(range(7))  # 周一=0 … 周日=6 与引擎一致
WINDOWS = [
    ("中午每日自动发布", "11:00:00", "13:00:00"),
    ("晚上每日自动发布", "18:00:00", "21:00:00"),
    ("睡前每日自动发布", "21:30:00", "23:30:00"),
]
PUBLISH_COUNT = 1
OPEN_SETTLE_SEC = 12


def get(path: str, timeout: float = 90.0) -> dict[str, Any]:
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path: str, body: dict | None = None, timeout: float = 180.0) -> dict[str, Any]:
    data = json.dumps({} if body is None else body).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"POST {path} {e.code}: {detail[:500]}") from e


def delete(path: str, timeout: float = 90.0) -> dict[str, Any]:
    req = urllib.request.Request(API + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"DELETE {path} {e.code}: {detail[:500]}") from e


def is_channels(platform: str | None) -> bool:
    p = str(platform or "").strip().lower()
    name_hint = p
    return p in EXCLUDE_PLATFORMS or "视频号" in str(platform or "")


def clear_all_tasks() -> None:
    tasks = get("/reach/publish/tasks").get("tasks") or []
    print(f"==> tasks before: {len(tasks)}")
    for t in tasks:
        tid = t.get("id")
        print(
            f"  - {tid} enabled={t.get('enabled')} name={t.get('name')!r} "
            f"targets={len(t.get('targets') or [])}"
        )
        try:
            res = delete(f"/reach/publish/tasks/{tid}")
            print(f"    deleted task {tid}: {res}")
        except Exception as exc:
            print(f"    delete task fail {tid}: {exc}")

    scheds = get("/reach/publish/schedules").get("schedules") or []
    for s in scheds:
        if not s.get("enabled"):
            continue
        sid = s.get("id")
        try:
            res = delete(f"/reach/publish/schedules/{sid}")
            print(f"  disabled schedule {sid}: {res}")
        except Exception as exc:
            print(f"  disable schedule fail {sid}: {exc}")

    con = sqlite3.connect(DB)
    cur = con.cursor()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    n = cur.execute(
        "UPDATE reach_publish_schedules SET enabled=0, updated_at=? "
        "WHERE customer_id=1 AND enabled=1",
        (now,),
    ).rowcount
    n2 = cur.execute(
        """
        UPDATE reach_publish_triggers
        SET status='cancelled', updated_at=?,
            note=COALESCE(note,'') || char(10) || 'ops: clear all timed tasks 2026-08-08'
        WHERE customer_id=1
          AND status IN (
            'pending','preparing','materialized','makeup_pending','blocked_reservation'
          )
        """,
        (now,),
    ).rowcount
    con.commit()
    con.close()
    print(f"==> db disabled schedules={n} cancelled triggers={n2}")


def list_profiles() -> list[dict[str, Any]]:
    data = get("/reach/chrome-profiles")
    return list(data.get("profiles") or [])


def probe_each_account() -> list[dict[str, Any]]:
    profiles = list_profiles()
    print(f"==> profiles total={len(profiles)}")
    results: list[dict[str, Any]] = []
    for p in profiles:
        name = str(p.get("name") or "")
        plat = str(p.get("platform") or "").strip().lower()
        provisioning = str(p.get("provisioning_status") or "")
        print(
            f"\n-- probe {name} platform={plat} provision={provisioning} "
            f"list_status={p.get('login_status')}"
        )
        if not name or not plat:
            results.append({**p, "probe_login": "stale_unknown", "probe_note": "no platform"})
            print("   skip: unbound")
            continue
        if is_channels(plat) or "视频号" in name:
            results.append(
                {
                    **p,
                    "probe_login": "excluded_channels",
                    "probe_note": "user exclude 视频号",
                }
            )
            print("   skip: 视频号 excluded")
            continue
        if provisioning != "explicit":
            results.append(
                {
                    **p,
                    "probe_login": "skipped_not_explicit",
                    "probe_note": "not explicit account",
                }
            )
            print("   skip: not explicit")
            continue
        try:
            open_res = post(
                "/reach/chrome-profiles/open",
                {"name": name, "platform": plat, "dry_run": False},
                timeout=120,
            )
            print(f"   open ok port={((open_res.get('open') or {}).get('cdp_port'))}")
        except Exception as exc:
            results.append(
                {**p, "probe_login": "open_failed", "probe_note": str(exc)[:200]}
            )
            print(f"   open FAIL: {exc}")
            continue
        time.sleep(OPEN_SETTLE_SEC)
        try:
            live = list_profiles()
            hit = next((x for x in live if x.get("name") == name), None)
            status = (hit or {}).get("login_status") or "stale_unknown"
            source = (hit or {}).get("login_status_source") or ""
            results.append(
                {
                    **(hit or p),
                    "probe_login": status,
                    "probe_note": source,
                }
            )
            print(f"   login_status={status} source={source}")
        except Exception as exc:
            results.append(
                {**p, "probe_login": "probe_failed", "probe_note": str(exc)[:200]}
            )
            print(f"   probe FAIL: {exc}")
    return results


def create_three_tasks(logged_in: list[dict[str, Any]]) -> None:
    if not logged_in:
        print("==> no verified logged-in accounts (excl 视频号); skip create")
        return
    targets = []
    for a in logged_in:
        targets.append(
            {
                "platform": str(a.get("platform")).strip().lower(),
                "chrome_profile": a.get("name"),
                "publish_count": PUBLISH_COUNT,
            }
        )
    print("\n==> create targets:")
    for t in targets:
        print(f"  {t['platform']} {t['chrome_profile']} x{t['publish_count']}")

    for name, start, end in WINDOWS:
        body = {
            "name": name,
            "timezone": "Asia/Shanghai",
            "targets": targets,
            "windows": [{"start": start, "end": end}],
            "weekdays": WEEKDAYS,
            "auto_production": True,
            "min_gap_minutes": 0,
            "enabled": True,
            "accept_risk": True,
            "template_name": "default-vertical",
            "theme": "default",
            "category": "default",
        }
        try:
            res = post("/reach/publish/tasks", body, timeout=120)
            task = res.get("task") or {}
            print(
                f"  CREATED {name} ({start}-{end}) task_id={task.get('id')} "
                f"schedules={len(task.get('targets') or [])}"
            )
        except Exception as exc:
            print(f"  CREATE FAIL {name}: {exc}")


def main() -> int:
    print("BJ", datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"))
    h = get("/health")
    print(
        "engine",
        h.get("engine_version"),
        h.get("status"),
        h.get("active_customer") or h.get("customer_name"),
    )
    # release publish slot lightly if stuck
    try:
        active = get("/reach/publish/runs/active/status")
        if active.get("active") and active.get("run_id"):
            print("active run", active.get("run_id"), active.get("status"), "— leave as-is")
    except Exception:
        pass

    print("\n===== 1. CLEAR TIMED TASKS =====")
    clear_all_tasks()

    print("\n===== 2. PROBE REAL LOGINS =====")
    probes = probe_each_account()
    verified = [
        p
        for p in probes
        if p.get("probe_login") == "verified_logged_in"
        and not is_channels(str(p.get("platform") or ""))
        and "视频号" not in str(p.get("name") or "")
    ]
    print("\n===== PROBE SUMMARY =====")
    by: dict[str, list[str]] = {}
    for p in probes:
        st = str(p.get("probe_login") or "?")
        by.setdefault(st, []).append(
            f"{p.get('name')}({p.get('platform')})"
        )
    for st, names in sorted(by.items()):
        print(f"  {st}: {len(names)} → {', '.join(names)}")
    print(f"verified_logged_in (non-channels): {len(verified)}")

    print("\n===== 3. CREATE 中午 / 晚上 / 睡前 =====")
    create_three_tasks(verified)

    print("\n===== FINAL TASKS =====")
    tasks = get("/reach/publish/tasks").get("tasks") or []
    for t in tasks:
        if not t.get("enabled"):
            continue
        print(
            f"  {t.get('name')} id={t.get('id')} targets="
            f"{[x.get('chrome_profile') for x in (t.get('targets') or [])]}"
        )
        wins = t.get("windows") or []
        print(f"    windows={wins}")
    print("DONE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("FATAL", exc)
        sys.exit(1)
