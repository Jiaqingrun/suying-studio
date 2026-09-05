#!/bin/bash
# Install / uninstall LaunchAgent for 速影行业包 → T2S 自动备份（仅 current）
set -euo pipefail

LABEL="com.qr.suying-industry-pack-backup"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/scripts/backup_industry_packs_to_zspace.py"
LOG_OUT="$HOME/.qr/logs/suying-industry-pack-backup.launchd.out"
LOG_ERR="$HOME/.qr/logs/suying-industry-pack-backup.launchd.err"
# 1 hour; fingerprint skip when unchanged; never auto-creates releases/
INTERVAL="${INTERVAL:-3600}"

mkdir -p "$HOME/.qr/logs" "$HOME/Library/LaunchAgents"

cmd="${1:-status}"

install_plist() {
  if [[ ! -f "$PY" ]]; then
    echo "missing $PY" >&2
    exit 1
  fi
  PYTHON_BIN="$(command -v python3)"
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${PY}</string>
  </array>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>StartInterval</key><integer>${INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${LOG_OUT}</string>
  <key>StandardErrorPath</key><string>${LOG_ERR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  echo "installed ${LABEL} (interval=${INTERVAL}s; current only, no auto releases)"
  echo "plist: $PLIST"
  echo "manual: ${PYTHON_BIN} ${PY}"
}

case "$cmd" in
  install)
    install_plist
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "uninstalled ${LABEL}"
    ;;
  status)
    echo "label: ${LABEL}"
    echo "plist: ${PLIST}"
    if [[ -f "$PLIST" ]]; then
      plutil -p "$PLIST" | head -40
    else
      echo "plist: missing"
    fi
    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | head -40 || echo "launchd: not loaded"
    if [[ -f "$HOME/.qr/suying-industry-pack-backup-state.json" ]]; then
      echo "--- state ---"
      cat "$HOME/.qr/suying-industry-pack-backup-state.json"
    fi
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}" >&2
    exit 2
    ;;
esac
