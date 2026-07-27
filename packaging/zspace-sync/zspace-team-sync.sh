#!/bin/bash
# Per-user wrapper installed by 速影. Paths are resolved from the current HOME.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/anaconda3/bin:/opt/homebrew/bin:$PATH"

SYNC_PY="${HOME}/QR/tools/zspace-team-sync.py"
LOG_DIR="${HOME}/.qr/logs"
LOG_FILE="${LOG_DIR}/zspace-team-sync.log"
mkdir -p "$LOG_DIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG_FILE"
}

if [[ -x /opt/anaconda3/bin/python3 ]]; then
  PY=/opt/anaconda3/bin/python3
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY=/usr/bin/python3
fi

if [[ ! -f "$SYNC_PY" ]]; then
  log "error: missing ${SYNC_PY}"
  exit 2
fi
log "start: per-user sync service"
exec "$PY" "$SYNC_PY" "$@"
