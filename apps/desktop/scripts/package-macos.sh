#!/usr/bin/env bash
# Build a self-contained macOS「速影 Studio.app」(scheme A):
#   Tauri shell + embedded Studio engine + CPython.
# Ollama / large models stay on the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO_ROOT="$(cd "${ROOT}/../.." && pwd)"
cd "$ROOT"

VERSION="$(node -p "require('./package.json').version")"
ARCH="$(uname -m)"
STAMP="$(date +%Y%m%d)"
APP_NAME="速影 Studio"
APP_BUNDLE="${APP_NAME}.app"
RUNTIME_FLAVOR="${RUNTIME_FLAVOR:-clone}"
OUT_NAME="速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}"
RELEASE_ROOT="${SUYING_RELEASE_ROOT:-${HOME}/Suying/releases}"
mkdir -p "$RELEASE_ROOT"
RELEASE_KIT="${RELEASE_ROOT}/${OUT_NAME}"
BUNDLE_DIR="${ROOT}/src-tauri/target/release/bundle"
EMBED="${EMBED:-1}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
[[ "$RUNTIME_FLAVOR" == "core" || "$RUNTIME_FLAVOR" == "clone" ]] || {
  echo "ERROR: RUNTIME_FLAVOR 仅支持 core 或 clone" >&2
  exit 1
}
export RUNTIME_FLAVOR
export SUYING_RELEASE_ROOT="$RELEASE_ROOT"

echo "==> Building ${APP_NAME} ${VERSION} (${ARCH}) [embed=${EMBED} flavor=${RUNTIME_FLAVOR}]"
npm run tauri build

APP_SRC="${BUNDLE_DIR}/macos/${APP_BUNDLE}"
# Compat: older productName produced 速影.app
if [[ ! -d "$APP_SRC" && -d "${BUNDLE_DIR}/macos/速影.app" ]]; then
  APP_SRC="${BUNDLE_DIR}/macos/速影.app"
fi

if [[ ! -d "$APP_SRC" ]]; then
  echo "ERROR: missing ${BUNDLE_DIR}/macos/${APP_BUNDLE}" >&2
  exit 1
fi

