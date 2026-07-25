#!/usr/bin/env bash
# 一键打开 Chrome 独立用户数据目录 + 抖音创作者中心
# 用法:
#   ./scripts/open_chrome_profile.sh            # 默认 始峰
#   ./scripts/open_chrome_profile.sh 账号01
#   ./scripts/open_chrome_profile.sh list       # 列出本地配置
set -euo pipefail
NAME="${1:-始峰}"
ROOT="${SUYING_CHROME_PROFILES:-$HOME/QR-Volume/速影工作区/chrome-profiles}"
URL="${2:-https://creator.douyin.com/}"

if [[ "$NAME" == "list" || "$NAME" == "-l" || "$NAME" == "--list" ]]; then
  if [[ -f "$ROOT/accounts.txt" ]]; then
    grep -v '^#' "$ROOT/accounts.txt" | grep -v '^$'
  else
    ls -1 "$ROOT"
  fi
  exit 0
fi

DIR="$ROOT/$NAME"
mkdir -p "$DIR"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ ! -x "$CHROME" ]]; then
  echo "未找到 Google Chrome，请先安装到 /Applications"
  exit 1
fi
# 每个账号独立 user-data-dir，登录态互不覆盖（不是 Cookie 池切换）
EXT="${SUYING_CHROME_POPUP_EXT:-$ROOT/_extensions/suying-dismiss-popups}"
ARGS=(--user-data-dir="$DIR" --no-first-run --no-default-browser-check)
if [[ -f "$EXT/manifest.json" ]]; then
  ARGS+=(--load-extension="$EXT")
  echo "已加载弹窗拦截扩展: $EXT"
fi
exec "$CHROME" "${ARGS[@]}" "$URL"
