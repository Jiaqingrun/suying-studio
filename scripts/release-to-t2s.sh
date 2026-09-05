#!/usr/bin/env bash
# 一体包正式发布：升版 → 暂存到 ~/Suying/releases → 默认极空间 API 直传 → 本机覆盖 → 立即删除本机产物。
# 禁止把交付物落到桌面。禁止默认依赖 ~/QR/dev/T2s/ZSPACE NFS 挂载。
# 本机 releases 仅作出包暂存，推送成功后不保留历史包（KEEP_LOCAL 默认 0）。
#
# Usage:
#   ./scripts/release-to-t2s.sh                 # patch + core + API 直传 + 安装 + 清本地（默认）
#   RUNTIME_FLAVOR=clone ./scripts/release-to-t2s.sh
#   BUMP=minor ./scripts/release-to-t2s.sh
#   SKIP_BUMP=1 ./scripts/release-to-t2s.sh     # 用当前版本重打
#   PUBLISH=0 WEB_STAGE=1 ./scripts/release-to-t2s.sh  # 备用：网页暂存
#   PUBLISH=0 WEB_STAGE=0 ./scripts/release-to-t2s.sh  # 只打本地，不推仓（保留本机产物）
#   KEEP_LOCAL=1 ./scripts/release-to-t2s.sh    # 显式保留本机套件（调试用）
#   INSTALL_LOCAL=0 ./scripts/release-to-t2s.sh # 推送后不覆盖本机 App
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_ROOT="${SUYING_RELEASE_ROOT:-$HOME/Suying/releases}"
RUNTIME_FLAVOR="${RUNTIME_FLAVOR:-core}"
BUMP="${BUMP:-patch}"
SKIP_BUMP="${SKIP_BUMP:-0}"
# 2026-08-15：默认极空间 API 直传；网页暂存须显式 PUBLISH=0 WEB_STAGE=1
PUBLISH="${PUBLISH:-1}"
WEB_STAGE="${WEB_STAGE:-0}"
# 2026-08-21：推送后默认不保留本机历史包；仅 PUBLISH=0 本地打或 KEEP_LOCAL=1 时保留
KEEP_LOCAL="${KEEP_LOCAL:-0}"
INSTALL_LOCAL="${INSTALL_LOCAL:-1}"
DEFAULTS_JSON="${ROOT}/packaging/release_defaults.json"

[[ "$RUNTIME_FLAVOR" == "core" || "$RUNTIME_FLAVOR" == "clone" ]] || {
  echo "ERROR: RUNTIME_FLAVOR 仅支持 core 或 clone" >&2
  exit 1
}

mkdir -p "$RELEASE_ROOT"
export SUYING_RELEASE_ROOT="$RELEASE_ROOT"
export RUNTIME_FLAVOR

if [[ "$SKIP_BUMP" != "1" ]]; then
  echo "==> Bump version (${BUMP})"
  python3 "${ROOT}/scripts/bump_app_version.py" --bump "$BUMP"
fi

VERSION="$(python3 "${ROOT}/scripts/bump_app_version.py" --print-current)"
ARCH="$(uname -m)"
BUILD_DATE="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_NAME="速影-${VERSION}-product-macos-${ARCH}-${RUNTIME_FLAVOR}"
KIT="${RELEASE_ROOT}/${OUT_NAME}"
ZIP="${RELEASE_ROOT}/${OUT_NAME}.zip"

echo "==> Package ${VERSION} flavor=${RUNTIME_FLAVOR} → ${RELEASE_ROOT}（暂存）"
RUNTIME_FLAVOR="$RUNTIME_FLAVOR" BUILD_APP=1 bash "${ROOT}/scripts/package-product.sh"

[[ -d "$KIT" ]] || { echo "ERROR: 缺少套件目录 ${KIT}" >&2; exit 1; }
[[ -f "$ZIP" ]] || { echo "ERROR: 缺少 ZIP ${ZIP}" >&2; exit 1; }
MANIFEST="${KIT}/速影 Studio.app/Contents/Resources/runtime/RUNTIME_MANIFEST.json"
[[ -f "$MANIFEST" ]] || MANIFEST="${KIT}/速影.app/Contents/Resources/runtime/RUNTIME_MANIFEST.json"
[[ -f "$MANIFEST" ]] || { echo "ERROR: 缺少 RUNTIME_MANIFEST.json" >&2; exit 1; }

