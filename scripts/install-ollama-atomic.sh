#!/usr/bin/env bash
# 速影：原子替换受管 Ollama 二进制（禁止 cp 覆盖运行中文件）
# 用法：
#   install-ollama-atomic.sh <source_ollama_bin> [--skip-restart] [--skip-functional-probe]
set -euo pipefail

SOURCE="${1:-}"
SKIP_RESTART=0
SKIP_FUNCTIONAL=0
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-restart) SKIP_RESTART=1; shift ;;
    --skip-functional-probe) SKIP_FUNCTIONAL=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$SOURCE" && -f "$SOURCE" ]] || {
  echo "ERROR: 需要源二进制路径" >&2
  exit 2
}

LABEL="com.qr.suying.ollama"
TOOL_BIN="${HOME}/Suying/runtime/tools/bin"
DEST="${TOOL_BIN}/ollama"
PREV="${TOOL_BIN}/ollama.prev"
STAGING_DIR="${HOME}/Suying/runtime/tools/.staging"
STAGING="${STAGING_DIR}/ollama.$$.$RANDOM"
LOG_DIR="${HOME}/Suying/logs"
AGENT_SCRIPT="$(cd "$(dirname "$0")" && pwd)/install-ollama-agent.sh"

log() { printf '[ollama-atomic] %s\n' "$*"; }
fail() { printf '[ollama-atomic] ERROR: %s\n' "$*" >&2; exit 1; }

sha256_of() {
  shasum -a 256 "$1" | awk '{print $1}'
}

codesign_ok() {
  codesign --verify --strict "$1" >/dev/null 2>&1
}

listener_pid() {
  lsof -t -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | head -1 || true
}

listener_cmd() {
  local pid="$1"
  [[ -n "$pid" ]] || { echo ""; return; }
  ps -p "$pid" -o command= 2>/dev/null || true
}

is_managed_cmd() {
  local cmd="$1"
  [[ "$cmd" == *"$TOOL_BIN/ollama"* || "$cmd" == *"Suying/runtime/tools/bin/ollama"* ]]
}

launchagent_loaded() {
  launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1
}

stop_managed() {
  local pid cmd
  if launchagent_loaded; then
    log "bootout LaunchAgent ${LABEL}"
    launchctl bootout "gui/$(id -u)" "${HOME}/Library/LaunchAgents/${LABEL}.plist" >/dev/null 2>&1 || true
    launchctl unload "${HOME}/Library/LaunchAgents/${LABEL}.plist" >/dev/null 2>&1 || true
  fi
  pid="$(listener_pid)"
  if [[ -n "$pid" ]]; then
    cmd="$(listener_cmd "$pid")"
    if is_managed_cmd "$cmd"; then
      log "停止受管监听 pid=${pid}"
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 20); do
        [[ -z "$(listener_pid)" ]] && break
        sleep 0.25
      done
      if [[ -n "$(listener_pid)" ]]; then
        kill -9 "$pid" 2>/dev/null || true
        sleep 0.5
      fi
    elif [[ -n "$cmd" ]]; then
      # 外部占用时仍允许替换磁盘上的受管二进制（不杀外部进程）；
      # 启动阶段会 fail-closed。
      log "WARN: 11434 被外部进程占用，跳过停服，仅替换磁盘文件: pid=${pid}"
    fi
  fi
}

start_managed() {
  [[ -x "$AGENT_SCRIPT" ]] || fail "缺少 ${AGENT_SCRIPT}"
  bash "$AGENT_SCRIPT" install
}

rollback_bin() {
  if [[ -f "$PREV" ]]; then
    log "回滚到上一版二进制"
    mv -f "$PREV" "$DEST"
    chmod 755 "$DEST"
  fi
}

mkdir -p "$TOOL_BIN" "$STAGING_DIR" "$LOG_DIR"

# 1) 物化到独立临时文件
mkdir -p "$STAGING_DIR"
STAGING="${STAGING_DIR}/ollama.$$.$RANDOM.$RANDOM"
rm -rf "$STAGING"
/bin/cp "$SOURCE" "$STAGING"
[[ -f "$STAGING" ]] || fail "staging 未生成普通文件: $STAGING"
/bin/chmod 755 "$STAGING"
SRC_SHA="$(sha256_of "$SOURCE")"
STG_SHA="$(sha256_of "$STAGING")"
[[ "$SRC_SHA" == "$STG_SHA" ]] || {
  rm -f "$STAGING"
  fail "staging SHA 与源不一致"
}
codesign_ok "$STAGING" || {
  rm -f "$STAGING"
  fail "staging codesign --verify --strict 失败"
}

# 可选：对照 depot sidecar SHA
if [[ -f "${SOURCE}.sha256" ]]; then
  EXPECT="$(awk '{print $1}' "${SOURCE}.sha256")"
  [[ "$STG_SHA" == "$EXPECT" ]] || {
    rm -f "$STAGING"
    fail "staging SHA 与 ${SOURCE}.sha256 不符"
  }
fi

