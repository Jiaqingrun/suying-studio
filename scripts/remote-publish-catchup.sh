#!/usr/bin/env bash
# Customer publish catch-up helper: free slot lightly, cool known wall profiles,
# enqueue non-wall deferred makeup, then tick schedules for due windows.
#
# Usage:
#   ./scripts/remote-publish-catchup.sh --host xlf-remote
#   ./scripts/remote-publish-catchup.sh --host xlf-remote --cool-profiles "视频号-5345,视频号-5331"
set -euo pipefail

HOST=""
PORT="${SUYING_PORT:-8766}"
COOL_PROFILES="${COOL_PROFILES:-视频号-5345}"
COOL_MINUTES="${COOL_MINUTES:-45}"
MAKEUP=1
TICK=1

usage() {
  cat <<'EOF'
用法:
  remote-publish-catchup.sh --host SSH_ALIAS [选项]

选项:
  --port N
  --cool-profiles "A,B"   登录墙账号冷却，逗号分隔（默认 视频号-5345）
  --cool-minutes N        冷却分钟（默认 45）
  --no-makeup             不提交 deferred 补发
  --no-tick               不 tick 定时
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --cool-profiles) COOL_PROFILES="${2:-}"; shift 2 ;;
    --cool-minutes) COOL_MINUTES="${2:-}"; shift 2 ;;
    --no-makeup) MAKEUP=0; shift ;;
    --no-tick) TICK=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }

ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
  PORT="$PORT" COOL_PROFILES="$COOL_PROFILES" COOL_MINUTES="$COOL_MINUTES" \
  MAKEUP="$MAKEUP" TICK="$TICK" \
  'bash -s' <<'REMOTE'
set -euo pipefail
export PORT COOL_PROFILES COOL_MINUTES MAKEUP TICK
python3 - <<'PY'
import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = f"http://127.0.0.1:{os.environ.get('PORT') or '8766'}"
COOL = {p.strip() for p in (os.environ.get("COOL_PROFILES") or "").split(",") if p.strip()}
COOL_MIN = max(5, int(os.environ.get("COOL_MINUTES") or "45"))
DO_MAKEUP = (os.environ.get("MAKEUP") or "1") == "1"
DO_TICK = (os.environ.get("TICK") or "1") == "1"


def get(path, timeout=20):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path, body=None, timeout=90):
    data = json.dumps({} if body is None else body).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


print("catchup · cool=", sorted(COOL), "minutes=", COOL_MIN)
active = get("/reach/publish/runs/active/status")
print(
    "slot",
    active.get("active"),
    active.get("run_id"),
    active.get("status"),
    active.get("source"),
    (active.get("current_item") or {}).get("chrome_profile"),
    (active.get("current_item") or {}).get("phase"),
)

# Only auto-cancel deferred_auto thrash on cooled wall profiles.
cur = (active.get("current_item") or {}).get("chrome_profile") or ""
src = str(active.get("source") or "")
if (
    active.get("active")
    and active.get("run_id")
    and src in ("deferred_auto", "deferred_manual")
    and cur in COOL
    and str(active.get("status") or "") in ("waiting_login", "running", "queued")
):
    print("cancel thrash", active.get("run_id"), cur)
    try:
        print(post(f"/reach/publish/runs/{active['run_id']}/cancel"))
    except Exception as exc:
        print("cancel err", exc)
    time.sleep(2)

