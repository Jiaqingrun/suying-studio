#!/usr/bin/env bash
# Build 速影.app and install to /Applications + Desktop.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/desktop"
npm install
npm run build
npm run tauri build -- --bundles app

APP_SRC="$ROOT/apps/desktop/src-tauri/target/release/bundle/macos/速影.app"
if [[ ! -d "$APP_SRC" ]]; then
  echo "Build failed: missing $APP_SRC" >&2
  exit 1
fi

# Quit running app so we can replace the bundle
osascript -e 'tell application "速影" to quit' 2>/dev/null || true
sleep 1
pkill -x "速影" 2>/dev/null || true

install_one() {
  local dest="$1"
  rm -rf "$dest"
  ditto "$APP_SRC" "$dest"
  echo "Installed: $dest"
}

install_one "/Applications/速影.app"
install_one "$HOME/Desktop/速影.app"

# Also keep release bundle path current
echo ""
echo "Built: $APP_SRC"
echo "请重新打开「速影」→ 触达 Tab，顶部即「封面设置（模板套）」"
