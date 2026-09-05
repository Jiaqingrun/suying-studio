#!/usr/bin/env bash
# Layered regression before shipping a product kit (Phase4).
# Usage: ./scripts/gate_package_regression.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> gate: desktop tsc"
(cd apps/desktop && npx tsc --noEmit)

echo "==> gate: smoke_test"
python3 scripts/smoke_test.py

echo "==> gate: smoke_system_events"
python3 scripts/smoke_system_events.py

echo "==> gate: smoke_chrome_migrate"
python3 scripts/smoke_chrome_migrate.py

echo "==> gate: smoke_remote_deploy (script self-check)"
python3 scripts/smoke_remote_deploy.py

echo "==> gate: version alignment"
APP_VER="$(node -p "require('./apps/desktop/package.json').version")"
TAURI_VER="$(node -p "require('./apps/desktop/src-tauri/tauri.conf.json').version")"
CARGO_VER="$(grep -E '^version' apps/desktop/src-tauri/Cargo.toml | head -1 | sed 's/.*"\(.*\)"/\1/')"
ENG_VER="$(python3 -c "from engine.version import ENGINE_VERSION; print(ENGINE_VERSION)")"
echo "    app=$APP_VER tauri=$TAURI_VER cargo=$CARGO_VER engine=$ENG_VER"
[[ "$APP_VER" == "$TAURI_VER" && "$APP_VER" == "$CARGO_VER" && "$APP_VER" == "$ENG_VER" ]] || {
  echo "ERROR: version mismatch" >&2
  exit 1
}

echo "==> gate_package_regression: OK ($APP_VER)"