# 桌面不得残留本轮交付物
DESKTOP_LEAK="$(find "${HOME}/Desktop" -maxdepth 1 \( -name "速影-${VERSION}*" -o -name '速影 Studio.app' -o -name '速影.app' \) 2>/dev/null || true)"
if [[ -n "${DESKTOP_LEAK}" ]]; then
  echo "ERROR: 检测到交付物落到桌面，已禁止：" >&2
  printf '%s\n' "$DESKTOP_LEAK" >&2
  exit 1
fi

if [[ "$PUBLISH" != "1" && "$WEB_STAGE" != "1" ]]; then
  echo "==> PUBLISH=0 WEB_STAGE=0：仅本地套件 ${KIT}（本机保留，未推仓）"
  echo "    推送规范：docs/T2S_UPDATE_PUSH.md（默认 API；网页暂存须 WEB_STAGE=1）"
  exit 0
fi

CUSTOMER_REF="${SUYING_CUSTOMER_REF:-}"
DELIVERY_ID="${SUYING_DELIVERY_ID:-}"
if [[ -z "$CUSTOMER_REF" || -z "$DELIVERY_ID" ]]; then
  read -r CUSTOMER_REF DELIVERY_ID < <(
    python3 - "$DEFAULTS_JSON" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
print(data.get("customer_ref") or "", data.get("delivery_id") or "")
PY
  )
fi
CUSTOMER_REF="${SUYING_CUSTOMER_REF:-$CUSTOMER_REF}"
DELIVERY_ID="${SUYING_DELIVERY_ID:-$DELIVERY_ID}"
[[ -n "$CUSTOMER_REF" && -n "$DELIVERY_ID" ]] || {
  echo "ERROR: 缺少 customer_ref / delivery_id（packaging/release_defaults.json 或环境变量）" >&2
  exit 1
}

RELEASE_SEQ="${RELEASE_SEQ:-}"
if [[ -z "$RELEASE_SEQ" ]]; then
  RELEASE_SEQ="$(
    cd "$ROOT" && python3 - <<'PY'
import json, sys, tempfile
from pathlib import Path
ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT))
seq = 0
try:
    from engine.ops.suying_sync import load_zspace_session
    from scripts.publish_update_repo import REMOTE_ROOT, _client_class

    session = load_zspace_session()
    client = _client_class()(f"http://127.0.0.1:{session['local_port']}", session)
    with tempfile.TemporaryDirectory() as td:
        latest = Path(td) / "latest.json"
        try:
            client.download(f"{REMOTE_ROOT}/latest.json", latest)
            seq = int(json.loads(latest.read_text(encoding="utf-8")).get("release_seq") or 0)
        except Exception:
            seq = 0
except Exception:
    # 无极空间会话时允许人工指定 RELEASE_SEQ（网页暂存备用路径也适用）
    seq = 0
print(seq + 1)
PY
  )"
fi

STAGE_DIR="${RELEASE_ROOT}/t2s-web-stage/${VERSION}-${BUILD_DATE}"
PUBLISH_CMD=(
  python3 "${ROOT}/scripts/publish_update_repo.py"
  --package "$ZIP"
  --version "$VERSION"
  --build-date "$BUILD_DATE"
  --release-seq "$RELEASE_SEQ"
  --runtime-manifest "$MANIFEST"
  --customer-ref "$CUSTOMER_REF"
  --delivery-id "$DELIVERY_ID"
)
if [[ -n "${RELEASE_NOTES:-}" ]]; then
  PUBLISH_CMD+=(--notes "$RELEASE_NOTES")
fi

if [[ "$PUBLISH" == "1" ]]; then
  echo "==> API publish T2S version=${VERSION} release_seq=${RELEASE_SEQ} build=${BUILD_DATE}"
  "${PUBLISH_CMD[@]}"
  MODE="api"
  STAGE_DIR=""
elif [[ "$WEB_STAGE" == "1" ]]; then
  echo "==> Web-stage（备用）version=${VERSION} release_seq=${RELEASE_SEQ} → ${STAGE_DIR}"
  echo "    规范：docs/T2S_UPDATE_PUSH.md"
  PUBLISH_CMD+=(--stage-dir "$STAGE_DIR")
  "${PUBLISH_CMD[@]}"
  MODE="web_stage"
else
  echo "ERROR: 内部状态：PUBLISH/WEB_STAGE 均未启用" >&2
  exit 1
fi

