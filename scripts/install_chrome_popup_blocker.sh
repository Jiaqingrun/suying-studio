#!/usr/bin/env bash
# 为速影各 Chrome 账号安装并启用「创作台弹窗拦截」扩展
#
# 说明：Chrome 137+ 官方版已禁用 --load-extension，因此：
#   1) 写入 Preferences（开发者模式 + 拦截 window.open 弹窗）
#   2) 打开 chrome://extensions，并打开扩展目录，便于点一次「加载已解压的扩展程序」
#   3) 自动化发布时 CDP 仍会注入同等拦截脚本（即使扩展未加载也生效）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT_SRC="$ROOT/tools/chrome-ext-dismiss-popups"
PROFILES="${SUYING_CHROME_PROFILES:-$HOME/QR-Volume/速影工作区/chrome-profiles}"
SHARED="$PROFILES/_extensions/suying-dismiss-popups"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ONLY_PROFILE="${1:-}"

if [[ ! -f "$EXT_SRC/manifest.json" ]]; then
  echo "缺少扩展源: $EXT_SRC"
  exit 1
fi
if [[ ! -x "$CHROME" ]]; then
  echo "未找到 Google Chrome"
  exit 1
fi

mkdir -p "$SHARED"
rsync -a --delete "$EXT_SRC/" "$SHARED/"
echo "扩展已同步 → $SHARED"

python3 - "$PROFILES" "$SHARED" "$ONLY_PROFILE" <<'PY'
import hashlib, json, sys
from pathlib import Path

profiles_root = Path(sys.argv[1])
ext = Path(sys.argv[2]).resolve()
only = (sys.argv[3] or "").strip()
manifest = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
eid = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(str(ext).encode()).hexdigest()[:32])
print("extension_id", eid)

names = []
if only:
    names = [only]
else:
    for p in sorted(profiles_root.iterdir()):
        if not p.is_dir():
            continue
        n = p.name
        if n.startswith("_") or n.startswith("."):
            continue
        if n in ("accounts.json",):
            continue
        # skip files
        if (p / "Default").exists() or n.endswith(("-01", "-02", "-03")) or any(
            x in n for x in ("抖音", "视频号", "小红书", "快手", "始峰")
        ):
            names.append(n)

for name in names:
    d = profiles_root / name
    if not d.is_dir():
        print("skip missing", name)
        continue
    (d / ".suying_load_extension").write_text(str(ext) + "\n", encoding="utf-8")
    pref = d / "Default" / "Preferences"
    pref.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if pref.exists():
        try:
            data = json.loads(pref.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("extensions", {}).setdefault("ui", {})["developer_mode"] = True
    data.setdefault("browser", {})["allow_javascript_apple_events"] = True
    data.setdefault("profile", {}).setdefault("default_content_setting_values", {})["popups"] = 2
    settings = data.setdefault("extensions", {}).setdefault("settings", {})
    settings[eid] = {
        "account_extension_type": 0,
        "active_permissions": {
            "api": [],
            "explicit_host": list(manifest.get("host_permissions") or []),
            "manifest_permissions": [],
            "scriptable_host": list(manifest.get("host_permissions") or []),
        },
        "commands": {},
        "content_settings": [],
        "creation_flags": 1,
        "disable_reasons": [],
        "from_bookmark": False,
        "from_webstore": False,
        "granted_permissions": {
            "api": [],
            "explicit_host": list(manifest.get("host_permissions") or []),
            "manifest_permissions": [],
            "scriptable_host": list(manifest.get("host_permissions") or []),
        },
        "incognito_content_settings": [],
        "incognito_preferences": {},
        "location": 4,
        "manifest": manifest,
        "path": str(ext),
        "preferences": {},
        "state": 1,
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
        "withholding_permissions": False,
    }
    pref.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("patched", name)
PY

# 关掉占用中的 Chrome，再打开扩展页 + Finder
osascript -e 'tell application "Google Chrome" to quit' >/dev/null 2>&1 || true
sleep 1
killall -9 "Google Chrome" >/dev/null 2>&1 || true
sleep 1

TARGET="${ONLY_PROFILE:-抖音-01}"
UD="$PROFILES/$TARGET"
mkdir -p "$UD"
echo ""
echo "正在打开：$TARGET → chrome://extensions"
echo "请确认右上角「开发者模式」已打开，然后点「加载已解压的扩展程序」"
echo "选择文件夹：$SHARED"
echo ""
open "$SHARED"
"$CHROME" --user-data-dir="$UD" --no-first-run --no-default-browser-check "chrome://extensions" >/dev/null 2>&1 &

echo "已启用："
echo "  • Chrome 内容设置：拦截站点 window.open 弹窗"
echo "  • 扩展目录已打开：加载一次「速影 · 创作台弹窗拦截」即可（四账号可共用同一目录）"
echo "  • 其它账号请运行: $0 视频号-01  （或 小红书-01 / 快手-01）"
echo "  • 自动化发布另有 CDP 注入保底，即使未点加载也会关营销弹窗"
