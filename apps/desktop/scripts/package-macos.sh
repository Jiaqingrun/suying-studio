#!/usr/bin/env bash
# Build a customer-facing macOS release kit for 速影.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(node -p "require('./package.json').version")"
ARCH="$(uname -m)"
STAMP="$(date +%Y%m%d)"
OUT_NAME="速影-${VERSION}-macos-${ARCH}"
DESKTOP_KIT="${HOME}/Desktop/${OUT_NAME}"
BUNDLE_DIR="${ROOT}/src-tauri/target/release/bundle"

echo "==> Building 速影 ${VERSION} (${ARCH})"
npm run tauri build

APP_SRC="${BUNDLE_DIR}/macos/速影.app"
DMG_SRC="$(ls -1 "${BUNDLE_DIR}/dmg/"*.dmg 2>/dev/null | head -1 || true)"

if [[ ! -d "$APP_SRC" ]]; then
  echo "ERROR: missing ${APP_SRC}" >&2
  exit 1
fi

# Ad-hoc sign so local Gatekeeper is slightly less angry (not Apple notarized).
echo "==> Ad-hoc codesign"
codesign --force --deep --sign - "$APP_SRC" 2>/dev/null || true

echo "==> Assembling Desktop kit: ${DESKTOP_KIT}"
rm -rf "$DESKTOP_KIT"
mkdir -p "$DESKTOP_KIT"
ditto "$APP_SRC" "${DESKTOP_KIT}/速影.app"

if [[ -n "${DMG_SRC}" && -f "${DMG_SRC}" ]]; then
  cp "$DMG_SRC" "${DESKTOP_KIT}/速影-${VERSION}.dmg"
fi

# Also refresh the standalone Desktop app for daily use
rm -rf "${HOME}/Desktop/速影.app"
ditto "$APP_SRC" "${HOME}/Desktop/速影.app"

cat > "${DESKTOP_KIT}/安装说明.txt" << EOF
速影 ${VERSION}（macOS ${ARCH}）
打包日期：${STAMP}

【安装】
1. 将「速影.app」拖到「应用程序」，或直接双击本目录内的 App。
2. 若提示无法打开：右键 → 打开 → 仍要打开。
3. 推荐使用「速影-${VERSION}.dmg」（若有）安装。

【引擎依赖（必读）】
速影桌面端需本机 Montage 引擎协同：
- 默认查找：~/Suying/montage-studio 或 ~/QR/dev/montage-studio
- 也可设置环境变量 SUYING_ROOT 指向含 engine/main.py 的仓库根目录
- Python 需带 uvicorn（可用 SUYING_PYTHON 指定解释器）

当前版本的客户交付仍为「App + 本机引擎」；激活密令（1 机买断）为后续闸门，不在本包内。

【校验】
见同目录 SHA256.txt
EOF

(
  cd "$DESKTOP_KIT"
  shasum -a 256 "速影.app/Contents/MacOS/appsdesktop" > SHA256.txt
  if [[ -f "速影-${VERSION}.dmg" ]]; then
    shasum -a 256 "速影-${VERSION}.dmg" >> SHA256.txt
  fi
)

# Zip for easier private transfer (keeps .app intact)
ZIP_PATH="${HOME}/Desktop/${OUT_NAME}.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$DESKTOP_KIT" "$ZIP_PATH"

echo "==> Done"
echo "    Kit:  ${DESKTOP_KIT}"
echo "    Zip:  ${ZIP_PATH}"
echo "    App:  ${HOME}/Desktop/速影.app"
ls -lh "$DESKTOP_KIT" "$ZIP_PATH" "${HOME}/Desktop/速影.app" 2>/dev/null | sed 's/^/    /'
