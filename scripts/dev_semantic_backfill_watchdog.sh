#!/usr/bin/env bash
# Dev-only watchdog: keep semantic.v1 full backfill running on this machine.
# - Engine must have SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1
# - Explicitly clears SUYING_ALLOW_VISION_CASCADE (full sweep must stay on 9B)
# - Restarts scripts/dev_full_semantic_backfill.sh when its PID-file holder dies
# Intentional pause: touch ~/Suying/data/dev_full_semantic_backfill.pause
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${SUYING_API:-http://127.0.0.1:8766}"
DATA="${SUYING_DATA:-$HOME/Suying/data}"
LOG="${DATA}/dev_semantic_backfill_watchdog.log"
PAUSE_FILE="${DATA}/dev_full_semantic_backfill.pause"
LOCK_DIR="${DATA}/dev_full_semantic_backfill.lockdir"
PID_FILE="${LOCK_DIR}/pid"
BACKFILL_LOG="${DATA}/dev_full_semantic_backfill.log"
POLL_SEC="${POLL_SEC:-30}"
PORT="${SUYING_PORT:-8766}"

mkdir -p "$DATA"
exec >>"$LOG" 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

json_get() {
  local expr="$1"
  python3 -c "import json,sys; d=json.load(sys.stdin); ${expr}" 2>/dev/null
}

engine_status() {
  curl -fsS --max-time 5 "$API/index/captions/status" 2>/dev/null || true
}

engine_override_ok() {
  local st="$1"
  [[ -n "$st" ]] || return 1
  printf '%s' "$st" | json_get 'print("1" if d.get("full_backfill_env_override") or d.get("full_backfill_enabled") else "0")' | grep -qx 1
}

remaining_of() {
  local st="$1"
  [[ -n "$st" ]] || { echo -1; return; }
  printf '%s' "$st" | json_get 'print(int(d.get("remaining") or 0))' || echo -1
}

loop_alive() {
  local pid
  [[ -f "$PID_FILE" ]] || return 1
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

ensure_engine() {
  local st
  st="$(engine_status)"
  if engine_override_ok "$st"; then
    # Also refuse cascade on the live process when possible (env is process-local).
    return 0
  fi
  log "engine missing override or down; restarting with FULL_BACKFILL=1 and CASCADE unset"
  local pids
  pids="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 2
  fi
  (
    cd "$ROOT"
    unset SUYING_ALLOW_VISION_CASCADE
    export SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1
    export SUYING_FORCE=1
    # shellcheck disable=SC2030
    env -u SUYING_ALLOW_VISION_CASCADE \
      SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1 \
      SUYING_FORCE=1 \
      nohup ./scripts/start-engine.sh >/dev/null 2>&1 &
  )
  local i
  for i in $(seq 1 40); do
    sleep 1
    st="$(engine_status)"
    if engine_override_ok "$st"; then
      log "engine override ready"
      return 0
    fi
  done
  log "WARN: engine override not ready after wait"
  return 1
}

release_claims() {
  local py
  if [[ -x /opt/anaconda3/bin/python3 ]]; then
    py=/opt/anaconda3/bin/python3
  else
    py="$(command -v python3)"
  fi
  "$py" - <<'PY'
import sqlite3
from pathlib import Path
db = Path.home() / "Suying" / "data" / "montage.db"
if not db.exists():
    raise SystemExit(0)
con = sqlite3.connect(str(db), timeout=30)
n = con.execute("select count(*) from cliplets where semantic_claim_token is not null").fetchone()[0]
con.execute(
    "update cliplets set semantic_claim_token=null, semantic_claimed_at=null "
    "where semantic_claim_token is not null"
)
con.commit()
print(n)
PY
}

start_loop() {
  if loop_alive; then
    return 0
  fi
  log "starting backfill loop (BATCH_SIZE=${BATCH_SIZE:-1})"
  (
    cd "$ROOT"
    nohup env BATCH_SIZE="${BATCH_SIZE:-1}" SLEEP_SEC="${SLEEP_SEC:-2}" \
      ./scripts/dev_full_semantic_backfill.sh >/dev/null 2>&1 &
  )
  sleep 3
  if loop_alive; then
    log "backfill loop pid=$(cat "$PID_FILE")"
    return 0
  fi
  log "WARN: backfill loop failed to start; see $BACKFILL_LOG"
  return 1
}

log "=== watchdog start poll=${POLL_SEC}s pause_file=$PAUSE_FILE ==="
if [[ "${KEEP_PAUSE:-0}" != "1" && -f "$PAUSE_FILE" ]]; then
  log "removing existing pause file for auto-run"
  rm -f "$PAUSE_FILE"
fi

while true; do
  if [[ -f "$PAUSE_FILE" ]]; then
    log "paused by $PAUSE_FILE; sleeping"
    sleep "$POLL_SEC"
    continue
  fi

  if ! ensure_engine; then
    sleep "$POLL_SEC"
    continue
  fi

  st="$(engine_status)"
  rem="$(remaining_of "$st")"
  if [[ "$rem" -lt 0 ]]; then
    log "WARN: cannot read remaining; retry"
    sleep "$POLL_SEC"
    continue
  fi
  if [[ "$rem" -le 0 ]]; then
    log "remaining=0; watchdog idle"
    sleep "$POLL_SEC"
    continue
  fi

  if ! loop_alive; then
    log "detected stop: remaining=${rem} loop_down -> resume"
    release_claims || true
    # Drop stale lockdir if holder is dead.
    if [[ -d "$LOCK_DIR" ]] && ! loop_alive; then
      rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
    start_loop || true
  fi

  sleep "$POLL_SEC"
done
