#!/usr/bin/env bash
# Run on the customer Mac after the product kit has been transferred.
set -euo pipefail

APP_SOURCE=""
CUSTOMER_NAME=""
CUSTOMER_CONFIG=""
INSTALL_DEPS=0
BIND_ZSPACE=0
UPDATE_REMOTE_ROOT=""
LICENSE_FILE=""
OFFLINE_TOOLS=""
SMOKE_JOB=0
MODEL_BUNDLE=""
MODEL_BUNDLE_TOOL=""
ALLOW_ONLINE_MODEL_PULL=0
MODEL_SOURCE="preexisting"
VERIFY_STATUS_ONLY=""
F5_KIT=""
F5_KIT_INSTALLER=""
F5_WEIGHTS=""
PORT="${SUYING_PORT:-8766}"

usage() {
  cat <<'EOF'
用法:
  remote-install.sh --app-source /path/to/速影.app --customer-name NAME [选项]

选项:
  --customer-config DIR   私下传入的正式客户配置（keyword-pack.json + brand/）
  --offline-tools DIR     正式路径：offline_bootstrap 物化的 ollama/ffmpeg 根目录
  --install-deps          兼容路径：用 Homebrew 安装缺失依赖（非正式交付）
  --bind-active-zspace   绑定极空间客户端当前登录账号与 nas_id
  --update-remote-root PATH 发布者个人空间签名更新仓库（配合 --bind-active-zspace）
  --license FILE          本机签名许可证；首装未提供时生成请求并以 PARTIAL 结束
  --smoke-job            片库有素材时创建 1 条真实任务并等待接单
  --model-bundle DIR      已传入且校验过的离线 Ollama 模型套件
  --model-bundle-tool PY  离线套件校验/原子导入工具
  --allow-online-model-pull 显式允许缺模型时联网拉取（默认禁止）
  --f5-kit FILE           F5 Runtime Kit tar.gz（装到 ~/Suying/runtime，不写入 App）
  --f5-kit-installer SH   install-f5-runtime-kit.sh 路径
  --f5-weights DIR        HF 权重 export 目录（import 到本机 hub）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-source) APP_SOURCE="${2:-}"; shift 2 ;;
    --customer-name) CUSTOMER_NAME="${2:-}"; shift 2 ;;
    --customer-config) CUSTOMER_CONFIG="${2:-}"; shift 2 ;;
    --install-deps) INSTALL_DEPS=1; shift ;;
    --bind-active-zspace) BIND_ZSPACE=1; shift ;;
    --update-remote-root) UPDATE_REMOTE_ROOT="${2:-}"; shift 2 ;;
    --license) LICENSE_FILE="${2:-}"; shift 2 ;;
    --offline-tools) OFFLINE_TOOLS="${2:-}"; shift 2 ;;
    --smoke-job) SMOKE_JOB=1; shift ;;
    --model-bundle) MODEL_BUNDLE="${2:-}"; shift 2 ;;
    --model-bundle-tool) MODEL_BUNDLE_TOOL="${2:-}"; shift 2 ;;
    --allow-online-model-pull) ALLOW_ONLINE_MODEL_PULL=1; shift ;;
    --f5-kit) F5_KIT="${2:-}"; shift 2 ;;
    --f5-kit-installer) F5_KIT_INSTALLER="${2:-}"; shift 2 ;;
    --f5-weights) F5_WEIGHTS="${2:-}"; shift 2 ;;
    --verify-status-only) VERIFY_STATUS_ONLY="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

verification_complete() {
  [[ "$1" == "passed" || "$1" == "verified_existing" ]]
}

if [[ -n "$VERIFY_STATUS_ONLY" ]]; then
  if verification_complete "$VERIFY_STATUS_ONLY"; then
    printf 'COMPLETE:%s\n' "$VERIFY_STATUS_ONLY"
    exit 0
  fi
  printf 'PARTIAL:%s\n' "$VERIFY_STATUS_ONLY"
  exit 20
fi

if [[ -z "$APP_SOURCE" || ! -d "$APP_SOURCE" || -z "$CUSTOMER_NAME" ]]; then
  usage
  exit 2
fi

log() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Opening 速影.app in the Aqua session unlocks Keychain access for osascript.
# Finder alone is not enough; plain SSH security -w returns empty / status 36.
warm_console_session() {
  local app_path="${1:-}"
  if [[ -n "$app_path" && -d "$app_path" ]]; then
    open "$app_path" >/dev/null 2>&1 || true
  else
    open -ga Finder >/dev/null 2>&1 || true
  fi
  # Keychain ACL unlock after open is not instantaneous over SSH helpers.
  sleep 6
}

read_device_secret_via_osascript() {
  local app_path="${1:-${APP:-}}"
  local attempt secret
  for attempt in 1 2 3 4 5; do
    warm_console_session "$app_path"
    if secret="$(
      osascript -e 'do shell script "security find-generic-password -s com.qr.suying.license -a device-binding-secret-v1 -w"'
    )" && [[ -n "$secret" ]]; then
      printf '%s' "$secret"
      return 0
    fi
    sleep 2
  done
  return 1
}

# Run a short command with the device secret injected for license verify/install.
run_with_device_secret() {
  local secret
  secret="$(read_device_secret_via_osascript "${APP:-}")" ||
    fail "无法在图形会话读取设备密钥；请先在客户机桌面解锁并打开速影"
  SUYING_DEVICE_SECRET_B64="$secret" "$@"
}

restart_desktop_client() {
  log "重启桌面客户端"
  osascript -e 'tell application "速影 Studio" to quit' 2>/dev/null || true
  osascript -e 'tell application "速影" to quit' 2>/dev/null || true
  osascript -e 'tell application id "com.qr.suying" to quit' 2>/dev/null || true
  for pid in $(pgrep -x appsdesktop 2>/dev/null || true); do
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$cmd" == *"/速影 Studio.app/"* || "$cmd" == *"/速影.app/"* ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
}

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
INSTALL_ROOT="${HOME}/Applications"
APP_BUNDLE_NAME="速影 Studio.app"
APP="${INSTALL_ROOT}/${APP_BUNDLE_NAME}"
BACKUP_ROOT="${HOME}/Suying/backups/apps"
STAGE="${INSTALL_ROOT}/.速影-Studio.app.installing"
mkdir -p "$INSTALL_ROOT" "$BACKUP_ROOT" "${HOME}/Suying/logs"

