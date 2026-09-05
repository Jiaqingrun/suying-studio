#!/usr/bin/env bash
# Embed studio + relocatable CPython into a built 速影.app (scheme A).
#
# Usage:
#   ./scripts/embed-app-runtime.sh /path/to/速影.app
#
# Result layout:
#   速影.app/Contents/Resources/runtime/
#     studio/     — day engine (Python)
#     python/     — python-build-standalone + pip deps
set -euo pipefail

STUDIO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-}"
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
RUNTIME_FLAVOR="${RUNTIME_FLAVOR:-clone}"
case "$RUNTIME_FLAVOR" in
  core) EMBED_CLONE_TTS=0 ;;
  clone) EMBED_CLONE_TTS=1 ;;
  *)
    echo "ERROR: RUNTIME_FLAVOR 仅支持 core 或 clone" >&2
    exit 1
    ;;
esac

runtime_source_fingerprint() {
  local root="${1:-$STUDIO_ROOT}"
  python3 "${STUDIO_ROOT}/scripts/runtime_source_fingerprint.py" "$root"
}

if [[ "$APP" == "--print-fingerprint" ]]; then
  runtime_source_fingerprint
  exit 0
fi

APP_VERSION="$(node -p "require('${STUDIO_ROOT}/apps/desktop/package.json').version")"

if [[ -z "$APP" || ! -d "$APP" ]]; then
  echo "Usage: $0 /path/to/速影.app" >&2
  exit 1
fi
APP="$(cd "$APP" && pwd)"
if [[ ! -d "${APP}/Contents/MacOS" ]]; then
  echo "ERROR: not a macOS .app bundle: ${APP}" >&2
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
PYTHON_DST="${RUNTIME}/python"
if [[ "$SKIP_PYTHON" == "1" ]]; then
  EXISTING_FLAVOR="$(cat "${RUNTIME}/BUNDLE_FLAVOR" 2>/dev/null || true)"
  [[ "$EXISTING_FLAVOR" == "$RUNTIME_FLAVOR" ]] || {
    echo "ERROR: SKIP_PYTHON=1 不允许跨 flavor 复用 Python runtime" >&2
    exit 1
  }
fi

echo "==> Embed runtime → ${RUNTIME}"
mkdir -p "$RUNTIME"
# Creative/Remotion runtime was retired for commercial offline distribution.
rm -rf "${RUNTIME}/creative"

# --- Studio ---
echo "    studio ← ${STUDIO_ROOT}"
mkdir -p "$STUDIO_DST"
# 生产模块勿用 **/publish_*.py 排除；仅排除 scripts/ 运维脚本
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
  --exclude 'scripts/publish_*.py' \
  --exclude 'scripts/sign_update_release.py' \
  --exclude 'scripts/build_runtime_manifest.py' \
  --exclude 'scripts/build_offline_depot.py' \
  --exclude 'scripts/build_legal_component.py' \
  --exclude 'scripts/build_relocatable_macos_tool.py' \
  --exclude 'scripts/safari_*.py' \
  --exclude 'scripts/chrome_*.py' \
  --exclude 'scripts/xhs_*.py' \
  --exclude 'scripts/suying_*_tick.py' \
  --exclude 'scripts/suying_batch10_qa_loop.py' \
  --exclude 'scripts/smoke_*.py' \
  --exclude 'scripts/qa_*.py' \
  --exclude 'scripts/quality_sample.py' \
  --exclude 'scripts/purge_blur_catalog.py' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'packaging/cache/' \
  "${STUDIO_ROOT}/engine" \
  "${STUDIO_ROOT}/scripts" \
  "${STUDIO_ROOT}/packaging" \
  "${STUDIO_ROOT}/requirements.txt" \
  "${STUDIO_ROOT}/requirements-clone.txt" \
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
if [[ -d "${STUDIO_ROOT}/configs/voice_packs" ]]; then
  rsync -a --delete "${STUDIO_ROOT}/configs/voice_packs/" "${STUDIO_DST}/configs/voice_packs/"
fi
mkdir -p "${STUDIO_DST}/docs"
for f in INSTALL.md CUSTOMER_INSTALL.md REMOTE_DEPLOY.md; do
  if [[ -f "${STUDIO_ROOT}/docs/${f}" ]]; then
    cp "${STUDIO_ROOT}/docs/${f}" "${STUDIO_DST}/docs/"
  fi
done

# Packaged runtime must ship license verification keys (readiness / entitlement).
TRUSTED_KEYS="${STUDIO_DST}/engine/security/trusted_release_keys.json"
if [[ ! -f "$TRUSTED_KEYS" ]]; then
  echo "ERROR: 嵌入 studio 缺少 trusted_release_keys.json" >&2
  exit 1
