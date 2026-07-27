#!/usr/bin/env bash
# Build a self-contained macOS 速影.app (scheme A):
#   Tauri shell + embedded studio + creative + CPython.
# Ollama / large models stay on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO_ROOT="$(cd "${ROOT}/../.." && pwd)"
cd "$ROOT"

VERSION="$(node -p "require('./package.json').version")"
ARCH="$(uname -m)"
STAMP="$(date +%Y%m%d)"
OUT_NAME="速影-${VERSION}-macos-${ARCH}"
DESKTOP_KIT="${HOME}/Desktop/${OUT_NAME}"
BUNDLE_DIR="${ROOT}/src-tauri/target/release/bundle"
EMBED="${EMBED:-1}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

echo "==> Building 速影 ${VERSION} (${ARCH}) [embed=${EMBED}]"
npm run tauri build

APP_SRC="${BUNDLE_DIR}/macos/速影.app"

if [[ ! -d "$APP_SRC" ]]; then
  echo "ERROR: missing ${APP_SRC}" >&2
  exit 1
fi

if [[ "${EMBED}" == "1" ]]; then
  echo "==> Embedding studio + creative + Python into App"
  bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"

  echo "==> Smoke embedded engine /health"
  PYBIN="${APP_SRC}/Contents/Resources/runtime/python/bin/python3"
  STUDIO="${APP_SRC}/Contents/Resources/runtime/studio"
  SMOKE_HOME="$(mktemp -d)"
  SMOKE_PORT="$("$PYBIN" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  SMOKE_LOG="${SMOKE_HOME}/engine.log"
  (
    cd "$STUDIO"
    exec env HOME="$SMOKE_HOME" PYTHONDONTWRITEBYTECODE=1 MONTAGE_PORT="$SMOKE_PORT" \
      "$PYBIN" -m engine.main >"$SMOKE_LOG" 2>&1
  ) &
  SMOKE_PID=$!
  cleanup_smoke() {
    kill "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
    rm -rf "$SMOKE_HOME"
  }
  trap cleanup_smoke EXIT
  SMOKE_OK=0
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:${SMOKE_PORT}/health" 2>/dev/null | "$PYBIN" -c \
      'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("status") == "ok" else 1)' \
      >/dev/null 2>&1; then
      SMOKE_OK=1
      break
    fi
    sleep 0.5
  done
  if [[ "$SMOKE_OK" != "1" ]]; then
    echo "ERROR: embedded engine /health smoke failed" >&2
    tail -80 "$SMOKE_LOG" >&2 || true
    exit 1
  fi
  echo "    embedded engine health ok (port ${SMOKE_PORT})"
  CORS_HEADERS="$(
    curl -si -X OPTIONS "http://127.0.0.1:${SMOKE_PORT}/jobs" \
      -H 'Origin: http://tauri.localhost' \
      -H 'Access-Control-Request-Method: POST' \
      -H 'Access-Control-Request-Headers: content-type'
  )"
  printf '%s\n' "$CORS_HEADERS" | tr -d '\r' |
    grep -Eqi '^access-control-allow-origin: http://tauri\.localhost$' || {
      echo "ERROR: embedded engine rejects Tauri 2 CORS origin" >&2
      exit 1
    }
  echo "    embedded engine Tauri CORS ok"
  cleanup_smoke
  trap - EXIT
  if rg --files "$APP_SRC/Contents/Resources/runtime/studio" | rg -q '/__pycache__/'; then
    echo "ERROR: embedded health smoke mutated App with __pycache__" >&2
    exit 1
  fi
  codesign --verify --deep --strict --verbose=2 "$APP_SRC"
else
  echo "==> Ad-hoc codesign (no embed)"
  codesign --force --deep --sign - "$APP_SRC" 2>/dev/null || true
fi

# Rebuild DMG from the (possibly fat) .app
DMG_OUT="${BUNDLE_DIR}/dmg/速影_${VERSION}_${ARCH}.dmg"
mkdir -p "${BUNDLE_DIR}/dmg"
rm -f "$DMG_OUT"
TMP_DMG="$(mktemp -d)/速影"
mkdir -p "$TMP_DMG"
ditto "$APP_SRC" "${TMP_DMG}/速影.app"
RELOCATED_PY="${TMP_DMG}/速影.app/Contents/Resources/runtime/python/bin/python3"
if [[ "${EMBED}" == "1" ]]; then
  "$RELOCATED_PY" -c 'import encodings,fastapi,uvicorn; print("    relocated python ok")' ||
    {
      echo "ERROR: embedded Python is not relocatable" >&2
      exit 1
    }
