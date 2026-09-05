#!/usr/bin/env bash
# Continuous customer publish catch-up loop (SSH).
# Promotes safe deferred → auto via API tick; cools wall profiles in DB; batch makeup.
#
# Usage:
#   ./scripts/remote-publish-catchup-loop.sh --host xlf-remote
#   ./scripts/remote-publish-catchup-loop.sh --host xlf-remote --interval 20 --max-minutes 120
set -euo pipefail

HOST=""
PORT="${SUYING_PORT:-8766}"
INTERVAL=20
MAX_MINUTES=180
COOL_PROFILES="${COOL_PROFILES:-视频号-5345,视频号-5331,视频号-7236}"
COOL_MINUTES="${COOL_MINUTES:-90}"

usage() {
  cat <<'EOF'
用法:
  remote-publish-catchup-loop.sh --host SSH_ALIAS [选项]
选项:
  --interval SEC      循环秒数（默认 20）
  --max-minutes N     最长运行分钟（默认 180）
  --cool-profiles A,B 登录墙账号临时冷却
  --cool-minutes N    冷却分钟（默认 90）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-}"; shift 2 ;;
    --max-minutes) MAX_MINUTES="${2:-}"; shift 2 ;;
    --cool-profiles) COOL_PROFILES="${2:-}"; shift 2 ;;
    --cool-minutes) COOL_MINUTES="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done
[[ -n "$HOST" ]] || { usage; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START_TS=$(date +%s)
END_TS=$((START_TS + MAX_MINUTES * 60))

echo "catchup-loop host=$HOST interval=${INTERVAL}s max=${MAX_MINUTES}m cool=$COOL_PROFILES"
echo "Ctrl+C 结束；同时可并行 watch-remote-publish.sh"

while true; do
  NOW=$(date +%s)
  if (( NOW >= END_TS )); then
    echo "达到 max-minutes，退出"
    exit 0
  fi
  printf '\n──────── catchup %s ────────\n' "$(date '+%H:%M:%S')"
  # Repair + tick path lives in engine; force schedule tick and makeup kick.
  set +e
  "$ROOT/scripts/remote-publish-catchup.sh" \
    --host "$HOST" \
    --port "$PORT" \
    --cool-profiles "$COOL_PROFILES" \
    --cool-minutes "$COOL_MINUTES"
  rc=$?
  set -e
  echo "catchup_rc=$rc"
  sleep "$INTERVAL"
done
