#!/usr/bin/env bash
# Atomically install F5 Runtime Kit into ~/Suying/runtime (never writes App tree).
# Works on ops Mac (repo present) and customer Mac (only App + this script).
#
# Usage:
#   ./scripts/install-f5-runtime-kit.sh /path/to/速影-f5-runtime-kit-….tar.gz
#   ./scripts/install-f5-runtime-kit.sh --app "/Applications/速影 Studio.app" KIT.tgz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Prefer App studio for imports; fall back to monorepo checkout.
APP="${SUYING_APP:-/Applications/速影 Studio.app}"
KIT_ARCHIVE=""
SKIP_PYTHON_CHECK=0
RUNTIME_DIR="${HOME}/Suying/runtime"
OVERLAY_DEST="${RUNTIME_DIR}/f5_site_packages"
MANIFEST_DEST="${RUNTIME_DIR}/f5_kit.manifest.json"

usage() {
  cat <<'EOF'
用法:
  install-f5-runtime-kit.sh [--app APP] [--skip-python-check] KIT.tar.gz

安装到:
  ~/Suying/runtime/f5_site_packages
  ~/Suying/runtime/f5_kit.manifest.json

- 不写 App / 不重签 RUNTIME_MANIFEST
- 校验 kit python_tag 与 App 内 cpython 一致（可用 --skip-python-check）
- 成功前将旧 overlay 移到 f5_site_packages.prev-<rev>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="${2:-}"; shift 2 ;;
    --skip-python-check) SKIP_PYTHON_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$KIT_ARCHIVE" ]]; then
        KIT_ARCHIVE="$1"
        shift
      else
        echo "未知参数: $1" >&2
        usage
        exit 2
      fi
      ;;
  esac
done

[[ -n "$KIT_ARCHIVE" && -f "$KIT_ARCHIVE" ]] || { usage; exit 2; }
KIT_ARCHIVE="$(cd "$(dirname "$KIT_ARCHIVE")" && pwd)/$(basename "$KIT_ARCHIVE")"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }

[[ "$OVERLAY_DEST" == "${HOME}/Suying/runtime/"* ]] || fail "overlay 路径不在 ~/Suying/runtime"

APP_PY="${APP}/Contents/Resources/runtime/python/bin/python3"
APP_STUDIO="${APP}/Contents/Resources/runtime/studio"
# PYTHONPATH for f5_kit helpers: App embed first, then monorepo parent of scripts/
PY_HELPERS=()
if [[ -x "$APP_PY" && -d "${APP_STUDIO}/engine" ]]; then
  PY_HELPERS=("$APP_PY")
  export PYTHONPATH="$APP_STUDIO${PYTHONPATH:+:$PYTHONPATH}"
else
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  if [[ -d "${REPO_ROOT}/engine/pack" ]]; then
    PY_HELPERS=("python3")
    export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
  fi
fi

mkdir -p "$RUNTIME_DIR"
STAGING="$(mktemp -d "${RUNTIME_DIR}/.f5_staging_XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

log "解压 Kit → staging"
tar -xzf "$KIT_ARCHIVE" -C "$STAGING"
[[ -d "${STAGING}/f5_site_packages" ]] || fail "归档缺少 f5_site_packages/"
[[ -f "${STAGING}/f5_kit.manifest.json" ]] || fail "归档缺少 f5_kit.manifest.json"
[[ -d "${STAGING}/f5_site_packages/f5_tts" ]] || \
  compgen -G "${STAGING}/f5_site_packages/f5_tts-*.dist-info" >/dev/null || \
  fail "staging 无 f5_tts 标记"

# python_tag check (pure python, no engine module required)
export STAGING APP SKIP_PYTHON_CHECK
python3 - <<'PY' || exit 1
import json, os, subprocess, sys
from pathlib import Path

stage = Path(os.environ["STAGING"])
man = json.loads((stage / "f5_kit.manifest.json").read_text(encoding="utf-8"))
required = str(man.get("requires_app_python") or man.get("python_tag") or "")
print(f"kit_rev={man.get('kit_rev')} python_tag={required}")
(stage / ".kit_rev").write_text(str(int(man.get("kit_rev") or 0)), encoding="utf-8")
if os.environ.get("SKIP_PYTHON_CHECK") == "1":
    print("python_tag check skipped")
    raise SystemExit(0)
app = Path(os.environ["APP"])
py = app / "Contents/Resources/runtime/python/bin/python3"
if not py.is_file():
    print(f"ERROR: App 内无 python: {py}", file=sys.stderr)
    raise SystemExit(2)
code = (
    "import platform,sys;"
    "print(f'cpython-{sys.version_info.major}.{sys.version_info.minor}"
    "-{sys.platform}-{platform.machine()}')"
)
have = subprocess.check_output([str(py), "-c", code], text=True).strip()
if required and have != required:
    print(f"ERROR: python_tag 不匹配 need={required} have={have}", file=sys.stderr)
    raise SystemExit(3)
print(f"python_tag ok: {have}")
PY

KIT_REV="$(cat "${STAGING}/.kit_rev" 2>/dev/null || echo 0)"

if [[ -d "$OVERLAY_DEST" ]]; then
  PREV_REV="0"
  if [[ -f "$MANIFEST_DEST" ]]; then
    PREV_REV="$(python3 -c "import json; print(json.load(open('${MANIFEST_DEST}')).get('kit_rev') or 0)" 2>/dev/null || echo 0)"
  fi
  PREV="${OVERLAY_DEST}.prev-${PREV_REV}"
  log "备份现有 overlay → ${PREV}"
  rm -rf "$PREV"
  mv "$OVERLAY_DEST" "$PREV"
fi

log "原子切换 overlay"
mv "${STAGING}/f5_site_packages" "$OVERLAY_DEST"
cp "${STAGING}/f5_kit.manifest.json" "$MANIFEST_DEST"
chmod 600 "$MANIFEST_DEST" 2>/dev/null || true

export OVERLAY_DEST MANIFEST_DEST KIT_ARCHIVE
python3 - <<'PY'
import json
import os
from pathlib import Path
ov = Path(os.environ["OVERLAY_DEST"])
man = json.loads(Path(os.environ["MANIFEST_DEST"]).read_text(encoding="utf-8"))
meta = ov / ".suying_f5_overlay.json"
data = {}
if meta.is_file():
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
data.update({
    "kit_rev": man.get("kit_rev"),
    "python_tag": man.get("python_tag"),
    "source_build": man.get("source_build"),
    "installed_from": os.environ["KIT_ARCHIVE"],
})
meta.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("manifest kit_rev=", man.get("kit_rev"))
PY

rm -rf "$STAGING"
trap - EXIT

log "验收：App Python 能通过 overlay import f5_tts"
if [[ -x "$APP_PY" ]]; then
  OVERLAY_DEST="$OVERLAY_DEST" "$APP_PY" - <<'PY' || fail "App python import f5_tts 失败"
import os, sys
from pathlib import Path
overlay = Path(os.environ["OVERLAY_DEST"])
sys.path.insert(0, str(overlay))
import f5_tts
print("import ok", getattr(f5_tts, "__file__", f5_tts))
PY
else
  echo "WARN: 无 App python，跳过 import 验收" >&2
fi

log "OK · F5 Runtime Kit 已安装到 ${OVERLAY_DEST} (kit_rev=${KIT_REV})"
echo "注意: 覆盖安装 /Applications 不会删除本目录；周更只需装 core App。"
