#!/usr/bin/env bash
# Build 速影 product kit: self-contained App (scheme A) + optional docs.
# Excludes customer materials, secrets, build caches, and customer project outputs.
#
# Usage:
#   ./scripts/package-product.sh
#   BUILD_APP=1 ./scripts/package-product.sh          # tauri build + embed
#   EMBED=0 BUILD_APP=1 ./scripts/package-product.sh  # shell only (legacy)
set -euo pipefail

STUDIO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP_DIR="${STUDIO_ROOT}/apps/desktop"
VERSION="$(node -p "require('${DESKTOP_DIR}/package.json').version")"
ARCH="$(uname -m)"
STAMP="$(date +%Y%m%d)"
RUNTIME_FLAVOR="${RUNTIME_FLAVOR:-clone}"
OUT_NAME="速影-${VERSION}-product-macos-${ARCH}-${RUNTIME_FLAVOR}"
# 交付物默认暂存到 ~/Suying/releases（禁止再写桌面；SUYING_RELEASE_ROOT 可覆盖）。
# 正式入口 release-to-t2s.sh 推送极空间并覆盖安装后会清空本机暂存，不堆历史包。
RELEASE_ROOT="${SUYING_RELEASE_ROOT:-${HOME}/Suying/releases}"
mkdir -p "$RELEASE_ROOT"
RELEASE_KIT="${RELEASE_ROOT}/${OUT_NAME}"
BUILD_APP="${BUILD_APP:-0}"
EMBED="${EMBED:-1}"
MODEL_BUNDLE_ROOT="${MODEL_BUNDLE_ROOT:-}"
APP_NAME="速影 Studio"
APP_BUNDLE="${APP_NAME}.app"
[[ "$RUNTIME_FLAVOR" == "core" || "$RUNTIME_FLAVOR" == "clone" ]] || {
  echo "ERROR: RUNTIME_FLAVOR 仅支持 core 或 clone" >&2
  exit 1
}
export RUNTIME_FLAVOR

echo "==> ${APP_NAME} 产品套件 ${VERSION} (${ARCH}) [BUILD_APP=${BUILD_APP} EMBED=${EMBED} flavor=${RUNTIME_FLAVOR}]"
echo "    studio:      ${STUDIO_ROOT}"

if [[ "${BUILD_APP}" == "1" ]]; then
  echo "==> Building App (tauri + embed)"
  (cd "${DESKTOP_DIR}" && EMBED="${EMBED}" bash scripts/package-macos.sh)
fi

APP_CANDIDATES=(
  "${DESKTOP_DIR}/src-tauri/target/release/bundle/macos/${APP_BUNDLE}"
  "${RELEASE_ROOT}/速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}/${APP_BUNDLE}"
  # legacy 0.2.x
  "${DESKTOP_DIR}/src-tauri/target/release/bundle/macos/速影.app"
  "${RELEASE_ROOT}/速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}/速影.app"
)
APP_SRC=""
for c in "${APP_CANDIDATES[@]}"; do
  if [[ -d "$c" ]]; then
    if [[ "${EMBED}" == "1" ]]; then
      candidate_flavor="$(
        cat "${c}/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true
      )"
      [[ "$candidate_flavor" == "$RUNTIME_FLAVOR" ]] || continue
    fi
    APP_SRC="$c"
    break
  fi
done
if [[ -z "$APP_SRC" ]]; then
  echo "ERROR: 找不到 ${APP_BUNDLE}。请先 BUILD_APP=1 或运行 npm run package:mac" >&2
  exit 1
fi

