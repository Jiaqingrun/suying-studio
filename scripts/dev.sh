#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

chmod +x scripts/start-engine.sh

echo "Starting Montage Studio engine on :8766 ..."
./scripts/start-engine.sh &
ENGINE_PID=$!

cleanup() {
  kill "$ENGINE_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 1
cd apps/desktop
npm install
npm run tauri dev
