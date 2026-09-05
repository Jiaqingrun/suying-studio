#!/usr/bin/env bash
# Operator-side one-command deployment to a customer Mac reachable over SSH.
# Canonical path: offline tools + offline models + signed kit + license.
# Legacy --install-deps (Homebrew) is opt-in only and is not a delivery path.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST=""
KIT=""
CUSTOMER_NAME=""
CUSTOMER_CONFIG=""
BUILD=0
INSTALL_DEPS=0
BIND_ZSPACE=1
SMOKE_JOB=0
MODEL_BUNDLE=""
OFFLINE_TOOLS=""
OFFLINE_DEPOT=""
ALLOW_ONLINE_MODEL_PULL=0
SKIP_MODELS=0
LICENSE_FILE=""
F5_RUNTIME_KIT="${F5_RUNTIME_KIT:-}"
F5_WEIGHTS_EXPORT="${F5_WEIGHTS_EXPORT:-}"
FORCE_CLONE_FLAVOR_APP="${FORCE_CLONE_FLAVOR_APP:-0}"
UPDATE_REMOTE_ROOT="/nvme11/my/data/速影/更新包"
OFFLINE_ZSPACE="${OFFLINE_ZSPACE:-1}"
STAGING_TTL_HOURS="${SUYING_REMOTE_STAGING_TTL_HOURS:-24}"

usage() {
  cat <<'EOF'
用法:
  deploy-remote.sh --host SSH_ALIAS --customer-name NAME [选项]

统一远程部署（默认离线，禁止隐式 brew / ollama pull）:
  --kit DIR                 产品套件目录；默认自动选最新
  --customer-config DIR     正式客户配置目录（不会写入通用产品包）
  --license FILE            已为客户机签发的单机许可证
  --offline-tools DIR       ollama/ + ffmpeg/ 组件根（offline_bootstrap 物化结果）
  --offline-depot DIR       签名 CAS 仓；未传 --offline-tools 时按档位物化
  --model-bundle DIR        运维端离线模型套件；默认按探测档位选择
  --skip-models             覆盖升级：不传模型（客户机已有）；ZIP 亦不含 offline-models/DMG
  --f5-kit FILE             F5 Runtime Kit（.tar.gz）；VIDEO_LOCK=clone 时必填（或 F5_RUNTIME_KIT）
  --f5-weights DIR          可选 HF 权重 export 目录（materialize_clone_tts_cache --export）
  --build                   部署前重新打 standalone 一体包
  --smoke-job               片库有素材时创建 1 条真实任务
  --no-zspace               不绑定极空间当前登录账号
  --no-offline-zspace       禁止从 T2S「速影/离线交付」暂存模型/工具（默认会尝试）
  --update-remote-root PATH 极空间签名更新仓；默认 T2S「速影/更新包」
  --config-truth            对本机 --customer-config/--seed 跑 config_truth_diff（只读）
  --skip-config-truth-hint  跳过关账默认的 config_truth 运维提醒

兼容（非正式交付）:
  --install-deps            允许远端用 Homebrew 补依赖（默认关闭）
  --allow-online-model-pull 显式允许缺包时从公网拉模型（默认禁止）
  --no-install-deps         兼容旧调用；离线已是默认
  FORCE_CLONE_FLAVOR_APP=1  应急：打并传 clone 内嵌 F5 一体包（默认 core+Kit）
EOF
}

CONFIG_TRUTH=0
SKIP_CONFIG_TRUTH_HINT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --kit) KIT="${2:-}"; shift 2 ;;
    --customer-name) CUSTOMER_NAME="${2:-}"; shift 2 ;;
    --customer-config) CUSTOMER_CONFIG="${2:-}"; shift 2 ;;
    --build) BUILD=1; shift ;;
    --install-deps) INSTALL_DEPS=1; shift ;;
    --no-install-deps) INSTALL_DEPS=0; shift ;;
    --no-zspace) BIND_ZSPACE=0; shift ;;
    --no-offline-zspace) OFFLINE_ZSPACE=0; shift ;;
    --smoke-job) SMOKE_JOB=1; shift ;;
    --model-bundle) MODEL_BUNDLE="${2:-}"; shift 2 ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --offline-tools) OFFLINE_TOOLS="${2:-}"; shift 2 ;;
    --offline-depot) OFFLINE_DEPOT="${2:-}"; shift 2 ;;
    --allow-online-model-pull) ALLOW_ONLINE_MODEL_PULL=1; shift ;;
    --license) LICENSE_FILE="${2:-}"; shift 2 ;;
    --f5-kit) F5_RUNTIME_KIT="${2:-}"; shift 2 ;;
    --f5-weights) F5_WEIGHTS_EXPORT="${2:-}"; shift 2 ;;
    --update-remote-root) UPDATE_REMOTE_ROOT="${2:-}"; shift 2 ;;
    --config-truth) CONFIG_TRUTH=1; shift ;;
    --skip-config-truth-hint) SKIP_CONFIG_TRUTH_HINT=1; shift ;;
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
[[ "$STAGING_TTL_HOURS" =~ ^[1-9][0-9]*$ ]] ||
  fail "SUYING_REMOTE_STAGING_TTL_HOURS 必须为正整数小时"

