#!/bin/bash
# 客户机：极空间团队空间 → 本机片库 单向增量（LaunchAgent 每 10 分钟）
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/anaconda3/bin:/opt/homebrew/bin:$PATH"

TOOLS="${HOME}/QR/tools"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SYNC_PY="${ROOT}/one-way-media-pull.py"
if [[ -f "${TOOLS}/one-way-media-pull.py" ]]; then
  SYNC_PY="${TOOLS}/one-way-media-pull.py"
fi

LOG_DIR="${HOME}/.qr/logs"
mkdir -p "$LOG_DIR" "${HOME}/.qr"
LOG="${LOG_DIR}/one-way-media-pull.launchd.log"
LOCK="${HOME}/.qr/one-way-media-pull.session.lock"

if [[ -x /opt/anaconda3/bin/python3 ]]; then
  PY=/opt/anaconda3/bin/python3
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY=/usr/bin/python3
fi

{
  echo "$(date '+%Y-%m-%d %H:%M:%S') session-start"
  if [[ ! -f "$SYNC_PY" ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') error: missing ${SYNC_PY}"
    exit 2
  fi
  if [[ -f "$LOCK" ]]; then
    old_pid="$(head -1 "$LOCK" 2>/dev/null || true)"
    if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') skip: another session pid=$old_pid"
      exit 0
    fi
  fi
  echo "$$" >"$LOCK"
  "$PY" "$SYNC_PY" "$@"
  rc=$?
  rm -f "$LOCK"
  echo "$(date '+%Y-%m-%d %H:%M:%S') session-end rc=$rc"
  exit $rc
} >>"$LOG" 2>&1
