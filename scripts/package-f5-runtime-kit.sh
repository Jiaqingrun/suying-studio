#!/usr/bin/env bash
# Build a versioned F5 Runtime Kit tarball from a clone-capable App / runtime stage,
# or re-package an existing overlay directory.
# Output（暂存）: ~/Suying/releases/f5-runtime/kits/速影-f5-runtime-kit-<python_tag>-r<rev>.tar.gz
# 正式真源在 T2S；publish_f5_kit_repo.py 推送成功后默认删除本机 kit。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FROM_APP="${FROM_APP:-}"
FROM_STAGE="${FROM_STAGE:-}"
FROM_OVERLAY="${FROM_OVERLAY:-}"
KIT_REV="${KIT_REV:-}"
SOURCE_BUILD="${SOURCE_BUILD:-}"
OUT_DIR="${OUT_DIR:-${HOME}/Suying/releases/f5-runtime/kits}"
TMP_OVERLAY=""
CLEAN_STAGING=0

usage() {
  cat <<'EOF'
用法:
  package-f5-runtime-kit.sh [--from-app APP] [--from-runtime-stage DIR]
                            [--from-overlay DIR] [--kit-rev N] [--source-build TAG]
                            [--out-dir DIR]

默认：若已有 ~/Suying/runtime/f5_site_packages 且含 f5_tts，则从该 overlay 打包；
否则要求 --from-app / --from-runtime-stage（源须能 import f5_tts）。

环境变量等同长选项：FROM_APP / FROM_STAGE / FROM_OVERLAY / KIT_REV / SOURCE_BUILD / OUT_DIR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-app) FROM_APP="${2:-}"; shift 2 ;;
    --from-runtime-stage) FROM_STAGE="${2:-}"; shift 2 ;;
    --from-overlay) FROM_OVERLAY="${2:-}"; shift 2 ;;
    --kit-rev) KIT_REV="${2:-}"; shift 2 ;;
    --source-build) SOURCE_BUILD="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

OVERLAY_DEFAULT="${HOME}/Suying/runtime/f5_site_packages"

materialize_args=()
if [[ -n "$FROM_STAGE" ]]; then
  materialize_args+=(--from-runtime-stage "$FROM_STAGE" --clean)
  CLEAN_STAGING=1
elif [[ -n "$FROM_APP" ]]; then
  materialize_args+=(--from-app "$FROM_APP" --clean)
  CLEAN_STAGING=1
elif [[ -n "$FROM_OVERLAY" ]]; then
  :
elif [[ -d "${OVERLAY_DEFAULT}/f5_tts" ]] || compgen -G "${OVERLAY_DEFAULT}/f5_tts-*.dist-info" >/dev/null 2>&1; then
  FROM_OVERLAY="$OVERLAY_DEFAULT"
else
  echo "ERROR: 无可用 overlay；请传 --from-app / --from-runtime-stage / --from-overlay" >&2
  exit 1
fi

