#!/usr/bin/env bash
# 客户机多轮生产+上传 E2E（密采样）控制面。
#
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --start
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --status
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --tail
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --report
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --stop
#   ./scripts/remote-e2e-acceptance.sh --host xlf-remote --once
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST=""
ROUNDS=3
INTERVAL=2
ROUND_GAP=15
PRODUCE_TIMEOUT=900
UPLOAD_TIMEOUT=420
TEMPLATE="fast-ship"
PLATFORM="channels"
CHROME_PROFILE="视频号-5331"
CUSTOMER=""
ACTION=""
EXTRA=()
LOCAL_ROOT="${HOME}/Suying/logs/e2e-acceptance"

usage() {
  cat <<'EOF'
用法:
  remote-e2e-acceptance.sh --host SSH_ALIAS --start|status|tail|report|stop|once [选项]

选项:
  --rounds N  --interval SEC  --round-gap SEC
  --produce-timeout SEC  --upload-timeout SEC
  --template NAME  --platform ID  --chrome-profile NAME  --customer NAME
  --skip-upload  --no-stop-on-fail  --allow-busy-slots
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --rounds) ROUNDS="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-}"; shift 2 ;;
    --round-gap) ROUND_GAP="${2:-}"; shift 2 ;;
    --produce-timeout) PRODUCE_TIMEOUT="${2:-}"; shift 2 ;;
    --upload-timeout) UPLOAD_TIMEOUT="${2:-}"; shift 2 ;;
    --template) TEMPLATE="${2:-}"; shift 2 ;;
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --chrome-profile) CHROME_PROFILE="${2:-}"; shift 2 ;;
    --customer) CUSTOMER="${2:-}"; shift 2 ;;
    --skip-upload|--no-stop-on-fail|--allow-busy-slots) EXTRA+=("$1"); shift ;;
    --stop|--once) ACTION="${1#--}"; shift ;;
    --json-status) ACTION="json-status"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

[[ -n "$HOST" ]] || { usage; exit 2; }
[[ -n "$ACTION" ]] || { echo "须指定动作 --start|status|tail|report|stop|once" >&2; usage; exit 2; }

ssh_h() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20 "$HOST" "$@"
}

install_runner() {
  ssh_h 'mkdir -p "$HOME/Suying/tools/e2e" "$HOME/Suying/logs/e2e-acceptance"'
  scp -o BatchMode=yes -o ConnectTimeout=15 \
    "$ROOT/scripts/remote_e2e_acceptance.py" \
    "$HOST:Suying/tools/e2e/remote_e2e_acceptance.py" >/dev/null
}