fi
ln -sf /Applications "${TMP_DMG}/Applications"
hdiutil create -volname "速影" -srcfolder "$TMP_DMG" -ov -format UDZO "$DMG_OUT" >/dev/null
rm -rf "$(dirname "$TMP_DMG")"
if [[ -n "$NOTARY_PROFILE" ]]; then
  echo "==> Notarizing DMG with profile ${NOTARY_PROFILE}"
  xcrun notarytool submit "$DMG_OUT" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_OUT"
  xcrun stapler validate "$DMG_OUT"
fi
DMG_SRC="$DMG_OUT"

echo "==> Assembling Desktop kit: ${DESKTOP_KIT}"
rm -rf "$DESKTOP_KIT"
mkdir -p "$DESKTOP_KIT"
ditto "$APP_SRC" "${DESKTOP_KIT}/速影.app"
cp "$DMG_SRC" "${DESKTOP_KIT}/速影-${VERSION}.dmg"

# Refresh standalone Desktop app for daily use
rm -rf "${HOME}/Desktop/速影.app"
ditto "$APP_SRC" "${HOME}/Desktop/速影.app"

if [[ "${EMBED}" == "1" && -f "${APP_SRC}/Contents/Resources/runtime/BUNDLE_LAYOUT.txt" ]]; then
  INSTALL_MODE="一体包"
  INSTALL_BODY=$(cat << EOF
速影 ${VERSION}（macOS ${ARCH}）· 一体包
打包日期：${STAMP}

【安装】
1. 打开「速影-${VERSION}.dmg」，将「速影.app」拖到「应用程序」。
2. 若提示无法打开：右键 → 打开 → 仍要打开。
3. 双击即可；日更引擎与创作引擎已打进 App，无需再拷 ~/Suying/montage-studio。

【首次运行】
1. 打开 App → 点「启动引擎」（应自动使用内嵌 Python）。
2. 本机安装 Ollama，并在「运维 → 本地 AI」拉取推荐模型（向量必选）。
3. 数据目录默认 ~/Suying/data；片库/成片请放外置盘或自选路径。

【不包含】
客户片库、成片、词池、API Key、Ollama 模型权重。

【校验】
见同目录 SHA256.txt
EOF
)
else
  INSTALL_MODE="壳+外置引擎"
  INSTALL_BODY=$(cat << EOF
速影 ${VERSION}（macOS ${ARCH}）
打包日期：${STAMP}

【安装】
1. 将「速影.app」拖到「应用程序」，或直接双击本目录内的 App。
2. 若提示无法打开：右键 → 打开 → 仍要打开。

【引擎依赖】
本包未嵌入运行时（EMBED=0）。需本机引擎：
- ~/Suying/montage-studio 或设置 SUYING_ROOT
- Python 需带 uvicorn（可用 SUYING_PYTHON）

【校验】
见同目录 SHA256.txt
EOF
)
fi

printf '%s\n' "$INSTALL_BODY" > "${DESKTOP_KIT}/安装说明.txt"

(
  cd "$DESKTOP_KIT"
  {
    echo "# SHA256 (${INSTALL_MODE})"
    shasum -a 256 "速影.app/Contents/MacOS/appsdesktop" 2>/dev/null || true
    shasum -a 256 "速影-${VERSION}.dmg"
    if [[ -f "速影.app/Contents/Resources/runtime/studio/engine/main.py" ]]; then
      shasum -a 256 "速影.app/Contents/Resources/runtime/studio/engine/main.py"
    fi
    if [[ -f "速影.app/Contents/Resources/runtime/creative/LICENSE" ]]; then
      shasum -a 256 "速影.app/Contents/Resources/runtime/creative/LICENSE"
    fi
  } > SHA256.txt
)

ZIP_PATH="${HOME}/Desktop/${OUT_NAME}.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$DESKTOP_KIT" "$ZIP_PATH"

echo "==> Done (${INSTALL_MODE})"
echo "    Kit:  ${DESKTOP_KIT}"
echo "    Zip:  ${ZIP_PATH}"
echo "    App:  ${HOME}/Desktop/速影.app"
du -sh "$DESKTOP_KIT" "$ZIP_PATH" "${HOME}/Desktop/速影.app" 2>/dev/null | sed 's/^/    /'
