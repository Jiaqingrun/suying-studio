#!/usr/bin/env bash
# Embed studio + creative + relocatable CPython into a built 速影.app (scheme A).
#
# Usage:
#   ./scripts/embed-app-runtime.sh /path/to/速影.app
#   OPENMONTAGE_ROOT=/path/to/openmontage ./scripts/embed-app-runtime.sh ~/Desktop/速影.app
#
# Result layout:
#   速影.app/Contents/Resources/runtime/
#     studio/     — day engine (Python)
#     creative/   — OpenMontage slim tree (AGPL)
#     python/     — python-build-standalone + pip deps
set -euo pipefail

STUDIO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENMONTAGE_ROOT="${OPENMONTAGE_ROOT:-$(cd "${STUDIO_ROOT}/../openmontage" 2>/dev/null && pwd || true)}"
APP="${1:-}"
APP_VERSION="$(node -p "require('${STUDIO_ROOT}/apps/desktop/package.json').version")"
SKIP_PYTHON="${SKIP_PYTHON:-0}"
PY_RELEASE="${PY_RELEASE:-20260325}"
PY_VERSION="${PY_VERSION:-3.12.13}"
CACHE_DIR="${STUDIO_ROOT}/packaging/cache"
ARCH="$(uname -m)"
# Default relocatable standalone CPython for private multi-machine installs.
# Set PREFER_STANDALONE=0 to fall back to a host-bound system venv when offline.
PREFER_STANDALONE="${PREFER_STANDALONE:-1}"
ALLOW_HOST_VENV_FALLBACK="${ALLOW_HOST_VENV_FALLBACK:-0}"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:--}"

if [[ -z "$APP" || ! -d "$APP" ]]; then
  echo "Usage: $0 /path/to/速影.app" >&2
  exit 1
fi
APP="$(cd "$APP" && pwd)"
if [[ ! -d "${APP}/Contents/MacOS" ]]; then
  echo "ERROR: not a macOS .app bundle: ${APP}" >&2
  exit 1
fi
if [[ -z "${OPENMONTAGE_ROOT}" || ! -d "${OPENMONTAGE_ROOT}/tools" ]]; then
  echo "ERROR: 找不到 openmontage（设置 OPENMONTAGE_ROOT）" >&2
  exit 1
fi

case "$ARCH" in
  arm64) PY_TRIPLE="aarch64-apple-darwin" ;;
  x86_64) PY_TRIPLE="x86_64-apple-darwin" ;;
  *)
    echo "ERROR: unsupported arch ${ARCH}" >&2
    exit 1
    ;;
esac

RESOURCES="${APP}/Contents/Resources"
RUNTIME="${RESOURCES}/runtime"
STUDIO_DST="${RUNTIME}/studio"
CREATIVE_DST="${RUNTIME}/creative"
PYTHON_DST="${RUNTIME}/python"

echo "==> Embed runtime → ${RUNTIME}"
mkdir -p "$RUNTIME"

# --- Studio ---
echo "    studio ← ${STUDIO_ROOT}"
mkdir -p "$STUDIO_DST"
rsync -a --delete --delete-excluded \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'node_modules/' \
  --exclude 'src-tauri/target/' \
  --exclude 'apps/desktop/node_modules/' \
  --exclude 'apps/desktop/dist/' \
  --exclude 'apps/desktop/src-tauri/' \
  --exclude 'configs/customers/' \
  --exclude 'tools/chrome-for-testing/' \
  --exclude '**/fixtures/' \
  --exclude '**/bootstrap_shifeng.py' \
  --exclude '**/convert_shifeng_keyword_pack.py' \
  --include '**/publish_trail.py' \
  --include '**/publish_assets.py' \
  --exclude '**/publish_*.py' \
  --exclude '**/safari_*.py' \
  --include '**/chrome_runtime.py' \
  --exclude '**/chrome_*.py' \
  --exclude '**/xhs_*.py' \
  --exclude '**/suying_*_tick.py' \
  --exclude '**/suying_batch10_qa_loop.py' \
  --exclude '**/smoke_*.py' \
  --exclude '**/qa_*.py' \
  --exclude '**/quality_sample.py' \
  --exclude '**/purge_blur_catalog.py' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'packaging/cache/' \
  "${STUDIO_ROOT}/engine" \
  "${STUDIO_ROOT}/scripts" \
  "${STUDIO_ROOT}/packaging" \
  "${STUDIO_ROOT}/requirements.txt" \
  "${STUDIO_ROOT}/pyproject.toml" \
  "${STUDIO_DST}/"

