#!/usr/bin/env bash
# Cloud Agent install: Python engine venv + desktop npm deps.
# Idempotent. Does not start servers (see scripts/cloud-agent-start.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export DEBIAN_FRONTEND=noninteractive

pkgs=()
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  pkgs+=(python3-venv)
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  pkgs+=(ffmpeg)
fi
if ! command -v lsof >/dev/null 2>&1; then
  pkgs+=(lsof)
fi
if ! command -v tmux >/dev/null 2>&1; then
  pkgs+=(tmux)
fi
if ! command -v curl >/dev/null 2>&1; then
  pkgs+=(curl)
fi
if ((${#pkgs[@]})); then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends "${pkgs[@]}"
fi

# jsdom (desktop tests) requires Node >= 22.22.2. /exec-daemon/node can be older
# and appears first on PATH, so expose a new enough node via /usr/local/cargo/bin.
version_ge() {
  printf '%s\n%s\n' "$2" "$1" | sort -V -C
}
node_ver() {
  "$1" -v 2>/dev/null | sed 's/^v//'
}
if ! version_ge "$(node_ver node)" "22.22.2"; then
  picked=""
  shopt -s nullglob
  for candidate in /home/ubuntu/.nvm/versions/node/*/bin/node /usr/local/bin/node; do
    [[ -x "$candidate" ]] || continue
    ver="$(node_ver "$candidate")"
    if version_ge "$ver" "22.22.2"; then
      picked="$candidate"
      break
    fi
  done
  shopt -u nullglob
  if [[ -z "$picked" ]]; then
    NODE_VERSION="22.23.3"
    prefix="/usr/local/lib/nodejs/node-v${NODE_VERSION}-linux-x64"
    if [[ ! -x "$prefix/bin/node" ]]; then
      tmp="$(mktemp)"
      curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o "$tmp"
      sudo mkdir -p /usr/local/lib/nodejs
      sudo tar -xJf "$tmp" -C /usr/local/lib/nodejs
      rm -f "$tmp"
    fi
    picked="$prefix/bin/node"
  fi
  bindir="$(dirname "$picked")"
  sudo ln -sfn "$picked" /usr/local/cargo/bin/node
  sudo ln -sfn "$bindir/npm" /usr/local/cargo/bin/npm
  sudo ln -sfn "$bindir/npx" /usr/local/cargo/bin/npx
  hash -r
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

cd "$ROOT/apps/desktop"
npm ci