# 关账默认：本机≠客户机 — config_truth 为运维动作（只读；禁止静默覆盖 profile_json）
if [[ "$SKIP_CONFIG_TRUTH_HINT" != "1" ]]; then
  log "CLOSEOUT: 能力随包、规则/词池在客户库。部署前后请对照 seed 跑："
  printf '    python3 scripts/config_truth_diff.py --customer "%s" --seed "%s"\n' \
    "$CUSTOMER_NAME" \
    "${CUSTOMER_CONFIG:-configs/customers/<客户名>}"
  printf '    （显式导入用 config_truth_import.py；禁止静默覆盖 profile_json / chrome 绑定）\n'
fi
if [[ "$CONFIG_TRUTH" == "1" ]]; then
  seed_path="${CUSTOMER_CONFIG:-}"
  if [[ -z "$seed_path" ]]; then
    cand="${ROOT}/configs/customers/${CUSTOMER_NAME}"
    [[ -d "$cand" ]] && seed_path="$cand"
  fi
  [[ -n "$seed_path" && -d "$seed_path" ]] || fail "--config-truth 需要有效 --customer-config 或 configs/customers/<名>"
  log "运行 config_truth_diff（只读） customer=${CUSTOMER_NAME} seed=${seed_path}"
  python3 "${ROOT}/scripts/config_truth_diff.py" --customer "$CUSTOMER_NAME" --seed "$seed_path" \
    || fail "config_truth_diff 失败"
fi

offline_tools_ok() {
  local root="$1"
  [[ -f "${root}/ollama/ollama" || -f "${root}/ollama" ]] || return 1
  [[ -x "${root}/ffmpeg/bin/ffmpeg" && -x "${root}/ffmpeg/bin/ffprobe" ]] || return 1
  return 0
}

normalize_offline_tools() {
  local root="$1"
  if [[ -f "${root}/ollama/ollama" ]]; then
    printf '%s\n' "$root"
    return 0
  fi
  # Some materializations place the binary at ollama (file) instead of ollama/ollama.
  if [[ -f "${root}/ollama" && -x "${root}/ffmpeg/bin/ffmpeg" ]]; then
    local staged="${work:-}/offline-tools-normalized"
    mkdir -p "${staged}/ollama"
    cp "${root}/ollama" "${staged}/ollama/ollama"
    chmod 755 "${staged}/ollama/ollama"
    rm -rf "${staged}/ffmpeg"
    ditto "${root}/ffmpeg" "${staged}/ffmpeg"
    printf '%s\n' "$staged"
    return 0
  fi
  return 1
}

stage_offline_from_zspace() {
  local need_models="${1:-1}" need_tools="${2:-1}"
  if [[ "$OFFLINE_ZSPACE" != "1" ]]; then
    return 1
  fi
  local args=(
    python3 "${ROOT}/scripts/stage_offline_deploy_from_zspace.py"
    --profile "$remote_profile"
    --arch "$remote_arch"
  )
  [[ "$need_models" != "1" ]] && args+=(--skip-models)
  [[ "$need_tools" != "1" ]] && args+=(--skip-tools)
  log "从极空间暂存离线交付资产（profile=${remote_profile} arch=${remote_arch}）"
  local json
  json="$("${args[@]}")" || return 1
  if [[ "$need_models" == "1" ]]; then
    local mb
    mb="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("model_bundle",""))')"
    [[ -n "$mb" && -f "${mb}/bundle.json" ]] && MODEL_BUNDLE="$mb"
  fi
  if [[ "$need_tools" == "1" ]]; then
    local ot
    ot="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("offline_tools",""))')"
    [[ -n "$ot" ]] && OFFLINE_TOOLS="$ot"
  fi
  return 0
}