prune_accepted_app_backups() {
  BACKUP_ROOT="$BACKUP_ROOT" "$PY" - <<'PY'
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

root = Path(os.environ["BACKUP_ROOT"]).expanduser().resolve()
candidates = []
# 兼容: 速影 Studio.app.20260801T…Z-v0.5.3 · 速影-Studio.app.… · .local-2026…
stamp_pattern = re.compile(r"(\d{8}T\d{6}Z)")

def backup_created_at(path: Path) -> float:
    matched = stamp_pattern.search(path.name)
    if matched:
        return datetime.strptime(
            matched.group(1), "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc).timestamp()
    return path.stat().st_mtime

for pattern in (
    "速影-Studio.app.*",
    "速影 Studio.app.*",  # 本机备份命名（空格）
    "速影.app.*",
    "速影 Studio-*.app",  # 独立整包备份
):
    for path in root.glob(pattern):
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            candidates.append((backup_created_at(path), path))
        except OSError:
            continue
# 同路径只计一次（多 pattern 命中）
uniq: dict[str, tuple[float, Path]] = {}
for created_at, path in candidates:
    key = str(path.resolve())
    prev = uniq.get(key)
    if prev is None or created_at > prev[0]:
        uniq[key] = (created_at, path)
candidates = sorted(uniq.values(), key=lambda item: item[0], reverse=True)
# 默认仅保留最近 1 份大体积 .app 实体（可环境变量 KEEP_APP_BACKUPS 上调）
keep_n = max(1, int(os.environ.get("KEEP_APP_BACKUPS", "1")))
kept_recent = candidates[0][1] if candidates else None
for _modified_at, path in candidates[keep_n:]:
    resolved = path.resolve()
    if resolved.parent != root:
        raise RuntimeError(f"refuse backup cleanup outside root: {resolved}")
    shutil.rmtree(resolved)
    print(f"PRUNED_BACKUP:{resolved}")
if kept_recent:
    print(f"KEPT_LATEST_BACKUP:{kept_recent}")
PY
}

INSTALL_LOCK="${HOME}/Suying/runtime/install/remote-install.lock"
mkdir -p "$(dirname "$INSTALL_LOCK")"
if ! mkdir "$INSTALL_LOCK" 2>/dev/null; then
  old_pid="$(sed -n '1p' "${INSTALL_LOCK}/pid" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    fail "已有速影安装任务运行中（pid ${old_pid}）"
  fi
  rm -rf "$INSTALL_LOCK"
  mkdir "$INSTALL_LOCK" || fail "无法获取安装锁"
fi
printf '%s\n' "$$" > "${INSTALL_LOCK}/pid"
BACKUP_APP=""
INSTALL_SUCCEEDED=0
KEEP_PARTIAL_APP=0
cleanup_install() {
  status=$?
  trap - EXIT
  rm -rf "$INSTALL_LOCK"
  if [[ "$status" -ne 0 && "$INSTALL_SUCCEEDED" != "1" && "$KEEP_PARTIAL_APP" != "1" && -n "$BACKUP_APP" && -d "$BACKUP_APP" ]]; then
    rm -rf "$APP"
    mv "$BACKUP_APP" "$APP" || true
    printf 'ROLLBACK: 已恢复上一版速影 Studio.app\n' >&2
  fi
  exit "$status"
}
trap cleanup_install EXIT

log "安装 App（保留可回滚旧版本）"
rm -rf "$STAGE"
ditto "$APP_SOURCE" "$STAGE"
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null || true
codesign --verify --deep --strict "$STAGE" || fail "App 签名校验失败"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print:CFBundleIdentifier' "${STAGE}/Contents/Info.plist" 2>/dev/null || true)"
[[ "$BUNDLE_ID" == "com.qr.suying" ]] || fail "App Bundle ID 不匹配: ${BUNDLE_ID:-missing}"

PY="${STAGE}/Contents/Resources/runtime/python/bin/python3"
STUDIO="${STAGE}/Contents/Resources/runtime/studio"
[[ -x "$PY" && -d "$STUDIO/engine" ]] || fail "一体包缺少内嵌 Python 或引擎"
if [[ -f "${STAGE}/Contents/Resources/runtime/python/pyvenv.cfg" ]] &&
   grep -Eq '^home = /(Users|opt)/' "${STAGE}/Contents/Resources/runtime/python/pyvenv.cfg"; then
  fail "内嵌 Python 仍绑定打包机；必须使用 standalone CPython 重打包"
fi
PYTHONDONTWRITEBYTECODE=1 "$PY" -c 'import encodings,fastapi,uvicorn,edge_tts,numpy; print("standalone python ok")' ||
  fail "内嵌 Python 不可迁移"
BUNDLE_VERSION="$(cat "${STAGE}/Contents/Resources/runtime/BUNDLE_VERSION" 2>/dev/null || true)"
BUNDLE_ARCH="$(cat "${STAGE}/Contents/Resources/runtime/BUNDLE_ARCH" 2>/dev/null || true)"
BUNDLE_FLAVOR="$(cat "${STAGE}/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true)"
[[ "$BUNDLE_FLAVOR" == "core" || "$BUNDLE_FLAVOR" == "clone" ]] ||
  fail "runtime 缺少有效 BUNDLE_FLAVOR，必须按 core/clone 新合同重打产品包"
APP_VERSION="$(/usr/libexec/PlistBuddy -c 'Print:CFBundleShortVersionString' "${STAGE}/Contents/Info.plist" 2>/dev/null || true)"
PY_ARCH="$("$PY" -c 'import platform; print(platform.machine())')"
[[ -n "$BUNDLE_VERSION" && "$BUNDLE_VERSION" == "$APP_VERSION" ]] ||
  fail "runtime/App 版本不匹配，必须完整重打产品包"
[[ -n "$BUNDLE_ARCH" && "$BUNDLE_ARCH" == "$(uname -m)" && "$PY_ARCH" == "$(uname -m)" ]] ||
  fail "runtime/Python 架构不匹配，必须为客户机架构完整重打产品包"
EXPECTED_SOURCE_SHA="$(cat "${STAGE}/Contents/Resources/runtime/RUNTIME_SOURCE_SHA256" 2>/dev/null || true)"
ACTUAL_SOURCE_SHA="$(
  STUDIO="$STUDIO" "$PY" - <<'PY'
import hashlib
import os
from pathlib import Path
root = Path(os.environ["STUDIO"])
digest = hashlib.sha256()
paths = [root / "requirements.txt", root / "pyproject.toml"]
for tree in (root / "engine", root / "scripts"):
    paths.extend(path for path in tree.rglob("*") if path.is_file())
for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root)
    if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
        continue
    digest.update(relative.as_posix().encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
)"
[[ -n "$EXPECTED_SOURCE_SHA" && "$EXPECTED_SOURCE_SHA" == "$ACTUAL_SOURCE_SHA" ]] ||
  fail "内嵌 runtime 源码/requirements 指纹不匹配，必须重新 embed"
"$PY" -m pip check || fail "内嵌 Python 依赖不完整"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STUDIO" RUNTIME_ROOT="${STAGE}/Contents/Resources/runtime" "$PY" -c \
  'import os; from pathlib import Path; from engine.security.runtime_integrity import verify_runtime; verify_runtime(Path(os.environ["RUNTIME_ROOT"]))' ||
  fail "签名 runtime manifest 校验失败"
