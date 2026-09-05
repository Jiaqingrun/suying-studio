#!/usr/bin/env bash
# Dev-only: periodically report semantic.v1 full-backfill progress.
# Env:
#   POLL_SEC=300   report interval (default 300s / 5min)
#   SUYING_API     engine base (default http://127.0.0.1:8766)
# Stop: pkill -f scripts/dev_semantic_backfill_progress.sh
#       or touch ~/Suying/data/dev_full_semantic_backfill.pause  (still reports, notes paused)
set -uo pipefail

API="${SUYING_API:-http://127.0.0.1:8766}"
DATA="${SUYING_DATA:-$HOME/Suying/data}"
LOG="${DATA}/dev_semantic_backfill_progress.log"
PAUSE_FILE="${DATA}/dev_full_semantic_backfill.pause"
POLL_SEC="${POLL_SEC:-300}"
STATE_FILE="${DATA}/dev_semantic_backfill_progress.state"

mkdir -p "$DATA"
exec >>"$LOG" 2>&1

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

fetch_status() {
  curl -fsS --max-time 8 "$API/index/captions/status" 2>/dev/null || true
}

report_once() {
  local st json_line tmp
  st="$(fetch_status)"
  if [[ -z "$st" ]]; then
    log "engine_unreachable loop=$(pgrep -f 'scripts/dev_full_semantic_backfill.sh' >/dev/null && echo up || echo down) watchdog=$(pgrep -f 'scripts/dev_semantic_backfill_watchdog.sh' >/dev/null && echo up || echo down)"
    return 1
  fi

  tmp="$(mktemp)"
  printf '%s' "$st" >"$tmp"
  json_line="$(
    STATUS_FILE="$tmp" PREV_FILE="$STATE_FILE" PAUSE_FILE="$PAUSE_FILE" python3 - <<'PY'
import json, os, time
from pathlib import Path

d = json.loads(Path(os.environ["STATUS_FILE"]).read_text(encoding="utf-8"))
passed = int(d.get("passed") or 0)
rejected = int(d.get("rejected") or 0)
remaining = int(d.get("remaining") or 0)
claimed = int(d.get("claimed") or 0)
eligible = int(d.get("eligible") or (passed + rejected + remaining))
done = passed + rejected
pct = (100.0 * done / eligible) if eligible else 0.0
now = time.time()

prev_path = Path(os.environ["PREV_FILE"])
delta_passed = None
eta = None
rate = None
if prev_path.exists():
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        dt = max(1.0, now - float(prev.get("ts") or now))
        dp = passed - int(prev.get("passed") or passed)
        delta_passed = dp
        rate = dp / dt * 3600.0
        if dp > 0 and remaining > 0:
            eta = remaining / (dp / dt) / 3600.0
    except Exception:
        pass

prev_path.write_text(
    json.dumps({"ts": now, "passed": passed, "remaining": remaining}, ensure_ascii=False),
    encoding="utf-8",
)

pause = Path(os.environ["PAUSE_FILE"]).exists()
loop_up = os.system("pgrep -f 'scripts/dev_full_semantic_backfill.sh' >/dev/null 2>&1") == 0
watch_up = os.system("pgrep -f 'scripts/dev_semantic_backfill_watchdog.sh' >/dev/null 2>&1") == 0
enabled = bool(d.get("full_backfill_enabled") or d.get("full_backfill_env_override"))

parts = [
    f"passed={passed}",
    f"rejected={rejected}",
    f"remaining={remaining}",
    f"claimed={claimed}",
    f"eligible={eligible}",
    f"progress={pct:.1f}%",
]
if delta_passed is not None:
    parts.append(f"delta_passed={delta_passed}")
if rate is not None:
    parts.append(f"rate≈{rate:.1f}/h")
if eta is not None:
    parts.append(f"eta≈{eta:.1f}h")
parts.append(f"loop={'up' if loop_up else 'down'}")
parts.append(f"watchdog={'up' if watch_up else 'down'}")
parts.append(f"override={'on' if enabled else 'off'}")
if pause:
    parts.append("PAUSED")
print(" ".join(parts))
PY
  )"
  rm -f "$tmp"
  log "$json_line"

  if [[ -n "$json_line" ]] && command -v osascript >/dev/null 2>&1; then
    rem="$(printf '%s' "$json_line" | python3 -c 'import sys,re; m=re.search(r"remaining=(\d+)", sys.stdin.read()); print(m.group(1) if m else "")')"
    passed="$(printf '%s' "$json_line" | python3 -c 'import sys,re; m=re.search(r"passed=(\d+)", sys.stdin.read()); print(m.group(1) if m else "")')"
    if [[ "$rem" == "0" ]]; then
      osascript -e "display notification \"semantic.v1 回填完成 · passed=${passed}\" with title \"速影向量化\"" >/dev/null 2>&1 || true
    fi
  fi
}

log "=== progress reporter start poll=${POLL_SEC}s log=$LOG ==="
report_once || true

while true; do
  sleep "$POLL_SEC"
  report_once || true
  # Stop quietly when finished and loop gone (watchdog may still idle).
  st="$(fetch_status)"
  if [[ -n "$st" ]]; then
    rem="$(printf '%s' "$st" | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("remaining") or 0))' 2>/dev/null || echo -1)"
    if [[ "$rem" == "0" ]]; then
      log "remaining=0; reporter will keep idling (Ctrl/pkill to stop)"
    fi
  fi
done
