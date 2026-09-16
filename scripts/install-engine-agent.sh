#!/usr/bin/env bash
# 速影引擎 LaunchAgent：写 plist + wrapper，KeepAlive 常驻 :8766。
# V-08 H1：attach 模式必须验证 LISTEN + /readiness，pid 活着但端口/控制面挂掉 → exit 1。
set -euo pipefail

ACTION="${1:-install}"
LABEL="com.qr.suying.engine"
PORT="${SUYING_PORT:-8766}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
BIN_DIR="${HOME}/Suying/runtime/bin"
WRAPPER="${BIN_DIR}/suying-engine-agent.sh"
LOG_DIR="${HOME}/Suying/logs"
OUT_LOG="${LOG_DIR}/engine-agent.out.log"
ERR_LOG="${LOG_DIR}/engine-agent.err.log"

# Prefer bundled Studio; fall back to SUYING_ROOT / this repo (dev).
resolve_studio() {
  if [[ -n "${SUYING_STUDIO:-}" && -d "${SUYING_STUDIO}" ]]; then
    printf '%s\n' "${SUYING_STUDIO}"
    return 0
  fi
  local app_studio="/Applications/速影 Studio.app/Contents/Resources/runtime/studio"
  if [[ -d "$app_studio" ]]; then
    printf '%s\n' "$app_studio"
    return 0
  fi
  if [[ -n "${SUYING_ROOT:-}" && -f "${SUYING_ROOT}/engine/main.py" ]]; then
    printf '%s\n' "${SUYING_ROOT}"
    return 0
  fi
  local here
  here="$(cd "$(dirname "$0")/.." && pwd)"
  if [[ -f "${here}/engine/main.py" ]]; then
    printf '%s\n' "$here"
    return 0
  fi
  return 1
}

resolve_python() {
  local studio="$1"
  local bundled="/Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3"
  if [[ -x "$bundled" ]]; then
    printf '%s\n' "$bundled"
    return 0
  fi
  if [[ -x "${studio}/.venv/bin/python3" ]]; then
    printf '%s\n' "${studio}/.venv/bin/python3"
    return 0
  fi
  command -v python3
}

usage() {
  cat <<'EOF'
用法:
  install-engine-agent.sh install    # 写 wrapper + plist 并 bootstrap/kickstart
  install-engine-agent.sh kickstart  # launchctl kickstart -k
  install-engine-agent.sh status     # launchctl + port/readiness 摘要
  install-engine-agent.sh uninstall  # bootout + 删 plist（保留 wrapper）
EOF
}

