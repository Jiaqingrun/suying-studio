#!/usr/bin/env bash
# Post core overwrite install: verify F5 overlay survives (30s smoke).
# Usage:
#   VERIFY_F5_OVERLAY=1 ./scripts/verify_f5_overlay_post_install.sh
#   ./scripts/verify_f5_overlay_post_install.sh --require-clone
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${SUYING_APP:-/Applications/速影 Studio.app}"
API="${SUYING_API:-http://127.0.0.1:8766}"
REQUIRE_CLONE=0
REQUIRE_OVERLAY_SOURCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --require-clone) REQUIRE_CLONE=1; shift ;;
    --require-overlay-source) REQUIRE_OVERLAY_SOURCE=1; shift ;;
    --app) APP="${2:-}"; shift 2 ;;
    --api) API="${2:-}"; shift 2 ;;
    -h|--help)
      echo "verify_f5_overlay_post_install.sh [--require-clone] [--require-overlay-source]"
      exit 0
      ;;
    *) echo "未知: $1" >&2; exit 2 ;;
  esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

# Never touch F5 runtime
[[ -d "${HOME}/Suying/runtime" ]] || true
if [[ -d "${HOME}/Suying/runtime/f5_site_packages" ]]; then
  ok "overlay dir present"
else
  if [[ "$REQUIRE_CLONE" == "1" ]]; then
    fail "缺少 ~/Suying/runtime/f5_site_packages；请 install-f5-runtime-kit（勿 merge 进 App）"
  fi
  echo "WARN: 无 F5 overlay（若本机不需要 clone 可忽略）"
fi

if [[ -d "$APP" ]]; then
  BV="$(cat "${APP}/Contents/Resources/runtime/BUNDLE_VERSION" 2>/dev/null || true)"
  BF="$(cat "${APP}/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true)"
  ok "App BUNDLE_VERSION=${BV:-?} flavor=${BF:-?}"
  # Guardrail: install must not have deleted kit
  if [[ -d "${HOME}/Suying/runtime/f5_site_packages" ]]; then
    ok "core install did not remove F5 kit path"
  fi
fi

export ROOT API REQUIRE_CLONE REQUIRE_OVERLAY_SOURCE
python3 - <<'PY'
import json, os, sys, urllib.request
from pathlib import Path

sys.path.insert(0, os.environ["ROOT"])
from engine.pack.voice_clone import apply_f5_site_overlay, clone_runtime_status, f5_available

apply_f5_site_overlay(force=True)
st = clone_runtime_status()
print(json.dumps(st, ensure_ascii=False, indent=2))

# Prefer live engine if up
api = os.environ.get("API", "http://127.0.0.1:8766")
live = None
try:
    with urllib.request.urlopen(api + "/jobs/pipeline", timeout=3) as resp:
        live = json.loads(resp.read().decode())
except Exception:
    live = None
if isinstance(live, dict) and isinstance(live.get("clone_runtime"), dict):
    print("live_engine_clone_runtime:", json.dumps(live["clone_runtime"], ensure_ascii=False))
    st = live["clone_runtime"]

require_clone = os.environ.get("REQUIRE_CLONE") == "1"
require_src = os.environ.get("REQUIRE_OVERLAY_SOURCE") == "1"
avail = bool(st.get("clone_available") or f5_available())
if require_clone and not avail:
    print("FAIL: clone_available false", file=sys.stderr)
    raise SystemExit(2)
if require_src and st.get("f5_source") not in ("overlay", "bundle+overlay"):
    print(f"FAIL: expected overlay source, got {st.get('f5_source')}", file=sys.stderr)
    raise SystemExit(3)
if avail and st.get("f5_kit_compat_ok") is False:
    print("FAIL: f5_kit_compat_ok false", st.get("f5_kit_compat_reasons"), file=sys.stderr)
    raise SystemExit(4)
print("verify ok")
PY

ok "F5 overlay post-install verify finished"
