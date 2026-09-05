#!/usr/bin/env bash
# 速影引擎启动（权威入口）。默认 http://127.0.0.1:8766
# 文档：README.md · docs/INSTALL.md · docs/SOP.md
# 强制再启（会与旧进程抢端口）：SUYING_FORCE=1 ./scripts/start-engine.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
# GUI/launchd 可能缺 Homebrew；出片需要 ffmpeg / ollama CLI。
for _bin in /opt/homebrew/bin /usr/local/bin; do
  if [[ -d "$_bin" && ":${PATH}:" != *":${_bin}:"* ]]; then
    export PATH="${_bin}:${PATH}"
  fi
done
unset _bin

# 本机 lab 覆盖（不进打包）：~/Suying/runtime/local.env
# 例：SUYING_ALLOW_VISION_CASCADE=1 恢复 9B→27B 级联（勿与全库回填同时开）。
_LOCAL_ENV="${SUYING_LOCAL_ENV:-$HOME/Suying/runtime/local.env}"
if [[ -f "$_LOCAL_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$_LOCAL_ENV"
  set +a
fi
unset _LOCAL_ENV

# Prefer env / conda / brew over Apple CLT python (often missing deps).
# Full-library semantic.v1 migration must never share VRAM with 27B cascade.
# local.env may set CASCADE=1; FULL_BACKFILL wins and clears it.
if [[ "${SUYING_ALLOW_SEMANTIC_FULL_BACKFILL:-}" == "1" || "${SUYING_ALLOW_SEMANTIC_FULL_BACKFILL:-}" == "true" ]]; then
  unset SUYING_ALLOW_VISION_CASCADE
elif [[ "${SUYING_ALLOW_VISION_CASCADE:-}" != "1" && "${SUYING_ALLOW_VISION_CASCADE:-}" != "true" ]]; then
  unset SUYING_ALLOW_VISION_CASCADE
fi
if [[ -n "${SUYING_PYTHON:-${MONTAGE_PYTHON:-}}" ]]; then
  PY="${SUYING_PYTHON:-$MONTAGE_PYTHON}"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PY="$ROOT/.venv/bin/python3"
elif [[ -x /opt/anaconda3/bin/python3 ]]; then
  PY=/opt/anaconda3/bin/python3
elif [[ -x /opt/homebrew/bin/python3 ]]; then
  PY=/opt/homebrew/bin/python3
else
  PY="$(command -v python3)"
fi

PORT="${SUYING_PORT:-${MONTAGE_PORT:-8766}}"
if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "速影引擎: 端口 ${PORT} 已有进程在听（可能已在跑）。" >&2
    echo "  查看: lsof -nP -iTCP:${PORT} -sTCP:LISTEN" >&2
    echo "  停掉: kill \$(lsof -t -iTCP:${PORT} -sTCP:LISTEN)" >&2
    if [[ "${SUYING_FORCE:-0}" != "1" ]]; then
      echo "  已退出（需要热加载请先停旧进程，或 SUYING_FORCE=1 强制再启）。" >&2
      exit 0
    fi
  fi
fi

echo "速影引擎启动: PY=${PY} ROOT=${ROOT} port_hint=${PORT}"
exec "${PY}" -m engine.main
