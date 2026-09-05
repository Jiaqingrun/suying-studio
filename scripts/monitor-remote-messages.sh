#!/usr/bin/env bash
# 远程监测客户机消息接收 / 跳转回复（默认 2 小时，本机记录并可自动修正）
#
# Usage:
#   ./scripts/monitor-remote-messages.sh --host xlf-remote
#   ./scripts/monitor-remote-messages.sh --host xlf-remote --once
#   ./scripts/monitor-remote-messages.sh --host xlf-remote --no-fix
#   ./scripts/monitor-remote-messages.sh --host xlf-remote --duration 7200 --interval 30
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUNBUFFERED=1
exec python3 "${ROOT}/scripts/monitor_remote_messages.py" "$@"