# Ensure scheme A embed even if App came from a prior shell-only build
if [[ "${EMBED}" == "1" ]]; then
  RUNTIME="${APP_SRC}/Contents/Resources/runtime"
  CURRENT_SOURCE_SHA="$(bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" --print-fingerprint)"
  BUNDLED_VERSION="$(cat "${RUNTIME}/BUNDLE_VERSION" 2>/dev/null || true)"
  BUNDLED_ARCH="$(cat "${RUNTIME}/BUNDLE_ARCH" 2>/dev/null || true)"
  BUNDLED_FLAVOR="$(cat "${RUNTIME}/BUNDLE_FLAVOR" 2>/dev/null || true)"
  BUNDLED_SOURCE_SHA="$(cat "${RUNTIME}/RUNTIME_SOURCE_SHA256" 2>/dev/null || true)"
  if [[ -n "$BUNDLED_VERSION" && "$BUNDLED_VERSION" != "$VERSION" ]] ||
     [[ -n "$BUNDLED_ARCH" && "$BUNDLED_ARCH" != "$ARCH" ]] ||
     [[ "$BUNDLED_FLAVOR" != "$RUNTIME_FLAVOR" ]]; then
    if [[ "$BUILD_APP" != "1" ]]; then
      echo "ERROR: App runtime 版本/架构/flavor 不匹配（${BUNDLED_VERSION:-unknown}/${BUNDLED_ARCH:-unknown}/${BUNDLED_FLAVOR:-unknown}），必须 BUILD_APP=1 完整构建" >&2
      exit 1
    fi
  fi
  if [[ ! -f "${APP_SRC}/Contents/Resources/runtime/BUNDLE_LAYOUT.txt" ]]; then
    echo "==> App missing embed — running embed-app-runtime.sh"
    bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"
  elif [[ ! -x "${APP_SRC}/Contents/Resources/runtime/python/bin/python3" ]]; then
    echo "==> App missing embedded Python — re-running embed"
    bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"
  elif [[ "$BUNDLED_SOURCE_SHA" != "$CURRENT_SOURCE_SHA" ]]; then
    echo "==> App runtime source/requirements fingerprint stale — re-running embed"
    bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"
  else
    "${APP_SRC}/Contents/Resources/runtime/python/bin/python3" -m pip check
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${APP_SRC}/Contents/Resources/runtime/studio" \
      "${APP_SRC}/Contents/Resources/runtime/python/bin/python3" -c \
      'import engine; from pathlib import Path; \
p=Path(engine.__file__).resolve().parent / "security" / "trusted_release_keys.json"; \
assert p.is_file(), f"missing {p}"; \
import encodings,fastapi,uvicorn,edge_tts,numpy,engine.main'
    echo "==> App runtime fingerprint, imports and trusted keys verified"
  fi
fi

echo "==> Assembling kit → ${RELEASE_KIT}"
if [[ -e "$RELEASE_KIT" ]]; then
  chmod -R u+w "$RELEASE_KIT" 2>/dev/null || true
  rm -rf "$RELEASE_KIT"
fi
if [[ -e "$RELEASE_KIT" ]]; then
  echo "ERROR: 无法清空旧套件目录: ${RELEASE_KIT}" >&2
  exit 1
fi
mkdir -p "$RELEASE_KIT"

ditto "$APP_SRC" "${RELEASE_KIT}/${APP_BUNDLE}"
echo "==> Building offline bootstrap"
(cd "${DESKTOP_DIR}/src-tauri" && cargo build --release --offline --bin suying-bootstrap)
BOOTSTRAP_BIN="${DESKTOP_DIR}/src-tauri/target/release/suying-bootstrap"
codesign --force --sign - "$BOOTSTRAP_BIN"
mkdir -p "${RELEASE_KIT}/bootstrap"
cp "$BOOTSTRAP_BIN" "${RELEASE_KIT}/bootstrap/suying-bootstrap-${ARCH}"
chmod 755 "${RELEASE_KIT}/bootstrap/suying-bootstrap-${ARCH}"

# 人工安装 DMG 独立于产品/远程 ZIP；只认当前版本与架构的精确路径。
MANUAL_DMG="${RELEASE_ROOT}/速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}.dmg"
BUILT_DMG="${DESKTOP_DIR}/src-tauri/target/release/bundle/dmg/速影_${VERSION}_${ARCH}_${RUNTIME_FLAVOR}.dmg"
if [[ ! -f "$MANUAL_DMG" && -f "$BUILT_DMG" ]]; then
  MANUAL_DMG="$BUILT_DMG"
fi

# Product-facing install guide
if [[ -f "${STUDIO_ROOT}/docs/CUSTOMER_INSTALL.md" ]]; then
  cp "${STUDIO_ROOT}/docs/CUSTOMER_INSTALL.md" "${RELEASE_KIT}/安装说明.md"