write_remote_launcher() {
  local mode="$1" # bg|fg
  local run_id="e2e-$(date -u +%Y%m%dT%H%M%SZ)-r${ROUNDS}"
  local tmp
  tmp="$(mktemp)"
  local extra_json='[]'
  if [[ ${#EXTRA[@]} -gt 0 ]]; then
    extra_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' -- "${EXTRA[@]}")"
  fi
  ROUNDS="$ROUNDS" INTERVAL="$INTERVAL" ROUND_GAP="$ROUND_GAP" \
  PRODUCE_TIMEOUT="$PRODUCE_TIMEOUT" UPLOAD_TIMEOUT="$UPLOAD_TIMEOUT" \
  TEMPLATE="$TEMPLATE" PLATFORM="$PLATFORM" CHROME_PROFILE="$CHROME_PROFILE" \
  CUSTOMER="$CUSTOMER" MODE="$mode" RUN_ID="$run_id" \
  EXTRA_JSON="$extra_json" \
  python3 - "$tmp" <<'PY'
import json, os, pathlib, sys
out = pathlib.Path(sys.argv[1])
extra = json.loads(os.environ.get("EXTRA_JSON") or "[]")
run_id = os.environ["RUN_ID"]
mode = os.environ["MODE"]
args = [
    "$HOME/Suying/tools/e2e/remote_e2e_acceptance.py",
    "--rounds", os.environ["ROUNDS"],
    "--interval", os.environ["INTERVAL"],
    "--round-gap", os.environ["ROUND_GAP"],
    "--produce-timeout", os.environ["PRODUCE_TIMEOUT"],
    "--upload-timeout", os.environ["UPLOAD_TIMEOUT"],
    "--template", os.environ["TEMPLATE"],
    "--platform", os.environ["PLATFORM"],
    "--chrome-profile", os.environ["CHROME_PROFILE"],
    "--accept-risk",
    "--run-id", "$RUN_ID",
    "--out-dir", "$ROOT",
]
cust = (os.environ.get("CUSTOMER") or "").strip()
if cust:
    args.extend(["--customer", cust])
args.extend(extra)

def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"

parts = []
for a in args:
    if a == "$RUN_ID":
        parts.append('"$RUN_ID"')
    elif a == "$ROOT":
        parts.append('"$ROOT"')
    elif a.startswith("$HOME"):
        parts.append(f'"{a}"')
    else:
        parts.append(sh_quote(a))
args_lit = " ".join(parts)

if mode == "bg":
    run_block = f'''nohup "$PY" "${{ARGS[@]}}" >"$OUT/daemon.out" 2>&1 &
echo $! >"$OUT/runner.pid"
echo "STARTED run_id=$RUN_ID pid=$(cat "$OUT/runner.pid") out=$OUT"
'''
else:
    run_block = '''echo "RUNNING run_id=$RUN_ID out=$OUT"
"$PY" "${ARGS[@]}"
ec=$?
echo "EXIT=$ec"
exit $ec
'''

script = f'''#!/usr/bin/env bash
set -euo pipefail
RUN_ID={sh_quote(run_id)}
ROOT="$HOME/Suying/logs/e2e-acceptance"
OUT="$ROOT/$RUN_ID"
mkdir -p "$OUT"
echo "$OUT" > "$ROOT/LATEST"
PY=""
for c in \\
  "/Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3" \\
  "$HOME/Desktop/速影 Studio.app/Contents/Resources/runtime/python/bin/python3" \\
  "/Applications/速影.app/Contents/Resources/runtime/python/bin/python3" \\
  "$HOME/Desktop/速影.app/Contents/Resources/runtime/python/bin/python3"; do
  if [[ -x "$c" ]]; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || PY="$(command -v python3)"
ARGS=( {args_lit} )
python3 - <<'META'
import json
from pathlib import Path
run_id = {json.dumps(run_id, ensure_ascii=False)}
root = Path.home() / "Suying/logs/e2e-acceptance"
meta = {{
  "run_id": run_id,
  "out_dir": str(root / run_id),
  "pid_file": str(root / run_id / "runner.pid"),
}}
(root / "current_run.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
)
META
{run_block}
'''
out.write_text(script, encoding="utf-8")
print(run_id)
PY
  scp -o BatchMode=yes -q "$tmp" "$HOST:Suying/tools/e2e/launch_e2e.sh"
  rm -f "$tmp"
  echo "$run_id"
}

pull_report() {
  local run_id="$1"
  mkdir -p "$LOCAL_ROOT/$HOST/$run_id"
  scp -o BatchMode=yes -q \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/REPORT.md" \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/summary.json" \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/console.log" \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/slot_events.jsonl" \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/samples.jsonl" \
    "$LOCAL_ROOT/$HOST/$run_id/" 2>/dev/null || true
  scp -o BatchMode=yes -q -r \
    "$HOST:Suying/logs/e2e-acceptance/$run_id/rounds" \
    "$LOCAL_ROOT/$HOST/$run_id/" 2>/dev/null || true
  echo "已拉回 → $LOCAL_ROOT/$HOST/$run_id/"
}

case "$ACTION" in
  start)
    install_runner
    RUN_ID="$(write_remote_launcher bg | tail -n1)"
    ssh_h 'bash "$HOME/Suying/tools/e2e/launch_e2e.sh"'
    echo "run_id=$RUN_ID"
    echo "监控: ./scripts/remote-e2e-acceptance.sh --host $HOST --status"
    echo "日志: ./scripts/remote-e2e-acceptance.sh --host $HOST --tail"
    ;;
  once)
    install_runner
    RUN_ID="$(write_remote_launcher fg | tail -n1)"
    set +e
    ssh_h 'bash "$HOME/Suying/tools/e2e/launch_e2e.sh"'
    ec=$?
    set -e
    pull_report "$RUN_ID"
    [[ -f "$LOCAL_ROOT/$HOST/$RUN_ID/REPORT.md" ]] && head -n 60 "$LOCAL_ROOT/$HOST/$RUN_ID/REPORT.md"
    exit "$ec"
    ;;
  status)
    ssh_h 'bash -s' <<'REMOTE'
set +e
ROOT="$HOME/Suying/logs/e2e-acceptance"
LATEST=$(cat "$ROOT/LATEST" 2>/dev/null || true)
echo "latest_dir=$LATEST"
[[ -f "$ROOT/current_run.json" ]] && { echo "meta:"; cat "$ROOT/current_run.json"; }
if [[ -n "$LATEST" && -f "$LATEST/runner.pid" ]]; then
  PID=$(cat "$LATEST/runner.pid")
  if kill -0 "$PID" 2>/dev/null; then echo "running pid=$PID"; else echo "not_running last_pid=$PID"; fi
  echo "--- console (tail 30) ---"
  if [[ -f "$LATEST/console.log" ]]; then tail -n 30 "$LATEST/console.log"
  else tail -n 30 "$LATEST/daemon.out"; fi
  if [[ -f "$LATEST/REPORT.md" ]]; then echo "--- REPORT head ---"; head -n 35 "$LATEST/REPORT.md"; fi
