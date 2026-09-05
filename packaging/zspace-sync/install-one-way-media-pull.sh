#!/bin/bash
# 安装/卸载：极空间「最近项目」→ 本机片库 Camera 单向增量（每 10 分钟）
# 目的：settings.library_root + 徐玲飞/HWABR-Q/HWABR-Q相册备份/Camera
# LaunchAgent 直接 /bin/bash 跑包装脚本（本机 APFS，无 Terminal，不依赖引擎在线）。
set -euo pipefail

ACTION="${1:-install}"
LABEL="com.qr.one-way-media-pull"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
TOOLS="${HOME}/QR/tools"
LOG_DIR="${HOME}/.qr/logs"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_SH="${TOOLS}/one-way-media-pull.sh"

usage() {
  cat <<'EOF'
用法:
  install-one-way-media-pull.sh install
  install-one-way-media-pull.sh uninstall
  install-one-way-media-pull.sh status
  install-one-way-media-pull.sh run-once
EOF
}

remove_legacy() {
  # 清理历史错误产物：写盘探测、open.command、引擎 wake 代理
  local domain="gui/$(id -u)"
  for lab in com.qr.write-probe3 com.qr.write-probe2 com.qr.write-probe; do
    local pl="${HOME}/Library/LaunchAgents/${lab}.plist"
    launchctl bootout "$domain" "$pl" >/dev/null 2>&1 || true
    launchctl unload "$pl" >/dev/null 2>&1 || true
    rm -f "$pl"
  done
  rm -f \
    "${TOOLS}/one-way-media-pull-wake.sh" \
    "${TOOLS}/one-way-media-pull.command" \
    "${TOOLS}/write-probe-once.command" \
    "${HOME}/.qr/logs/one-way-media-pull.open.log" \
    /tmp/qr-write-probe.sh \
    /tmp/qr-app-probe.log \
    /tmp/qr-finder-move.log 2>/dev/null || true
}

install_tools() {
  mkdir -p "$TOOLS" "$LOG_DIR" "${HOME}/Library/LaunchAgents"
  for name in one-way-media-pull.py one-way-media-pull.sh zspace-team-sync.py install-one-way-media-pull.sh; do
    src="${SRC_DIR}/${name}"
    dst="${TOOLS}/${name}"
    if [[ -f "$src" ]]; then
      src_abs="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")"
      dst_abs="$(cd "$(dirname "$dst")" 2>/dev/null && pwd)/$(basename "$dst")" || dst_abs=""
      if [[ "$src_abs" != "$dst_abs" ]]; then
        cp -f "$src" "$dst"
      fi
      chmod +x "$dst"
    fi
  done
  xattr -dr com.apple.quarantine "$TOOLS" 2>/dev/null || true
  if [[ ! -f "${TOOLS}/one-way-media-pull.py" || ! -f "${TOOLS}/zspace-team-sync.py" || ! -f "$SCRIPT_SH" ]]; then
    echo "缺少 one-way-media-pull 工具文件" >&2
    exit 2
  fi
}

write_plist() {
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT_SH}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/one-way-media-pull.launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/one-way-media-pull.launchd.log</string>
  <key>Nice</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>ThrottleInterval</key>
  <integer>60</integer>
</dict>
</plist>
EOF
}

load_agent() {
  local domain="gui/$(id -u)"
  launchctl bootout "$domain" "$PLIST" >/dev/null 2>&1 || true
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$PLIST"
  launchctl enable "${domain}/${LABEL}" >/dev/null 2>&1 || true
}

case "$ACTION" in
  -h|--help) usage; exit 0 ;;
  uninstall)
    launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    rm -f "$PLIST"
    remove_legacy
    echo "已卸载 ${LABEL}"
    ;;
  status)
    echo "plist: $PLIST"
    if [[ -f "$PLIST" ]]; then
      /usr/libexec/PlistBuddy -c 'Print :StartInterval' "$PLIST" 2>/dev/null | sed 's/^/StartInterval(s)=/'
      /usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$PLIST" 2>/dev/null | sed 's/^/ProgramArguments: /'
    else
      echo "未安装"
    fi
    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | head -35 || echo "launchctl: 未加载"
    echo "--- recent state ---"
    if [[ -f "${HOME}/.qr/one-way-media-pull-state.json" ]]; then
      python3 -c "import json;from pathlib import Path;d=json.loads((Path.home()/'.qr'/'one-way-media-pull-state.json').read_text());print({k:d.get(k) for k in ['remote','local','finished_at','downloaded','failed','skipped_same','pending_total']})"
    else
      echo "(尚无状态文件)"
    fi
    pgrep -fl one-way-media-pull || echo "(当前无同步进程)"
    ;;
  run-once)
    install_tools
    exec /bin/bash "$SCRIPT_SH"
    ;;
  install)
    remove_legacy
    install_tools
    write_plist
    load_agent
    echo "已安装 ${LABEL}：每 600 秒后台增量检查（无 Terminal）"
    echo "远端：团队空间/手机相册备份/徐玲飞/Iphone/…/最近项目"
    echo "本地：settings.library_root + 徐玲飞/HWABR-Q/HWABR-Q相册备份/Camera"
    echo "日志: ${LOG_DIR}/one-way-media-pull.launchd.log / one-way-media-pull.log"
    echo "立即触发: /bin/bash ${SCRIPT_SH}"
    ;;
  *)
    echo "未知动作: $ACTION" >&2
    usage
    exit 2
    ;;
esac