log "只读探测客户机 Install Profile"
remote_probe="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" 'printf "%s|%s\n" "$(uname -m)" "$(sysctl -n hw.memsize)"')"
IFS='|' read -r remote_arch remote_ram_bytes <<< "$remote_probe"
local_arch="$(uname -m)"
if [[ "$BUILD" == "1" && "$remote_arch" != "$local_arch" ]]; then
  fail "--build 只能生成本机架构：本机 ${local_arch}，远端 ${remote_arch}；请传入预构建的 ${remote_arch} 签名套件"
fi
[[ "$remote_ram_bytes" =~ ^[0-9]+$ ]] || fail "无法读取客户机统一内存"
remote_ram_gb=$((remote_ram_bytes / 1073741824))
if (( remote_ram_gb >= 96 )); then
  remote_profile="max"
elif (( remote_ram_gb >= 32 )); then
  remote_profile="pro"
elif (( remote_ram_gb >= 16 )); then
  remote_profile="standard"
else
  remote_profile="lite"
fi
log "客户机档位：${remote_profile}（${remote_ram_gb}GB / ${remote_arch}）"

# needs_clone: VIDEO_LOCK.voice.provider=clone → must have F5 Kit (or emergency fat App)
needs_clone=0
if [[ -n "$CUSTOMER_CONFIG" && -f "${CUSTOMER_CONFIG}/brand/VIDEO_LOCK.json" ]]; then
  needs_clone="$(
    python3 - "${CUSTOMER_CONFIG}/brand/VIDEO_LOCK.json" <<'PY'
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("1" if str((data.get("voice") or {}).get("provider") or "").lower() == "clone" else "0")
PY
  )"
fi
# Primary delivery: always core App + external F5 Runtime Kit when clone needed.
# Emergency one-USB clone flavor: FORCE_CLONE_FLAVOR_APP=1
if [[ "$FORCE_CLONE_FLAVOR_APP" == "1" ]]; then
  requested_flavor="clone"
else
  requested_flavor="core"
fi
log "客户需要 clone TTS：${needs_clone}；App flavor：${requested_flavor}（主路径 core+Kit）"

if [[ "$BUILD" == "1" ]]; then
  log "重打可迁移一体包"
  (
    cd "${ROOT}/apps/desktop"
    RUNTIME_FLAVOR="$requested_flavor" PREFER_STANDALONE=1 npm run package:mac
  )
  RUNTIME_FLAVOR="$requested_flavor" BUILD_APP=0 "${ROOT}/scripts/package-product.sh"
fi

if [[ -z "$KIT" ]]; then
  newest=""
  release_root="${SUYING_RELEASE_ROOT:-$HOME/Suying/releases}"
  for candidate in \
    "${release_root}"/速影-*-product-macos-"${remote_arch}"-"${requested_flavor}" \
    "${HOME}/Desktop"/速影-*-product-macos-"${remote_arch}"-"${requested_flavor}"
  do
    [[ -d "$candidate" ]] || continue
    if [[ -z "$newest" || "$candidate" -nt "$newest" ]]; then
      newest="$candidate"
    fi
  done
  [[ -n "$newest" ]] || fail "找不到产品套件；传 --kit 或 --build（优先 ~/Suying/releases）"
  KIT="$newest"
fi
KIT="$(cd "$KIT" && pwd)"
[[ -d "${KIT}/速影 Studio.app" || -d "${KIT}/速影.app" ]] || fail "套件缺少 速影 Studio.app: ${KIT}"
if [[ -d "${KIT}/速影 Studio.app" ]]; then
  KIT_APP="${KIT}/速影 Studio.app"
else
  KIT_APP="${KIT}/速影.app"
fi

PY="${KIT_APP}/Contents/Resources/runtime/python/bin/python3"
[[ -x "$PY" ]] || fail "套件缺少内嵌 Python"
BUNDLE_FLAVOR="$(cat "${KIT_APP}/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true)"
[[ "$BUNDLE_FLAVOR" == "$requested_flavor" ]] ||
  fail "套件 flavor=${BUNDLE_FLAVOR:-missing} 与要求 ${requested_flavor} 不匹配；请按合同重打"

