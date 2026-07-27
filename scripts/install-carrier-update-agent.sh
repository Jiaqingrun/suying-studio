#!/usr/bin/env bash
# Install LaunchAgent that only checks T2S carrier mirror for app/latest.json.
# Does NOT sync media library. Relies on 极空间 client to keep ~/Suying/carrier fresh.
set -euo pipefail

LABEL="com.suying.carrier-update"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
TOOLS="${HOME}/QR/tools"
mkdir -p "$TOOLS" "${HOME}/.qr/logs" "${HOME}/Library/LaunchAgents"

cat > "${TOOLS}/suying-carrier-update-check.sh" << 'EOS'
#!/usr/bin/env bash
set -euo pipefail
CARRIER="${SUYING_CARRIER:-$HOME/Suying/carrier}"
MANIFEST="${CARRIER}/app/latest.json"
STATE="${HOME}/.qr/suying-carrier-update-state.json"
LOG="${HOME}/.qr/logs/suying-carrier-update.log"
mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ ! -f "$MANIFEST" ]]; then
  echo "{\"checked_at\":\"$ts\",\"ok\":false,\"reason\":\"manifest_missing\",\"carrier\":\"$CARRIER\"}" > "$STATE"
  echo "$ts missing $MANIFEST" >> "$LOG"
  exit 0
fi
# Prefer engine API if up; else just record manifest version
if curl -sf "http://127.0.0.1:8766/ops/app-update" -o /tmp/suying-app-update.json 2>/dev/null; then
  cp /tmp/suying-app-update.json "$STATE"
  echo "$ts api_ok" >> "$LOG"
else
  ver="$(python3 -c "import json;print(json.load(open('$MANIFEST')).get('version',''))" 2>/dev/null || echo "")
  echo "{\"checked_at\":\"$ts\",\"ok\":true,\"remote_version\":\"$ver\",\"via\":\"file\"}" > "$STATE"
  echo "$ts file_ok ver=$ver" >> "$LOG"
fi
EOS
chmod +x "${TOOLS}/suying-carrier-update-check.sh"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${TOOLS}/suying-carrier-update-check.sh</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${HOME}/.qr/logs/suying-carrier-update.out</string>
  <key>StandardErrorPath</key><string>${HOME}/.qr/logs/suying-carrier-update.err</string>
</dict>
</plist>
EOF

uid="$(id -u)"
launchctl bootout "gui/${uid}" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/${uid}" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
echo "Installed ${LABEL}"
echo "  plist: $PLIST"
echo "  script: ${TOOLS}/suying-carrier-update-check.sh"
echo "  carrier: \${SUYING_CARRIER:-\$HOME/Suying/carrier}"