chmod +x "${STUDIO_DST}/scripts/"*.sh 2>/dev/null || true
mkdir -p "${STUDIO_DST}/configs"
rsync -a --delete \
  --exclude '.DS_Store' \
  "${STUDIO_ROOT}/configs/samples/" "${STUDIO_DST}/configs/samples/" 2>/dev/null || true
if [[ -d "${STUDIO_ROOT}/configs/industry" ]]; then
  rsync -a "${STUDIO_ROOT}/configs/industry/" "${STUDIO_DST}/configs/industry/"
fi
if [[ -d "${STUDIO_ROOT}/configs/seeds" ]]; then
  rsync -a "${STUDIO_ROOT}/configs/seeds/" "${STUDIO_DST}/configs/seeds/"
fi
mkdir -p "${STUDIO_DST}/docs"
for f in INSTALL.md CUSTOMER_INSTALL.md REMOTE_DEPLOY.md DEVELOPMENT_STANDARDS.md AUTO_INGEST.md SEMANTIC_PIPELINE.md; do
  if [[ -f "${STUDIO_ROOT}/docs/${f}" ]]; then
    cp "${STUDIO_ROOT}/docs/${f}" "${STUDIO_DST}/docs/"
  fi
done

# --- Creative (OpenMontage) ---
echo "    creative ← ${OPENMONTAGE_ROOT}"
mkdir -p "$CREATIVE_DST"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'node_modules/' \
  --exclude '**/node_modules/' \
  --exclude 'projects/' \
  --exclude 'output/' \
  --exclude 'pipeline/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'backlot/' \
  --exclude 'ink-theater/' \
  --exclude 'tests/' \
  --exclude '.agents/' \
  --exclude '.claude/' \
  --exclude '.cursor/' \
  --exclude 'AGENTS.md' \
  --exclude 'AGENT_GUIDE.md' \
  --exclude 'CLAUDE.md' \
  --exclude 'CODEX.md' \
  --exclude 'COPILOT.md' \
  --exclude 'CURSOR.md' \
  --exclude 'PROMPT_GALLERY.md' \
  --exclude 'diagram.png' \
  --exclude 'tutorials/' \
  "${OPENMONTAGE_ROOT}/" "${CREATIVE_DST}/"
mkdir -p "${CREATIVE_DST}/projects"
cat > "${CREATIVE_DST}/PRODUCT_NOTE.txt" << 'NOTE'
本目录为速影内置「创作引擎」运行时（基于开源 OpenMontage，AGPL-3.0）。
对外产品名称仅为「速影」。请保留本目录内 LICENSE 与版权声明。
客户项目产出请放在外置盘或 ~/Suying/projects，不要写回 App bundle。
NOTE