PYTHONDWRITEBYTECODE=1 PYTHONPATH="$STUDIO" "$PY" -c 'import engine.main' || fail "内嵌引擎模块不可导入"

# Prefer Keychain when readable; otherwise seed/use on-disk binding cache from
# the already-installed signed license (SSH upgrades cannot rely on Keychain).
DEVICE_SECRET=""
INSTALLED_LICENSE="${HOME}/Suying/runtime/security/license.suying-license"
if [[ -n "$LICENSE_FILE" || -f "$INSTALLED_LICENSE" ]]; then
  if [[ -d "$APP" ]]; then
    log "尝试从 Keychain 读取设备密钥（失败则回退绑定缓存）"
    open "$APP" >/dev/null 2>&1 || true
    sleep 3
    DEVICE_SECRET="$(
      osascript -e 'do shell script "security find-generic-password -s com.qr.suying.license -a device-binding-secret-v1 -w"' 2>/dev/null
    )" || DEVICE_SECRET=""
  fi
  if [[ -z "$DEVICE_SECRET" && -f "$INSTALLED_LICENSE" ]]; then
    log "Keychain 不可读，使用已安装许可证初始化本机绑定缓存"
    PYTHONPATH="$STUDIO" "$PY" - <<'PY'
from pathlib import Path
from engine.security.license import bootstrap_device_binding_cache_from_license
print(bootstrap_device_binding_cache_from_license(
    Path.home() / "Suying/runtime/security/license.suying-license"
))
PY
  fi
fi

if [[ -d "$APP" ]]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_APP="${BACKUP_ROOT}/速影-Studio.app.${stamp}"
  [[ ! -e "$BACKUP_APP" ]] || BACKUP_APP="${BACKUP_APP}.$$"
  mv "$APP" "$BACKUP_APP"
elif [[ -d "${INSTALL_ROOT}/速影.app" ]]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_APP="${BACKUP_ROOT}/速影-Studio.app.${stamp}"
  [[ ! -e "$BACKUP_APP" ]] || BACKUP_APP="${BACKUP_APP}.$$"
  mv "${INSTALL_ROOT}/速影.app" "$BACKUP_APP"
fi
mv "$STAGE" "$APP"
rm -rf "${HOME}/Desktop/速影 Studio.app" "${HOME}/Desktop/速影.app"
ln -s "$APP" "${HOME}/Desktop/速影 Studio.app"

if [[ -w /Applications ]]; then
  rm -rf "/Applications/速影 Studio.app" "/Applications/速影.app"
  ln -s "$APP" "/Applications/速影 Studio.app"
fi

# F5 Runtime Kit: machine-local overlay ONLY — never write into App / never rm kit on App replace
if [[ -n "$F5_KIT" ]]; then
  [[ -f "$F5_KIT" ]] || fail "F5 Kit 不存在: $F5_KIT"
  installer="${F5_KIT_INSTALLER:-}"
  if [[ -z "$installer" || ! -f "$installer" ]]; then
    installer="$(cd "$(dirname "$0")" && pwd)/install-f5-runtime-kit.sh"
  fi
  [[ -f "$installer" ]] || fail "缺少 install-f5-runtime-kit.sh"
  log "安装 F5 Runtime Kit（App 外 ~/Suying/runtime）"
  chmod +x "$installer" 2>/dev/null || true
  bash "$installer" --app "$APP" "$F5_KIT" || fail "F5 Runtime Kit 安装失败"
fi
if [[ -n "$F5_WEIGHTS" ]]; then
  [[ -d "$F5_WEIGHTS" ]] || fail "F5 权重目录不存在: $F5_WEIGHTS"
  log "导入 F5 HF 权重"
  STUDIO="${APP}/Contents/Resources/runtime/studio"
  PY="${APP}/Contents/Resources/runtime/python/bin/python3"
  if [[ -f "${STUDIO}/scripts/materialize_clone_tts_cache.py" ]]; then
    PYTHONPATH="$STUDIO" "$PY" "${STUDIO}/scripts/materialize_clone_tts_cache.py" \
      --import-from "$F5_WEIGHTS" || fail "F5 权重 import 失败"
  else
    # kit 可能未 embed 该脚本；用 tar 内容直接 rsync 到 hub
    HUB="${HOME}/.cache/huggingface/hub"
    mkdir -p "$HUB"
    rsync -a "${F5_WEIGHTS}/" "${HUB}/"
  fi
fi

PY="${APP}/Contents/Resources/runtime/python/bin/python3"
STUDIO="${APP}/Contents/Resources/runtime/studio"
LICENSE_TOOL="${APP}/Contents/Resources/runtime/studio/scripts/device_license.py"
LICENSE_REQUEST="${HOME}/Desktop/速影-许可请求.json"
if [[ -n "$LICENSE_FILE" ]]; then
  [[ -f "$LICENSE_FILE" ]] || fail "许可证文件不存在: $LICENSE_FILE"
  if [[ -n "$DEVICE_SECRET" ]]; then
    SUYING_DEVICE_SECRET_B64="$DEVICE_SECRET" \
      env HOME="$HOME" PYTHONPATH="$STUDIO" \
      "$PY" "$LICENSE_TOOL" install --license "$LICENSE_FILE" ||
      fail "许可证验签或本机绑定校验失败"
  else
    SUYING_ALLOW_CACHED_LICENSE_BINDING=1 \
      env HOME="$HOME" PYTHONPATH="$STUDIO" \
      "$PY" -c 'from pathlib import Path; from engine.security.license import install_license_file; install_license_file(Path("'"$LICENSE_FILE"'").expanduser(), allow_cached_device_binding=True); print("license installed via cache binding")' ||
      fail "许可证验签或本机绑定校验失败"
  fi
elif [[ -f "$INSTALLED_LICENSE" ]]; then
  log "升级保留已安装许可证"
  if [[ -n "$DEVICE_SECRET" ]]; then
    SUYING_DEVICE_SECRET_B64="$DEVICE_SECRET" \
      env HOME="$HOME" PYTHONPATH="$STUDIO" \
      "$PY" -c 'from pathlib import Path; from engine.security.license import verify_license_file; verify_license_file(Path.home() / "Suying/runtime/security/license.suying-license"); print("license ok")' ||
      fail "现有许可证无法验签"
  else
    SUYING_ALLOW_CACHED_LICENSE_BINDING=1 \
      env HOME="$HOME" PYTHONPATH="$STUDIO" \
      "$PY" -c 'from pathlib import Path; from engine.security.license import verify_license_file; verify_license_file(Path.home() / "Suying/runtime/security/license.suying-license", allow_cached_device_binding=True); print("license ok via cache")' ||
      fail "现有许可证无法验签"
  fi