else
  echo "no active run dir"
fi
REMOTE
    ;;
  tail)
    ssh_h 'bash -s' <<'REMOTE'
ROOT="$HOME/Suying/logs/e2e-acceptance"
LATEST=$(cat "$ROOT/LATEST" 2>/dev/null || true)
[[ -n "$LATEST" ]] || { echo "no LATEST"; exit 1; }
if [[ -f "$LATEST/console.log" ]]; then exec tail -n 100 -f "$LATEST/console.log"; fi
exec tail -n 100 -f "$LATEST/daemon.out"
REMOTE
    ;;
  report)
    LATEST=$(ssh_h 'cat "$HOME/Suying/logs/e2e-acceptance/LATEST" 2>/dev/null || true')
    [[ -n "$LATEST" ]] || { echo "no LATEST" >&2; exit 1; }
    RUN_ID=$(basename "$LATEST")
    pull_report "$RUN_ID"
    [[ -f "$LOCAL_ROOT/$HOST/$RUN_ID/REPORT.md" ]] && cat "$LOCAL_ROOT/$HOST/$RUN_ID/REPORT.md"
    ;;
  stop)
    ssh_h 'bash -s' <<'REMOTE'
set +e
ROOT="$HOME/Suying/logs/e2e-acceptance"
LATEST=$(cat "$ROOT/LATEST" 2>/dev/null || true)
if [[ -n "$LATEST" && -f "$LATEST/runner.pid" ]]; then
  PID=$(cat "$LATEST/runner.pid")
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null
    sleep 1
    kill -9 "$PID" 2>/dev/null
    echo "stopped $PID"
  else
    echo "already dead $PID"
  fi
fi
curl -fsS -X POST http://127.0.0.1:8766/reach/auto-upload/cancel >/dev/null 2>&1 || true
REMOTE
    ;;
  json-status)
    ssh_h 'bash -s' <<'REMOTE'
set +e
ROOT="$HOME/Suying/logs/e2e-acceptance"
LATEST=$(cat "$ROOT/LATEST" 2>/dev/null || true)
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path.home() / "Suying/logs/e2e-acceptance"
latest = (root / "LATEST").read_text().strip() if (root / "LATEST").exists() else ""
out = {
  "ok": True,
  "latest_dir": latest,
  "running": False,
  "run_id": "",
  "rounds_ok": None,
  "rounds_total": None,
  "has_report": False,
  "console_tail": "",
  "summary": None,
}
if latest:
  p = Path(latest)
  out["run_id"] = p.name
  pid_file = p / "runner.pid"
  if pid_file.exists():
    try:
      pid = int(pid_file.read_text().strip() or "0")
      os.kill(pid, 0)
      out["running"] = True
      out["pid"] = pid
    except Exception:
      out["running"] = False
  report = p / "REPORT.md"
  summary = p / "summary.json"
  out["has_report"] = report.exists()
  if summary.exists():
    try:
      data = json.loads(summary.read_text(encoding="utf-8"))
      meta = data.get("meta") or {}
      out["rounds_ok"] = meta.get("rounds_ok")
      out["rounds_total"] = meta.get("rounds_total")
      out["summary"] = {
        "rounds_ok": meta.get("rounds_ok"),
        "rounds_total": meta.get("rounds_total"),
        "finished_at": meta.get("finished_at"),
        "engine": (meta.get("preflight") or {}).get("engine_version"),
      }
      rounds = data.get("rounds") or []
      if rounds:
        last = rounds[-1]
        out["last_round"] = {
          "index": last.get("round_index"),
          "ok": last.get("ok"),
          "job_id": last.get("job_id"),
          "output_id": last.get("output_id"),
          "queue_id": last.get("queue_id"),
          "upload_outcome": last.get("upload_outcome"),
          "duration_sec": last.get("duration_sec"),
        }
    except Exception as e:
      out["summary_error"] = str(e)
  log = p / "console.log"
  if not log.exists():
    log = p / "daemon.out"
  if log.exists():
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    out["console_tail"] = "\n".join(lines[-12:])
print(json.dumps(out, ensure_ascii=False))
PY
REMOTE
    ;;
  *) usage; exit 2 ;;
esac
