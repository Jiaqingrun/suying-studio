#!/usr/bin/env bash
# SLA probe: LaunchAgent engine supervisor (local machine).
set -euo pipefail
PORT="${SUYING_PORT:-8766}"
LABEL="com.qr.suying.engine"
FAIL=0
ok() { echo "OK  $*"; }
bad() { echo "FAIL $*"; FAIL=1; }

echo "== suying engine supervisor check =="

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  ok "LaunchAgent loaded: ${LABEL}"
else
  if [[ -f "${HOME}/Library/LaunchAgents/${LABEL}.plist" ]]; then
    bad "plist exists but not loaded"
  else
    bad "LaunchAgent not installed (expected on customer / post-install)"
  fi
fi

LISTEN=0
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "port ${PORT} LISTEN"
  LISTEN=1
else
  bad "port ${PORT} not listening"
fi

HEALTH_JSON="$(curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
if [[ -n "$HEALTH_JSON" ]]; then
  ok "GET /health answered"
  echo "  ${HEALTH_JSON:0:200}"
  STATUS="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status',''))" "$HEALTH_JSON" 2>/dev/null || true)"
  if [[ "$STATUS" == "starting" ]]; then
    ok "boot phase starting (acceptable warm-up)"
  elif [[ "$STATUS" == "ok" || "$STATUS" == "degraded" || "$STATUS" == "blocked" ]]; then
    ok "health status=${STATUS}"
  else
    bad "unexpected health status=${STATUS:-empty}"
  fi
else
  bad "GET /health unreachable"
fi

READY_JSON="$(curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/readiness" 2>/dev/null || true)"
if [[ -n "$READY_JSON" ]]; then
  ok "GET /readiness answered"
  python3 - <<PY || true
import json,sys
d=json.loads('''${READY_JSON}''')
print("  ready=", d.get("ready"), " offline_class=", d.get("offline_class"), " boot=", (d.get("boot") or {}).get("boot_phase"))
PY
else
  if [[ "$LISTEN" == "1" ]]; then
    bad "listen but /readiness down"
  fi
fi

SUP="$(curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/ops/engine-supervisor" 2>/dev/null || true)"
if [[ -n "$SUP" ]]; then
  ok "GET /ops/engine-supervisor"
else
  # older engines
  echo "NOTE /ops/engine-supervisor unavailable (pre-0.6.39)"
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: OK"
exit 0