else
  KEEP_PARTIAL_APP=1
  printf 'PARTIAL:LICENSE_REQUIRED request_hint=open-app-then-rerun\n'
  printf '请先在客户机桌面打开速影以解锁 Keychain，再重跑远程安装生成许可请求\n' >&2
  exit 20
fi

log "检查本机依赖"
if [[ -n "$OFFLINE_TOOLS" ]]; then
  TOOL_BIN="${HOME}/Suying/runtime/tools/bin"
  FFMPEG_HOME="${HOME}/Suying/runtime/tools/ffmpeg"
  [[ -f "${OFFLINE_TOOLS}/ollama/ollama" ]] || fail "离线工具缺失: ollama"
  [[ -x "${OFFLINE_TOOLS}/ffmpeg/bin/ffmpeg" && -x "${OFFLINE_TOOLS}/ffmpeg/bin/ffprobe" ]] ||
    fail "离线工具缺失: relocatable ffmpeg/ffprobe"
  mkdir -p "$TOOL_BIN"
  # 禁止 cp 覆盖运行中的 Ollama：临时校验 → 停服 → rename 原子替换 → 功能探针/回滚
  ATOMIC_OLLAMA="${STUDIO:-}/scripts/install-ollama-atomic.sh"
  if [[ ! -x "$ATOMIC_OLLAMA" ]]; then
    ATOMIC_OLLAMA="$(cd "$(dirname "$0")" && pwd)/install-ollama-atomic.sh"
  fi
  [[ -x "$ATOMIC_OLLAMA" ]] || fail "缺少原子安装脚本: install-ollama-atomic.sh"
  log "原子安装受管 Ollama（签名校验 + 停服替换）"
  bash "$ATOMIC_OLLAMA" "${OFFLINE_TOOLS}/ollama/ollama" || fail "受管 Ollama 原子安装失败"
  rm -rf "$FFMPEG_HOME"
  ditto "${OFFLINE_TOOLS}/ffmpeg" "$FFMPEG_HOME"
  export PATH="${TOOL_BIN}:${FFMPEG_HOME}/bin:${PATH}"
fi
if [[ -n "$OFFLINE_TOOLS" && "$INSTALL_DEPS" == "1" ]]; then
  log "已提供 --offline-tools，忽略兼容路径 --install-deps"
  INSTALL_DEPS=0
fi
if ! command -v brew >/dev/null 2>&1 && [[ "$INSTALL_DEPS" == "1" ]]; then
  fail "缺少 Homebrew；正式交付请改用 --offline-tools，或先人工安装 https://brew.sh"
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  if [[ "$INSTALL_DEPS" != "1" ]]; then
    fail "缺少 FFmpeg；正式路径请传 --offline-tools（勿依赖 brew）"
  fi
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 brew install ffmpeg
fi
ffmpeg -version | sed -n '1p'

if ! curl -fsS --max-time 3 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  if ! command -v ollama >/dev/null 2>&1 && [[ ! -x "${HOME}/Suying/runtime/tools/bin/ollama" ]]; then
    if [[ "$INSTALL_DEPS" != "1" ]]; then
      fail "缺少 Ollama；正式路径请传 --offline-tools（勿依赖 brew）"
    fi
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 brew install --cask ollama
  fi
  INSTALL_OLLAMA_AGENT="${STUDIO}/scripts/install-ollama-agent.sh"
  if [[ -x "${HOME}/Suying/runtime/tools/bin/ollama" && -f "$INSTALL_OLLAMA_AGENT" ]]; then
    log "安装受管 Ollama LaunchAgent (com.qr.suying.ollama)"
    bash "$INSTALL_OLLAMA_AGENT" install || fail "受管 Ollama LaunchAgent 安装失败"
  elif [[ -n "$OFFLINE_TOOLS" ]]; then
    nohup "${HOME}/Suying/runtime/tools/bin/ollama" serve >"${HOME}/Suying/logs/ollama.log" 2>&1 < /dev/null &
  else
    open -a Ollama >/dev/null 2>&1 || true
  fi
  for _ in $(seq 1 60); do
    curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi
# 外部 Ollama 占用 11434 时 fail-closed
if ! python3 - <<'PY' 2>/dev/null; then
import json, subprocess, os, sys
port=11434
try:
    pid=int(subprocess.check_output(["lsof","-t","-nP","-iTCP:%d"%port,"-sTCP:LISTEN"],text=True).split()[0])
except Exception:
    sys.exit(0)
cmd=subprocess.check_output(["ps","-p",str(pid),"-o","command="],text=True).strip()
managed=os.path.expanduser("~/Suying/runtime/tools/bin/ollama")
managed_real=os.path.realpath(managed) if os.path.exists(managed) else managed
exe=""
try:
    out=subprocess.check_output(["lsof","-p",str(pid),"-Fn"],text=True,stderr=subprocess.DEVNULL)
    for line in out.splitlines():
        if line.startswith("n") and line[1:].rstrip("/").endswith("ollama"):
            exe=os.path.realpath(line[1:]); break
except Exception:
    pass
if exe and exe == managed_real:
    sys.exit(0)
if managed in cmd or "Suying/runtime/tools/bin/ollama" in cmd or managed_real in cmd:
    sys.exit(0)
if "ollama" in cmd.lower():
    print(f"EXTERNAL_OLLAMA pid={pid} cmd={cmd} exe={exe}")
    sys.exit(1)
PY
  fail "11434 被外部 Ollama 占用；请关闭外部 Ollama 后重试（速影不会误杀）"
fi
curl -fsS --max-time 3 "http://127.0.0.1:11434/api/tags" >/dev/null ||
  fail "Ollama 未启动；请检查 ${HOME}/Suying/logs/ollama.log"

log "启动内嵌引擎"
old_pids="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
for old_pid in $old_pids; do
  old_command="$(ps -p "$old_pid" -o command= 2>/dev/null || true)"
  if [[ "$old_command" != *"engine.main"* && "$old_command" != *"/速影 Studio.app/"* && "$old_command" != *"/速影.app/"* ]]; then
    fail "端口 ${PORT} 被非速影进程占用（pid ${old_pid}），拒绝误杀"
  fi
  kill "$old_pid" 2>/dev/null || true
done
sleep 1
PY="${APP}/Contents/Resources/runtime/python/bin/python3"
STUDIO="${APP}/Contents/Resources/runtime/studio"
ENGINE_LOG="${HOME}/Suying/logs/remote-install-engine.log"
mkdir -p "$(dirname "$ENGINE_LOG")"
# Prefer launching the GUI App so Rust/Python license gates see Keychain.
# Fall back to console-user engine.main if health does not come up.
LOCAL_ENV="${HOME}/Suying/runtime/local.env"
mkdir -p "$(dirname "$LOCAL_ENV")"
touch "$LOCAL_ENV"
if ! grep -q '^SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=' "$LOCAL_ENV" 2>/dev/null; then
  echo 'SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=1' >>"$LOCAL_ENV"
  log "已写入 local.env：部署期跳过工作区 Chrome 跨卷 merge（避免 health 门禁超时）"
