#!/usr/bin/env bash
# Install LaunchAgent that verifies the signed T2S update chain.
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
SIGNATURE="${MANIFEST}.sig"
STATE="${HOME}/.qr/suying-carrier-update-state.json"
LOG="${HOME}/.qr/logs/suying-carrier-update.log"
mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ ! -f "$MANIFEST" || ! -f "$SIGNATURE" ]]; then
  echo "{\"checked_at\":\"$ts\",\"ok\":false,\"reason\":\"manifest_missing\",\"carrier\":\"$CARRIER\"}" > "$STATE"
  echo "$ts missing signed manifest $MANIFEST" >> "$LOG"
  exit 0
fi
# Prefer engine API if up; otherwise use the same bundled verifier.
if curl -sf "http://127.0.0.1:8766/ops/app-update" -o /tmp/suying-app-update.json 2>/dev/null; then
  cp /tmp/suying-app-update.json "$STATE"
  echo "$ts api_ok" >> "$LOG"
else
  APP="/Applications/速影 Studio.app"
  [[ -d "$APP" ]] || APP="${HOME}/Applications/速影 Studio.app"
  [[ -d "$APP" ]] || APP="/Applications/速影.app"
  [[ -d "$APP" ]] || APP="${HOME}/Applications/速影.app"
  PY="${APP}/Contents/Resources/runtime/python/bin/python3"
  STUDIO="${APP}/Contents/Resources/runtime/studio"
  if [[ ! -x "$PY" || ! -d "$STUDIO/engine" ]]; then
    echo "{\"checked_at\":\"$ts\",\"ok\":false,\"reason\":\"verifier_missing\"}" > "$STATE"
    echo "$ts verifier_missing" >> "$LOG"
    exit 0
  fi
  if SUYING_CARRIER_ROOT="$CARRIER" PYTHONPATH="$STUDIO" "$PY" - "$STATE" <<'PY'
import json
import sys
from pathlib import Path
from engine.ops.app_update import check_update

result = check_update()
Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
raise SystemExit(0 if result.get("ok") else 1)
PY
  then
    echo "$ts bundled_verifier_ok" >> "$LOG"
  else
    echo "$ts signature_verify_failed" >> "$LOG"
  fi
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
