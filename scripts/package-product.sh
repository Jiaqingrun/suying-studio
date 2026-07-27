#!/usr/bin/env bash
# Build 速影 product kit: self-contained App (scheme A) + optional docs.
# Excludes customer materials, secrets, build caches, and customer project outputs.
#
# Usage:
#   ./scripts/package-product.sh
#   BUILD_APP=1 ./scripts/package-product.sh          # tauri build + embed
#   EMBED=0 BUILD_APP=1 ./scripts/package-product.sh  # shell only (legacy)
#   OPENMONTAGE_ROOT=/path/to/openmontage ./scripts/package-product.sh
set -euo pipefail

STUDIO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENMONTAGE_ROOT="${OPENMONTAGE_ROOT:-$(cd "${STUDIO_ROOT}/../openmontage" 2>/dev/null && pwd || true)}"
DESKTOP_DIR="${STUDIO_ROOT}/apps/desktop"
VERSION="$(node -p "require('${DESKTOP_DIR}/package.json').version")"
ARCH="$(uname -m)"
STAMP="$(date +%Y%m%d)"
OUT_NAME="速影-${VERSION}-product-macos-${ARCH}"
DESKTOP_KIT="${HOME}/Desktop/${OUT_NAME}"
BUILD_APP="${BUILD_APP:-0}"
EMBED="${EMBED:-1}"

echo "==> 速影产品套件 ${VERSION} (${ARCH}) [BUILD_APP=${BUILD_APP} EMBED=${EMBED}]"
echo "    studio:      ${STUDIO_ROOT}"
echo "    openmontage: ${OPENMONTAGE_ROOT:-<missing>}"

if [[ -z "${OPENMONTAGE_ROOT}" || ! -d "${OPENMONTAGE_ROOT}/tools" ]]; then
  echo "ERROR: 找不到 openmontage（设置 OPENMONTAGE_ROOT）" >&2
  exit 1
fi

if [[ "${BUILD_APP}" == "1" ]]; then
  echo "==> Building App (tauri + embed)"
  (cd "${DESKTOP_DIR}" && EMBED="${EMBED}" bash scripts/package-macos.sh)
fi

APP_CANDIDATES=(
  "${HOME}/Desktop/速影.app"
  "${DESKTOP_DIR}/src-tauri/target/release/bundle/macos/速影.app"
  "${HOME}/Desktop/速影-${VERSION}-macos-${ARCH}/速影.app"
)
APP_SRC=""
for c in "${APP_CANDIDATES[@]}"; do
  if [[ -d "$c" ]]; then
    APP_SRC="$c"
    break
  fi
done
if [[ -z "$APP_SRC" ]]; then
  echo "ERROR: 找不到 速影.app。请先 BUILD_APP=1 或运行 npm run package:mac" >&2
  exit 1
fi

# Ensure scheme A embed even if App came from a prior shell-only build
if [[ "${EMBED}" == "1" ]]; then
  if [[ ! -f "${APP_SRC}/Contents/Resources/runtime/BUNDLE_LAYOUT.txt" ]]; then
    echo "==> App missing embed — running embed-app-runtime.sh"
    bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"
  elif [[ ! -x "${APP_SRC}/Contents/Resources/runtime/python/bin/python3" ]]; then
    echo "==> App missing embedded Python — re-running embed"
    bash "${STUDIO_ROOT}/scripts/embed-app-runtime.sh" "$APP_SRC"
  else
    echo "==> App already embedded"
  fi
fi

echo "==> Assembling kit → ${DESKTOP_KIT}"
rm -rf "$DESKTOP_KIT"
mkdir -p "$DESKTOP_KIT"

ditto "$APP_SRC" "${DESKTOP_KIT}/速影.app"

DMG_SRC="$(ls -1 "${HOME}/Desktop/速影-${VERSION}-macos-${ARCH}/速影-"*.dmg 2>/dev/null | head -1 || true)"
if [[ -z "${DMG_SRC}" ]]; then
  DMG_SRC="$(ls -1 "${DESKTOP_DIR}/src-tauri/target/release/bundle/dmg/"*.dmg 2>/dev/null | head -1 || true)"
fi
if [[ -n "${DMG_SRC}" && -f "${DMG_SRC}" ]]; then
  cp "${DMG_SRC}" "${DESKTOP_KIT}/速影-${VERSION}.dmg"
fi

# Product-facing install guide
if [[ -f "${STUDIO_ROOT}/docs/CUSTOMER_INSTALL.md" ]]; then
  cp "${STUDIO_ROOT}/docs/CUSTOMER_INSTALL.md" "${DESKTOP_KIT}/安装说明.md"
fi
cp "${STUDIO_ROOT}/scripts/install-sync-service.sh" "${DESKTOP_KIT}/install-sync-service.sh"
cp "${STUDIO_ROOT}/scripts/remote-install.sh" "${DESKTOP_KIT}/remote-install.sh"
chmod +x "${DESKTOP_KIT}/install-sync-service.sh" "${DESKTOP_KIT}/remote-install.sh"

cat > "${DESKTOP_KIT}/版本信息.txt" << EOF
速影 ${VERSION}
打包日期：${STAMP}
架构：${ARCH}
形态：一体包（App 内含 studio + creative + Python）
内含：可选客户配置种子（仅词池模板/品牌样式/生产规则）
不含：客户实际片库/成片、本机密钥与数据库、账号令牌、Ollama 模型
EOF