fi
open "$APP" >/dev/null 2>&1 || true
API="http://127.0.0.1:${PORT}"
engine_ready=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 2 "${API}/health" >/dev/null 2>&1; then
    engine_ready=1
    break
  fi
  sleep 0.5
done
if [[ "$engine_ready" != "1" ]]; then
  log "App 未拉起引擎，改用图形会话启动内嵌引擎"
  engine_start_script="$(mktemp "${TMPDIR:-/tmp}/suying-engine-start.XXXXXX.sh")"
  cat >"$engine_start_script" <<EOF
#!/bin/bash
set -euo pipefail
cd $(printf '%q' "$STUDIO")
export HOME=$(printf '%q' "$HOME")
export PYTHONPATH=$(printf '%q' "$STUDIO")
export PYTHONDONTWRITEBYTECODE=1
export PATH=$(printf '%q' "$PATH")
export MONTAGE_PORT=$(printf '%q' "$PORT")
export SUYING_ALLOW_CACHED_LICENSE_BINDING=1
# Deploy health gate must not wait on ExFAT→APFS chrome-profile merge.
export SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=1
if [[ -n "${DEVICE_SECRET}" ]]; then
  export SUYING_DEVICE_SECRET_B64=$(printf '%q' "$DEVICE_SECRET")
fi
nohup $(printf '%q' "$PY") -m engine.main >$(printf '%q' "$ENGINE_LOG") 2>&1 < /dev/null &
echo \$! > $(printf '%q' "${HOME}/Suying/engine.pid")
EOF
  chmod 700 "$engine_start_script"
  /bin/bash "$engine_start_script"
  rm -f "$engine_start_script"
fi
API="http://127.0.0.1:${PORT}"
# ~90s: clone/torch cold start + DB init; still fail-closed if stuck.
for _ in $(seq 1 180); do
  curl -fsS --max-time 2 "${API}/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS --max-time 5 "${API}/health" >/dev/null || {
  tail -80 "$ENGINE_LOG" >&2 || true
  fail "引擎 health 未就绪"
}

log "批准并按 plan_id 应用本机安装计划"
PREVIEW_PLAN_RESULT="$(curl -fsS "${API}/setup/install-plan?include_narration=false")"
PREVIEW_PLAN_ID="$(
  PREVIEW_PLAN_RESULT="$PREVIEW_PLAN_RESULT" "$PY" -c \
    'import json,os; print(json.loads(os.environ["PREVIEW_PLAN_RESULT"])["plan_id"])'
)"
APPROVED_PLAN_RESULT="$(
  curl -fsS -X POST "${API}/setup/install-plan" \
    -H 'Content-Type: application/json' \
    -d "{\"plan_id\":\"${PREVIEW_PLAN_ID}\",\"include_narration\":false}"
)"
PLAN_ID="$(
  APPROVED_PLAN_RESULT="$APPROVED_PLAN_RESULT" "$PY" -c \
    'import json,os; print(json.loads(os.environ["APPROVED_PLAN_RESULT"])["plan_id"])'
)"
INSTALL_PLAN_RESULT="$(
  curl -fsS -X POST "${API}/setup/install-plan/configure" \
    -H 'Content-Type: application/json' \
    -d "{\"plan_id\":\"${PLAN_ID}\"}"
)"
INSTALL_PLAN_PATH="${HOME}/Suying/runtime/install/install-plan.json"
MODEL_LINES="$(
  INSTALL_PLAN_RESULT="$INSTALL_PLAN_RESULT" INSTALL_PLAN_PATH="$INSTALL_PLAN_PATH" "$PY" - <<'PY'
import json
import os
from pathlib import Path

result = json.loads(os.environ["INSTALL_PLAN_RESULT"])
plan = result["plan"]
target = Path(os.environ["INSTALL_PLAN_PATH"])
target.parent.mkdir(parents=True, exist_ok=True)
temp = target.with_suffix(".json.tmp")
temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
temp.replace(target)
target.chmod(0o600)
for model in plan.get("selected_models") or []:
    if model:
        print(model)
PY
)"
PROFILE_ID="$(INSTALL_PLAN_PATH="$INSTALL_PLAN_PATH" "$PY" -c 'import json,os,pathlib; print(json.loads(pathlib.Path(os.environ["INSTALL_PLAN_PATH"]).read_text())["profile"]["id"])')"
model_installed() {
  expected="$1"
  EXPECTED_MODEL="$expected" curl -fsS --max-time 5 "http://127.0.0.1:11434/api/tags" | \
    EXPECTED_MODEL="$expected" "$PY" -c '
import json, os, sys
expected = os.environ["EXPECTED_MODEL"]
def canonical(value):
    value = str(value or "")
    leaf = value.rsplit("/", 1)[-1]
    return value if ":" in leaf else value + ":latest"
names = {canonical(item.get("name") or item.get("model")) for item in json.load(sys.stdin).get("models", [])}
raise SystemExit(0 if canonical(expected) in names else 1)
'
}

if [[ -n "$MODEL_BUNDLE" ]]; then
  [[ -d "$MODEL_BUNDLE" && -f "${MODEL_BUNDLE}/bundle.json" ]] || fail "离线模型套件不存在: $MODEL_BUNDLE"
  [[ -f "$MODEL_BUNDLE_TOOL" ]] || fail "离线模型导入工具不存在: $MODEL_BUNDLE_TOOL"
  log "校验并原子导入 ${PROFILE_ID} 离线模型套件"
  "$PY" "$MODEL_BUNDLE_TOOL" import \
    --bundle "$MODEL_BUNDLE" \
    --models-dir "${OLLAMA_MODELS:-${HOME}/.ollama/models}" \
    --expected-profile "$PROFILE_ID" \
    --expected-arch "$(uname -m)" >/dev/null
  MODEL_SOURCE="offline_bundle"
fi

log "检查安装计划选中的 Ollama 模型"
while IFS= read -r model; do
  [[ -n "$model" ]] || continue
  if ! model_installed "$model"; then
    if [[ "$ALLOW_ONLINE_MODEL_PULL" != "1" ]]; then
      fail "缺少 Ollama 模型 ${model}；默认禁止客户机公网下载，请传入对应离线套件"
    fi
    log "显式在线兜底拉取 ${model}"
    ollama pull "$model"
    MODEL_SOURCE="online_fallback"
    model_installed "$model" || fail "Ollama 模型安装后仍不可见: ${model}"
  fi