if [[ "${EMBED}" == "1" ]]; then
  echo "==> Embedding Studio engine + Python into App"
  bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"

  echo "==> Smoke embedded engine /health"
  PYBIN="${APP_SRC}/Contents/Resources/runtime/python/bin/python3"
  STUDIO="${APP_SRC}/Contents/Resources/runtime/studio"
  SMOKE_HOME="$(mktemp -d)"
  BUILD_LICENSE="${HOME}/Suying/runtime/security/license.suying-license"
  BUILD_KEYCHAIN="${SUYING_KEYCHAIN_PATH:-}"
  if [[ ! -f "$BUILD_LICENSE" ]]; then
    echo "ERROR: 私下发布构建缺少本机运维许可证，无法执行受许可引擎冒烟" >&2
    rm -rf "$SMOKE_HOME"
    exit 1
  fi
  if [[ -z "$BUILD_KEYCHAIN" ]]; then
    BUILD_KEYCHAIN="$(
      /usr/bin/security default-keychain -d user |
        awk -F '"' 'NF >= 2 { print $2; exit }'
    )"
  fi
  if [[ "$BUILD_KEYCHAIN" != /* || ! -f "$BUILD_KEYCHAIN" ]]; then
    echo "ERROR: 无法定位当前用户默认钥匙串，拒绝启动隔离许可证冒烟" >&2
    rm -rf "$SMOKE_HOME"
    exit 1
  fi
  mkdir -p "${SMOKE_HOME}/Suying/runtime/security"
  cp "$BUILD_LICENSE" "${SMOKE_HOME}/Suying/runtime/security/license.suying-license"
  chmod 600 "${SMOKE_HOME}/Suying/runtime/security/license.suying-license"
  SMOKE_PORT="$("$PYBIN" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  SMOKE_LOG="${SMOKE_HOME}/engine.log"
  (
    cd "$STUDIO"
    exec env HOME="$SMOKE_HOME" SUYING_KEYCHAIN_PATH="$BUILD_KEYCHAIN" \
      PYTHONDONTWRITEBYTECODE=1 MONTAGE_PORT="$SMOKE_PORT" \
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
  # Fresh immutable runtime intentionally contains no pyc. First import may compile
  # the large engine graph in memory, so allow up to 60 seconds on slower Macs.
  for _ in $(seq 1 120); do
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

APP_KIB="$(du -sk "$APP_SRC" | awk '{print $1}')"
APP_MIB="$(( (APP_KIB + 1023) / 1024 ))"
if [[ "$RUNTIME_FLAVOR" == "core" ]]; then
  DEFAULT_MAX_APP_MIB=600
else
  DEFAULT_MAX_APP_MIB=2300
fi
MAX_APP_MIB="${SUYING_MAX_APP_MIB:-$DEFAULT_MAX_APP_MIB}"
[[ "$MAX_APP_MIB" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SUYING_MAX_APP_MIB 必须为正整数" >&2
  exit 1
}
echo "==> App size ${APP_MIB} MiB / budget ${MAX_APP_MIB} MiB (${RUNTIME_FLAVOR})"
if (( APP_MIB > MAX_APP_MIB )); then
  echo "ERROR: App 超过 ${RUNTIME_FLAVOR} flavor 体积预算，拒绝生成交付物" >&2
  exit 1
fi

# Rebuild DMG from the (possibly fat) .app
DMG_OUT="${BUNDLE_DIR}/dmg/速影_${VERSION}_${ARCH}_${RUNTIME_FLAVOR}.dmg"
mkdir -p "${BUNDLE_DIR}/dmg"
rm -f "$DMG_OUT"
TMP_DMG="$(mktemp -d)/速影"
mkdir -p "$TMP_DMG"
ditto "$APP_SRC" "${TMP_DMG}/${APP_BUNDLE}"
RELOCATED_PY="${TMP_DMG}/${APP_BUNDLE}/Contents/Resources/runtime/python/bin/python3"
if [[ "${EMBED}" == "1" ]]; then
  "$RELOCATED_PY" -c 'import encodings,fastapi,uvicorn,edge_tts,numpy; print("    relocated python ok")' ||
    {
      echo "ERROR: embedded Python is not relocatable" >&2
      exit 1
    }
fi
ln -sf /Applications "${TMP_DMG}/Applications"
hdiutil create -volname "速影 Studio" -srcfolder "$TMP_DMG" -ov -format UDZO "$DMG_OUT" >/dev/null
rm -rf "$(dirname "$TMP_DMG")"
if [[ -n "$NOTARY_PROFILE" ]]; then
  echo "==> Notarizing DMG with profile ${NOTARY_PROFILE}"
  xcrun notarytool submit "$DMG_OUT" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_OUT"
  xcrun stapler validate "$DMG_OUT"
fi
# 构建目录易堆历史 DMG（单包 ~0.2–3G）。正式真源在 T2S；本机 ~/Suying/releases 仅暂存且推后清空。
# 默认只留当前版本 build 产物，可用 SUYING_KEEP_BUILD_DMGS=N 多留最近若干个。
KEEP_BUILD_DMGS="${SUYING_KEEP_BUILD_DMGS:-1}"
if [[ "$KEEP_BUILD_DMGS" =~ ^[0-9]+$ ]] && (( KEEP_BUILD_DMGS >= 1 )); then
  pruned=0
  while IFS= read -r old_dmg; do
    [[ -n "$old_dmg" && -f "$old_dmg" ]] || continue
    rm -f -- "$old_dmg"
    pruned=$((pruned + 1))
  done < <(
    # mtime 新→旧；跳过前 KEEP 个，删其余
    find "${BUNDLE_DIR}/dmg" -maxdepth 1 -type f -name '*.dmg' -print0 2>/dev/null |
      xargs -0 ls -t 2>/dev/null |
      tail -n +"$((KEEP_BUILD_DMGS + 1))"
  )
  if (( pruned > 0 )); then
    echo "==> Pruned ${pruned} old build DMG(s) in ${BUNDLE_DIR}/dmg (keep ${KEEP_BUILD_DMGS})"
  fi
fi
DMG_SRC="$DMG_OUT"
MANUAL_DMG="${RELEASE_ROOT}/速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}.dmg"
rm -f "$MANUAL_DMG" "${MANUAL_DMG}.sha256"
cp "$DMG_SRC" "$MANUAL_DMG"
(
  cd "$(dirname "$MANUAL_DMG")"
  shasum -a 256 "$(basename "$MANUAL_DMG")" >"$(basename "$MANUAL_DMG").sha256"
)

echo "==> Assembling release kit: ${RELEASE_KIT}"
rm -rf "$RELEASE_KIT"
mkdir -p "$RELEASE_KIT"
ditto "$APP_SRC" "${RELEASE_KIT}/${APP_BUNDLE}"

# 运维本机不再把 App 拷到桌面；需要时可显式 SUYING_INSTALL_DESKTOP_SHORTCUT=1
if [[ "${SUYING_INSTALL_DESKTOP_SHORTCUT:-0}" == "1" ]]; then
  rm -rf "${HOME}/Desktop/${APP_BUNDLE}" "${HOME}/Desktop/速影.app"
  ditto "$APP_SRC" "${HOME}/Desktop/${APP_BUNDLE}"
fi

if [[ "${EMBED}" == "1" && -f "${APP_SRC}/Contents/Resources/runtime/BUNDLE_LAYOUT.txt" ]]; then
  INSTALL_MODE="一体包"
  INSTALL_BODY=$(cat << EOF
速影 Studio ${VERSION}（macOS ${ARCH}）· 一体包
打包日期：${STAMP}

【安装】
1. 打开独立的「速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}.dmg」，将「${APP_BUNDLE}」拖到「应用程序」。
2. 若提示无法打开：右键 → 打开 → 仍要打开。
3. 双击即可；日更引擎已打进 App，无需再拷 ~/Suying/montage-studio。

【首次运行】
1. 打开 App → 点「启动引擎」（应自动使用内嵌 Python）。
2. 本机安装 Ollama，并在「运维 → 本地 AI」拉取推荐模型（向量必选）。
3. 数据目录默认 ~/Suying/data；片库/成片默认 ~/Movies/速影工作区（可手改路径，外置盘可选）。

【不包含】
客户片库、成片、词池、API Key、Ollama 模型权重。

【校验】
DMG 校验见其旁边的「速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}.dmg.sha256」。
EOF
)
else
  INSTALL_MODE="壳+外置引擎"
  INSTALL_BODY=$(cat << EOF
速影 Studio ${VERSION}（macOS ${ARCH}）
打包日期：${STAMP}

【安装】
1. 将「${APP_BUNDLE}」拖到「应用程序」，或直接双击本目录内的 App。
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

printf '%s\n' "$INSTALL_BODY" > "${RELEASE_KIT}/安装说明.txt"

(
  cd "$RELEASE_KIT"
  {
    echo "# SHA256 (${INSTALL_MODE})"
    shasum -a 256 "${APP_BUNDLE}/Contents/MacOS/appsdesktop" 2>/dev/null || true
    if [[ -f "${APP_BUNDLE}/Contents/Resources/runtime/studio/engine/main.py" ]]; then
      shasum -a 256 "${APP_BUNDLE}/Contents/Resources/runtime/studio/engine/main.py"
    fi
  } > SHA256.txt
)

ZIP_PATH="${RELEASE_ROOT}/${OUT_NAME}.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$RELEASE_KIT" "$ZIP_PATH"

echo "==> Done (${INSTALL_MODE})"
echo "    Kit:  ${RELEASE_KIT}"
echo "    Zip:  ${ZIP_PATH}"
echo "    DMG (独立): ${MANUAL_DMG}"
echo "    App bundle (build): ${APP_SRC}"
du -sh "$RELEASE_KIT" "$ZIP_PATH" "$MANUAL_DMG" "$APP_SRC" 2>/dev/null | sed 's/^/    /'
