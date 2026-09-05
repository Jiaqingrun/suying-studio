#!/usr/bin/env bash
# Real-time customer-machine publish monitor (SSH → engine localhost APIs).
# Designed for Cursor/ops terminal; refresh interval defaults to 5s.
#
# Usage:
#   ./scripts/watch-remote-publish.sh --host xlf-remote
#   ./scripts/watch-remote-publish.sh --host xlf-remote --interval 3
#   ./scripts/watch-remote-publish.sh --host xlf-remote --once
set -euo pipefail

HOST=""
PORT="${SUYING_PORT:-8766}"
INTERVAL="${WATCH_INTERVAL:-5}"
ONCE=0

usage() {
  cat <<'EOF'
用法:
  watch-remote-publish.sh --host SSH_ALIAS [选项]

选项:
  --port N           引擎端口，默认 8766
  --interval SECONDS 刷新间隔（默认 5）
  --once             只打一轮后退出
  -h, --help         帮助
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-}"; shift 2 ;;
    --once) ONCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: --interval 必须为正整数秒" >&2
  exit 2
}

probe_once() {
  ssh -o BatchMode=yes -o ConnectTimeout=12 "$HOST" \
    PORT="$PORT" 'bash -s' <<'REMOTE'
set -euo pipefail
API="http://127.0.0.1:${PORT}"
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "ERROR: 远端无 python3" >&2; exit 2; }

export API
"$PY" - <<'PY'
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    BJ = ZoneInfo("Asia/Shanghai")
except Exception:
    BJ = timezone.utc

API = os.environ.get("API") or "http://127.0.0.1:8766"


def get(path: str, timeout: float = 8.0):
    try:
        with urllib.request.urlopen(API + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"_error": str(exc), "path": path}