done <<< "$MODEL_LINES"

log "创建并激活客户"
CUSTOMER_NAME="$CUSTOMER_NAME" API="$API" "$PY" - <<'PY'
import json
import os
import urllib.error
import urllib.request

api = os.environ["API"]
name = os.environ["CUSTOMER_NAME"]

def request(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        api + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc

ensured = request("POST", "/ops/zspace-sync/ensure-customer", {"name": name, "register": True})
p = ensured["paths"]
customers = request("GET", "/customers")
customer = next((item for item in customers if item["name"] == name), None)
payload = {
    "name": name,
    "library_root": p["library_root"],
    "output_root": p["output_root"],
    "keyword_pack_path": p.get("keyword_pack_path") or None,
}
if customer is None:
    customer = request("POST", "/customers", payload)
# 覆盖安装必须保留已存在客户的权威片库/成片路径。ensure-customer 返回的是
# 首装占位目录，不能用它覆盖已经绑定的外置盘或同步目录。
request("POST", "/customers/activate", {"name": name})
request("PUT", "/settings", {"onboarded": True, "active_customer": name})
print(json.dumps({"customer_id": customer["id"], "paths": p}, ensure_ascii=False))
PY

CUSTOMER_ROOT="${HOME}/Suying/sync/速影客户/${CUSTOMER_NAME}"
if [[ -n "$CUSTOMER_CONFIG" ]]; then
  [[ -d "$CUSTOMER_CONFIG" ]] || fail "客户配置目录不存在: $CUSTOMER_CONFIG"
  log "导入正式客户配置"
  if [[ -f "${CUSTOMER_CONFIG}/keyword-pack.json" ]]; then
    mkdir -p "${CUSTOMER_ROOT}/03-词池"
    cp -f "${CUSTOMER_CONFIG}/keyword-pack.json" "${CUSTOMER_ROOT}/03-词池/keyword-pack.json"
  fi
  if [[ -d "${CUSTOMER_CONFIG}/brand" ]]; then
    mkdir -p "${CUSTOMER_ROOT}/05-品牌"
    rsync -a --exclude '*.mp4' --exclude '*.mov' --exclude '.env*' \
      "${CUSTOMER_CONFIG}/brand/" "${CUSTOMER_ROOT}/05-品牌/"
  fi
  if [[ -f "${CUSTOMER_CONFIG}/profile.sample.json" ]]; then
    cp -f "${CUSTOMER_CONFIG}/profile.sample.json" "${CUSTOMER_ROOT}/profile.sample.json"
  fi
fi

log "重载词池并绑定当前极空间"
CUSTOMER_NAME="$CUSTOMER_NAME" CUSTOMER_ROOT="$CUSTOMER_ROOT" API="$API" \
  BIND_ZSPACE="$BIND_ZSPACE" "$PY" - <<'PY'
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

api = os.environ["API"]
name = os.environ["CUSTOMER_NAME"]
root = Path(os.environ["CUSTOMER_ROOT"])

def request(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        api + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}

customers = request("GET", "/customers")
customer = next(item for item in customers if item["name"] == name)
imported_pack = root / "03-词池" / "keyword-pack.json"
current_pack = Path(str(customer.get("keyword_pack_path") or "")).expanduser()
pack = current_pack if current_pack.is_file() else imported_pack
# 覆盖安装禁止用 profile.sample.json 整表替换 DB profile_json（会冲掉 reach/Chrome 绑定）。
# Chrome 登录态在工作区 chrome-profiles/，安装脚本不得读写或删除该树。
patch = {}
if not current_pack.is_file() and imported_pack.is_file():
    patch["keyword_pack_path"] = str(pack)
if patch:
    request("PATCH", f"/customers/{customer['id']}", patch)
if pack.is_file():
    try:
        request("POST", f"/keywords/reload-from-path?customer_id={customer['id']}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Overlay upgrades: keep DB authority when disk/config pack conflicts.
        if exc.code == 409:
            print(f"skip keyword reload (keep current pack): {detail}")
        else:
            raise RuntimeError(f"POST /keywords/reload-from-path: {exc.code} {detail}") from exc

if os.environ["BIND_ZSPACE"] == "1":
    vuex = Path.home() / "Library/Application Support/zspace/vuex.json"
    if not vuex.is_file():
        raise RuntimeError("NEED_HUMAN: 极空间客户端未登录")
    state = (json.loads(vuex.read_text(encoding="utf-8")).get("state") or {})
    user = state.get("user") or {}
    nas = state.get("nas") or {}
    body = {
        "username": str(user.get("username") or "").strip(),
        "nas_id": str(nas.get("nasId") or "").strip(),
        "nas_name": str(nas.get("nasName") or "").strip(),
    }
    if not body["username"] or not body["nas_id"] or not user.get("token"):
        raise RuntimeError("NEED_HUMAN: 极空间账号或设备未处于登录状态")
    request("POST", "/ops/zspace-sync/bind", body)
print(json.dumps(request("GET", "/keywords/active-summary"), ensure_ascii=False))
PY

if [[ "$BIND_ZSPACE" == "1" ]]; then
  SYNC_ARGS=(
    --nas-user "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["user"]["username"])'
    )"
    --nas-id "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["nas"]["nasId"])'
    )"
    --nas-name "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["nas"].get("nasName") or "")'
    )"
  )
  if [[ -n "$UPDATE_REMOTE_ROOT" ]]; then
    SYNC_ARGS+=(--update-remote-root "$UPDATE_REMOTE_ROOT")
  fi
  bash "${APP}/Contents/Resources/runtime/studio/scripts/install-sync-service.sh" "${SYNC_ARGS[@]}"
  curl -fsS -X POST "${API}/ops/carrier/install-update-agent" >/dev/null
fi

log "执行部署门禁"
HEALTH_JSON="$(curl -fsS "${API}/health")"
HEALTH_JSON="$HEALTH_JSON" CUSTOMER_NAME="$CUSTOMER_NAME" "$PY" - <<'PY'
import json
import os
d = json.loads(os.environ["HEALTH_JSON"])
assert d.get("status") == "ok", d
host = ((d.get("ollama") or {}).get("host") or {})
assert host.get("ffmpeg_ok") is True, host
assert host.get("ollama_cli") is True, host
assert (d.get("ollama") or {}).get("ready") is True, d.get("ollama")
assert d.get("active_customer") == os.environ.get("CUSTOMER_NAME", d.get("active_customer"))
print("health gate ok")
PY

# Clone TTS gate: if VIDEO_LOCK pins provider=clone, runtime must expose clone_available.
CLONE_GATE_JSON="$(curl -fsS "${API}/jobs/pipeline" || true)"
CLONE_GATE_JSON="$CLONE_GATE_JSON" STUDIO="$STUDIO" PYTHONPATH="$STUDIO" "$PY" - <<'PY'
import json
import os
import sys
from pathlib import Path

