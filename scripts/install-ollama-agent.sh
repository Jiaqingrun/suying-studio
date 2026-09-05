#!/usr/bin/env bash
# 速影受管 Ollama：用户级 LaunchAgent，固定 ~/Suying/runtime/tools/bin/ollama serve
set -euo pipefail

ACTION="install"
LABEL="com.qr.suying.ollama"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
TOOL_BIN="${HOME}/Suying/runtime/tools/bin"
OLLAMA_BIN="${TOOL_BIN}/ollama"
LOG_DIR="${HOME}/Suying/logs"
OLLAMA_OUT="${LOG_DIR}/ollama.out.log"
OLLAMA_ERR="${LOG_DIR}/ollama.err.log"
# 兼容旧路径：保留 ollama.log 软链指向 out
OLLAMA_LOG_LEGACY="${LOG_DIR}/ollama.log"
STATE_DIR="${HOME}/Suying/runtime/ollama"
OLLAMA_MODELS="${STATE_DIR}/models"
REQUIRE_NARRATION_MODEL="${SUYING_OLLAMA_REQUIRE_NARRATION_MODEL:-qwen3.5:9b}"
REQUIRE_EMBED_MODEL="${SUYING_OLLAMA_REQUIRE_EMBED_MODEL:-nomic-embed-text}"
SKIP_FUNCTIONAL="${SUYING_OLLAMA_SKIP_FUNCTIONAL:-0}"

usage() {
  cat <<'EOF'
用法:
  install-ollama-agent.sh install   # 安装并 bootstrap 受管 ollama serve
  install-ollama-agent.sh uninstall # bootout + 删除 plist（不删模型）
  install-ollama-agent.sh status    # launchctl + 端口所有权摘要
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|uninstall|status) ACTION="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR" "$STATE_DIR" "$TOOL_BIN"

rotate_log() {
  local f="$1"
  local max_bytes="${2:-52428800}" # 50MB
  [[ -f "$f" ]] || return 0
  local sz
  sz="$(stat -f%z "$f" 2>/dev/null || echo 0)"
  if [[ "$sz" -gt "$max_bytes" ]]; then
    mv -f "$f" "${f}.1" 2>/dev/null || true
    : >"$f"
  fi
}

# Prefer existing ~/.ollama/models when the managed models dir is empty.
# Avoid installing a LaunchAgent that points at an empty models tree.
if [[ ! -e "$OLLAMA_MODELS" ]]; then
  if [[ -d "${HOME}/.ollama/models/manifests" || -d "${HOME}/.ollama/models/blobs" ]]; then
    ln -s "${HOME}/.ollama/models" "$OLLAMA_MODELS"
    echo "NOTE: linked $OLLAMA_MODELS -> ${HOME}/.ollama/models"
  else
    mkdir -p "$OLLAMA_MODELS"
  fi
elif [[ -d "$OLLAMA_MODELS" && ! -L "$OLLAMA_MODELS" ]]; then
  if [[ ! -d "$OLLAMA_MODELS/manifests" && ! -d "$OLLAMA_MODELS/blobs" ]]; then
    if [[ -d "${HOME}/.ollama/models/manifests" || -d "${HOME}/.ollama/models/blobs" ]]; then
      rmdir "$OLLAMA_MODELS" 2>/dev/null || true
      if [[ ! -e "$OLLAMA_MODELS" ]]; then
        ln -s "${HOME}/.ollama/models" "$OLLAMA_MODELS"
        echo "NOTE: replaced empty $OLLAMA_MODELS with symlink to ${HOME}/.ollama/models"
      fi
    fi
  fi
fi

