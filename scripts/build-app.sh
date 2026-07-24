#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../apps/desktop"
npm install
npm run build
npm run tauri build -- --bundles app
echo ""
echo "Built: src-tauri/target/release/bundle/macos/Montage Studio.app"
