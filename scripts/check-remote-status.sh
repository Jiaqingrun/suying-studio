#!/usr/bin/env bash
# Query runtime status on a customer Mac over SSH (health / jobs / pause / recent fails).
# Usage:
#   ./scripts/check-remote-status.sh --host xlf-remote
#   ./scripts/check-remote-status.sh --host xlf-remote --watch 30
#   ./scripts/check-remote-status.sh --host xlf-remote --job-id 123
set -euo pipefail

HOST=""
PORT="${SUYING_PORT:-8766}"
WATCH=""
JOB_ID=""
ONCE_JSON=0

usage() {
  cat <<'EOF'
用法:
  check-remote-status.sh --host SSH_ALIAS [选项]

选项:
  --port N          引擎端口，默认 8766
  --watch SECONDS   每 SECONDS 秒轮询一次（默认不轮询）
  --job-id ID       额外查询指定任务详情
  --json            只输出远端 JSON（便于管道）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --watch) WATCH="${2:-}"; shift 2 ;;
    --job-id) JOB_ID="${2:-}"; shift 2 ;;
    --json) ONCE_JSON=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }
[[ -z "$WATCH" || "$WATCH" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: --watch 必须为正整数秒" >&2
  exit 2
}

remote_probe() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" \
    PORT="$PORT" JOB_ID="$JOB_ID" ONCE_JSON="$ONCE_JSON" 'bash -s' <<'REMOTE'
set -euo pipefail
API="http://127.0.0.1:${PORT}"
PY_CANDIDATES=(
  "/Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3"
  "$HOME/Desktop/速影 Studio.app/Contents/Resources/runtime/python/bin/python3"
  "/Applications/速影.app/Contents/Resources/runtime/python/bin/python3"
  "$HOME/Desktop/速影.app/Contents/Resources/runtime/python/bin/python3"
)
PY=""
for c in "${PY_CANDIDATES[@]}"; do
  if [[ -x "$c" ]]; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "ERROR: 找不到 python3" >&2; exit 2; }

BUNDLE_VERSION=""
BUNDLE_FLAVOR=""
for app in \
  "/Applications/速影 Studio.app" \
  "$HOME/Desktop/速影 Studio.app" \
  "/Applications/速影.app" \
  "$HOME/Desktop/速影.app"; do
  if [[ -d "$app" ]]; then
    BUNDLE_VERSION="$(cat "$app/Contents/Resources/runtime/BUNDLE_VERSION" 2>/dev/null || true)"
    BUNDLE_FLAVOR="$(cat "$app/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true)"
    APP_PATH="$app"
    break
  fi
done

fetch() {
  local path="$1"
  curl -fsS --max-time 5 "${API}${path}" 2>/dev/null || echo '{"error":"unreachable"}'
}

HEALTH="$(fetch /health)"
RUNTIME="$(fetch /ops/runtime-health)"
JOBS="$(fetch '/jobs?limit=8')"
OLLAMA="$(fetch /health/ollama)"
LICENSE="$(fetch /license/status)"
JOB_DETAIL='{"skipped":true}'
if [[ -n "${JOB_ID:-}" ]]; then
  JOB_DETAIL="$(fetch "/jobs/${JOB_ID}")"
fi

export HEALTH RUNTIME JOBS OLLAMA LICENSE JOB_DETAIL BUNDLE_VERSION BUNDLE_FLAVOR APP_PATH ONCE_JSON
"$PY" - <<'PY'
import json
import os
from datetime import datetime

def loads(name):
    try:
        return json.loads(os.environ.get(name) or "{}")
    except Exception as exc:
        return {"error": f"parse_failed:{exc}"}

health = loads("HEALTH")
runtime = loads("RUNTIME")
jobs = loads("JOBS")
ollama = loads("OLLAMA")
license_status = loads("LICENSE")
job_detail = loads("JOB_DETAIL")
payload = {
    "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "app_path": os.environ.get("APP_PATH") or None,
    "bundle_version": os.environ.get("BUNDLE_VERSION") or None,
    "bundle_flavor": os.environ.get("BUNDLE_FLAVOR") or None,
    "health": health,
    "runtime_health": runtime,
    "ollama": ollama,
    "license": license_status,
    "license_kind": license_status.get("license_kind") if isinstance(license_status, dict) else None,
    "license_issue_seq": license_status.get("issue_seq") if isinstance(license_status, dict) else None,
    "license_expires_at": license_status.get("expires_at") if isinstance(license_status, dict) else None,
    "license_device_key_id": license_status.get("device_key_id") if isinstance(license_status, dict) else None,
    "license_authorized": license_status.get("authorized") if isinstance(license_status, dict) else None,
    "license_code": (license_status.get("code") if isinstance(license_status, dict) else None) or "",
    "recent_jobs": jobs,
    "job_detail": job_detail,
}
if os.environ.get("ONCE_JSON") == "1":
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0)