fi

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
  # clone flavor carries F5; core omits it and will fail closed for clone-locked customers.
  if [[ "${EMBED_CLONE_TTS:-1}" == "1" ]]; then
    echo "    pip install -r requirements-clone.txt (F5-TTS)"
    if [[ -f "${STUDIO_DST}/requirements-clone.txt" ]]; then
      "$PYBIN" -m pip install -r "${STUDIO_DST}/requirements-clone.txt"
    else
      "$PYBIN" -m pip install "f5-tts>=1.1.0"
    fi
    PYTHONPATH="${STUDIO_DST}${PYTHONPATH:+:${PYTHONPATH}}" \
      "$PYBIN" -c "import f5_tts; print('    f5-tts ok')" \
      || { echo "ERROR: EMBED_CLONE_TTS=1 but f5-tts import failed" >&2; exit 1; }
    # Optional: bake HF hub weights into the bundle when ops provides a materialization dir.
    CLONE_HF_SRC="${SUYING_CLONE_HF_CACHE:-}"
    if [[ -n "$CLONE_HF_SRC" && -d "$CLONE_HF_SRC" ]]; then
      HF_DST="${RUNTIME}/huggingface/hub"
      mkdir -p "$HF_DST"
      echo "    materialize clone HF cache from ${CLONE_HF_SRC}"
      rsync -a --delete "${CLONE_HF_SRC%/}/" "${HF_DST}/"
    fi
  else
    echo "    WARN: EMBED_CLONE_TTS=0 — clone VIDEO_LOCK customers will be blocked at deploy/runtime"
  fi
  "$PYBIN" -m pip check
  # engine 以 studio 根为 PYTHONPATH 导入（与 remote-install / 运行时一致），不是 site-packages 包名
  PYTHONPATH="${STUDIO_DST}${PYTHONPATH:+:${PYTHONPATH}}" \
    "$PYBIN" -c "import encodings,uvicorn,fastapi,edge_tts,numpy,engine.main; print('    deps/imports ok', uvicorn.__version__)" \
    || { echo "ERROR: embedded runtime dependency/import verification failed" >&2; exit 1; }
  if [[ "${PREFER_STANDALONE}" == "1" && -f "${PYTHON_DST}/pyvenv.cfg" ]]; then
    echo "ERROR: production runtime unexpectedly contains host-bound pyvenv.cfg" >&2
    exit 1
  fi
else
  echo "    SKIP_PYTHON=1 - leave existing runtime/python if any"
  PYBIN="${PYTHON_DST}/bin/python3"
  if [[ ! -x "$PYBIN" ]]; then
    echo "ERROR: SKIP_PYTHON=1 requires existing embedded python at ${PYBIN}" >&2
    exit 1
  fi
fi

BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "${RUNTIME}/BUNDLE_LAYOUT.txt" <<EOF
速影一体包布局（scheme A）
studio:   Contents/Resources/runtime/studio
python:   Contents/Resources/runtime/python
data:     ~/Suying/data（不在 App 内）
Ollama:   本机安装（不打进 App）
arch:     ${ARCH}
built:    ${BUILT_AT}
EOF
printf '%s\n' "${APP_VERSION}" > "${RUNTIME}/BUNDLE_VERSION"
printf '%s\n' "${ARCH}" > "${RUNTIME}/BUNDLE_ARCH"
printf '%s\n' "${RUNTIME_FLAVOR}" > "${RUNTIME}/BUNDLE_FLAVOR"
runtime_source_fingerprint "${STUDIO_DST}" > "${RUNTIME}/RUNTIME_SOURCE_SHA256"
PY_RUNTIME_VERSION="$("${PYTHON_DST}/bin/python3" -c 'import platform; print(platform.python_version())')"
printf '%s\n' "${PY_RUNTIME_VERSION}" > "${RUNTIME}/BUNDLE_PYTHON_VERSION"

# Runtime is immutable after manifest generation. Remove generated bytecode first;
# all production launch paths set PYTHONDONTWRITEBYTECODE=1.
"$PYBIN" -c 'import pathlib,shutil,sys; root=pathlib.Path(sys.argv[1]); caches=list(root.rglob("__pycache__")); files=list(root.rglob("*.py[co]")); [shutil.rmtree(p, ignore_errors=True) for p in caches]; [p.unlink(missing_ok=True) for p in files]' "${RUNTIME}"
echo "    compile checked-hash bytecode (sealed by runtime manifest)"
"$PYBIN" -m compileall -q --invalidation-mode checked-hash \
  -s "${RUNTIME}" -p "速影.app/Contents/Resources/runtime" \
  "${STUDIO_DST}" "${PYTHON_DST}/lib"

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
if [[ "${SIGN_RUNTIME_MANIFEST:-1}" == "1" ]]; then
  echo "==> build + sign runtime manifest"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${STUDIO_ROOT}:${STUDIO_DST}" "$PYBIN" \
    "${STUDIO_ROOT}/scripts/build_runtime_manifest.py" \
    --runtime-root "${RUNTIME}" \
    --bundle-version "${APP_VERSION}" \
    --arch "${ARCH}"
else
  rm -f "${RUNTIME}/RUNTIME_MANIFEST.json" "${RUNTIME}/RUNTIME_MANIFEST.json.sig"
  echo "WARN: SIGN_RUNTIME_MANIFEST=0，仅允许开发构建" >&2
fi
# BUNDLE_TEAM_ID and runtime manifest were added after the first signature, so seal once more.
if [[ "$CODESIGN_IDENTITY" == "-" ]]; then
  codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP"
else
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
fi

echo "==> Done"
du -sh "$APP" "$STUDIO_DST" "$PYTHON_DST" 2>/dev/null | sed 's/^/    /'
