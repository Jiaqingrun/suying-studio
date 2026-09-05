#!/usr/bin/env bash
# Wait until remote semantic heal backlog is clear, produce 产品介绍, pull to Desktop.
set -euo pipefail
API_REMOTE_SSH="${API_REMOTE_SSH:-xlf-remote}"
DEST="${DEST:-$HOME/Desktop/客户机-产品介绍-heal后.mp4}"
POLL="${POLL:-45}"
MAX_WAIT_SEC="${MAX_WAIT_SEC:-14400}"  # 4h
LOG="${LOG:-$HOME/Suying/data/ops/wait_heal_product_pull.log}"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

remote_status() {
  ssh "$API_REMOTE_SSH" 'python3 ~/Suying/scripts/semantic_idle_heal.py --data-root $HOME/Suying/data status' 2>/dev/null
}

is_clear() {
  python3 -c '
import json,sys
d=json.load(sys.stdin)
miss=int(d.get("embedding_missing") or 0)
idle=bool(d.get("idle"))
print(1 if miss==0 else 0, miss, "idle" if idle else d.get("reason",""))
' <<<"$1"
}

log "start wait clear then product-intro pull dest=$DEST"

# Speed up reembed while waiting (restart loop with higher limit if running)
ssh "$API_REMOTE_SSH" 'bash ~/Suying/scripts/semantic_idle_heal_loop.sh stop 2>/dev/null || true
INTERVAL=60 VERIFY_BATCH=1 REEMBED_LIMIT=80 \
  SUYING_DATA=$HOME/Suying/data bash ~/Suying/scripts/semantic_idle_heal_loop.sh' || true

start_ts=$(date +%s)
while true; do
  now=$(date +%s)
  if (( now - start_ts > MAX_WAIT_SEC )); then
    log "timeout after ${MAX_WAIT_SEC}s"
    exit 2
  fi
  st=$(remote_status || echo '{}')
  read -r clear miss note <<<"$(is_clear "$st")"
  log "check embedding_missing=$miss clear=$clear note=$note"
  if [[ "$clear" == "1" ]]; then
    # also require idle for produce
    if [[ "$note" == "idle" ]]; then
      break
    fi
    log "cleared but not idle ($note), wait..."
  fi
  sleep "$POLL"
done

log "backlog clear — create product intro job (active rule)"
JOB_JSON=$(ssh "$API_REMOTE_SSH" 'curl -sS -X POST http://127.0.0.1:8766/jobs -H "Content-Type: application/json" -d "{\"mode\":\"count\",\"target_count\":1,\"use_active_rule\":true,\"customer_name\":\"北京始峰伟业\",\"rush\":true,\"orientation\":\"portrait\"}"')
log "create: $JOB_JSON"
JOB_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' <<<"$JOB_JSON")
if [[ -z "$JOB_ID" ]]; then
  log "no job id"; exit 3
fi

# poll job
REMOTE_PATH=""
for i in $(seq 1 120); do
  sleep 10
  info=$(ssh "$API_REMOTE_SSH" "python3 - <<'PY'
import json, urllib.request, sqlite3
from pathlib import Path
jobs=json.load(urllib.request.urlopen('http://127.0.0.1:8766/jobs', timeout=15))
j=next((x for x in jobs if x.get('id')==$JOB_ID), {})
print(j.get('status'), j.get('produced_count',0))
con=sqlite3.connect(str(Path.home()/'Suying/data/montage.db'))
row=con.execute('select output_path, state from render_outputs where job_id=? and state=\"ready\" order by id desc limit 1', ($JOB_ID,)).fetchone()
if row:
  print('OUT', row[0])
else:
  print('OUT')
# events tail
ev=con.execute('select message from job_events where job_id=? order by id desc limit 1', ($JOB_ID,)).fetchone()
print('EV', (ev[0] if ev else ''))
PY")
  log "job $JOB_ID: $info"
  if echo "$info" | grep -q '^completed'; then
    REMOTE_PATH=$(echo "$info" | awk '/^OUT /{print substr($0,5)}' | head -1)
    break
  fi
  if echo "$info" | grep -Eq '^(failed|cancelled)'; then
    log "job terminal fail"
    exit 4
  fi
done

if [[ -z "${REMOTE_PATH// }" ]]; then
  # fallback glob
  REMOTE_PATH=$(ssh "$API_REMOTE_SSH" "ls -t /Users/xlf/Movies/速影工作区/速影客户/北京始峰伟业/02-成片/ready/\$(date +%Y-%m-%d)/montage_${JOB_ID}_*.mp4 2>/dev/null | head -1")
fi
if [[ -z "${REMOTE_PATH// }" ]]; then
  log "no output path"; exit 5
fi

log "scp $REMOTE_PATH -> $DEST"
scp "$API_REMOTE_SSH:$REMOTE_PATH" "$DEST"
# also json sidecar if exists
scp "$API_REMOTE_SSH:${REMOTE_PATH%.mp4}.json" "${DEST%.mp4}.json" 2>/dev/null || true
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$DEST" 2>/dev/null | awk '{print "duration",$1}'
open "$DEST" || true
log "DONE $DEST"
# leave heal loop running for on-demand verify