# Resolve F5 Runtime Kit when clone is required and App is core
if [[ "$needs_clone" == "1" && "$requested_flavor" == "core" ]]; then
  if [[ -z "$F5_RUNTIME_KIT" ]]; then
    newest_kit=""
    for candidate in \
      "${HOME}/Suying/releases/f5-runtime/kits"/速影-f5-runtime-kit-*.tar.gz \
      "${HOME}/Suying/releases"/速影-f5-runtime-kit-*.tar.gz
    do
      [[ -f "$candidate" ]] || continue
      if [[ -z "$newest_kit" || "$candidate" -nt "$newest_kit" ]]; then
        newest_kit="$candidate"
      fi
    done
    F5_RUNTIME_KIT="$newest_kit"
  fi
  [[ -n "$F5_RUNTIME_KIT" && -f "$F5_RUNTIME_KIT" ]] ||
    fail "VIDEO_LOCK=clone 需要 F5 Runtime Kit：传 --f5-kit 或先 package-f5-runtime-kit.sh（禁止仅用 core 包伪装 clone 就绪）"
  F5_RUNTIME_KIT="$(cd "$(dirname "$F5_RUNTIME_KIT")" && pwd)/$(basename "$F5_RUNTIME_KIT")"
  log "F5 Runtime Kit：${F5_RUNTIME_KIT}"
fi
BUNDLE_ARCH="$(cat "${KIT_APP}/Contents/Resources/runtime/BUNDLE_ARCH" 2>/dev/null || true)"
[[ "$BUNDLE_ARCH" == "$remote_arch" ]] ||
  fail "套件架构=${BUNDLE_ARCH:-missing} 与客户机 ${remote_arch} 不匹配"
if [[ "$remote_arch" == "$local_arch" ]]; then
  "$PY" -c 'import encodings,fastapi,uvicorn,edge_tts,numpy' || fail "套件 Python 不可运行"
else
  log "跨架构部署：本机不执行客户 Python，远端安装门禁将完成 import/health 验证"
fi
if [[ -f "${KIT_APP}/Contents/Resources/runtime/python/pyvenv.cfg" ]] &&
   grep -Eq '^home = /(Users|opt)/' "${KIT_APP}/Contents/Resources/runtime/python/pyvenv.cfg"; then
  fail "套件仍是 host-bound venv；请用 --build 重打"
fi
codesign --verify --deep --strict "${KIT_APP}" ||
  fail "本地套件签名校验失败"
# LaunchServices Chrome path is required for Cookie persistence on customer Macs.
if ! grep -q 'LaunchServices' \
  "${KIT_APP}/Contents/Resources/runtime/studio/engine/reach/chrome_runtime.py"; then
  fail "套件 chrome_runtime 缺少 LaunchServices 启动路径；请重 embed / --build 后再部署"
fi

if [[ "$SKIP_MODELS" == "1" ]]; then
  [[ -z "$MODEL_BUNDLE" ]] || fail "--skip-models 与 --model-bundle 互斥"
  [[ "$ALLOW_ONLINE_MODEL_PULL" != "1" ]] ||
    fail "--skip-models 与 --allow-online-model-pull 互斥"
  log "覆盖升级：跳过模型套件传输（客户机应已有模型；安装门禁仍核验）"
  MODEL_BUNDLE=""
elif [[ -z "$MODEL_BUNDLE" ]]; then
  for candidate in \
    "${KIT}/offline-models/${remote_profile}" \
    "${HOME}/Suying/offline/速影-offline-models-macos-${remote_arch}/${remote_profile}" \
    "${HOME}/Suying/incoming/zspace-offline/models/macos-${remote_arch}/${remote_profile}" \
    "${HOME}/Desktop/速影-offline-models-macos-${remote_arch}/${remote_profile}"; do
    if [[ -f "${candidate}/bundle.json" ]]; then
      MODEL_BUNDLE="$candidate"
      break
    fi
  done
  if [[ -z "$MODEL_BUNDLE" ]]; then
    stage_offline_from_zspace 1 0 || true
  fi
fi
if [[ "$SKIP_MODELS" != "1" ]]; then
  if [[ -n "$MODEL_BUNDLE" ]]; then
    MODEL_BUNDLE="$(cd "$MODEL_BUNDLE" && pwd)"
    python3 "${ROOT}/scripts/ollama_model_bundle.py" verify \
      --bundle "$MODEL_BUNDLE" --expected-profile "$remote_profile" --expected-arch "$remote_arch" \
      >/dev/null || fail "离线模型套件校验失败"
  elif [[ "$ALLOW_ONLINE_MODEL_PULL" != "1" ]]; then
    fail "缺少 ${remote_profile} 离线模型套件；先在运维端生成，或显式 --skip-models / --allow-online-model-pull"
  fi
fi

