#!/usr/bin/env bash
# 开发机磁盘安全回收：可重建产物 + 历史 App/离线中间件。
# 不碰：片库、montage.db、Chrome 配置、cache/library、运行中 Offline models 主套件（除非 --aggressive）。
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:${PATH:-}"

STUDIO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
AGGRESSIVE=0
KEEP_APP_BACKUPS=2

usage() {
  cat <<'EOF'
用法: scripts/prune-dev-disk.sh [--dry-run] [--aggressive] [--keep-app-backups N]

默认清理：
  - apps/*/src-tauri/target（Rust 编译缓存，可重建）
  - packaging/cache、pytest/ruff 缓存、_smoke_*
  - ~/Suying/backups/apps 历史 App 实体（保留最近 N 个，默认 2）
  - /Applications 与 ~/Applications 旁的 *.preclose-* / *.bak-* 旧版
  - 离线打包中间件重复树（保留 depot-ready / models / verify-python-pro / ffmpeg-legal）
  - cache/temp 与 cache/frames（不动 cache/library）

--aggressive：额外删除 ~/Suying/offline/速影-offline-*（模型/depot/verify 工作副本；真相源在 T2S「速影/离线交付/」，见 docs/T2S_OFFLINE_DEPLOY.md；本机 Ollama 仍用 ~/.ollama）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --aggressive) AGGRESSIVE=1; shift ;;
    --keep-app-backups)
      KEEP_APP_BACKUPS="${2:?}"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

bytes_of() {
  if [[ -e "$1" ]]; then du -sk "$1" 2>/dev/null | awk '{print $1}'; else echo 0; fi
}

safe_rm() {
  local path="$1" reason="$2"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    return 0
  fi
  local kb size
  kb="$(bytes_of "$path")"
  size="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY [$reason] $size  $path"
  else
    echo "DEL [$reason] $size  $path"
    rm -rf -- "$path"
  fi
  FREED_KB=$((FREED_KB + kb))
}

FREED_KB=0
echo "==> prune-dev-disk  dry_run=${DRY_RUN} aggressive=${AGGRESSIVE} keep_app_backups=${KEEP_APP_BACKUPS}"
AVAIL_BEFORE="$(df -k /System/Volumes/Data | awk 'NR==2{print $4}')"

# --- Rust / 打包中间 ---
safe_rm "${STUDIO_ROOT}/apps/desktop/src-tauri/target" "rust-target"
safe_rm "${STUDIO_ROOT}/apps/desktop-v2/src-tauri/target" "rust-target-v2"
OPS_ROOT="$(cd "${STUDIO_ROOT}/../速影辅助工具" 2>/dev/null && pwd || true)"
if [[ -n "${OPS_ROOT:-}" ]]; then
  safe_rm "${OPS_ROOT}/apps/ops-desktop/src-tauri/target" "rust-target-ops"
fi
safe_rm "${STUDIO_ROOT}/packaging/cache" "packaging-cache"
safe_rm "${STUDIO_ROOT}/.pytest_cache" "pytest-cache"
safe_rm "${STUDIO_ROOT}/.ruff_cache" "ruff-cache"
shopt -s nullglob
for d in "${STUDIO_ROOT}"/_smoke_*; do
  safe_rm "$d" "smoke-scratch"
done

# --- App 实体备份：命名兼容空格/连字符/版本后缀 ---
BACKUP_APPS="${HOME}/Suying/backups/apps"
if [[ -d "$BACKUP_APPS" ]]; then
  KEEP_APP_BACKUPS="$KEEP_APP_BACKUPS" BACKUP_APPS="$BACKUP_APPS" DRY_RUN="$DRY_RUN" python3 - <<'PY'
import os, re, shutil
from pathlib import Path
from datetime import datetime, timezone

root = Path(os.environ["BACKUP_APPS"]).expanduser().resolve()
keep = max(1, int(os.environ["KEEP_APP_BACKUPS"]))
dry = os.environ.get("DRY_RUN") == "1"
stamp = re.compile(r"(\d{8}T\d{6}Z)")
patterns = (
    "速影-Studio.app.*",
    "速影 Studio.app.*",
    "速影.app.*",
    "速影 Studio-*.app",
)

def created_at(p: Path) -> float:
    m = stamp.search(p.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0

uniq: dict[str, tuple[float, Path]] = {}
for pat in patterns:
    for path in root.glob(pat):
        if path.is_symlink() or not path.exists():
            continue
        if path.name.startswith("."):
            continue
        key = str(path.resolve())
        ts = created_at(path)
        prev = uniq.get(key)
        if prev is None or ts > prev[0]:
            uniq[key] = (ts, path)

ordered = sorted(uniq.values(), key=lambda x: x[0], reverse=True)
for _ts, path in ordered[keep:]:
    resolved = path.resolve()
    if resolved.parent != root:
        raise SystemExit(f"refuse outside backup root: {resolved}")
    action = "DRY" if dry else "DEL"
    print(f"{action} [app-backup] {resolved.name}")
    if not dry:
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)
if ordered:
    kept = ", ".join(p.name for _t, p in ordered[:keep])
    print(f"KEEP_APP_BACKUPS({keep}): {kept}")
PY
fi

# 安装目录旁旧版（非当前运行包）
shopt -s nullglob
for d in \
  /Applications/速影\ Studio.app.preclose-* \
  /Applications/速影\ Studio.app.bak* \
  "${HOME}/Applications/速影 Studio.app.preclose-"* \
  "${HOME}/Applications/速影 Studio.app.bak"* \
  "${HOME}/Suying/backups/速影 Studio-"*.app
do
  safe_rm "$d" "sidecar-app-backup"
done

# kits / releases 旧 ZIP 全清（现行发版走 carrier / T2S；本机不得堆历史包）
for d in "${HOME}/Suying/backups/kits" "${HOME}/Suying/backups/releases"; do
  if [[ -d "$d" ]]; then
    for f in "$d"/*; do
      safe_rm "$f" "old-product-zip"
    done
  fi
done

# ~/Suying/releases 仅允许 LAST_PUBLISH.json；其余交付暂存一律清掉
RELEASE_LIVE="${HOME}/Suying/releases"
if [[ -d "$RELEASE_LIVE" ]]; then
  shopt -s nullglob
  for f in "$RELEASE_LIVE"/* "$RELEASE_LIVE"/.[!.]* "$RELEASE_LIVE"/..?*; do
    base="$(basename "$f")"
    [[ "$base" == "LAST_PUBLISH.json" ]] && continue
    [[ "$base" == ".DS_Store" ]] && continue
    safe_rm "$f" "local-release-staging"
  done
  shopt -u nullglob
fi

# --- 离线中间件（部署正式入口保留）---
OFF="${HOME}/Suying/offline"
KEEP_OFF=(
  "速影-offline-depot-arm64-ready"
  "速影-offline-models-macos-arm64"
  "速影-offline-verify-python-pro"
  "速影-ffmpeg-macos-arm64-legal"
  "速影-legal-component"
  "速影-0.7.34-product-macos-arm64-core"
  "速影-0.7.34-product-macos-arm64-core.zip"
  "速影-0.7.34-release.json"
  "OFFLINE_READY.json"
  "OFFLINE_APP_VERSION.txt"
)
if [[ -d "$OFF" ]]; then
  for d in "$OFF"/*; do
    base="$(basename "$d")"
    keep=0
    for k in "${KEEP_OFF[@]}"; do
      if [[ "$base" == "$k" ]]; then keep=1; break; fi
    done
    if [[ "$keep" == "0" ]]; then
      safe_rm "$d" "offline-intermediate"
    fi
  done
fi
if [[ "$AGGRESSIVE" == "1" ]]; then
  safe_rm "${OFF}/速影-offline-models-macos-arm64" "offline-models-aggressive"
fi

# --- 工作缓存白名单（DISK_CLEANUP_LOCK）---
CACHE_ROOT="${HOME}/Movies/速影工作区/cache"
if [[ -d "$CACHE_ROOT" ]]; then
  safe_rm "${CACHE_ROOT}/temp" "work-cache-temp"
  mkdir -p "${CACHE_ROOT}/temp"
  safe_rm "${CACHE_ROOT}/frames" "rebuild-cache-frames"
  mkdir -p "${CACHE_ROOT}/frames"
fi

# App 系统缓存
safe_rm "${HOME}/Library/Caches/com.qr.suying" "app-cache"
safe_rm "${HOME}/Library/Caches/com.qr.suying-ops" "ops-cache"

AVAIL_AFTER="$(df -k /System/Volumes/Data | awk 'NR==2{print $4}')"
python3 - <<PY
freed_kb = ${FREED_KB}
df_delta = int("${AVAIL_AFTER}") - int("${AVAIL_BEFORE}")
print(f"==> listed_delete≈{freed_kb/1024/1024:.1f} GiB  df_avail_delta≈{df_delta/1024/1024:.1f} GiB")
print(f"    free_now: {int('${AVAIL_AFTER}')/1024/1024:.1f} GiB")
PY

# 强约束自检
for must in \
  "${HOME}/Suying/data/montage.db" \
  "${HOME}/Library/Application Support/com.qr.suying/chrome-profiles" \
  "${HOME}/Movies/速影工作区/cache/library"
do
  if [[ ! -e "$must" ]]; then
    echo "WARN: expected still present: $must" >&2
  fi
done
echo "==> done (未改代码仓源文件；下次 cargo/tauri build 会慢首次编译)"
