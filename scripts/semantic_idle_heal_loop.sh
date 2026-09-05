#!/usr/bin/env bash
# Background idle loop for semantic heal (does not block production hard — waits for idle).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export SUYING_API="${SUYING_API:-http://127.0.0.1:8766}"
export SUYING_DATA="${SUYING_DATA:-$HOME/Suying/data}"
INTERVAL="${INTERVAL:-120}"
VERIFY_BATCH="${VERIFY_BATCH:-3}"
REEMBED_LIMIT="${REEMBED_LIMIT:-30}"
LOG_DIR="${SUYING_DATA}/ops"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/semantic_idle_heal.log"
PIDFILE="$LOG_DIR/semantic_idle_heal.pid"

if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "$PIDFILE" ]]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
  else
    echo "not running"
  fi
  exit 0
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "already running pid=$(cat "$PIDFILE")"
  exit 0
fi

nohup python3 "$ROOT/scripts/semantic_idle_heal.py" \
  --api "$SUYING_API" \
  --data-root "$SUYING_DATA" \
  --export "${EXPORT:-$SUYING_DATA/ops/semantic_peer_export.json}" \
  --interval "$INTERVAL" \
  --verify-batch "$VERIFY_BATCH" \
  --reembed-limit "$REEMBED_LIMIT" \
  loop >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "started pid=$(cat "$PIDFILE") log=$LOG"