ARCH_FILE="$(file -b "$STAGING" || true)"
HOST_ARCH="$(uname -m)"
# Only enforce arch for Mach-O binaries; scripts/tests may use stub shell files.
if echo "$ARCH_FILE" | grep -Eqi 'Mach-O'; then
  case "$HOST_ARCH" in
    arm64|aarch64)
      echo "$ARCH_FILE" | grep -Eqi 'arm64|aarch64' ||
        fail "架构不匹配: host=${HOST_ARCH} file=${ARCH_FILE}"
      ;;
    x86_64)
      echo "$ARCH_FILE" | grep -Eqi 'x86_64' ||
        fail "架构不匹配: host=${HOST_ARCH} file=${ARCH_FILE}"
      ;;
  esac
fi

NEED_REPLACE=1
OLD_SHA=""
if [[ -f "$DEST" ]]; then
  OLD_SHA="$(sha256_of "$DEST")"
  if [[ "$OLD_SHA" == "$STG_SHA" ]] && codesign_ok "$DEST"; then
    NEED_REPLACE=0
    log "目标已是相同 SHA（${STG_SHA:0:12}…），跳过二进制替换"
  fi
fi

if [[ "$NEED_REPLACE" == "1" ]]; then
  # 2) 工具变化时先停服务，再 rename 原子替换（绝不 cp 覆盖运行中文件）
  stop_managed
  if [[ -f "$DEST" ]]; then
    rm -f "$PREV"
    mv -f "$DEST" "$PREV"
  fi
  mv -f "$STAGING" "$DEST"
  chmod 755 "$DEST"
  log "原子替换完成 sha=${STG_SHA:0:16}… prev=${OLD_SHA:0:16}…"
else
  rm -f "$STAGING"
fi

# 3) 启动 / 重启并核对
if [[ "$SKIP_RESTART" == "1" ]]; then
  log "跳过重启（--skip-restart）"
  exit 0
fi

if ! start_managed; then
  log "启动失败，尝试回滚"
  stop_managed || true
  rollback_bin
  start_managed || fail "回滚后仍无法启动受管 Ollama"
  fail "新二进制启动失败，已回滚"
fi

NEW_PID="$(listener_pid)"
[[ -n "$NEW_PID" ]] || {
  rollback_bin
  start_managed || true
  fail "启动后 11434 无监听"
}
NEW_CMD="$(listener_cmd "$NEW_PID")"
is_managed_cmd "$NEW_CMD" || {
  fail "启动后监听非受管进程: pid=${NEW_PID} cmd=${NEW_CMD}"
}
LIVE_SHA="$(sha256_of "$DEST")"
[[ "$LIVE_SHA" == "$STG_SHA" || "$NEED_REPLACE" == "0" ]] || fail "启动后磁盘 SHA 漂移"

VER_JSON="$(curl -fsS --max-time 5 http://127.0.0.1:11434/api/version || true)"
[[ -n "$VER_JSON" ]] || {
  stop_managed || true
  rollback_bin
  start_managed || true
  fail "/api/version 不可达"
}

if [[ "$SKIP_FUNCTIONAL" != "1" ]]; then
  # 最小功能探针：优先 chat（旁白），再 embed；失败则回滚
  CHAT_OK=0
  EMBED_OK=0
  if curl -fsS --noproxy '*' --max-time 90 \
    -H 'Content-Type: application/json' \
    -d '{"model":"qwen3.5:9b","stream":false,"think":false,"keep_alive":"10m","messages":[{"role":"user","content":"只回复 OK"}],"options":{"num_predict":8}}' \
    http://127.0.0.1:11434/api/chat 2>/dev/null | grep -q '"content"'; then
    CHAT_OK=1
  fi
  if curl -fsS --noproxy '*' --max-time 30 \
    -H 'Content-Type: application/json' \
    -d '{"model":"nomic-embed-text","prompt":"suying probe","keep_alive":0}' \
    http://127.0.0.1:11434/api/embeddings 2>/dev/null | grep -q '"embedding"'; then
    EMBED_OK=1
  fi
  # 若模型尚未安装，仅要求 tags 可达；正式旁白档位安装会另做模型门禁
  if [[ "$CHAT_OK" != "1" && "$EMBED_OK" != "1" ]]; then
    if ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      stop_managed || true
      rollback_bin
      start_managed || true
      fail "功能探针与 tags 均失败，已回滚"
    fi
    log "WARN: chat/embed 探针未通过（可能模型未装），但 tags 可达"
  else
    log "功能探针 chat=${CHAT_OK} embed=${EMBED_OK}"
  fi
fi

CDHASH="$(codesign -dv --verbose=4 "$DEST" 2>&1 | awk -F= '/CDHash/{print $2; exit}' || true)"
log "就绪 pid=${NEW_PID} sha=${LIVE_SHA:0:16}… cdhash=${CDHASH:0:16}… version=${VER_JSON}"
printf '%s\n' "{\"ok\":true,\"pid\":${NEW_PID},\"sha256\":\"${LIVE_SHA}\",\"cdhash\":\"${CDHASH}\",\"version\":${VER_JSON}}"