work="$(mktemp -d)"
deployment_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-$(basename "$work" | tr -cd 'A-Za-z0-9')"
remote_stage_root="\$HOME/Suying/incoming/remote-deploy"
remote_base="${remote_stage_root}/${deployment_id}"
remote_zip="${remote_base}/suying-product.zip"
remote_installer="${remote_base}/remote-install.sh"
remote_bundle_tool="${remote_base}/ollama_model_bundle.py"
remote_model_bundle="${remote_base}/offline-models/${remote_profile}"
remote_offline_tools="${remote_base}/offline-tools"
REMOTE_STAGE_CREATED=0
REMOTE_STAGE_FINALIZED=0

record_remote_retention() {
  local outcome="$1"
  local status_code="$2"
  local expires_at
  local expires_epoch
  expires_epoch="$(( $(date +%s) + STAGING_TTL_HOURS * 3600 ))"
  expires_at="$(
    python3 - "$STAGING_TTL_HOURS" <<'PY'
from datetime import datetime, timedelta, timezone
import sys
print((datetime.now(timezone.utc) + timedelta(hours=int(sys.argv[1]))).isoformat())
PY
  )"
  ssh -o BatchMode=yes "$HOST" "
set -euo pipefail
BASE=${remote_base}
ROOT=\"\$HOME/Suying/incoming/remote-deploy\"
case \"\$BASE\" in
  \"\$ROOT\"/*) ;;
  *) echo 'ERROR: 拒绝记录非独立部署 staging' >&2; exit 2 ;;
esac
mkdir -p \"\$BASE\"
umask 077
{
  printf 'deployment_id=%s\n' $(printf '%q' "$deployment_id")
  printf 'outcome=%s\n' $(printf '%q' "$outcome")
  printf 'status_code=%s\n' $(printf '%q' "$status_code")
  printf 'path=%s\n' \"\$BASE\"
  printf 'ttl_hours=%s\n' $(printf '%q' "$STAGING_TTL_HOURS")
  printf 'expires_at=%s\n' $(printf '%q' "$expires_at")
  printf 'expires_epoch=%s\n' $(printf '%q' "$expires_epoch")
} > \"\$BASE/STAGING-RETENTION.txt\"
"
  printf 'STAGING_RETAINED: host=%s path=%s ttl=%sh expires_at=%s outcome=%s\n' \
    "$HOST" "${remote_base}" "$STAGING_TTL_HOURS" "$expires_at" "$outcome" >&2
}