def short(s, n=90):
    s = str(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


now_bj = datetime.now(BJ)
today = now_bj.date().isoformat()
print("=== 速影 · 客户机发布/补发 · 详细实时 ===")
print(f"北京时间 {now_bj.strftime('%Y-%m-%d %H:%M:%S')}  今日={today}")

health = get("/health")
boot = health.get("boot") or {}
ws = health.get("workspace") or {}
counts_db = (ws.get("db_counts") or {}) if isinstance(ws, dict) else {}
print(
    f"引擎 v{health.get('engine_version') or health.get('_error') or '?'} "
    f"status={health.get('status') or '?'} "
    f"boot={boot.get('boot_phase') or '?'} "
    f"workspace={health.get('workspace_state') or ws.get('state') or '?'} "
    f"客户={health.get('active_customer') or health.get('customer_name') or '?'}"
)
if counts_db:
    print(
        "  库 "
        f"jobs={counts_db.get('jobs','?')} "
        f"成片={counts_db.get('render_outputs','?')} "
        f"触达队列={counts_db.get('reach_queue','?')}"
    )

active = get("/reach/publish/runs/active/status")
if active.get("_error"):
    print(f"ACTIVE 查询失败: {active.get('_error')}")
else:
    is_active = bool(active.get("active"))
    run_id = active.get("run_id") or "-"
    status = active.get("status") or "-"
    err = short(active.get("error"), 160)
    counts = active.get("counts") or {}
    cur = active.get("current_item") or {}
    cfg = active.get("config") or active.get("config_json") or {}
    src = active.get("source") or cfg.get("source") or "-"
    fs = cfg.get("finish_summary") if isinstance(cfg, dict) else None
    print(
        f"[槽] active={is_active}  run={run_id}  status={status}  source={src}"
    )
    if counts:
        print(
            "  进度 "
            f"总{counts.get('total',0)} "
            f"已发{counts.get('published',0)} "
            f"失败{counts.get('failed',0)} "
            f"跳过{counts.get('skipped',0)} "
            f"待补{counts.get('deferred',0)} "
            f"待确认{counts.get('awaiting_confirmation',0)} "
            f"剩余{counts.get('remaining',0)}"
        )
    if isinstance(fs, dict) and fs:
        print(
            "  finish_summary "
            + ", ".join(f"{k}={v}" for k, v in list(fs.items())[:10])
        )
    if cur:
        print(
            f"  当前条 item=#{cur.get('id')} 账号={cur.get('chrome_profile') or '-'} "
            f"平台={cur.get('platform') or '-'} phase={cur.get('phase') or '-'} "
            f"ordinal={cur.get('ordinal') if cur.get('ordinal') is not None else '-'}"
        )
        for k in ("title", "output_id", "queue_id", "retry_mode", "retry_after"):
            if cur.get(k) not in (None, ""):
                print(f"    {k}={short(cur.get(k), 80)}")
        if cur.get("error"):
            print(f"  条目错: {short(cur.get('error'), 160)}")
        evidence = cur.get("evidence_json") or cur.get("evidence") or {}
        if isinstance(evidence, dict) and evidence.get("fail_forward"):
            print(f"  fail_forward={evidence.get('fail_forward')}")
    deadline = cfg.get("login_wall_deadline") if isinstance(cfg, dict) else None
    if deadline:
        print(f"  登录宽限截止: {deadline}")
    if err and status in (
        "waiting_login",
        "paused_human",
        "failed",
        "outcome_unknown",
        "interrupted_system",
    ):
        print(f"  run错: {err}")

# Detailed deferred + makeup from API
makeup = get("/reach/publish/makeup")
if makeup.get("_error"):
    print(f"MAKEUP 查询失败: {makeup.get('_error')}")
else:
    c = makeup.get("counts") or {}
    print(
        f"[补发] deferred={c.get('deferred', 0)}  "
        f"过窗missed={c.get('missed_human_confirm', 0)}  "
        f"makeup_pending={c.get('makeup_pending', 0)}  "
        f"可 auto={c.get('auto_ready', c.get('deferred_auto', '?'))}"
    )
    deferred = list(makeup.get("deferred") or [])
    if deferred:
        by_p = Counter(i.get("chrome_profile") or "?" for i in deferred)
        by_mode = Counter(str(i.get("retry_mode") or "none") for i in deferred)
        by_plat = Counter(i.get("platform") or "?" for i in deferred)
        print(
            "  deferred 分布 账号="
            + ", ".join(f"{k}:{v}" for k, v in by_p.most_common(10))
        )
        print(
            "  deferred retry_mode="
            + dict(by_mode).__repr__()
            + "  平台="
            + ", ".join(f"{k}:{v}" for k, v in by_plat.most_common(6))
        )
        # next 8 lines with cooling / error
        print("  待补明细(最多12条):")
        for i in deferred[:12]:
            ra = i.get("retry_after") or ""
            cool = f" cool_until={ra}" if ra else ""
            print(
                f"    · #{i.get('id')} {i.get('chrome_profile') or '?'} "
                f"{i.get('platform') or ''} mode={i.get('retry_mode') or '-'} "
                f"phase={i.get('phase') or '-'}{cool} "
                f"err={short(i.get('error'), 70)}"
            )
        if len(deferred) > 12:
            print(f"    …另有 {len(deferred)-12} 条")
    today_trigs = [
        t
        for t in (makeup.get("triggers") or [])
        if (t.get("local_date") == today)
        or (today in str(t.get("planned_at") or ""))
    ]
    if today_trigs:
        print(
            "  今日 trigger(makeup视图): "
            + str(Counter(t.get("status") for t in today_trigs))
        )
        openish = [
            t
            for t in today_trigs
            if (t.get("status") or "")
            in ("pending", "preparing", "makeup_pending", "missed_human_confirm")
        ]
        for t in openish[:8]:
            print(
                f"    · t#{t.get('id')} {t.get('chrome_profile') or t.get('profile') or '?'} "
                f"st={t.get('status')} planned={t.get('planned_at') or '-'} "
                f"win={t.get('window_end') or '-'}"
            )

# Schedules: evening + any due-like summary
schedules = get("/reach/publish/schedules", timeout=14)
rows = schedules.get("schedules") or []
enabled = [s for s in rows if s.get("enabled")]
print(f"[计划] 启用中 {len(enabled)}/{len(rows)}")
evening = [s for s in enabled if "晚上" in (s.get("name") or "")]
morning = [s for s in enabled if "早上" in (s.get("name") or "") or "早晨" in (s.get("name") or "")]
noon = [s for s in enabled if "中午" in (s.get("name") or "") or "午" in (s.get("name") or "")]
for label, group in (("早班", morning), ("午班", noon), ("晚班", evening)):
    if not group:
        continue
    done = Counter()
    open_rows = []
    for s in group:
        prev = get(f"/reach/publish/schedules/{s.get('id')}/preview", timeout=8)
        if prev.get("_error"):
            continue
        for t in prev.get("triggers") or []:
            if t.get("local_date") != today and today not in str(t.get("planned_at") or ""):
                continue
            st = t.get("status") or "?"
            done[st] += 1
            if st in ("pending", "blocked_reservation", "preparing", "materialized"):
                open_rows.append(
                    (
                        st,
                        s.get("chrome_profile") or s.get("name"),
                        t.get("planned_at"),
                        t.get("window_end"),
                        t.get("id"),
                    )
                )
    print(f"  {label}今日: {dict(done) or '{}'}")
    for st, prof, pa, we, tid in open_rows[:10]:
        print(f"    · t#{tid} {prof} {st} planned={pa} end={we}")
    if len(open_rows) > 10:
        print(f"    …另有开放 {len(open_rows)-10} 条")

# SQLite deep peek (when mounted on customer host) — optional, skip if miss
try:
    import sqlite3
    from pathlib import Path

    db = Path.home() / "Suying/data/montage.db"
    if db.is_file():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        ts = con.execute(
            "select status, count(*) c from reach_publish_triggers "
            "where customer_id=1 group by status"
        ).fetchall()
        print("[DB trigger] " + ", ".join(f"{r['status']}={r['c']}" for r in ts))
        due = con.execute(
            """
            select t.id, t.status, s.chrome_profile, t.planned_at, t.window_end,
                   (select o.status from automation_occurrences o
                    where o.occurrence_key=t.occurrence_key limit 1) occ
            from reach_publish_triggers t
            join reach_publish_schedules s on s.id=t.schedule_id
            where t.customer_id=1
              and t.status in ('pending','preparing','materialized')
              and datetime(t.planned_at) <= datetime('now')
              and (t.window_end is null or datetime(t.window_end) >= datetime('now'))
            order by t.planned_at
            limit 12
            """
        ).fetchall()
        print(f"[DB 窗内 due] n={len(due)}")
        for r in due:
            print(
                f"  · t#{r['id']} {r['chrome_profile']} st={r['status']} "
                f"occ={r['occ'] or '-'} planned={r['planned_at']} end={r['window_end']}"
            )
        recent = con.execute(
            "select run_id, status, updated_at from reach_publish_runs "
            "where customer_id=1 order by id desc limit 5"
        ).fetchall()
        print("[DB 近5批] " + " | ".join(f"{r['run_id'][:8]}…={r['status']}" for r in recent))
        occ = con.execute(
            "select id, status, production_job_id, publish_run_id, "
            "substr(coalesce(error,''),1,60) err from automation_occurrences "
            "order by id desc limit 4"
        ).fetchall()
        print("[DB 近OCC]")
        for r in occ:
            print(
                f"  · occ#{r['id']} st={r['status']} job={r['production_job_id']} "
                f"run={r['publish_run_id'] or '-'} {r['err'] or ''}"
            )
        jobs = con.execute(
            "select id, status from jobs order by id desc limit 4"
        ).fetchall()
        print(
            "[DB 近JOB] "
            + " | ".join(f"#{r['id']}={r['status']}" for r in jobs)
        )
        con.close()
except Exception as exc:
    print(f"[DB] 跳过 ({exc})")

runtime = get("/ops/runtime-health", timeout=6)
if not runtime.get("_error"):
    pause = runtime.get("pause") if isinstance(runtime.get("pause"), dict) else {}
    print(
        "[运行时] "
        f"accepts_new_work={runtime.get('accepts_new_work', pause.get('accepts_new_work'))} "
        f"pause={pause.get('state') or runtime.get('paused')} "
        f"slots={runtime.get('slots') or runtime.get('job_slots') or '-'}"
    )
alerts = get("/reach/publish/human-alerts?limit=5")
if not alerts.get("_error"):
    items = alerts.get("alerts") or alerts.get("items") or []
    open_a = [a for a in items if not a.get("acked") and not a.get("resolved")]
    if open_a or items:
        print(f"[人机告警] open≈{len(open_a)} recent={len(items)}")
        for a in (open_a or items)[:4]:
            print(
                f"  · {a.get('kind') or a.get('type') or '?'} "
                f"{short(a.get('message') or a.get('title'), 100)}"
            )
print("--- 本轮结束 ---")
PY
REMOTE
}


if [[ "$ONCE" -eq 1 ]]; then
  probe_once
  exit 0
fi

echo "开始监测 host=${HOST} port=${PORT} interval=${INTERVAL}s  (Ctrl+C 结束)"
echo
while true; do
  # Prefer append-style output in Cursor so history is scrollable.
  # Use \n separators instead of clear (clear wipes transcript).
  printf '\n──────── %s ────────\n' "$(date '+%H:%M:%S')"
  if ! probe_once; then
    echo "(本轮探测失败，将重试)"
  fi
  echo "刷新间隔 ${INTERVAL}s · host=${HOST} · Ctrl+C 退出"
  sleep "$INTERVAL"
done
