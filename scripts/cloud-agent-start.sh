#!/usr/bin/env bash
# Cloud Agent start: FastAPI engine :8766 and Vite desktop UI :1420.
# Safe to rerun. Logs: /tmp/suying-engine.log /tmp/suying-desktop.log
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "missing $ROOT/.venv — run scripts/cloud-agent-install.sh first" >&2
  exit 1
fi
if [[ ! -d "$ROOT/apps/desktop/node_modules" ]]; then
  echo "missing apps/desktop/node_modules — run scripts/cloud-agent-install.sh first" >&2
  exit 1
fi

port_open() {
  local port="$1"
  curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${port}/" \
    || curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:${port}/health"
}

ensure_session() {
  local name="$1"
  local dir="$2"
  local cmd="$3"
  if tmux has-session -t "$name" 2>/dev/null; then
    tmux kill-session -t "$name" 2>/dev/null || true
  fi
  tmux new-session -d -s "$name" -c "$dir" "$cmd"
}

if ! port_open 8766; then
  ensure_session suying-engine "$ROOT" \
    "exec ./scripts/start-engine.sh >>/tmp/suying-engine.log 2>&1"
fi

NODE_DIR="$(dirname "$(command -v npm)")"
if ! port_open 1420; then
  # Default Vite host is localhost (::1 only). Bind IPv4 so 127.0.0.1 works.
  ensure_session suying-desktop "$ROOT/apps/desktop" \
    "export PATH=${NODE_DIR}:\$PATH; exec npm run dev -- --host 127.0.0.1 --port 1420 >>/tmp/suying-desktop.log 2>&1"
fi

echo "waiting for engine /health and desktop vite ..."
engine_ok=0
desktop_ok=0
for _ in $(seq 1 90); do
  if [[ "$engine_ok" -ne 1 ]]; then
    body="$(curl -sf "http://127.0.0.1:8766/health" 2>/dev/null || true)"
    if [[ -n "$body" ]] && ! grep -q '"status":"starting"' <<<"$body" && ! grep -q '"status": "starting"' <<<"$body"; then
      engine_ok=1
      echo "engine health: $(printf '%s' "$body" | head -c 240)"
    fi
  fi
  if [[ "$desktop_ok" -ne 1 ]]; then
    if curl -sf -o /dev/null "http://127.0.0.1:1420/"; then
      desktop_ok=1
      echo "desktop vite: http://127.0.0.1:1420/"
    fi
  fi
  if [[ "$engine_ok" -eq 1 && "$desktop_ok" -eq 1 ]]; then
    exit 0
  fi
  sleep 1
done

echo "timed out waiting for services" >&2
echo "--- /tmp/suying-engine.log ---" >&2
tail -n 80 /tmp/suying-engine.log >&2 || true
echo "--- /tmp/suying-desktop.log ---" >&2
tail -n 80 /tmp/suying-desktop.log >&2 || true
exit 1