prune_expired_remote_staging() {
  ssh -o BatchMode=yes "$HOST" '
set -euo pipefail
ROOT="$HOME/Suying/incoming/remote-deploy"
mkdir -p "$ROOT"
NOW="$(date +%s)"
for BASE in "$ROOT"/*; do
  [[ -d "$BASE" && ! -L "$BASE" ]] || continue
  case "$BASE" in
    "$ROOT"/*) ;;
    *) continue ;;
  esac
  MARKER="$BASE/STAGING-RETENTION.txt"
  [[ -f "$MARKER" ]] || continue
  EXPIRES="$(awk -F= '"'"'$1 == "expires_epoch" {print $2; exit}'"'"' "$MARKER")"
  [[ "$EXPIRES" =~ ^[0-9]+$ ]] || continue
  if (( NOW >= EXPIRES )); then
    rm -rf -- "$BASE"
    printf "PRUNED_EXPIRED_STAGING:%s\n" "$BASE"
  fi
done
'
}

cleanup_remote_staging() {
  ssh -o BatchMode=yes "$HOST" "
set -euo pipefail
BASE=${remote_base}
ROOT=\"\$HOME/Suying/incoming/remote-deploy\"
case \"\$BASE\" in
  \"\$ROOT\"/*) ;;
  *) echo 'ERROR: 拒绝清理非独立部署 staging' >&2; exit 2 ;;
esac
[[ \"\$BASE\" != \"\$ROOT\" ]]
rm -rf -- \"\$BASE\"
"
}

cleanup() {
  local status=$?
  trap - EXIT
  rm -rf "$work"
  if [[ "$REMOTE_STAGE_CREATED" == "1" && "$REMOTE_STAGE_FINALIZED" != "1" ]]; then
    record_remote_retention "FAILED_OR_INTERRUPTED" "$status" || true
  fi
  exit "$status"
}
trap cleanup EXIT

# Resolve offline tools (canonical). Legacy --install-deps skips the hard requirement.
if [[ -n "$OFFLINE_TOOLS" ]]; then
  OFFLINE_TOOLS="$(cd "$OFFLINE_TOOLS" && pwd)"
elif [[ -z "$OFFLINE_DEPOT" ]]; then
  for candidate in \
    "${HOME}/Suying/offline/速影-offline-verify-python-${remote_profile}" \
    "${HOME}/Suying/incoming/zspace-offline/verify-tools/macos-${remote_arch}/${remote_profile}" \
    "${HOME}/Suying/offline/速影-offline-verify/${remote_profile}" \
    "${HOME}/Desktop/速影-offline-verify-python-${remote_profile}" \
    "${HOME}/Desktop/速影-offline-verify/${remote_profile}" \
    "${HOME}/Suying/runtime/offline-staging"; do
    if offline_tools_ok "$candidate" 2>/dev/null || {
         [[ -f "${candidate}/ollama" && -x "${candidate}/ffmpeg/bin/ffmpeg" ]]
       }; then
      OFFLINE_TOOLS="$candidate"
      break
    fi
  done
  if [[ -z "$OFFLINE_TOOLS" ]]; then
    for depot_candidate in \
      "${HOME}/Suying/incoming/zspace-offline/depot/${local_arch}-ready" \
      "${HOME}/Suying/offline/速影-offline-depot-${local_arch}-ready" \
      "${HOME}/Suying/offline/速影-offline-depot-${local_arch}" \
      "${HOME}/Desktop/速影-offline-depot-${local_arch}-ready" \
      "${HOME}/Desktop/速影-offline-depot-${local_arch}"; do
      if [[ -f "${depot_candidate}/depot.json" ]]; then
        OFFLINE_DEPOT="$depot_candidate"
        break
      fi
    done
  fi
  if [[ -z "$OFFLINE_TOOLS" && -z "$OFFLINE_DEPOT" ]]; then
    stage_offline_from_zspace 0 1 || true
  fi
fi

if [[ -z "$OFFLINE_TOOLS" && -n "$OFFLINE_DEPOT" ]]; then
  OFFLINE_DEPOT="$(cd "$OFFLINE_DEPOT" && pwd)"
  log "从离线仓物化 ${remote_profile} 工具组件"
  stage="${work}/offline-staging"
  python3 "${ROOT}/scripts/offline_bootstrap.py" \
    --depot "$OFFLINE_DEPOT" \
    --profile "$remote_profile" \
    --workspace "$stage" >/dev/null
  OFFLINE_TOOLS="$stage"
fi

if [[ -n "$OFFLINE_TOOLS" ]]; then
  OFFLINE_TOOLS="$(normalize_offline_tools "$OFFLINE_TOOLS")" ||
    fail "离线工具目录无效（需要 ollama/ollama 与 ffmpeg/bin/{ffmpeg,ffprobe}）: ${OFFLINE_TOOLS:-}"
  # Never rsync a full depot/bootstrap workspace (may contain app zip + models).
  tools_slim="${work}/offline-tools-slim"
  mkdir -p "${tools_slim}/ollama"
  cp "${OFFLINE_TOOLS}/ollama/ollama" "${tools_slim}/ollama/ollama"
  chmod 755 "${tools_slim}/ollama/ollama"
  rm -rf "${tools_slim}/ffmpeg"
  ditto "${OFFLINE_TOOLS}/ffmpeg" "${tools_slim}/ffmpeg"
  OFFLINE_TOOLS="$tools_slim"
  log "离线工具：ollama + ffmpeg（已剔除 app/models）"
elif [[ "$INSTALL_DEPS" == "1" ]]; then
  log "WARNING: 未找到 offline-tools，将使用兼容路径 --install-deps（非正式交付）"
else
  fail "正式远程部署缺少 offline-tools；传 --offline-tools / --offline-depot，或显式 --install-deps 进入非正式兼容路径"
fi

kit_zip="${work}/suying-product.zip"
config_tgz="${work}/customer-config.tgz"

log "压缩为单文件并断点传输"
ditto -c -k --sequesterRsrc --keepParent "$KIT" "$kit_zip"
# 人工 DMG 与模型套件均为独立交付物；远程 ZIP 只传 App/安装脚本（模型走专用 rsync 或 --skip-models）。
if unzip -Z1 "$kit_zip" | grep -Eqi '\.dmg$'; then
  /usr/bin/zip -d "$kit_zip" '*.[dD][mM][gG]' >/dev/null
fi
if unzip -Z1 "$kit_zip" | grep -Eqi '(^|/)offline-models(/|$)'; then
  log "从远程 ZIP 剔除套件内 offline-models（避免与专用通道重复推送）"
  unzip -Z1 "$kit_zip" | grep -Ei '(^|/)offline-models(/|$)' | /usr/bin/zip -d "$kit_zip" -@ >/dev/null
fi
if unzip -Z1 "$kit_zip" | grep -Eqi '\.dmg$'; then
  fail "远程产品 ZIP 仍包含 DMG，拒绝传输"
fi
if unzip -Z1 "$kit_zip" | grep -Eqi '(^|/)offline-models(/|$)'; then
  fail "远程产品 ZIP 仍包含 offline-models，拒绝传输"
fi
kit_sha256="$(shasum -a 256 "$kit_zip" | awk '{print $1}')"
prune_expired_remote_staging || log "WARNING: 过期 staging 清理失败，本次部署继续"
ssh -o BatchMode=yes "$HOST" "mkdir -p ${remote_base}"
REMOTE_STAGE_CREATED=1
record_remote_retention "IN_PROGRESS" "0"
rsync -a --partial --progress -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
  "$kit_zip" "${HOST}:${remote_zip}"
rsync -a -e 'ssh -o BatchMode=yes' \
  "${ROOT}/scripts/remote-install.sh" \
  "${ROOT}/scripts/install-f5-runtime-kit.sh" \
  "${ROOT}/scripts/install-ollama-agent.sh" \
  "${ROOT}/scripts/install-ollama-atomic.sh" \
  "${ROOT}/scripts/ollama_model_bundle.py" \
  "${HOST}:${remote_base}/"
if [[ -n "${F5_RUNTIME_KIT:-}" && -f "$F5_RUNTIME_KIT" ]]; then
  log "传输 F5 Runtime Kit"
  rsync -a --partial --progress -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
    "$F5_RUNTIME_KIT" "${HOST}:${remote_base}/f5-runtime-kit.tar.gz"
fi
if [[ -n "${F5_WEIGHTS_EXPORT:-}" && -d "$F5_WEIGHTS_EXPORT" ]]; then
  log "传输 F5 HF 权重 export"
  rsync -a --partial --progress -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
    "${F5_WEIGHTS_EXPORT}/" "${HOST}:${remote_base}/f5-weights/"
fi
if [[ -n "$MODEL_BUNDLE" ]]; then
  log "断点传输 ${remote_profile} 离线模型套件"
  ssh -o BatchMode=yes "$HOST" "mkdir -p ${remote_model_bundle}/blobs"
  python3 - "$MODEL_BUNDLE" <<'PY' | ssh -o BatchMode=yes "$HOST" \
    "DEST=${remote_model_bundle}/blobs; SOURCE=\"\${OLLAMA_MODELS:-\${HOME}/.ollama/models}/blobs\"; while IFS= read -r blob; do case \"\$blob\" in sha256-[0-9a-f]*) ;; *) exit 2 ;; esac; partial=\"\$SOURCE/\${blob}-partial\"; target=\"\$DEST/\$blob\"; if [[ -f \"\$partial\" && ! -f \"\$target\" ]]; then /bin/cp -c \"\$partial\" \"\$target\" 2>/dev/null || cp \"\$partial\" \"\$target\"; fi; done"
import json
import sys
from pathlib import Path
bundle = Path(sys.argv[1])
metadata = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
for relative in metadata.get("files", {}):
    if relative.startswith("blobs/"):
        print(Path(relative).name)
PY
  rsync -a --partial --progress --delete -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
    "${MODEL_BUNDLE}/" "${HOST}:${remote_model_bundle}/"
fi

if [[ -n "$OFFLINE_TOOLS" ]]; then
  log "断点传输离线 Ollama/FFmpeg 工具"
  ssh -o BatchMode=yes "$HOST" "mkdir -p ${remote_offline_tools}"
  rsync -a --partial --progress --delete -e 'ssh -o BatchMode=yes -o ServerAliveInterval=30' \
    "${OFFLINE_TOOLS}/" "${HOST}:${remote_offline_tools}/"
fi

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
remote_license=""
if [[ -n "$LICENSE_FILE" ]]; then
  [[ -f "$LICENSE_FILE" ]] || fail "许可证文件不存在: $LICENSE_FILE"
  rsync -a --partial -e 'ssh -o BatchMode=yes' \
    "$LICENSE_FILE" "${HOST}:${remote_base}/customer.suying-license"
  remote_license="${remote_base}/customer.suying-license"
fi

log "远端解包并执行安装门禁"
remote_command="$(cat <<EOF
set -euo pipefail
BASE=${remote_base}
RELEASE=\${BASE}/release
EXPECTED_SHA256=${kit_sha256}
ACTUAL_SHA256="\$(shasum -a 256 "${remote_zip}" | awk '{print \$1}')"
[[ "\$ACTUAL_SHA256" == "\$EXPECTED_SHA256" ]] || {
  echo "ERROR: 产品套件 SHA256 校验失败" >&2
  exit 1
}
rm -rf "\$RELEASE"
mkdir -p "\$RELEASE"
ditto -x -k "${remote_zip}" "\$RELEASE"
chmod +x "${remote_installer}"
APP_SOURCE="\$(find "\$RELEASE" -maxdepth 3 -type d \\( -name '速影 Studio.app' -o -name '速影.app' \\) ! -path '*/__MACOSX/*' -print -quit)"
[[ -n "\$APP_SOURCE" ]] || { echo 'ERROR: 解包后未找到 速影 Studio.app' >&2; exit 1; }
[[ -f "\$APP_SOURCE/Contents/Info.plist" ]] || { echo "ERROR: App 不完整（缺 Info.plist）: \$APP_SOURCE" >&2; exit 1; }
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
[[ -n "$OFFLINE_TOOLS" ]] && remote_command+=" --offline-tools \"${remote_offline_tools}\""
[[ "$BIND_ZSPACE" == "1" ]] && remote_command+=" --bind-active-zspace"
[[ "$BIND_ZSPACE" == "1" ]] && remote_command+=" --update-remote-root $(printf '%q' "$UPDATE_REMOTE_ROOT")"
[[ "$SMOKE_JOB" == "1" ]] && remote_command+=" --smoke-job"
[[ -z "$remote_license" ]] || remote_command+=" --license \"${remote_license}\""
[[ -z "$MODEL_BUNDLE" ]] || remote_command+=" --model-bundle \"${remote_model_bundle}\" --model-bundle-tool \"${remote_bundle_tool}\""
[[ "$ALLOW_ONLINE_MODEL_PULL" == "1" ]] && remote_command+=" --allow-online-model-pull"
if [[ -n "$F5_RUNTIME_KIT" && -f "$F5_RUNTIME_KIT" ]]; then
  remote_command+=" --f5-kit \"${remote_base}/f5-runtime-kit.tar.gz\""
  remote_command+=" --f5-kit-installer \"${remote_base}/install-f5-runtime-kit.sh\""
fi
if [[ -n "$F5_WEIGHTS_EXPORT" && -d "$F5_WEIGHTS_EXPORT" ]]; then
  remote_command+=" --f5-weights \"${remote_base}/f5-weights\""
fi

set +e
ssh -o BatchMode=yes -o ServerAliveInterval=30 "$HOST" "$remote_command"
remote_status=$?
set -e
if [[ "$remote_status" -eq 20 ]]; then
  REQUEST_OUT="${HOME}/Desktop/${HOST//[^A-Za-z0-9._-]/_}-速影-许可请求.json"
  if ssh -o BatchMode=yes "$HOST" \
    'test -f "$HOME/Desktop/速影-许可请求.json" && grep -q device_key_id "$HOME/Desktop/速影-许可请求.json" && cat "$HOME/Desktop/速影-许可请求.json"' \
    >"$REQUEST_OUT" 2>/dev/null; then
    printf '许可请求已取回：%s\n' "$REQUEST_OUT" >&2
  else
    rm -f "$REQUEST_OUT"
  fi
  record_remote_retention "PARTIAL" "$remote_status"
  REMOTE_STAGE_FINALIZED=1
  printf 'PARTIAL：可能等待本机许可证、素材或真实任务验收；未生成成功回执，请查看远端输出\n' >&2
  exit 20
fi
if [[ "$remote_status" -ne 0 ]]; then
  record_remote_retention "FAILED" "$remote_status"
  REMOTE_STAGE_FINALIZED=1
  fail "远端安装失败（exit ${remote_status}）"
fi
log "完整验收通过，清理本次独立远端 staging"
if ! cleanup_remote_staging; then
  record_remote_retention "CLEANUP_FAILED" "1" || true
  REMOTE_STAGE_FINALIZED=1
  fail "部署已验收，但本次 staging 清理失败；已保留路径与 TTL"
fi
REMOTE_STAGE_FINALIZED=1
log "部署完整验收通过：${HOST} / ${CUSTOMER_NAME}"