def dig(obj, *keys, default=None):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur

print("=== 速影客户机状态 ===")
print(f"时间: {payload['checked_at']}")
print(f"App: {payload['app_path'] or '未找到'}")
print(f"版本: {payload['bundle_version'] or '?'} / flavor={payload['bundle_flavor'] or '?'}")
print(f"引擎: status={dig(health,'status', default='?')} version={dig(health,'engine_version', default='?')}")
print(f"客户: {dig(health,'active_customer', default='?')} (id={dig(health,'active_customer_id', default='?')})")
print(f"工作区: {dig(health,'workspace_state', default='?')} db_ok={dig(health,'workspace','db_ok', default='?')}")
print(
    "许可: "
    f"kind={dig(license_status,'license_kind', default='?')} "
    f"authorized={dig(license_status,'authorized', default='?')} "
    f"seq={dig(license_status,'issue_seq', default='?')} "
    f"expires={dig(license_status,'expires_at', default='-')} "
    f"code={dig(license_status,'code', default='') or '-'}"
)
print(
    "运行时: "
    f"accepts_new_work={dig(runtime,'accepts_new_work', default=dig(runtime,'pause','accepts_new_work', default='?'))} "
    f"paused={dig(runtime,'pause','is_paused', default=dig(runtime,'paused', default='?'))} "
    f"state={dig(runtime,'pause','state', default='?')} "
    f"reasons={dig(runtime,'pause','pause_reasons', default=dig(runtime,'pause_reason', default=''))}"
)
ready = dig(ollama, "ready", default=dig(ollama, "ok", default="?"))
models = dig(ollama, "models", default=[])
if isinstance(models, list):
    names = []
    for item in models:
        if isinstance(item, dict):
            names.append(str(item.get("name") or item.get("model") or ""))
        else:
            names.append(str(item))
    model_s = ",".join([n for n in names if n]) or "-"
else:
    model_s = str(models)
print(f"Ollama: ready={ready} models={model_s}")

job_rows = jobs
if isinstance(jobs, dict):
    job_rows = jobs.get("jobs") or jobs.get("items") or jobs.get("data") or []
if not isinstance(job_rows, list):
    job_rows = []
print("--- 最近任务 ---")
if not job_rows:
    print("(无)")
else:
    for row in job_rows[:8]:
        if not isinstance(row, dict):
            print(row)
            continue
        jid = row.get("id") or row.get("job_id") or "?"
        status = row.get("status") or "?"
        produced = row.get("produced_count")
        target = row.get("target_count")
        template = row.get("template_name") or ""
        err = row.get("error") or row.get("last_error") or ""
        updated = row.get("updated_at") or row.get("finished_at") or row.get("created_at") or ""
        line = f"#{jid} {status}"
        if produced is not None:
            line += f" produced={produced}"
            if target is not None:
                line += f"/{target}"
        if template:
            line += f" tpl={template}"
        if updated:
            line += f" @ {updated}"
        if err:
            line += f" err={err}"
        print(line)

pools = dig(runtime, "resource_gate", "pools", default={})
if isinstance(pools, dict) and pools:
    busy = []
    for name, pool in pools.items():
        if not isinstance(pool, dict):
            continue
        used = pool.get("used") or 0
        holders = pool.get("holders") or []
        if used:
            busy.append(f"{name}={used}:{','.join(map(str, holders))}")
    print("资源槽: " + (", ".join(busy) if busy else "空闲"))

if not job_detail.get("skipped"):
    print("--- 指定任务 ---")
    print(json.dumps(job_detail, ensure_ascii=False, indent=2)[:4000])
PY
REMOTE
}

if [[ -n "$WATCH" ]]; then
  while true; do
    clear 2>/dev/null || true
    remote_probe || true
    echo
    echo "每 ${WATCH}s 刷新；Ctrl+C 退出"
    sleep "$WATCH"
  done
else
  remote_probe
fi
