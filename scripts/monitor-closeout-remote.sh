#!/usr/bin/env bash
# 关账真机实验 · 远程监控后台启停（运维 Mac）
# 默认不启动；等用户说「开始」后再 --start。
#
# Usage:
#   ./scripts/monitor-closeout-remote.sh --host xlf-remote --start
#   ./scripts/monitor-closeout-remote.sh --host xlf-remote --status
#   ./scripts/monitor-closeout-remote.sh --host xlf-remote --tail
#   ./scripts/monitor-closeout-remote.sh --host xlf-remote --stop
#   ./scripts/monitor-closeout-remote.sh --host xlf-remote --once
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST=""
ACTION=""
PORT="${SUYING_PORT:-8766}"
INTERVAL="${CLOSEOUT_MONITOR_INTERVAL:-20}"
DURATION="${CLOSEOUT_MONITOR_DURATION:-10800}"
LOG_ROOT="${HOME}/Suying/logs/closeout-remote-monitor"

usage() {
  cat <<'EOF'
用法:
  monitor-closeout-remote.sh --host SSH_ALIAS --start|stop|status|tail|once

选项:
  --port N          默认 8766
  --interval SEC    采样间隔，默认 20
  --duration SEC    后台总时长，默认 10800（3h）

日志目录: ~/Suying/logs/closeout-remote-monitor/<host>/
  samples.jsonl   全量采样
  latest.json     最近一次
  anomalies.jsonl 异常摘要
  monitor.log     人读滚动日志
  monitor.pid     后台 PID
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-}"; shift 2 ;;
    --duration) DURATION="${2:-}"; shift 2 ;;
    --start|--stop|--status|--tail|--once) ACTION="${1#--}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" && -n "$ACTION" ]] || { usage; exit 2; }

HOST_SAFE="${HOST//\//_}"
DIR="${LOG_ROOT}/${HOST_SAFE}"
mkdir -p "$DIR"
PID_FILE="${DIR}/monitor.pid"
OUT_LOG="${DIR}/daemon.out"

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

case "$ACTION" in
  once)
    exec python3 "${ROOT}/scripts/monitor_closeout_remote.py" \
      --host "$HOST" --port "$PORT" --once
    ;;
  start)
    if is_running; then
      echo "已在运行 pid=$(cat "$PID_FILE") log=$DIR"
      exit 0
    fi
    nohup python3 "${ROOT}/scripts/monitor_closeout_remote.py" \
      --host "$HOST" --port "$PORT" \
      --interval "$INTERVAL" --duration "$DURATION" \
      >>"$OUT_LOG" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 0.4
    if is_running; then
      echo "已启动 pid=$(cat "$PID_FILE")"
      echo "日志: $DIR/monitor.log"
      echo "最新: $DIR/latest.json"
      echo "异常: $DIR/anomalies.jsonl"
    else
      echo "启动失败，见 $OUT_LOG" >&2
      exit 1
    fi
    ;;
  stop)
    if ! is_running; then
      echo "未在运行"
      rm -f "$PID_FILE"
      exit 0
    fi
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 0.5
    if is_running; then
      kill -9 "$(cat "$PID_FILE")" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "已停止"
    ;;
  status)
    if is_running; then
      echo "running pid=$(cat "$PID_FILE")"
    else
      echo "stopped"
    fi
    echo "dir=$DIR"
    if [[ -f "$DIR/latest.json" ]]; then
      python3 - <<PY
import json
from pathlib import Path
p = Path("${DIR}/latest.json")
data = json.loads(p.read_text(encoding="utf-8"))
snap = data.get("snap") or {}
anoms = data.get("anomalies") or []
print("latest_probed_at=", snap.get("probed_at"))
print("bundle=", snap.get("bundle_version"), snap.get("bundle_flavor"))
print("anomalies=", len(anoms))
for a in anoms[:8]:
    print(" ", a.get("code"), a.get("severity"), a.get("detail"))
PY
    fi
    ;;
  tail)
    touch "$DIR/monitor.log"
    exec tail -n 40 -f "$DIR/monitor.log"
    ;;
esac
