#!/usr/bin/env bash
# Fetch Tier A OFL fonts into configs/fx_assets/fonts/ (official sources only).
# Fonts are gitignored; run this (or copy OFL files) before packaging clone/core that need them.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/configs/fx_assets/fonts"
LIC="$ROOT/configs/fx_assets/licenses"
mkdir -p "$DEST"
cd "$DEST"

fetch() {
  local url="$1" out="$2"
  if [[ -f "$out" && -s "$out" ]]; then
    echo "skip existing $out"
    return 0
  fi
  echo "fetch $out <- $url"
  if curl -fL --retry 3 --connect-timeout 20 -o "$out.partial" "$url"; then
    mv "$out.partial" "$out"
  else
    rm -f "$out.partial"
    return 1
  fi
}

# Prefer local OFL installs when present (same files as Google Fonts / rsms Inter).
if [[ ! -f Inter-Regular.ttf || ! -s Inter-Regular.ttf ]]; then
  for cand in /Library/Fonts/Inter-Regular.ttf "$HOME/Library/Fonts/Inter-Regular.ttf"; do
    if [[ -f "$cand" ]]; then
      cp -f "$cand" Inter-Regular.ttf
      echo "copied local $cand"
      break
    fi
  done
fi
if [[ ! -f NotoSansSC-Regular.ttf || ! -s NotoSansSC-Regular.ttf ]]; then
  for cand in "$HOME/Library/Fonts/NotoSansSC.ttf" /Library/Fonts/NotoSansSC.ttf; do
    if [[ -f "$cand" ]]; then
      cp -f "$cand" NotoSansSC-Regular.ttf
      echo "copied local $cand"
      break
    fi
  done
fi

fetch "https://raw.githubusercontent.com/google/fonts/main/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf" \
  "ZCOOLKuaiLe-Regular.ttf" || true
fetch "https://github.com/lxgw/LxgwWenKai/releases/download/v1.501/LXGWWenKai-Regular.ttf" \
  "LXGWWenKai-Regular.ttf" || true

if [[ -f "$LIC/OFL-1.1.txt" ]]; then
  cp -f "$LIC/OFL-1.1.txt" "$DEST/OFL.txt"
fi

echo "Bundled fonts now:"
ls -la "$DEST" | head -30