port_listener_summary() {
  python3 - <<'PY' 2>/dev/null || true
import json, subprocess, os
port = 11434
try:
    out = subprocess.check_output(
        ["lsof", "-t", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN"],
        text=True,
    ).strip()
except subprocess.CalledProcessError:
    print(json.dumps({"listening": False}))
    raise SystemExit(0)
pid = int(out.splitlines()[0])
cmd = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
exe = cmd.split()[0] if cmd else ""
print(json.dumps({
    "listening": True,
    "pid": pid,
    "command": cmd,
    "executable": exe,
    "executable_exists": os.path.isfile(exe),
}))
PY
}

exact_model_visible() {
  local wanted="$1"
  python3 - "$wanted" <<'PY'
import json, sys, urllib.request
wanted = sys.argv[1].strip()
try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
        data = json.loads(r.read().decode())
except Exception as e:
    print(f"tags_error:{e}", file=sys.stderr)
    raise SystemExit(2)
names = []
for m in data.get("models") or []:
    n = (m.get("name") or m.get("model") or "").strip()
    if n:
        names.append(n)
names_l = {n.lower() for n in names}
wanted_l = wanted.lower()
if wanted_l in names_l:
    raise SystemExit(0)
# bare name only matches explicit :latest (no other tag wildcard)
if ":" not in wanted_l and f"{wanted_l}:latest" in names_l:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

functional_chat_probe() {
  local model="$1"
  curl -fsS --noproxy '*' --max-time 90 \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${model}\",\"stream\":false,\"think\":false,\"keep_alive\":\"10m\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 OK\"}],\"options\":{\"num_predict\":8}}" \
    http://127.0.0.1:11434/api/chat 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); c=((d.get("message") or {}).get("content") or "").strip(); raise SystemExit(0 if c else 1)' 2>/dev/null
}

functional_embed_probe() {
  local model="$1"
  curl -fsS --noproxy '*' --max-time 30 \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${model}\",\"prompt\":\"suying probe\",\"keep_alive\":0}" \
    http://127.0.0.1:11434/api/embeddings 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if (d.get("embedding") or []) else 1)' 2>/dev/null
}

if [[ "$ACTION" == "status" ]]; then
  echo "label=${LABEL}"
  launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | head -20 || echo "launchctl: not loaded"
  port_listener_summary
  exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "已卸载 ${LABEL}（模型目录保留: ${OLLAMA_MODELS}）"
  exit 0
fi

[[ -x "$OLLAMA_BIN" ]] || {
  echo "ERROR: 缺少受管 Ollama 二进制: $OLLAMA_BIN" >&2
  echo "请先通过 remote-install --offline-tools 安装" >&2
  exit 1
}

# 所有权 + 签名门禁
if ! codesign --verify --strict "$OLLAMA_BIN" >/dev/null 2>&1; then
  echo "ERROR: 受管 Ollama 签名校验失败: $OLLAMA_BIN" >&2
  exit 1
fi

listener="$(port_listener_summary)"
if echo "$listener" | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(0 if not d.get("listening") else 1)' 2>/dev/null; then
  :
else
  pid="$(echo "$listener" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pid",""))')"
  cmd="$(echo "$listener" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("command",""))')"
  if [[ "$cmd" != *"$TOOL_BIN/ollama"* && "$cmd" != *"Suying/runtime/tools/bin/ollama"* ]]; then
    echo "ERROR: 11434 已被外部 Ollama 占用 (pid=${pid})" >&2
    echo "  $cmd" >&2
    echo "请关闭外部 Ollama 应用后重试；速影不会误杀外部进程。" >&2
    exit 2
  fi
  echo "WARN: 已有受管 Ollama 监听，将重装 LaunchAgent"
  kill "$pid" 2>/dev/null || true
  sleep 1
fi

rotate_log "$OLLAMA_OUT"
rotate_log "$OLLAMA_ERR"
touch "$OLLAMA_OUT" "$OLLAMA_ERR"
ln -sfn "$OLLAMA_OUT" "$OLLAMA_LOG_LEGACY"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${OLLAMA_BIN}</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key>
    <string>127.0.0.1:11434</string>
    <key>OLLAMA_MODELS</key>
    <string>${OLLAMA_MODELS}</string>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${OLLAMA_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${OLLAMA_ERR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}"
launchctl kickstart -k "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true

ready=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == "1" ]] || {
  echo "ERROR: LaunchAgent 已安装但 11434 未响应" >&2
  exit 1
}

# 门禁：所有权正确
listener="$(port_listener_summary)"
pid="$(echo "$listener" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pid",""))')"
cmd="$(echo "$listener" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("command",""))')"
if [[ "$cmd" != *"$TOOL_BIN/ollama"* && "$cmd" != *"Suying/runtime/tools/bin/ollama"* ]]; then
  echo "ERROR: 启动后 11434 非受管进程 (pid=${pid})" >&2
  echo "  $cmd" >&2
  exit 2
fi

echo "Ollama LaunchAgent 已就绪: ${LABEL} pid=${pid}"
curl -fsS --max-time 5 "http://127.0.0.1:11434/api/version" 2>/dev/null | head -c 200 || true
echo

if [[ "$SKIP_FUNCTIONAL" == "1" ]]; then
  echo "NOTE: 跳过功能探针 (SUYING_OLLAMA_SKIP_FUNCTIONAL=1)"
  exit 0
fi

# 精确模型可见 + 最小功能请求（模型未装时仅告警，不硬失败——由 install-profile/回执门禁负责）
CHAT_OK=0
EMBED_OK=0
if exact_model_visible "$REQUIRE_NARRATION_MODEL"; then
  if functional_chat_probe "$REQUIRE_NARRATION_MODEL"; then
    CHAT_OK=1
    echo "功能探针 chat OK model=${REQUIRE_NARRATION_MODEL}"
  else
    echo "WARN: 模型 ${REQUIRE_NARRATION_MODEL} 可见但 chat 探针失败" >&2
  fi
else
  echo "NOTE: 精确模型尚未可见: ${REQUIRE_NARRATION_MODEL}"
fi
if exact_model_visible "$REQUIRE_EMBED_MODEL"; then
  if functional_embed_probe "$REQUIRE_EMBED_MODEL"; then
    EMBED_OK=1
    echo "功能探针 embed OK model=${REQUIRE_EMBED_MODEL}"
  else
    echo "WARN: 模型 ${REQUIRE_EMBED_MODEL} 可见但 embed 探针失败" >&2
  fi
else
  echo "NOTE: 精确模型尚未可见: ${REQUIRE_EMBED_MODEL}"
fi

# 若两者都可见却探针失败 → 硬失败（签名/runner 类故障）
if exact_model_visible "$REQUIRE_NARRATION_MODEL" && [[ "$CHAT_OK" != "1" ]]; then
  echo "ERROR: 旁白模型功能探针失败（可能 Code Signature Invalid / runner crash）" >&2
  exit 3
fi
if exact_model_visible "$REQUIRE_EMBED_MODEL" && [[ "$EMBED_OK" != "1" ]]; then
  echo "ERROR: 向量模型功能探针失败" >&2
  exit 3
fi

exit 0