fi
cp "${STUDIO_ROOT}/scripts/install-sync-service.sh" "${RELEASE_KIT}/install-sync-service.sh"
cp "${STUDIO_ROOT}/scripts/remote-install.sh" "${RELEASE_KIT}/remote-install.sh"
cp "${STUDIO_ROOT}/scripts/install-ollama-agent.sh" "${RELEASE_KIT}/install-ollama-agent.sh"
cp "${STUDIO_ROOT}/scripts/install-ollama-atomic.sh" "${RELEASE_KIT}/install-ollama-atomic.sh"
cp "${STUDIO_ROOT}/scripts/ollama_model_bundle.py" "${RELEASE_KIT}/ollama_model_bundle.py"
chmod +x "${RELEASE_KIT}/install-sync-service.sh" "${RELEASE_KIT}/remote-install.sh" \
  "${RELEASE_KIT}/install-ollama-agent.sh" "${RELEASE_KIT}/install-ollama-atomic.sh" \
  "${RELEASE_KIT}/ollama_model_bundle.py"

if [[ -n "$MODEL_BUNDLE_ROOT" ]]; then
  [[ -d "$MODEL_BUNDLE_ROOT" ]] || { echo "ERROR: MODEL_BUNDLE_ROOT 不存在: $MODEL_BUNDLE_ROOT" >&2; exit 1; }
  echo "==> Embedding verified offline model bundles"
  mkdir -p "${RELEASE_KIT}/offline-models"
  for profile in lite standard pro max; do
    [[ -f "${MODEL_BUNDLE_ROOT}/${profile}/bundle.json" ]] || continue
    python3 "${STUDIO_ROOT}/scripts/ollama_model_bundle.py" verify \
      --bundle "${MODEL_BUNDLE_ROOT}/${profile}" --expected-profile "$profile" --expected-arch "$ARCH" >/dev/null
    rsync -a "${MODEL_BUNDLE_ROOT}/${profile}/" "${RELEASE_KIT}/offline-models/${profile}/"
  done
fi

cat > "${RELEASE_KIT}/版本信息.txt" << EOF
速影 Studio ${VERSION}
打包日期：${STAMP}
架构：${ARCH}
形态：一体包（App 内含 Studio 引擎 + Python）
运行时 flavor：${RUNTIME_FLAVOR}（core=Edge；clone=Edge+F5）
App 正式名：${APP_NAME}（${APP_BUNDLE}）
内含：可选客户配置种子（仅词池模板/品牌样式/生产规则）
不含：客户实际片库/成片、本机密钥与数据库、账号令牌
Ollama 模型：仅当运维显式设置 MODEL_BUNDLE_ROOT 时附带已校验离线套件
人工安装 DMG：独立交付，不进入产品 ZIP
EOF

cat > "${RELEASE_KIT}/安装到-应用程序.sh" << EOS
#!/usr/bin/env bash
set -euo pipefail
HERE="\$(cd "\$(dirname "\$0")" && pwd)"
APP_BUNDLE="${APP_BUNDLE}"
if [[ ! -d "\${HERE}/\${APP_BUNDLE}" ]]; then
  echo "缺少 \${APP_BUNDLE}" >&2
  exit 1
fi
# 停旧版 App / 引擎，避免覆盖后旧进程仍占 8766 或 UI 不再拉起引擎
killall appsdesktop 2>/dev/null || true
for pid in \$(lsof -t -nP -iTCP:8766 -sTCP:LISTEN 2>/dev/null || true); do
  cmd="\$(ps -p "\$pid" -o command= 2>/dev/null || true)"
  if [[ "\$cmd" == *"engine.main"* || "\$cmd" == *".app/"* ]]; then
    kill "\$pid" 2>/dev/null || true
  fi
done
sleep 1
TARGET="/Applications/\${APP_BUNDLE}"
# ditto 合并会残留旧 flavor 文件，导致 runtime 完整性校验失败 → 引擎无法启动
rm -rf "\$TARGET"
ditto "\${HERE}/\${APP_BUNDLE}" "\$TARGET"
# 清理旧名快捷方式，避免双图标
rm -rf "\${HOME}/Desktop/速影.app" "/Applications/速影.app" 2>/dev/null || true
# 客户机可用桌面快捷方式；运维本机发布路径不再默认写桌面
if [[ "\${SUYING_INSTALL_DESKTOP_SHORTCUT:-0}" == "1" ]]; then
  ditto "\${HERE}/\${APP_BUNDLE}" "\${HOME}/Desktop/\${APP_BUNDLE}"
