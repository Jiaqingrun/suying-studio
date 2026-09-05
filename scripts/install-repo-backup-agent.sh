#!/bin/bash
# Install / uninstall LaunchAgent for 速影主仓 → T2S 自动备份
set -euo pipefail

LABEL="com.qr.suying-repo-backup"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/scripts/backup_repo_to_zspace.py"
LOG_OUT="$HOME/.qr/logs/suying-repo-backup.launchd.out"
LOG_ERR="$HOME/.qr/logs/suying-repo-backup.launchd.err"
# 10 minutes; fingerprint skip when unchanged
INTERVAL="${INTERVAL:-600}"

mkdir -p "$HOME/.qr/logs" "$HOME/Library/LaunchAgents"

cmd="${1:-status}"

install_plist() {
  if [[ ! -f "$PY" ]]; then
    echo "missing $PY" >&2
    exit 1
  fi
  # Prefer project venv-less system/anaconda python3 used elsewhere for engine ops.
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
  echo "installed ${LABEL} (interval=${INTERVAL}s)"
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
    if [[ -f "$HOME/.qr/suying-repo-backup-state.json" ]]; then
      echo "--- state ---"
      cat "$HOME/.qr/suying-repo-backup-state.json"
    fi
    ;;
  run)
    exec python3 "$PY" "${@:2}"
    ;;
  *)
    echo "usage: $0 install|uninstall|status|run [--force|--dry-run]" >&2
    exit 2
    ;;
esac
