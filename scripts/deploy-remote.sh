#!/usr/bin/env bash
# Operator-side one-command deployment to a customer Mac reachable over SSH.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST=""
KIT=""
CUSTOMER_NAME=""
CUSTOMER_CONFIG=""
BUILD=0
INSTALL_DEPS=1
BIND_ZSPACE=1
SMOKE_JOB=0

usage() {
  cat <<'EOF'
用法:
  deploy-remote.sh --host SSH_ALIAS --customer-name NAME [选项]

选项:
  --kit DIR                产品套件目录；默认自动选最新
  --customer-config DIR    正式客户配置目录（不会写入通用产品包）
  --build                  部署前重新打 standalone 一体包
  --no-install-deps        不自动补 Ollama / FFmpeg
  --no-zspace              不绑定极空间当前登录账号
  --smoke-job              片库有素材时创建 1 条真实任务
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --kit) KIT="${2:-}"; shift 2 ;;
    --customer-name) CUSTOMER_NAME="${2:-}"; shift 2 ;;
    --customer-config) CUSTOMER_CONFIG="${2:-}"; shift 2 ;;
    --build) BUILD=1; shift ;;
    --no-install-deps) INSTALL_DEPS=0; shift ;;
    --no-zspace) BIND_ZSPACE=0; shift ;;
    --smoke-job) SMOKE_JOB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" && -n "$CUSTOMER_NAME" ]] || { usage; exit 2; }
if [[ -n "$CUSTOMER_CONFIG" ]]; then
  CUSTOMER_CONFIG="$(cd "$CUSTOMER_CONFIG" && pwd)"
fi

log() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

log "预检 SSH 与目标架构"
remote_arch="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" 'uname -m')"
local_arch="$(uname -m)"
[[ "$remote_arch" == "$local_arch" ]] ||
  fail "架构不匹配：本机 ${local_arch}，远端 ${remote_arch}"

if [[ "$BUILD" == "1" ]]; then
  log "重打可迁移一体包"
  (
    cd "${ROOT}/apps/desktop"
    PREFER_STANDALONE=1 npm run package:mac
  )
  BUILD_APP=0 PUBLISH_CARRIER=1 CARRIER_ROOT="${HOME}/Suying/carrier" \
    "${ROOT}/scripts/package-product.sh"
fi

if [[ -z "$KIT" ]]; then
  candidates=("${HOME}/Desktop"/速影-*-product-macos-"${local_arch}")
  [[ -d "${candidates[0]:-}" ]] || fail "找不到产品套件；传 --kit 或 --build"
  KIT="${candidates[0]}"
fi
KIT="$(cd "$KIT" && pwd)"
[[ -d "${KIT}/速影.app" ]] || fail "套件缺少 速影.app: ${KIT}"

PY="${KIT}/速影.app/Contents/Resources/runtime/python/bin/python3"
[[ -x "$PY" ]] || fail "套件缺少内嵌 Python"
"$PY" -c 'import encodings,fastapi,uvicorn' || fail "套件 Python 不可运行"
if [[ -f "${KIT}/速影.app/Contents/Resources/runtime/python/pyvenv.cfg" ]] &&
   grep -Eq '^home = /(Users|opt)/' "${KIT}/速影.app/Contents/Resources/runtime/python/pyvenv.cfg"; then
  fail "套件仍是 host-bound venv；请用 --build 重打"
fi
codesign --verify --deep --strict "${KIT}/速影.app" ||
  fail "本地套件签名校验失败"

work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT
kit_zip="${work}/suying-product.zip"
config_tgz="${work}/customer-config.tgz"
remote_base="\$HOME/Suying/incoming/remote-deploy"
remote_zip="${remote_base}/suying-product.zip"
remote_installer="${remote_base}/remote-install.sh"

log "压缩为单文件并断点传输"
ditto -c -k --sequesterRsrc --keepParent "$KIT" "$kit_zip"
ssh -o BatchMode=yes "$HOST" "mkdir -p ${remote_base}"
rsync -a --partial --progress -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
  "$kit_zip" "${HOST}:${remote_zip}"
rsync -a -e 'ssh -o BatchMode=yes' \
  "${ROOT}/scripts/remote-install.sh" "${HOST}:${remote_installer}"

remote_config=""
if [[ -n "$CUSTOMER_CONFIG" ]]; then
  log "传输正式客户配置（独立于通用产品包）"
  (
    cd "$CUSTOMER_CONFIG"
    tar -czf "$config_tgz" \
      --exclude='.DS_Store' --exclude='*.mp4' --exclude='*.mov' --exclude='.env*' .
  )
  rsync -a --partial --progress -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
    "$config_tgz" "${HOST}:${remote_base}/customer-config.tgz"
  remote_config="${remote_base}/customer-config"
fi

log "远端解包并执行安装门禁"
remote_command="$(cat <<EOF
set -euo pipefail
BASE=${remote_base}
RELEASE=\${BASE}/release
rm -rf "\$RELEASE"
mkdir -p "\$RELEASE"
ditto -x -k "${remote_zip}" "\$RELEASE"
chmod +x "${remote_installer}"
APP_SOURCE="\$(find "\$RELEASE" -maxdepth 3 -type d -name '速影.app' -print -quit)"
[[ -n "\$APP_SOURCE" ]] || { echo 'ERROR: 解包后未找到速影.app' >&2; exit 1; }
EOF
)"
if [[ -n "$remote_config" ]]; then
  remote_command+="
rm -rf \"${remote_config}\"
mkdir -p \"${remote_config}\"
tar -xzf \"${remote_base}/customer-config.tgz\" -C \"${remote_config}\"
"
fi
remote_command+="
bash \"${remote_installer}\" --app-source \"\$APP_SOURCE\" --customer-name $(printf '%q' "$CUSTOMER_NAME")"
[[ -z "$remote_config" ]] ||
  remote_command+=" --customer-config \"${remote_config}\""
[[ "$INSTALL_DEPS" == "1" ]] && remote_command+=" --install-deps"
[[ "$BIND_ZSPACE" == "1" ]] && remote_command+=" --bind-active-zspace"
[[ "$SMOKE_JOB" == "1" ]] && remote_command+=" --smoke-job"

ssh -o BatchMode=yes -o ServerAliveInterval=30 "$HOST" "$remote_command"
log "部署成功：${HOST} / ${CUSTOMER_NAME}"
