#!/usr/bin/env bash
# Dev-only: loop strict semantic.v1 backfill for the active customer.
# Requires engine started with SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1
# and WITHOUT SUYING_ALLOW_VISION_CASCADE (full sweep stays on 9B).
set -uo pipefail

API="${SUYING_API:-http://127.0.0.1:8766}"
BATCH_SIZE="${BATCH_SIZE:-1}"
SLEEP_SEC="${SLEEP_SEC:-2}"
DATA="${SUYING_DATA:-$HOME/Suying/data}"
LOG="${DATA}/dev_full_semantic_backfill.log"
PAUSE_FILE="${DATA}/dev_full_semantic_backfill.pause"
LOCK_DIR="${DATA}/dev_full_semantic_backfill.lockdir"
PID_FILE="${LOCK_DIR}/pid"
REJECT_BREAK_PCT="${REJECT_BREAK_PCT:-95}"
MIN_PROCESSED_FOR_BREAK="${MIN_PROCESSED_FOR_BREAK:-20}"
TRANSIENT_RETRIES="${TRANSIENT_RETRIES:-5}"

mkdir -p "$DATA"

holder_alive() {
  local pid
  [[ -f "$PID_FILE" ]] || return 1
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

# mkdir is the lock; never delete a live/racing holder.
# If pid is missing, wait briefly (writer may still be echoing $$) before treating as stale.
acquire_loop_lock() {
  local i pid age
  for i in $(seq 1 40); do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ >"$PID_FILE"
      return 0
    fi
    if holder_alive; then
      return 1
    fi
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -z "$pid" ]]; then
      sleep 0.25
      if holder_alive; then
        return 1
      fi
      # Still no pid after wait → likely crashed between mkdir and echo.
      age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
      if [[ "$age" -ge 5 ]]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
      fi
      sleep 0.25
      continue
    fi
    # Dead holder with pid on disk.
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
    sleep 0.25
  done
  return 1
}

if ! acquire_loop_lock; then
  echo "[$(date '+%F %T')] SKIP: another backfill loop holds $LOCK_DIR (pid=$(cat "$PID_FILE" 2>/dev/null || echo '?'))" >>"$LOG"
  exit 0
fi
cleanup() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

exec > >(tee -a "$LOG") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

curl_json() {
  local method="$1" url="$2" max_t="${3:-60}"
  local attempt=1 body code
  while [[ "$attempt" -le "$TRANSIENT_RETRIES" ]]; do
    body="$(mktemp)"
    code="$(curl -sS --max-time "$max_t" -o "$body" -w '%{http_code}' -X "$method" "$url" || echo 000)"
    if [[ "$code" =~ ^2 ]]; then
      cat "$body"
      rm -f "$body"
      return 0
    fi
    log "WARN: ${method} ${url} http=${code} attempt=${attempt}/${TRANSIENT_RETRIES} body=$(head -c 200 "$body" | tr '\n' ' ')"
    rm -f "$body"
    if [[ "$code" == "409" ]]; then
      # Busy or disabled — brief wait then retry unless permanently disabled detail.
      sleep $((attempt * 5))
      attempt=$((attempt + 1))
      continue
    fi
    sleep $((attempt * 3))
    attempt=$((attempt + 1))
  done
  return 1
}

log "=== dev full semantic.v1 backfill start batch=${BATCH_SIZE} pid=$$ ==="

status="$(curl_json GET "$API/index/captions/status" 30)" || {
  log "ABORT: cannot read captions status"
  exit 2
}
log "status0=${status}"
enabled="$(printf '%s' "$status" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str(bool(d.get("full_backfill_enabled"))).lower())')"
if [[ "$enabled" != "true" ]]; then
  log "ABORT: full_backfill not enabled (start engine with SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1, without VISION_CASCADE)"
  exit 2
fi

total_passed=0
total_rejected=0
total_errors=0
batch_i=0
no_progress_streak=0

while true; do
  if [[ -f "$PAUSE_FILE" ]]; then
    log "PAUSE: found $PAUSE_FILE; exiting loop"
    exit 0
  fi

  status="$(curl_json GET "$API/index/captions/status" 30)" || {
    log "WARN: status fetch failed; sleep and retry"
    sleep 10
    continue
  }
  remaining="$(printf '%s' "$status" | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("remaining") or 0))')"
  log "remaining=${remaining} passed_sum=${total_passed} rejected_sum=${total_rejected} errors_sum=${total_errors}"
  if [[ "$remaining" -le 0 ]]; then
    log "DONE: no eligible remaining"
    break
  fi

  batch_i=$((batch_i + 1))
  result="$(curl_json POST "$API/index/captions?limit=${BATCH_SIZE}&use_vision=true" 7200)" || {
    log "WARN: batch request failed; sleep and retry"
    sleep 15
    continue
  }
  log "batch${batch_i}=${result}"

  read -r passed rejected processed err_n <<<"$(printf '%s' "$result" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(
  int(d.get("passed") or 0),
  int(d.get("rejected") or 0),
  int(d.get("processed") or 0),
  len(d.get("errors") or []),
)
')"
  total_passed=$((total_passed + passed))
  total_rejected=$((total_rejected + rejected))
  total_errors=$((total_errors + err_n))

  if [[ "$processed" -le 0 ]]; then
    no_progress_streak=$((no_progress_streak + 1))
    log "WARN: batch made no progress streak=${no_progress_streak}"
    if [[ "$no_progress_streak" -ge 5 ]]; then
      log "STOP: no progress x5"
      break
    fi
    sleep 15
    continue
  fi
  no_progress_streak=0

  done_n=$((total_passed + total_rejected))
  if [[ "$done_n" -ge "$MIN_PROCESSED_FOR_BREAK" && "$total_rejected" -gt 0 ]]; then
    pct="$(python3 -c "print(int(100*${total_rejected}/${done_n}))")"
    if [[ "$pct" -ge "$REJECT_BREAK_PCT" ]]; then
      log "STOP: reject rate ${pct}% >= ${REJECT_BREAK_PCT}% after ${done_n} (circuit)"
      break
    fi
  fi

  sleep "$SLEEP_SEC"
done

final="$(curl_json GET "$API/index/captions/status" 30 || true)"
log "final_status=${final}"
log "=== finished passed=${total_passed} rejected=${total_rejected} errors=${total_errors} ==="