write_wrapper() {
  local studio="$1"
  local py="$2"
  local q_home q_studio q_py
  q_home="$(printf '%q' "$HOME")"
  q_studio="$(printf '%q' "$studio")"
  q_py="$(printf '%q' "$py")"
  mkdir -p "$BIN_DIR" "$LOG_DIR"
  cat >"$WRAPPER" <<EOF
#!/bin/bash
set -euo pipefail
export HOME=${q_home}
export PATH="/opt/homebrew/bin:/usr/local/bin:\${HOME}/Suying/runtime/tools/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH=${q_studio}
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export SUYING_ROOT=${q_studio}
export MONTAGE_ROOT=${q_studio}
export MONTAGE_PORT=${PORT}
export SUYING_PORT=${PORT}
export SUYING_BUNDLED_RUNTIME=1
export SUYING_ALLOW_CACHED_LICENSE_BINDING=1
export SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=1
if [[ -f "\${HOME}/Suying/runtime/local.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "\${HOME}/Suying/runtime/local.env"
  set +a
fi
PORT=${PORT}
PY=${q_py}
STUDIO=${q_studio}

is_suying_cmd() {
  case "\$1" in
    *engine.main*|*"/速影 Studio.app/"*|*"Contents/Resources/runtime/"*|*"\${STUDIO}"*) return 0 ;;
    *) return 1 ;;
  esac
}

# Kickstart / attach probe: /readiness only (not slow /health). See ENGINE_SUPERVISOR.md.
readiness_ok() {
  curl -fsS --noproxy '*' --max-time 2 "http://127.0.0.1:\${PORT}/readiness" >/dev/null 2>&1
}

# Graceful stop for zombie engine.main (alive, no LISTEN). Never touch ffmpeg.
stop_zombie_engine_main() {
  local p cmd
  while read -r p; do
    [[ -n "\$p" ]] || continue
    cmd="\$(ps -p "\$p" -o command= 2>/dev/null || true)"
    if is_suying_cmd "\$cmd"; then
      echo "suying-engine-agent: stopping zombie engine.main pid=\$p" >&2
      kill "\$p" 2>/dev/null || true
    fi
  done < <(pgrep -f 'engine\\.main' 2>/dev/null || true)
  sleep 1
}

# If a healthy engine already owns the port, pin to that PID so KeepAlive
# does not fight App-spawned children; exit non-zero when it dies OR port/readiness fails.
if command -v lsof >/dev/null 2>&1; then
  if pids="\$(lsof -t -nP -iTCP:\${PORT} -sTCP:LISTEN 2>/dev/null || true)"; then
    if [[ -n "\$pids" ]]; then
      first_pid="\$(echo "\$pids" | head -1)"
      cmd="\$(ps -p "\$first_pid" -o command= 2>/dev/null || true)"
      if is_suying_cmd "\$cmd" && readiness_ok; then
        echo "suying-engine-agent: attach pid=\$first_pid"
        # V-08 H1: health-aware attach — pid alive but !LISTEN or /readiness down → exit 1
        while kill -0 "\$first_pid" 2>/dev/null; do
          if command -v lsof >/dev/null 2>&1; then
            owner="\$(lsof -t -nP -iTCP:\${PORT} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
            if [[ -z "\$owner" || "\$owner" != "\$first_pid" ]]; then
              echo "suying-engine-agent: pid alive but port \${PORT} not listening (owner=\${owner:-none})" >&2
              exit 1
            fi
          fi
          if ! readiness_ok; then
            echo "suying-engine-agent: pid alive but /readiness down" >&2
            exit 1
          fi
          sleep 5
        done
        echo "suying-engine-agent: attached pid exited"
        exit 1
      fi
      if ! is_suying_cmd "\$cmd"; then
        echo "suying-engine-agent: port \${PORT} taken by non-suying: \$cmd" >&2
        exit 2
      fi
      # Stale/broken suying listener — drop only our processes then start.
      for pid in \$pids; do
        c="\$(ps -p "\$pid" -o command= 2>/dev/null || true)"
        if is_suying_cmd "\$c"; then
          kill "\$pid" 2>/dev/null || true
        fi
      done
      sleep 1
    fi
  fi
fi

# No LISTEN but engine.main still alive → clear before bind (TERM only; not ffmpeg).
if ! lsof -nP -iTCP:\${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  if pgrep -f 'engine\\.main' >/dev/null 2>&1; then
    stop_zombie_engine_main
  fi
fi

cd "\$STUDIO"
echo "suying-engine-agent: start py=\$PY studio=\$STUDIO port=\$PORT"
exec "\$PY" -m engine.main
EOF
  chmod 755 "$WRAPPER"
}

write_plist() {
  local studio="$1"
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${WRAPPER}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>SUYING_ALLOW_CACHED_LICENSE_BINDING</key>
    <string>1</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>${studio}</string>
  <key>StandardOutPath</key>
  <string>${OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${ERR_LOG}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>15</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
EOF
}

do_kickstart() {
  local uid
  uid="$(id -u)"
  local target="gui/${uid}/${LABEL}"
  if ! launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootstrap "gui/${uid}" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
  fi
  launchctl kickstart -k "$target"
}

do_status() {
  local uid
  uid="$(id -u)"
  echo "label=${LABEL}"
  echo "plist=${PLIST} exists=$([[ -f $PLIST ]] && echo yes || echo no)"
  echo "wrapper=${WRAPPER} exists=$([[ -f $WRAPPER ]] && echo yes || echo no)"
  if launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
    echo "loaded=yes"
  else
    echo "loaded=no"
  fi
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "listen=yes"
  else
    echo "listen=no"
  fi
  if curl -fsS --noproxy '*' --max-time 2 "http://127.0.0.1:${PORT}/readiness" >/dev/null 2>&1; then
    echo "readiness=ok"
  else
    echo "readiness=down"
  fi
}

case "$ACTION" in
  -h|--help) usage; exit 0 ;;
  status) do_status; exit 0 ;;
  uninstall)
    uid="$(id -u)"
    launchctl bootout "gui/${uid}/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "uninstalled ${LABEL}"
    exit 0
    ;;
  kickstart)
    [[ -f "$PLIST" ]] || { echo "ERROR: plist missing; run install first" >&2; exit 1; }
    do_kickstart
    echo "kickstarted ${LABEL}"
    exit 0
    ;;
  install) ;;
  *) echo "未知参数: $ACTION" >&2; usage; exit 2 ;;
esac

STUDIO="$(resolve_studio)" || {
  echo "ERROR: 找不到 studio（engine/main.py）" >&2
  exit 1
}
PY="$(resolve_python "$STUDIO")"
LICENSE_DIR="${HOME}/Suying/runtime/security"
if [[ ! -f "${LICENSE_DIR}/license.suying-license" || ! -f "${LICENSE_DIR}/device-key-id" ]]; then
  echo "WARN: 许可证磁盘缓存不完整；agent 可能无法常驻业务就绪" >&2
fi

write_wrapper "$STUDIO" "$PY"
write_plist "$STUDIO"
uid="$(id -u)"
launchctl bootout "gui/${uid}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${uid}" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
do_kickstart
echo "installed ${LABEL}"
echo "  studio=${STUDIO}"
echo "  python=${PY}"
echo "  wrapper=${WRAPPER}"
do_status