if [[ ${#materialize_args[@]} -gt 0 ]]; then
  TMP_OVERLAY="$(mktemp -d "${TMPDIR:-/tmp}/suying-f5-kit-XXXXXX")"
  trap 'rm -rf "$TMP_OVERLAY"' EXIT
  materialize_args+=(--overlay "$TMP_OVERLAY")
  [[ -n "$KIT_REV" ]] && materialize_args+=(--kit-rev "$KIT_REV")
  [[ -n "$SOURCE_BUILD" ]] && materialize_args+=(--source-build "$SOURCE_BUILD")
  python3 "${ROOT}/scripts/materialize_f5_overlay.py" "${materialize_args[@]}"
  PACK_SRC="$TMP_OVERLAY"
else
  PACK_SRC="$(cd "$FROM_OVERLAY" && pwd)"
  wm=(--write-manifest --overlay "$PACK_SRC")
  [[ -n "$KIT_REV" ]] && wm+=(--kit-rev "$KIT_REV")
  [[ -n "$SOURCE_BUILD" ]] && wm+=(--source-build "$SOURCE_BUILD")
  python3 "${ROOT}/scripts/materialize_f5_overlay.py" "${wm[@]}"
fi

# Refresh installed manifest is OK when packaging from live overlay; for temp tree
# materialize already wrote host f5_kit.manifest — rewrite from pack tree via Python.
export PACK_SRC ROOT
python3 - <<'PY'
import json, os, shutil, sys
from pathlib import Path
ROOT = Path(os.environ["ROOT"])
sys.path.insert(0, str(ROOT))
from engine.pack.f5_kit import (
    build_kit_manifest,
    default_kit_manifest_path,
    load_kit_manifest,
    load_overlay_meta,
    next_kit_rev,
    python_tag_from_executable,
    write_kit_manifest,
)

src = Path(os.environ["PACK_SRC"]).resolve()
meta = load_overlay_meta(src) or {}
kit_rev = int(os.environ.get("KIT_REV") or meta.get("kit_rev") or 0)
if kit_rev < 1:
    existing = load_kit_manifest()
    kit_rev = int(existing["kit_rev"]) if existing and existing.get("kit_rev") else next_kit_rev()
py_tag = str(meta.get("python_tag") or "")
if not py_tag:
    for p in (
        Path("/Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3"),
    ):
        if p.is_file():
            py_tag = python_tag_from_executable(p)
            break
    else:
        py_tag = python_tag_from_executable()
source_build = os.environ.get("SOURCE_BUILD") or str(meta.get("source_build") or "")
man = build_kit_manifest(
    overlay=src,
    kit_rev=kit_rev,
    python_tag=py_tag,
    source_build=source_build,
)
# Bundle layout: f5_site_packages/ + f5_kit.manifest.json
bundle = Path(os.environ.get("BUNDLE_DIR") or "")
if not bundle:
    # parent decides; write next to pack via env set below
    pass
print(json.dumps({"kit_rev": kit_rev, "python_tag": py_tag, "manifest": man}, ensure_ascii=False))
(src / ".bundle_kit_manifest.json").write_text(
    json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
# Also update machine installed manifest when packaging from live overlay
if str(src) == str(Path.home() / "Suying" / "runtime" / "f5_site_packages"):
    write_kit_manifest(man)
PY

KIT_META="$(python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROOT"])
src = Path(os.environ["PACK_SRC"])
man = json.loads((src / ".bundle_kit_manifest.json").read_text(encoding="utf-8"))
print(f"{man['kit_rev']}\t{man['python_tag']}\t{man['package_sha256']}\t{man['bytes']}")
PY
)"
KIT_REV_EFF="$(echo "$KIT_META" | cut -f1)"
PY_TAG="$(echo "$KIT_META" | cut -f2)"
ARCHIVE_NAME="速影-f5-runtime-kit-${PY_TAG}-r${KIT_REV_EFF}.tar.gz"
mkdir -p "$OUT_DIR"
OUT_ARCHIVE="${OUT_DIR}/${ARCHIVE_NAME}"
BUNDLE_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/suying-f5-bundle-XXXXXX")"
trap 'rm -rf "$TMP_OVERLAY" "$BUNDLE_STAGE"' EXIT

mkdir -p "${BUNDLE_STAGE}/f5_site_packages"
# Copy contents (exclude staging helper)
rsync -a --exclude '.bundle_kit_manifest.json' "${PACK_SRC}/" "${BUNDLE_STAGE}/f5_site_packages/"
cp "${PACK_SRC}/.bundle_kit_manifest.json" "${BUNDLE_STAGE}/f5_kit.manifest.json"
# Fix manifest bytes/sha for tree inside bundle (optional recompute)
export BUNDLE_STAGE ROOT
python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["ROOT"])
from engine.pack.f5_kit import build_kit_manifest
stage = Path(os.environ["BUNDLE_STAGE"])
overlay = stage / "f5_site_packages"
raw = json.loads((stage / "f5_kit.manifest.json").read_text(encoding="utf-8"))
man = build_kit_manifest(
    overlay=overlay,
    kit_rev=int(raw["kit_rev"]),
    python_tag=str(raw["python_tag"]),
    source_build=str(raw.get("source_build") or ""),
)
(stage / "f5_kit.manifest.json").write_text(
    json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(man["package_sha256"], man["bytes"])
PY

(
  cd "$BUNDLE_STAGE"
  tar -czf "$OUT_ARCHIVE" f5_site_packages f5_kit.manifest.json
)
SHA="$(shasum -a 256 "$OUT_ARCHIVE" | awk '{print $1}')"
echo "$SHA  $(basename "$OUT_ARCHIVE")" > "${OUT_ARCHIVE}.sha256"
export OUT_ARCHIVE BUNDLE_STAGE SHA
python3 - <<'PY'
import json
import os
from pathlib import Path
arch = Path(os.environ["OUT_ARCHIVE"])
meta_path = Path(str(arch) + ".meta.json")
bundle_man = Path(os.environ["BUNDLE_STAGE"]) / "f5_kit.manifest.json"
man = json.loads(bundle_man.read_text(encoding="utf-8"))
man["archive"] = arch.name
man["archive_sha256"] = os.environ["SHA"]
man["archive_bytes"] = arch.stat().st_size
meta_path.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(meta_path)
PY

echo "==> Kit archive: $OUT_ARCHIVE"
echo "==> SHA256: $SHA"
echo "==> kit_rev=${KIT_REV_EFF} python_tag=${PY_TAG}"