# --- Relocatable CPython ---
if [[ "${SKIP_PYTHON}" != "1" ]]; then
  PY_ASSET="cpython-${PY_VERSION}+${PY_RELEASE}-${PY_TRIPLE}-install_only_stripped.tar.gz"
  PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_ASSET}"
  # Optional mirror (set PY_MIRROR_PREFIX to a CDN that mirrors GitHub releases)
  if [[ -n "${PY_MIRROR_PREFIX:-}" ]]; then
    PY_URL="${PY_MIRROR_PREFIX%/}/${PY_RELEASE}/${PY_ASSET}"
  fi
  mkdir -p "$CACHE_DIR"
  PY_TGZ="${CACHE_DIR}/${PY_ASSET}"
  DOWNLOAD_OK=0
  if [[ "${PREFER_STANDALONE}" == "1" ]]; then
    if [[ -f "$PY_TGZ" && -s "$PY_TGZ" ]]; then
      echo "    cache hit ${PY_ASSET}"
      DOWNLOAD_OK=1
    else
      echo "    download ${PY_ASSET}"
      rm -f "${PY_TGZ}.partial"
      if curl -fL --http1.1 --retry 2 --retry-delay 2 --retry-all-errors \
        --connect-timeout 15 --max-time 180 \
        -o "${PY_TGZ}.partial" "$PY_URL"; then
        mv "${PY_TGZ}.partial" "$PY_TGZ"
        DOWNLOAD_OK=1
      else
        echo "    WARN: standalone CPython download failed"
        rm -f "${PY_TGZ}.partial"
      fi
    fi
  else
    echo "    PREFER_STANDALONE=0 — use system venv --copies (set PREFER_STANDALONE=1 for relocatable CPython)"
  fi

  echo "    python → ${PYTHON_DST}"
  rm -rf "$PYTHON_DST"

  if [[ "$DOWNLOAD_OK" == "1" ]]; then
    mkdir -p "${RUNTIME}/.py-extract"
    tar -xzf "$PY_TGZ" -C "${RUNTIME}/.py-extract"
    if [[ -d "${RUNTIME}/.py-extract/python" ]]; then
      mv "${RUNTIME}/.py-extract/python" "$PYTHON_DST"
    else
      top="$(find "${RUNTIME}/.py-extract" -mindepth 1 -maxdepth 1 -type d | head -1)"
      mv "$top" "$PYTHON_DST"
    fi
    rm -rf "${RUNTIME}/.py-extract"
  elif [[ "$ALLOW_HOST_VENV_FALLBACK" == "1" ]]; then
    pick_sys_py() {
      if [[ -n "${SUYING_PYTHON:-${MONTAGE_PYTHON:-}}" ]]; then
        echo "${SUYING_PYTHON:-$MONTAGE_PYTHON}"
        return
      fi
      for c in python3.12 python3.11 python3; do
        if command -v "$c" >/dev/null 2>&1; then
          echo "$c"
          return
        fi
      done
      return 1
    }
    if ! SYS_PY="$(pick_sys_py)"; then
      echo "ERROR: no system Python for fallback venv" >&2
      exit 1
    fi
    echo "    fallback venv from ${SYS_PY}"
    "$SYS_PY" -m venv --copies "$PYTHON_DST"
  else
    echo "ERROR: standalone CPython is required for cross-machine delivery." >&2
    echo "       Fix download/cache, or explicitly set PREFER_STANDALONE=0 ALLOW_HOST_VENV_FALLBACK=1 for local-only debugging." >&2
    exit 1
  fi

  PYBIN="${PYTHON_DST}/bin/python3"
  if [[ ! -x "$PYBIN" ]]; then
    echo "ERROR: embedded python missing at ${PYBIN}" >&2
    exit 1
  fi
  echo "    pip install -r requirements.txt"
  "$PYBIN" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$PYBIN" -m pip install -r "${STUDIO_DST}/requirements.txt"
  "$PYBIN" -c "import uvicorn, fastapi; print('    deps ok', uvicorn.__version__)"
  if [[ "${PREFER_STANDALONE}" == "1" && -f "${PYTHON_DST}/pyvenv.cfg" ]]; then
    echo "ERROR: production runtime unexpectedly contains host-bound pyvenv.cfg" >&2
    exit 1
  fi
else
  echo "    SKIP_PYTHON=1 — leave existing runtime/python if any"
fi

cat > "${RUNTIME}/BUNDLE_LAYOUT.txt" << EOF
速影一体包布局（scheme A）
studio:   Contents/Resources/runtime/studio
creative: Contents/Resources/runtime/creative
python:   Contents/Resources/runtime/python
data:     ~/Suying/data（不在 App 内）
Ollama:   本机安装（不打进 App）
arch:     ${ARCH}
built:    $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
printf '%s\n' "${APP_VERSION}" > "${RUNTIME}/BUNDLE_VERSION"

# Sign after mutating the bundle. Distribution builds must pass a Developer ID
# identity; local builds intentionally default to ad-hoc.
echo "==> codesign (${CODESIGN_IDENTITY})"
if [[ "$CODESIGN_IDENTITY" == "-" ]]; then
  codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP"
else
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
fi
TEAM_ID="$(
  codesign -dv --verbose=4 "$APP" 2>&1 |
    awk -F= '/^TeamIdentifier=/{print $2; exit}'
)"
[[ "$TEAM_ID" == "not set" ]] && TEAM_ID=""
printf '%s\n' "$TEAM_ID" > "${RUNTIME}/BUNDLE_TEAM_ID"
# BUNDLE_TEAM_ID was added after the first signature, so seal once more.
if [[ "$CODESIGN_IDENTITY" == "-" ]]; then
  codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP"
else
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
fi

echo "==> Done"
du -sh "$APP" "$STUDIO_DST" "$CREATIVE_DST" "$PYTHON_DST" 2>/dev/null | sed 's/^/    /'