studio = Path(os.environ["STUDIO"])
sys.path.insert(0, str(studio))
from engine.pack.voice_clone import (  # noqa: E402
    active_customer_requires_clone,
    assert_clone_runtime_ready,
    clone_runtime_status,
)

pipe = {}
raw = (os.environ.get("CLONE_GATE_JSON") or "").strip()
if raw:
    try:
        pipe = json.loads(raw)
    except json.JSONDecodeError:
        pipe = {}
api_clone = pipe.get("clone_runtime") if isinstance(pipe, dict) else None
status = api_clone if isinstance(api_clone, dict) and api_clone else clone_runtime_status()
if active_customer_requires_clone():
    assert_clone_runtime_ready()
    if not status.get("ok_for_clone_lock", status.get("clone_available")):
        raise SystemExit(f"clone gate failed: {status}")
    src = status.get("f5_source")
    if src == "missing":
        raise SystemExit("clone gate: f5_source missing — install F5 Runtime Kit (App-external)")
    print("clone gate ok", {k: status.get(k) for k in (
        "clone_available", "f5_source", "f5_kit_rev", "f5_kit_compat_ok", "weights_ready"
    )})
else:
    print("clone gate skipped (VIDEO_LOCK not clone)", status.get("clone_available"))
PY

CORS_HEADERS="$(
  curl -si -X OPTIONS "${API}/jobs" \
    -H 'Origin: http://tauri.localhost' \
    -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: content-type'
)"
printf '%s\n' "$CORS_HEADERS" | tr -d '\r' |
  grep -Eqi '^access-control-allow-origin: http://tauri\.localhost$' ||
  fail "Tauri CORS 预检未放行"

SMOKE_RESULT="SMOKE_NOT_REQUESTED"
if [[ "$SMOKE_JOB" == "1" ]]; then
  receipt_path="${HOME}/Suying/runtime/install/install-receipt.json"
  smoke_done="$(
    INSTALL_PLAN_PATH="$INSTALL_PLAN_PATH" RECEIPT_PATH="$receipt_path" CUSTOMER_NAME="$CUSTOMER_NAME" \
      "$PY" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

plan = json.loads(Path(os.environ["INSTALL_PLAN_PATH"]).read_text(encoding="utf-8"))
receipt_path = Path(os.environ["RECEIPT_PATH"])
if not receipt_path.is_file():
    print("0")
else:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    customer_ref = hashlib.sha256(os.environ["CUSTOMER_NAME"].encode("utf-8")).hexdigest()[:12]
    checks = receipt.get("gates") or {}
    same = receipt.get("plan_id") == plan.get("plan_id") and receipt.get("customer_ref") == customer_ref
    print("1" if same and checks.get("smoke_job") in {"passed", "verified_existing"} else "0")
PY
  )"
  if [[ "$smoke_done" == "1" ]]; then
    log "同一安装计划的真实任务冒烟已通过，跳过重复建任务"
    SMOKE_RESULT="verified_existing"
  else
    # 覆盖安装必须以引擎已绑定的权威片库为准；~/Suying/sync/.../01-片库
    # 只是 ensure-customer 首装占位，扫它会误报 NEED_MEDIA。
    MEDIA_ROOT="$(
      HEALTH_JSON="$(curl -fsS "${API}/health")"
      export HEALTH_JSON
      "$PY" -c '
import json, os
from pathlib import Path
health = json.loads(os.environ["HEALTH_JSON"])
root = str((health.get("paths") or {}).get("library_root") or "").strip()
print(root if root and Path(root).is_dir() else "")
'
    )"
    if [[ -z "$MEDIA_ROOT" ]]; then
      MEDIA_ROOT="${CUSTOMER_ROOT}/01-片库"
    fi
    log "冒烟片库根：${MEDIA_ROOT}"
    media_count="$(
    find "${MEDIA_ROOT}" -type f \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.mkv' \) |
      wc -l | tr -d ' '
    )"
    if [[ "$media_count" -gt 0 ]]; then
      JOB_JSON="$(
        curl -fsS -X POST "${API}/jobs" \
          -H 'Origin: http://tauri.localhost' \
          -H 'Content-Type: application/json' \
          -d '{"mode":"count","target_count":1,"template_name":"default-vertical","theme":"default"}'
      )"
      JOB_ID="$(
        JOB_JSON="$JOB_JSON" "$PY" -c \
          'import json,os; d=json.loads(os.environ["JOB_JSON"]); assert d.get("id"), d; print(d["id"])'
      )"
      log "等待真实任务 ${JOB_ID} 完成"
      SMOKE_RESULT=""
      for _ in $(seq 1 120); do
        JOB_STATE="$(
          curl -fsS "${API}/jobs?limit=200" |
            JOB_ID="$JOB_ID" "$PY" -c '
import json, os, sys
job_id = int(os.environ["JOB_ID"])
row = next((item for item in json.load(sys.stdin) if int(item.get("id") or 0) == job_id), None)
if row is None:
    print("missing|0")
else:
    status = row.get("status", "unknown")
    produced = int(row.get("produced_count") or 0)
    print(f"{status}|{produced}")
'
        )"
        IFS='|' read -r JOB_STATUS JOB_PRODUCED <<< "$JOB_STATE"
        if [[ "$JOB_STATUS" == "completed" && "$JOB_PRODUCED" -ge 1 ]]; then
          SMOKE_RESULT="passed"
          break
        fi
        case "$JOB_STATUS" in
          failed|circuit_open|cancelled)
            fail "真实任务 ${JOB_ID} 验收失败：${JOB_STATUS}"
            ;;
        esac
        sleep 10
      done
      [[ "$SMOKE_RESULT" == "passed" ]] ||
        fail "真实任务 ${JOB_ID} 在 20 分钟内未完成，拒绝写成功回执"
    else
      SMOKE_RESULT="NEED_MEDIA"
      log "PARTIAL: NEED_MEDIA（片库为空，未创建真实任务）"
    fi
  fi
fi

if ! verification_complete "$SMOKE_RESULT"; then
  # PARTIAL keeps the usable App/dependencies in place, but is not success.
  KEEP_PARTIAL_APP=1
  restart_desktop_client
  open "$APP" >/dev/null 2>&1 || true
  log "PARTIAL: ${SMOKE_RESULT}；未写安装回执"
  exit 20
fi