# Cool wall-profile rows so deferred_auto does not thrash login walls.
# Use auto + future retry_after (not permanent manual) so engine chain resumes after cool.
db_candidates = [
    Path.home() / "Suying/data/montage.db",
    Path.home() / "Suying/data/db/montage.db",
]
db = next((p for p in db_candidates if p.is_file()), None)
if db:
    now = datetime.now(timezone.utc)
    cool_until = (now + timedelta(minutes=COOL_MIN)).replace(tzinfo=None).isoformat(sep=" ")
    stamp = now.replace(tzinfo=None).isoformat(sep=" ")
    ready_at = now.replace(tzinfo=None).isoformat(sep=" ")
    conn = sqlite3.connect(str(db))
    cur_db = conn.cursor()
    cool_list = tuple(COOL) if COOL else tuple()
    if cool_list:
        placeholders = ",".join("?" for _ in cool_list)
        rows = cur_db.execute(
            f"""
            SELECT id FROM reach_publish_run_items
            WHERE chrome_profile IN ({placeholders})
              AND phase IN ('deferred','failed','skipped','waiting_login','soft_skipped')
            """,
            cool_list,
        ).fetchall()
        for (item_id,) in rows:
            cur_db.execute(
                """
                UPDATE reach_publish_run_items
                SET phase=CASE WHEN phase IN ('waiting_login','soft_skipped') THEN 'deferred' ELSE phase END,
                    retry_mode='auto',
                    retry_after=?,
                    retry_run_id=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (cool_until, stamp, item_id),
            )
        print("cooled_wall_items", [r[0] for r in rows], "until", cool_until)
    # Promote other safe deferred phase rows to ready auto (engine also repairs; this helps pre-0.7.7).
    if cool_list:
        more_placeholders = ",".join("?" for _ in cool_list)
        ready_rows = cur_db.execute(
            f"""
            SELECT id, error FROM reach_publish_run_items
            WHERE phase='deferred'
              AND (retry_run_id IS NULL OR retry_run_id='')
              AND chrome_profile NOT IN ({more_placeholders})
            """,
            cool_list,
        ).fetchall()
    else:
        ready_rows = cur_db.execute(
            """
            SELECT id, error FROM reach_publish_run_items
            WHERE phase='deferred'
              AND (retry_run_id IS NULL OR retry_run_id='')
            """
        ).fetchall()
    promoted = []
    for item_id, err in ready_rows:
        err_s = err or ""
        # Permanent human walls only — 缺文案 is healable by engine (0.7.9+).
        hard = any(
            x in err_s
            for x in (
                "配置尚未完成登录",
                "禁止使用未登录",
                "账号未登录或验证卡住",
                "需要重新扫码",
                "true_login_wall",
                "CDP 不可用",
                "已有受管 Chrome 运行",
            )
        )
        if hard:
            cur_db.execute(
                "UPDATE reach_publish_run_items SET retry_mode='none', retry_after=NULL, updated_at=? WHERE id=?",
                (stamp, item_id),
            )
            continue
        cur_db.execute(
            """
            UPDATE reach_publish_run_items
            SET phase='deferred', retry_mode='auto', retry_after=?, retry_run_id=NULL, updated_at=?
            WHERE id=?
            """,
            (ready_at, stamp, item_id),
        )
        promoted.append(item_id)
    # Also promote failed/skipped asset gaps (engine heal path).
    soft_rows = cur_db.execute(
        """
        SELECT id, error FROM reach_publish_run_items
        WHERE phase IN ('failed','skipped')
          AND (retry_run_id IS NULL OR retry_run_id='')
          AND error LIKE '%缺文案%'
        """
    ).fetchall()
    for item_id, err in soft_rows:
        cur_db.execute(
            """
            UPDATE reach_publish_run_items
            SET phase='deferred', retry_mode='auto', retry_after=?, retry_run_id=NULL, updated_at=?
            WHERE id=?
            """,
            (ready_at, stamp, item_id),
        )
        promoted.append(item_id)
    conn.commit()
    conn.close()
    print("promoted_auto_deferred", promoted)

# Non-wall deferred phase makeup
if DO_MAKEUP:
    deferred = get("/reach/publish/deferred").get("items") or []
    ids = [
        int(i["id"])
        for i in deferred
        if i.get("phase") == "deferred"
        and (i.get("chrome_profile") or "") not in COOL
    ]
    active = get("/reach/publish/runs/active/status")
    if active.get("active"):
        print("makeup skipped: slot busy")
    elif not ids:
        print("makeup skipped: no non-wall deferred")
    else:
        res = post("/reach/publish/makeup/retry", {"item_ids": ids})
        print(
            "makeup",
            res.get("ok"),
            res.get("run_id"),
            res.get("status"),
            "n=",
            len(ids),
            ids,
        )

if DO_TICK:
    active = get("/reach/publish/runs/active/status")
    if active.get("active"):
        print("tick deferred (slot busy)")
    else:
        print("tick", post("/reach/publish/schedules/tick"))

active = get("/reach/publish/runs/active/status")
print(
    "final",
    active.get("active"),
    active.get("run_id"),
    active.get("status"),
    active.get("source"),
    (active.get("current_item") or {}).get("chrome_profile"),
    (active.get("current_item") or {}).get("phase"),
)
makeup = get("/reach/publish/makeup")
print("counts", makeup.get("counts"))
print("done")
PY
REMOTE
