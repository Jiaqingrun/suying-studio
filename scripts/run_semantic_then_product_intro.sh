#!/usr/bin/env bash
# Bounded semantic backfill, then auto-create strict product-intro sample when plan is feasible.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${SUYING_API:-http://127.0.0.1:8766}"
LOG="${SUYING_DATA:-$HOME/Suying/data}/semantic_product_run.log"
BATCH_SIZE="${BATCH_SIZE:-20}"
MAX_BATCHES="${MAX_BATCHES:-30}"
REJECT_BREAK="${REJECT_BREAK:-40}"

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

health_ok() {
  curl -fsS "$API/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("status")=="ok" else 1)'
}

dry_run_clips() {
  curl -fsS -X POST "$API/dry-run" -H 'Content-Type: application/json' -d '{
    "theme":"产品",
    "template_name":"default-vertical",
    "category":"default",
    "strict_semantic_v1": true
  }' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("clips") or []))'
}

status_json() {
  curl -fsS "$API/index/captions/status"
}

run_batch() {
  curl -fsS -X POST "$API/index/captions?limit=${BATCH_SIZE}&use_vision=true"
}

create_product_job() {
  curl -fsS -X POST "$API/jobs" -H 'Content-Type: application/json' -d '{
    "mode":"count",
    "target_count":1,
    "template_name":"default-vertical",
    "theme":"产品",
    "category":"default",
    "strict_semantic_v1": true
  }'
}

wait_job_output() {
  local job_id="$1"
  local i out_id path status
  for i in $(seq 1 360); do
    sleep 5
    read -r status out_id path <<<"$(python3 <<PY
import json, urllib.request
job_id=${job_id}
base='${API}'
with urllib.request.urlopen(base+'/jobs') as r:
    jobs=json.load(r)
job=next((j for j in jobs if j.get('id')==job_id), {})
status=job.get('status','')
with urllib.request.urlopen(base+'/outputs') as r:
    outs=json.load(r)
match=[o for o in outs if o.get('job_id')==job_id]
if match:
    o=sorted(match, key=lambda x: x.get('id',0))[-1]
    print(status, o.get('id',''), o.get('file_path',''))
else:
    print(status, '', '')
PY
)"
    log "job ${job_id} poll status=${status} output=${out_id}"
    if [[ ("$status" == "completed" || "$status" == "ready") && -n "$out_id" && -n "$path" && -f "$path" ]]; then
      echo "$out_id|$path"
      return 0
    fi
    if [[ "$status" == "circuit_open" || "$status" == "failed" ]]; then
      return 1
    fi
  done
  return 1
}

run_quality_backfill() {
  curl -fsS -X POST "$API/cliplets/quality/backfill?limit=${1:-500}&only_unscored=true"
}

count_unscored() {
  python3 <<'PY'
import sqlite3
con=sqlite3.connect('/Users/qr/QR-Volume/速影工作区/db/montage.db')
n=con.execute("""
select count(*) from cliplets c join assets a on c.asset_id=a.id
where a.customer_id=1 and c.status='usable' and c.score=1.0
""").fetchone()[0]
print(n)
PY
}

log "=== start semantic backfill + product intro orchestrator ==="
health_ok || { log "engine not healthy"; exit 1; }

unscored="$(count_unscored)"
log "legacy unscored cliplets (score=1.0): ${unscored}"
while [[ "${unscored}" -gt 0 ]]; do
  qresult="$(run_quality_backfill 500)"
  log "quality backfill: ${qresult}"
  updated="$(printf '%s' "$qresult" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("updated",0))')"
  unscored="$(count_unscored)"
  log "remaining unscored=${unscored}"
  if [[ "${updated}" -le 0 ]]; then
    log "quality backfill made no progress; stop rescoring loop"
    break
  fi
done

clips="$(dry_run_clips || echo 0)"
log "initial dry-run clips=${clips}"
if [[ "${clips}" -ge 5 ]]; then
  log "enough clips already; creating product job"
else
  for n in $(seq 1 "$MAX_BATCHES"); do
    st="$(status_json)"
    remaining="$(printf '%s' "$st" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("remaining",0))')"
    claimed="$(printf '%s' "$st" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("claimed",0))')"
    log "batch ${n}/${MAX_BATCHES} remaining=${remaining} claimed=${claimed}"
    if [[ "${remaining}" -le 0 ]]; then
      log "no remaining eligible rows"
      break
    fi
    result="$(run_batch)"
    log "batch result: ${result}"
    passed="$(printf '%s' "$result" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("passed",0))')"
    rejected="$(printf '%s' "$result" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("rejected",0))')"
    total=$((passed + rejected))
    if [[ "$total" -gt 0 ]]; then
      reject_pct=$(( rejected * 100 / total ))
      log "batch reject_rate=${reject_pct}% (${rejected}/${total})"
      if [[ "$reject_pct" -ge "$REJECT_BREAK" ]]; then
        log "circuit: reject rate >= ${REJECT_BREAK}% — stopping backfill"
        exit 2
      fi
    fi
    clips="$(dry_run_clips || echo 0)"
    log "dry-run clips after batch ${n}: ${clips}"
    if [[ "${clips}" -ge 5 ]]; then
      log "plan feasible; stop backfill early"
      break
    fi
  done
fi

clips="$(dry_run_clips || echo 0)"
if [[ "${clips}" -lt 5 ]]; then
  log "still insufficient strict product clips (${clips}/5); no job created"
  exit 3
fi

job_json="$(create_product_job)"
job_id="$(printf '%s' "$job_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
log "created job ${job_id}: ${job_json}"

if out="$(wait_job_output "$job_id")"; then
  out_id="${out%%|*}"
  path="${out#*|}"
  log "product intro ready output=${out_id} path=${path}"
  open "$path" || true
  exit 0
else
  log "job ${job_id} did not reach ready output"
  exit 4
fi
