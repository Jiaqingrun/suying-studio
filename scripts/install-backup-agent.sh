#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.qr.suying.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="${SUYING_PYTHON:-$HOME/Suying/runtime/python/bin/python3}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Suying/runtime/logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PYTHON</string>
    <string>$ROOT/scripts/suying-backup.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$HOME/Suying/runtime/logs/backup-agent.log</string>
  <key>StandardErrorPath</key><string>$HOME/Suying/runtime/logs/backup-agent.error.log</string>
</dict></plist>
EOF
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装每周数据保护任务"