cat > "${DESKTOP_KIT}/安装到-应用程序.sh" << 'EOS'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ ! -d "${HERE}/速影.app" ]]; then
  echo "缺少 速影.app" >&2
  exit 1
fi
ditto "${HERE}/速影.app" "${HOME}/Desktop/速影.app"
ditto "${HERE}/速影.app" "/Applications/速影.app" 2>/dev/null || true
echo "已安装："
echo "  ${HOME}/Desktop/速影.app"
echo "  /Applications/速影.app（若有权限）"
echo ""
echo "下一步：打开速影 → 启动引擎；并安装 Ollama + 向量模型（运维页）。"
EOS
chmod +x "${DESKTOP_KIT}/安装到-应用程序.sh"

(
  cd "$DESKTOP_KIT"
  {
    echo "# SHA256"
    shasum -a 256 "速影.app/Contents/MacOS/appsdesktop" 2>/dev/null || true
    [[ -f "速影-${VERSION}.dmg" ]] && shasum -a 256 "速影-${VERSION}.dmg"
    shasum -a 256 "速影.app/Contents/Resources/runtime/studio/engine/main.py" 2>/dev/null || true
    shasum -a 256 "速影.app/Contents/Resources/runtime/creative/LICENSE" 2>/dev/null || true
    shasum -a 256 "速影.app/Contents/Resources/runtime/python/bin/python3" 2>/dev/null || true
  } > SHA256.txt
)

ZIP_PATH="${HOME}/Desktop/${OUT_NAME}.zip"
rm -f "$ZIP_PATH"
echo "==> Zipping ${ZIP_PATH}"
ditto -c -k --sequesterRsrc --keepParent "$DESKTOP_KIT" "$ZIP_PATH"

# Optionally publish into T2S carrier layout (install/update mirror only — no media)
CARRIER_ROOT="${CARRIER_ROOT:-${HOME}/Suying/carrier}"
if [[ "${PUBLISH_CARRIER:-1}" == "1" ]]; then
  echo "==> Publishing to carrier ${CARRIER_ROOT}"
  mkdir -p "${CARRIER_ROOT}/app" "${CARRIER_ROOT}/seed/industry" "${CARRIER_ROOT}/seed/templates" "${CARRIER_ROOT}/seed/customers" "${CARRIER_ROOT}/backups"
  PKG_NAME=""
  if [[ -f "${DESKTOP_KIT}/速影-${VERSION}.dmg" ]]; then
    PKG_NAME="速影-${VERSION}.dmg"
    cp -f "${DESKTOP_KIT}/速影-${VERSION}.dmg" "${CARRIER_ROOT}/app/${PKG_NAME}"
  else
    PKG_NAME="速影-${VERSION}-app.zip"
    ditto -c -k --sequesterRsrc --keepParent "${DESKTOP_KIT}/速影.app" "${CARRIER_ROOT}/app/${PKG_NAME}"
  fi
  SHA="$(shasum -a 256 "${CARRIER_ROOT}/app/${PKG_NAME}" | awk '{print $1}')"
  CARRIER_ROOT="$CARRIER_ROOT" VERSION="$VERSION" PKG_NAME="$PKG_NAME" SHA="$SHA" STAMP="$STAMP" python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["CARRIER_ROOT"]) / "app"
man = {
    "version": os.environ["VERSION"],
    "package": os.environ["PKG_NAME"],
    "sha256": os.environ["SHA"],
    "force": False,
    "notes": f"product kit {os.environ['STAMP']}",
}
(root / "latest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", root / "latest.json")
PY
  if [[ -d "${STUDIO_ROOT}/configs/industry" ]]; then
    rsync -a --delete "${STUDIO_ROOT}/configs/industry/" "${CARRIER_ROOT}/seed/industry/" 2>/dev/null || \
      cp -R "${STUDIO_ROOT}/configs/industry/." "${CARRIER_ROOT}/seed/industry/" 2>/dev/null || true
  fi
  if [[ -d "${STUDIO_ROOT}/configs/seeds" ]]; then
    rsync -a --delete "${STUDIO_ROOT}/configs/seeds/" "${CARRIER_ROOT}/seed/customers/" 2>/dev/null || \
      cp -R "${STUDIO_ROOT}/configs/seeds/." "${CARRIER_ROOT}/seed/customers/" 2>/dev/null || true
  fi
  cat > "${CARRIER_ROOT}/seed/install-defaults.json" << EOF
{
  "version": 1,
  "vector_default_off": true,
  "media_sync_default_off": true,
  "carrier_only": true,
  "workspace_under": "~/Suying/customers/<name>",
  "notes": "T2S 仅同步本载体目录；片库留在客户本机。"
}
EOF
  echo "    Carrier app/: ${PKG_NAME} sha256=${SHA:0:12}…"
fi

echo "==> Done"
echo "    Kit: ${DESKTOP_KIT}"
echo "    Zip: ${ZIP_PATH}"
[[ "${PUBLISH_CARRIER:-1}" == "1" ]] && echo "    Carrier: ${CARRIER_ROOT}"
du -sh "$DESKTOP_KIT" "$ZIP_PATH" 2>/dev/null | sed 's/^/    /'