LOCAL_RETAINED=true
if [[ "$KEEP_LOCAL" != "1" ]]; then
  LOCAL_RETAINED=false
fi

# 推送/暂存成功后默认本机覆盖，再删暂存（避免删完无法安装）
if [[ "$INSTALL_LOCAL" == "1" ]]; then
  INSTALL_SH="${KIT}/安装到-应用程序.sh"
  [[ -x "$INSTALL_SH" || -f "$INSTALL_SH" ]] || {
    echo "ERROR: 缺少安装脚本 ${INSTALL_SH}" >&2
    exit 1
  }
  echo "==> Install local App from kit"
  bash "$INSTALL_SH"
fi

cat > "${RELEASE_ROOT}/LAST_PUBLISH.json" << EOF
{
  "version": "${VERSION}",
  "runtime_flavor": "${RUNTIME_FLAVOR}",
  "release_seq": ${RELEASE_SEQ},
  "build_date": "${BUILD_DATE}",
  "mode": "${MODE}",
  "local_retained": ${LOCAL_RETAINED},
  "stage_dir": "${STAGE_DIR}"
}
EOF

purge_local_release_artifacts() {
  # 删除本机交付产物与历史残留；仅保留 LAST_PUBLISH.json（及可选网页暂存当前目录）
  local keep_stage="${1:-}"
  echo "==> 清理本机 releases 暂存（不保留历史包）"
  shopt -s nullglob
  local item
  for item in "${RELEASE_ROOT}"/速影-*; do
    rm -rf "$item"
  done
  if [[ -d "${RELEASE_ROOT}/t2s-web-stage" ]]; then
    if [[ -n "$keep_stage" && -d "$keep_stage" ]]; then
      for item in "${RELEASE_ROOT}/t2s-web-stage"/*; do
        [[ "$item" == "$keep_stage" ]] && continue
        rm -rf "$item"
      done
    else
      rm -rf "${RELEASE_ROOT}/t2s-web-stage"
    fi
  fi
  # f5-runtime kits 亦不得在本机堆历史；正式真源在 T2S
  if [[ -d "${RELEASE_ROOT}/f5-runtime" ]]; then
    rm -rf "${RELEASE_ROOT}/f5-runtime"
  fi
  shopt -u nullglob
}

if [[ "$KEEP_LOCAL" != "1" ]]; then
  if [[ "$MODE" == "web_stage" ]]; then
    # 网页暂存须保留 stage 供人工上传；产品套件/ZIP/DMG 仍立即删
    purge_local_release_artifacts "$STAGE_DIR"
    echo "    已保留网页暂存：${STAGE_DIR}"
    echo "    打开极空间网页 → T2S → M.2存储11/速影/更新包/ ，按 ${STAGE_DIR}/WEB_UPLOAD.txt 顺序上传"
    if [[ "$INSTALL_LOCAL" == "1" ]]; then
      echo "    本机 App 已在清理前覆盖（INSTALL_LOCAL=1）"
    else
      echo "    未覆盖本机 App（INSTALL_LOCAL=0）；上传完成后可从 T2S 拉包或 KEEP_LOCAL=1 重打"
    fi
  else
    purge_local_release_artifacts ""
    echo "==> Done · ${VERSION} → T2S API release_seq=${RELEASE_SEQ} · 本机 releases 已清空（仅留 LAST_PUBLISH.json）"
    if [[ "$INSTALL_LOCAL" == "1" ]]; then
      echo "    本机已覆盖 /Applications/速影 Studio.app"
    else
      echo "    未覆盖本机 App（INSTALL_LOCAL=0）"
    fi
  fi
else
  if [[ "$MODE" == "web_stage" ]]; then
    echo "==> Done · ${VERSION} 网页暂存就绪 · release_seq=${RELEASE_SEQ} · KEEP_LOCAL=1"
    echo "    打开极空间网页 → T2S → M.2存储11/速影/更新包/ ，按 ${STAGE_DIR}/WEB_UPLOAD.txt 顺序上传"
    echo "    然后本机覆盖：bash ${KIT}/安装到-应用程序.sh"
  else
    echo "==> Done · ${VERSION} → T2S API release_seq=${RELEASE_SEQ} · KEEP_LOCAL=1 保留 ${KIT}"
    if [[ "$INSTALL_LOCAL" != "1" ]]; then
      echo "    本机覆盖：bash ${KIT}/安装到-应用程序.sh"
    fi
  fi
fi