log "全部门禁通过，写入脱敏安装回执"
OLLAMA_TAGS_JSON="$(curl -fsS --max-time 5 "http://127.0.0.1:11434/api/tags")"
RECEIPT_HEALTH_JSON="$(curl -fsS --max-time 5 "${API}/health" || true)"
RECEIPT_RUNTIME_JSON="$(curl -fsS --max-time 5 "${API}/ops/runtime-health" || true)"
RECEIPT_PIPELINE_JSON="$(curl -fsS --max-time 5 "${API}/jobs/pipeline" || true)"
RECEIPT_OLLAMA_JSON="$(curl -fsS --max-time 8 "${API}/health/ollama" || true)"
# 磁盘真相：受管二进制 SHA / 签名 / 版本（不依赖引擎进程是否已加载最新代码）
RECEIPT_OLLAMA_DISK_JSON="$(
  "$PY" - <<'PY' 2>/dev/null || true
import json, hashlib, os, subprocess
from pathlib import Path
bin_path = Path.home() / "Suying/runtime/tools/bin/ollama"
sha = None
if bin_path.is_file():
    h = hashlib.sha256()
    with bin_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    sha = h.hexdigest()
sign = subprocess.run(["codesign", "--verify", "--strict", str(bin_path)], capture_output=True, text=True)
ver = ""
try:
    import urllib.request
    ver = urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3).read().decode()
except Exception as e:
    ver = json.dumps({"error": str(e)})
print(json.dumps({
    "managed_binary": str(bin_path),
    "sha256": sha,
    "codesign_ok": sign.returncode == 0,
    "api_version_raw": ver[:300],
}, ensure_ascii=False))
PY
)"
RUNTIME_SHA="$(
  if [[ -f "${APP}/Contents/Resources/runtime/RUNTIME_SOURCE_SHA256" ]]; then
    tr -d '[:space:]' <"${APP}/Contents/Resources/runtime/RUNTIME_SOURCE_SHA256"
  fi
)"
CHROME_ROOT="${HOME}/Library/Application Support/com.qr.suying/chrome-profiles"
INSTALL_PLAN_PATH="$INSTALL_PLAN_PATH" CUSTOMER_NAME="$CUSTOMER_NAME" SMOKE_RESULT="$SMOKE_RESULT" MODEL_SOURCE="$MODEL_SOURCE" \
  OLLAMA_TAGS_JSON="$OLLAMA_TAGS_JSON" RECEIPT_HEALTH_JSON="$RECEIPT_HEALTH_JSON" \
  RECEIPT_RUNTIME_JSON="$RECEIPT_RUNTIME_JSON" RECEIPT_PIPELINE_JSON="$RECEIPT_PIPELINE_JSON" \
  RECEIPT_OLLAMA_JSON="$RECEIPT_OLLAMA_JSON" RECEIPT_OLLAMA_DISK_JSON="$RECEIPT_OLLAMA_DISK_JSON" \
  RUNTIME_SHA="$RUNTIME_SHA" CHROME_ROOT="$CHROME_ROOT" "$PY" - <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

plan_path = Path(os.environ["INSTALL_PLAN_PATH"])
plan = json.loads(plan_path.read_text(encoding="utf-8"))
def canonical(name):
    value = str(name or "")
    leaf = value.rsplit("/", 1)[-1]
    return value if ":" in leaf else value + ":latest"

def _loads(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}

selected = {canonical(name): str(name) for name in plan.get("selected_models") or []}
models = []
for item in json.loads(os.environ["OLLAMA_TAGS_JSON"]).get("models") or []:
    api_name = str(item.get("name") or item.get("model") or "")
    selected_name = selected.get(canonical(api_name))
    if selected_name:
        models.append(
            {
                "name": selected_name,
                "digest": item.get("digest"),
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
                "source": os.environ["MODEL_SOURCE"],
            }
        )
missing = sorted(set(selected.values()) - {item["name"] for item in models})
if missing:
    raise RuntimeError(f"安装回执缺少计划模型: {missing}")
health = _loads(os.environ.get("RECEIPT_HEALTH_JSON", ""))
runtime = _loads(os.environ.get("RECEIPT_RUNTIME_JSON", ""))
pipeline = _loads(os.environ.get("RECEIPT_PIPELINE_JSON", ""))
clone = pipeline.get("clone_runtime") if isinstance(pipeline.get("clone_runtime"), dict) else {}
chrome_root = Path(os.environ.get("CHROME_ROOT") or "").expanduser()
chrome_bytes = 0
if chrome_root.is_dir():
    for p in chrome_root.rglob("*"):
        try:
            if p.is_file():
                chrome_bytes += p.stat().st_size
        except OSError:
            pass
receipt = {
    "schema_version": "suying.install.receipt.v2",
    "plan_id": plan["plan_id"],
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "app_version": plan["app_version"],
    "engine_version": health.get("engine_version"),
    "runtime_source_sha256": (os.environ.get("RUNTIME_SHA") or "").strip() or None,
    "host_fingerprint": (plan.get("host") or {}).get("fingerprint"),
    "profile_id": (plan.get("profile") or {}).get("id"),
    "gate_profile_version": (plan.get("settings_patch") or {}).get("gate_profile_version")
        or ((plan.get("profile") or {}).get("gate_profile") or {}).get("gate_profile_version"),
    "gate_profile": (plan.get("profile") or {}).get("gate_profile"),
    "customer_ref": hashlib.sha256(
        os.environ["CUSTOMER_NAME"].encode("utf-8")
    ).hexdigest()[:12],
    "models": models,
    "model_source": os.environ["MODEL_SOURCE"],
    "ollama": {
        **(_loads(os.environ.get("RECEIPT_OLLAMA_JSON", "")) or {}),
        "disk": _loads(os.environ.get("RECEIPT_OLLAMA_DISK_JSON", "")) or {},
    },
    "runtime": {
        "pause_state": (runtime.get("pause") or {}).get("state") or health.get("runtime_state"),
        "accepts_new_work": runtime.get("accepts_new_work"),
        "health_qps_approx": (runtime.get("health") or {}).get("health_qps_approx"),
        "chrome_profiles_root": str(chrome_root) if chrome_root else None,
        "chrome_profiles_bytes": chrome_bytes or None,
        "clone_ok_for_lock": clone.get("ok_for_clone_lock", clone.get("clone_available")),
    },
    "gates": {
        "health": True,
        "cors": True,
        "customer": True,
        "smoke_job": os.environ["SMOKE_RESULT"],
    },
}
if not receipt.get("gate_profile_version"):
    raise SystemExit("FAIL: install-receipt missing gate_profile_version — refuse success")
target = plan_path.with_name("install-receipt.json")
temp = target.with_suffix(".json.tmp")
target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
temp.chmod(0o600)
temp.replace(target)
print(json.dumps(receipt, ensure_ascii=False))
PY

INSTALL_SUCCEEDED=1
log "完整验收后清理旧 App 备份（仅保留最近 1 个回滚点）"
prune_accepted_app_backups ||
  log "WARNING: 旧 App 备份清理失败；不影响已验收安装，保留现有备份"
restart_desktop_client
open "$APP" >/dev/null 2>&1 || true
log "远程部署完整验收通过"