fi
APP="/Applications/\${APP_BUNDLE}"
[[ -d "\$APP" ]] || APP="\${HERE}/\${APP_BUNDLE}"
LICENSE="\${HOME}/Suying/runtime/security/license.suying-license"
if [[ ! -f "\$LICENSE" ]]; then
  PY="\${APP}/Contents/Resources/runtime/python/bin/python3"
  STUDIO="\${APP}/Contents/Resources/runtime/studio"
  mkdir -p "\${HOME}/Suying/ops"
  PYTHONPATH="\$STUDIO" "\$PY" "\${STUDIO}/scripts/device_license.py" request \\
    --output "\${HOME}/Suying/ops/速影-许可请求.json"
fi
echo "已安装："
echo "  /Applications/\${APP_BUNDLE}（若有权限）"
echo "  \${APP}"
echo ""
# F5 Runtime Kit 位于 ~/Suying/runtime/f5_site_packages —— 本脚本只替换 App，禁止删除该目录
if [[ -d "\${HOME}/Suying/runtime/f5_site_packages" ]]; then
  echo "本机 F5 Runtime Kit 仍在：~/Suying/runtime/f5_site_packages（未被覆盖安装删除）"
fi
if [[ "\${VERIFY_F5_OVERLAY:-0}" == "1" ]]; then
  # 可选：由运维在 PATH 中提供仓库脚本
  if command -v verify_f5_overlay_post_install.sh >/dev/null 2>&1; then
    verify_f5_overlay_post_install.sh --require-overlay-source || true
  elif [[ -f "\${HOME}/QR/dev/速影/scripts/verify_f5_overlay_post_install.sh" ]]; then
    bash "\${HOME}/QR/dev/速影/scripts/verify_f5_overlay_post_install.sh" --require-overlay-source || true
  fi
fi
if [[ -f "\$LICENSE" ]]; then
  echo "下一步：打开速影 Studio；并从签名离线仓安装 Ollama / FFmpeg / 模型。"
else
  echo "PARTIAL: LICENSE_REQUIRED"
  echo "请将 ~/Suying/ops/速影-许可请求.json 交给运维签发，导入后再启动引擎。"
fi
EOS
chmod +x "${RELEASE_KIT}/安装到-应用程序.sh"

(
  cd "$RELEASE_KIT"
  {
    echo "# SHA256"
    shasum -a 256 "${APP_BUNDLE}/Contents/MacOS/appsdesktop" 2>/dev/null || true
    shasum -a 256 "${APP_BUNDLE}/Contents/Resources/runtime/studio/engine/main.py" 2>/dev/null || true
    shasum -a 256 "${APP_BUNDLE}/Contents/Resources/runtime/python/bin/python3" 2>/dev/null || true
    shasum -a 256 "bootstrap/suying-bootstrap-${ARCH}"
  } > SHA256.txt
)

ZIP_PATH="${RELEASE_ROOT}/${OUT_NAME}.zip"
rm -f "$ZIP_PATH"
echo "==> Zipping ${ZIP_PATH}"
ditto -c -k --sequesterRsrc --keepParent "$RELEASE_KIT" "$ZIP_PATH"

# Publishing is deliberately separate: use scripts/release-to-t2s.sh
# (default PUBLISH=1 API; see docs/T2S_UPDATE_PUSH.md).
if [[ "${PUBLISH_CARRIER:-0}" == "1" ]]; then
  echo "ERROR: PUBLISH_CARRIER 已禁用；请使用 scripts/release-to-t2s.sh / publish_update_repo.py" >&2
  exit 1
fi

# 打包后清 Tauri 中间 DMG/bundle，避免 target/release/bundle 叠到数十 GB（产物已在 RELEASE_ROOT）。
# 设 KEEP_BUNDLE_ARTIFACTS=1 可保留；deps/增量编译缓存仍在 target/release/deps。
if [[ "${KEEP_BUNDLE_ARTIFACTS:-0}" != "1" ]]; then
  BUNDLE_DIR="${DESKTOP_DIR}/src-tauri/target/release/bundle"
  if [[ -d "$BUNDLE_DIR" ]]; then
    echo "==> Pruning tauri bundle intermediates → ${BUNDLE_DIR}"
    rm -rf "${BUNDLE_DIR}/dmg" "${BUNDLE_DIR}/macos" 2>/dev/null || true
  fi
fi

echo "==> Done"
echo "    Kit: ${RELEASE_KIT}"
echo "    Zip: ${ZIP_PATH}"
if [[ -f "$MANUAL_DMG" ]]; then
  echo "    DMG (独立): ${MANUAL_DMG}"
fi
du -sh "$RELEASE_KIT" "$ZIP_PATH" 2>/dev/null | sed 's/^/    /'
