#!/bin/bash
# 速影极空间同步服务一键安装/卸载。全部路径按当前登录用户的 HOME 生成。
set -euo pipefail

ACTION="install"
NAS_USER=""
NAS_ID=""
NAS_NAME=""
UPDATE_REMOTE_ROOT=""
VOLUME_UUID=""
ENABLE_MEDIA=0
PURGE_CONFIG=0

usage() {
  cat <<'EOF'
用法:
  install-sync-service.sh --nas-user USER --nas-id ID [--nas-name NAME]
  install-sync-service.sh uninstall [--purge-config]

可选:
  --volume-uuid UUID       记录客户外置盘 UUID
  --update-remote-root PATH 发布者个人空间中的签名更新仓库绝对路径
  --enable-media-sync      显式启用媒体同步；默认仅同步 T2S 载体
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    install|uninstall) ACTION="$1"; shift ;;
    --nas-user) NAS_USER="${2:-}"; shift 2 ;;
    --nas-id) NAS_ID="${2:-}"; shift 2 ;;
    --nas-name) NAS_NAME="${2:-}"; shift 2 ;;
    --update-remote-root) UPDATE_REMOTE_ROOT="${2:-}"; shift 2 ;;
    --volume-uuid) VOLUME_UUID="${2:-}"; shift 2 ;;
    --enable-media-sync) ENABLE_MEDIA=1; shift ;;
    --purge-config) PURGE_CONFIG=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

LABEL="com.qr.zspace-team-sync"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ "$ACTION" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST" "${HOME}/QR/tools/zspace-team-sync.py" "${HOME}/QR/tools/zspace-team-sync.sh"
  if [[ "$PURGE_CONFIG" == "1" ]]; then
    rm -f "${HOME}/.qr/suying-sync.json"
  fi
  echo "已卸载当前用户 ${USER:-$(id -un)} 的速影同步服务"
  exit 0
fi

if [[ -z "$NAS_USER" || -z "$NAS_ID" ]]; then
  echo "安装必须提供 --nas-user 与 --nas-id" >&2
  usage
  exit 2
fi
if [[ "$ENABLE_MEDIA" == "1" && -z "$VOLUME_UUID" ]]; then
  echo "--enable-media-sync 必须同时提供 --volume-uuid" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STUDIO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON=""
if [[ -x "${STUDIO_ROOT}/../python/bin/python3" && -d "${STUDIO_ROOT}/engine" ]]; then
  PYTHON="${STUDIO_ROOT}/../python/bin/python3"
elif command -v python3 >/dev/null 2>&1 && [[ -d "${STUDIO_ROOT}/engine" ]]; then
  PYTHON="$(command -v python3)"
else
  for APP in "/Applications/速影.app" "${HOME}/Applications/速影.app"; do
    if [[ -x "${APP}/Contents/Resources/runtime/python/bin/python3" ]]; then
      PYTHON="${APP}/Contents/Resources/runtime/python/bin/python3"
      STUDIO_ROOT="${APP}/Contents/Resources/runtime/studio"
      break
    fi
  done
fi
if [[ -z "$PYTHON" || ! -d "${STUDIO_ROOT}/engine" ]]; then
  echo "未找到速影运行时；请先安装速影.app" >&2
  exit 3
fi

export PYTHONPATH="${STUDIO_ROOT}"
"$PYTHON" - "$NAS_USER" "$NAS_ID" "$NAS_NAME" "$VOLUME_UUID" "$ENABLE_MEDIA" "$UPDATE_REMOTE_ROOT" <<'PY'
import sys
from pathlib import Path

from engine.ops.suying_sync import bind_zspace_account, install_service, load_config, save_config

nas_user, nas_id, nas_name, volume_uuid, media, update_remote_root = sys.argv[1:]
cfg = load_config()
cfg["volume_uuid"] = volume_uuid
cfg["sync_mode"] = "media" if media == "1" else "carrier_only"
cfg["media_sync_enabled"] = media == "1"
# 媒体默认同机 Movies 工作区；外置盘 volume_* 仅在显式 --volume-uuid 时使用。
cfg["volume_relpath"] = "极空间团队文件同步" if volume_uuid else ""
cfg["carrier_mirror"] = str(Path.home() / "Suying" / "carrier")
# Keep carrier mirror on the team/public carrier path. The publisher personal
# update repo is optional and NAS-specific; never overwrite carrier_remote_root
# with it on machines bound to a different nas_id.
if update_remote_root and media != "1":
    cfg["update_remote_root"] = update_remote_root
# Only set carrier_remote_root when explicitly empty and no public relpath works;
# prefer leaving it blank so sync falls back to /public/<carrier_relpath>.
if not str(cfg.get("carrier_remote_root") or "").strip():
    cfg["carrier_remote_root"] = ""
_media = Path.home() / "Movies" / "速影工作区"
cfg["local_root"] = str(_media)
cfg["work_root"] = str(_media)

# v1/v2 back-compat migration:
# When enabling media sync, promote legacy customers[].aliases into v3 media_sources.
# This makes the subsequent zspace-team-sync.py pull scope fail-closed and customer-isolated.
if media == "1":
    media_sources = cfg.get("media_sources") or []
    if not media_sources:
        # Best-effort backup to keep manual recovery possible.
        cfg_path = Path.home() / ".qr" / "suying-sync.json"
        if cfg_path.exists():
            import time

            ts = int(time.time())
            bak = cfg_path.with_suffix(cfg_path.suffix + f".bak.{ts}")
            try:
                bak.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        sources = []
        for c in cfg.get("customers") or []:
            cust_name = str(c.get("name") or "").strip()
            for a in c.get("aliases") or []:
                remote = a.get("remote")
                local = a.get("local")
                if not remote or not local:
                    continue
                sources.append(
                    {
                        "customer_key": cust_name,
                        "display_name": cust_name,
                        "remote_root": str(remote),
                        "local_target": str(local),
                        "pull_only": True,
                    }
                )
        cfg["media_sources"] = sources
        # Mark as v3 so zspace-team-sync.py can rely on media_sources.
        cfg["version"] = 3
save_config(cfg)
bind_zspace_account(username=nas_user, nas_id=nas_id, nas_name=nas_name)
result = install_service()
print(f"安装完成: {result['plist']}")
print(f"配置文件: {result['config']}")
PY
