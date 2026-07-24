#!/bin/bash
# Wrapper: ensure QR volume mounted at ~/QR-Volume, then sync ZSpace team space ↔ disk.
# Safe under StartOnMount/WatchPaths: exits quickly if QR disk is not connected.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/anaconda3/bin:/opt/homebrew/bin:$PATH"

VOLUME_UUID="EF259876-00CC-4DB0-928C-15CC218A007D"
MOUNT_POINT="/Users/qr/QR-Volume"
MOUNT_SCRIPT="/Users/qr/QR/tools/mount-qr-volume.sh"
SYNC_PY="/Users/qr/QR/tools/zspace-team-sync.py"
LOG_DIR="${HOME}/.qr/logs"
LOG_FILE="${LOG_DIR}/zspace-team-sync.log"
mkdir -p "$LOG_DIR"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG_FILE"
}

mount_point_of_uuid() {
  diskutil info "$VOLUME_UUID" 2>/dev/null | awk -F': ' '/Mount Point/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}'
}

# Fast path: disk not plugged in → exit without touching 极空间.
if ! diskutil info "$VOLUME_UUID" >/dev/null 2>&1; then
  log "skip: QR volume not connected"
  exit 0
fi

# Ensure mounted at ~/QR-Volume (retry briefly for remount race after /Volumes/QR appears).
for _ in 1 2 3 4 5 6; do
  if [[ -x "$MOUNT_SCRIPT" ]]; then
    /bin/bash "$MOUNT_SCRIPT" || true
  fi
  CURRENT="$(mount_point_of_uuid)"
  if [[ "$CURRENT" == "$MOUNT_POINT" ]]; then
    break
  fi
  sleep 2
done

CURRENT="$(mount_point_of_uuid)"
if [[ "$CURRENT" != "$MOUNT_POINT" ]]; then
  log "skip: QR volume not at ${MOUNT_POINT} (now: ${CURRENT:-unmounted})"
  exit 0
fi

if [[ -x /opt/anaconda3/bin/python3 ]]; then
  PY=/opt/anaconda3/bin/python3
else
  PY=/usr/bin/python3
fi

log "start: volume mounted at ${MOUNT_POINT}"
exec "$PY" "$SYNC_PY" "$@"
