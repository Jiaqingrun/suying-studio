#!/usr/bin/env python3
"""CDP login probe (no 视频号) → 中午/晚上/睡前 multi-account auto tasks.

Creator-home without login wall is accepted as logged-in (matches chrome_publish
maybe→ready). Engine DOM form/shell still preferred when available.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

API = "http://127.0.0.1:8766"
BJ = timezone(timedelta(hours=8))
OPEN_SETTLE_SEC = 10
OPEN_RETRY_SETTLE = 14
OPEN_TIMEOUT = 55
WEEKDAYS = list(range(7))
WINDOWS = [
    ("中午每日自动发布", "11:00:00", "13:00:00"),
    ("晚上每日自动发布", "18:00:00", "21:00:00"),
    ("睡前每日自动发布", "21:30:00", "23:30:00"),
]
PUBLISH_COUNT = 1
STUDIO_ROOT = (
    "/Users/xlf/Applications/速影 Studio.app/Contents/Resources/runtime/studio"
)
CREATOR_HOSTS = {
    "xhs": ("creator.xiaohongshu.com", "www.xiaohongshu.com"),
    "douyin": ("creator.douyin.com",),
    "kuaishou": ("cp.kuaishou.com", "creator.kuaishou.com"),
}


def log(msg: str) -> None:
    print(msg, flush=True)


def get(path: str, timeout: float = 45.0) -> dict[str, Any]:
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path: str, body: dict | None = None, timeout: float = 60.0) -> dict[str, Any]:
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
        raise RuntimeError(f"POST {path} {e.code}: {detail[:400]}") from e


def delete(path: str, timeout: float = 45.0) -> dict[str, Any]:
    req = urllib.request.Request(API + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"DELETE {path} {e.code}: {detail[:400]}") from e


def is_channels(platform: str | None, name: str = "") -> bool:
    p = str(platform or "").strip().lower()
    n = str(name or "")
    return p in {"channels", "video_wechat", "shipinhao"} or "视频号" in n


def setup_path() -> None:
    if STUDIO_ROOT not in sys.path:
        sys.path.insert(0, STUDIO_ROOT)


def probe_login_on_port(port: int, platform: str) -> dict[str, Any]:
    from engine.reach.browser import entry_for_platform
    from engine.reach.cdp_client import CdpSession, list_tabs
    from engine.reach.publish_runner import _probe_login_ready_across_frames

    entry = entry_for_platform(platform)
    host = (urlparse(str(entry.get("url") or "")).hostname or "").lower()
    pages = [
        tab
        for tab in list_tabs(f"http://127.0.0.1:{int(port)}")
        if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")
    ]
    pages.sort(
        key=lambda tab: (
            0
            if host and host in (urlparse(str(tab.get("url") or "")).hostname or "").lower()
            else 1,
            0 if "/login" not in str(tab.get("url") or "").lower() else 1,
        )
    )
    if not pages:
        return {"login_status": "stale_unknown", "note": "no_pages"}
    sess = CdpSession(str(pages[0]["webSocketDebuggerUrl"]), timeout=5.0)
    try:
        probe = _probe_login_ready_across_frames(sess)
    finally:
        sess.close()
    page_url = str(pages[0].get("url") or probe.get("url") or "")
    status = (
        "logged_out"
        if probe.get("login")
        else "verified_logged_in"
        if probe.get("ready") and not probe.get("blocked")
        else "stale_unknown"
    )
    # Ops soft accept: creator-domain home without login wall (matches chrome_publish).
    if status == "stale_unknown" and not probe.get("login") and not probe.get("blocked"):
        ph = (urlparse(page_url).hostname or "").lower()
        allowed = CREATOR_HOSTS.get(platform.strip().lower(), ())
        if any(ph == h or ph.endswith("." + h) for h in allowed) and "/login" not in page_url.lower():
            status = "verified_logged_in"
            note = "creator_domain_no_wall"
        else:
            note = "cdp_direct"
    else:
        note = "cdp_ready" if status == "verified_logged_in" else "cdp_direct"
    return {
        "login_status": status,
        "probe": probe,
        "page_url": page_url,
        "note": note,
    }


def list_profiles() -> list[dict[str, Any]]:
    return list(get("/reach/chrome-profiles", timeout=60).get("profiles") or [])


def open_profile(name: str, platform: str) -> int | None:
    open_res = post(
        "/reach/chrome-profiles/open",
        {"name": name, "platform": platform, "dry_run": False},
        timeout=OPEN_TIMEOUT,
    )
    port = (open_res.get("open") or {}).get("cdp_port")
    return int(port) if port else None


def probe_account(name: str, platform: str) -> dict[str, Any]:
    log(f"  open {name} …")
    last_err = ""
    for attempt, settle in enumerate((OPEN_SETTLE_SEC, OPEN_RETRY_SETTLE), start=1):
        try:
            port = open_profile(name, platform)
            log(f"  open ok port={port} try={attempt}")
            if not port:
                return {"login_status": "open_no_port"}
        except Exception as exc:
            last_err = str(exc)[:200]
            log(f"  open FAIL try={attempt}: {exc}")
            time.sleep(2)
            continue
        time.sleep(settle)
        try:
            res = probe_login_on_port(int(port), platform)
            log(
                f"  status={res.get('login_status')} note={res.get('note')} "
                f"page={(res.get('page_url') or '')[:90]}"
            )
            if res.get("login_status") != "probe_failed":
                return res
        except Exception as exc:
            last_err = str(exc)[:200]
            log(f"  cdp FAIL try={attempt}: {exc}")
            time.sleep(2)
    return {"login_status": "probe_failed", "error": last_err}


def clear_enabled_tasks() -> None:
    """Remove previously created enabled task groups before rewrite."""
    tasks = get("/reach/publish/tasks").get("tasks") or []
    for t in tasks:
        if not t.get("enabled"):
            continue
        tid = t.get("id")
        name = t.get("name")
        log(f"  delete enabled task {name} id={tid}")
        try:
            delete(f"/reach/publish/tasks/{tid}")
        except Exception as exc:
            log(f"  delete FAIL {exc}")


def create_tasks_orm(verified: list[dict[str, Any]]) -> None:
    setup_path()
    os.environ.setdefault("SUYING_DATA_ROOT", "/Users/xlf/Suying/data")
    from engine.catalog.db import Customer, ReachPublishSchedule, get_session

    for task_name, start, end in WINDOWS:
        session = get_session()
        try:
            customer = session.query(Customer).filter_by(name="北京始峰伟业").one()
            cid = int(customer.id)
            task_group_id = secrets.token_hex(8)
            ids: list[int] = []
            for v in verified:
                windows = [
                    {
                        "start": start,
                        "end": end,
                        "weekdays": WEEKDAYS,
                        "count": PUBLISH_COUNT,
                        "min_gap_minutes": 0,
                    }
                ]
                source_config = {
                    "task_group_id": task_group_id,
                    "task_name": task_name,
                    "auto_production": True,
                    "template_name": "default-vertical",
                    "theme": "default",
                    "category": "default",
                    "accept_risk": True,
                    "schedule_revision": 1,
                    "ops_created": "cdp_verified_2026-08-08",
                    "ops_login_note": v.get("note") or "",
                }
                row = ReachPublishSchedule(
                    customer_id=cid,
                    name=task_name,
                    enabled=True,
                    timezone="Asia/Shanghai",
                    chrome_profile=v["name"],
                    platform=v["platform"],
                    content_source="generate_then_publish",
                    source_config_json=source_config,
                    times_json=[],
                    windows_json=windows,
                    items_per_trigger=1,
                    repeat_count=PUBLISH_COUNT,
                    random_algorithm="window_seconds_v2",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(row)
                session.flush()
                ids.append(int(row.id))
            session.commit()
            # Triggers materialize on engine tick (ensure_window_triggers). Calling
            # it here races the live App process on occurrence_key uniqueness.
            log(
                f"  CREATED {task_name} group={task_group_id} "
                f"accounts={len(ids)} windows={start}-{end} "
                f"(triggers via engine tick)"
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def main() -> int:
    setup_path()
    log(f"BJ {datetime.now(BJ).strftime('%Y-%m-%d %H:%M:%S')}")
    h = get("/health")
    log(f"engine {h.get('engine_version')} {h.get('status')}")

    profiles = list_profiles()
    candidates = []
    for p in profiles:
        name = str(p.get("name") or "")
        plat = str(p.get("platform") or "").strip().lower()
        if str(p.get("provisioning_status") or "") != "explicit":
            continue
        if not name or not plat or is_channels(plat, name):
            if is_channels(plat, name):
                log(f"skip 视频号: {name}")
            continue
        candidates.append(p)
    log(f"to_probe={len(candidates)}")

    verified: list[dict[str, Any]] = []
    report: list[str] = []
    for p in candidates:
        name = p["name"]
        plat = str(p["platform"]).strip().lower()
        log(f"\n== probe {name} ({plat})")
        res = probe_account(name, plat)
        st = res.get("login_status")
        note = res.get("note") or ""
        report.append(f"{name}|{plat}|{st}|{note}|{(res.get('page_url') or '')[:55]}")
        if st == "verified_logged_in":
            verified.append(
                {
                    "name": name,
                    "platform": plat,
                    "probe": res,
                    "note": note,
                }
            )

    log("\n===== PROBE SUMMARY =====")
    for line in report:
        log(f"  {line}")
    log(f"verified_logged_in={len(verified)}")
    for v in verified:
        log(f"  OK {v['platform']} {v['name']} ({v.get('note')})")

    if not verified:
        log("no verified accounts — stop")
        return 0

    log("\n===== CLEAR PREVIOUS ENABLED TASKS =====")
    clear_enabled_tasks()

    log("\n===== CREATE 3 WINDOW TASKS =====")
    create_tasks_orm(verified)

    log("\n===== FINAL ENABLED TASKS =====")
    for t in get("/reach/publish/tasks").get("tasks") or []:
        if t.get("enabled"):
            log(
                f"  {t.get('name')} id={t.get('id')} "
                f"targets={[x.get('chrome_profile') for x in (t.get('targets') or [])]}"
            )
            log(f"    windows={t.get('windows')}")
    log("DONE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"FATAL {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
