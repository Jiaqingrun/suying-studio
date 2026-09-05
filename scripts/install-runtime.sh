#!/usr/bin/env bash
# Guided first-run setup for 速影: Python venv + Ollama + hardware-tier models.
# Usage:
#   ./scripts/install-runtime.sh
#   SUYING_ROOT=~/Suying/montage-studio ./scripts/install-runtime.sh
#   OLLAMA_ONLY=1 ./scripts/install-runtime.sh   # skip Python (scheme A App already embeds it)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${SUYING_ROOT:-${MONTAGE_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}}"
cd "$ROOT"
OLLAMA_ONLY="${OLLAMA_ONLY:-0}"

PROFILE_PY="${SUYING_PYTHON:-${MONTAGE_PYTHON:-python3}}"
command -v "$PROFILE_PY" >/dev/null 2>&1 ||
  { echo "❌ 无法生成 Install Profile：未找到 ${PROFILE_PY}" >&2; exit 1; }
PROFILE_LINE="$(
  PYTHONPATH="$ROOT" "$PROFILE_PY" -c '
from engine.catalog.host_profile import (
    install_vision_by_default,
    probe_host,
    recommend_models,
)
h = probe_host()
r = recommend_models(h)
embed = str(r["embed_model"])
vision = str(r["vision_model"]) if install_vision_by_default(h.tier) else ""
print("|".join([
    str(h.tier),
    embed,
    vision,
    str(int(round(float(h.ram_gb or 0)))),
    str(h.chip or h.arch or ""),
    str(h.arch or ""),
]))
'
)"
IFS='|' read -r TIER EMBED VISION RAM_GB CHIP ARCH <<< "$PROFILE_LINE"

echo "══════════════════════════════════════"
echo " 速影 · 运行时安装向导"
echo "══════════════════════════════════════"
echo "本机：${CHIP} · 约 ${RAM_GB}GB 内存 · ${ARCH}"
echo "推荐档位：${TIER}"
echo "  向量模型：${EMBED}（必选，约 0.3GB）"
if [[ -n "$VISION" ]]; then
  echo "  视觉模型：${VISION}（候选按需验证）"
else
  echo "  视觉模型：轻量档默认不安装（可稍后按需补装）"
fi
echo ""

if [[ "${OLLAMA_ONLY}" == "1" ]]; then
  echo "（OLLAMA_ONLY=1：跳过 Python 虚拟环境；一体包已内嵌解释器）"
else
# --- Python ---
pick_python() {
  if [[ -n "${SUYING_PYTHON:-${MONTAGE_PYTHON:-}}" ]]; then
    echo "${SUYING_PYTHON:-$MONTAGE_PYTHON}"
    return
  fi
  for c in python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      echo "$c"
      return
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "❌ 未找到 Python 3。请先安装："
  echo "   · https://www.python.org/downloads/ （勾选 Add to PATH）"
  echo "   · 或：brew install python@3.12"
  if command -v open >/dev/null 2>&1; then
    open "https://www.python.org/downloads/" || true
  fi
  exit 1
fi

PY_VER="$("$PY" -c 'import sys; print("%d.%d"%sys.version_info[:2])')"
PY_MAJOR="$("$PY" -c 'import sys; print(sys.version_info.major)')"
PY_MINOR="$("$PY" -c 'import sys; print(sys.version_info.minor)')"
if [[ "$PY_MAJOR" != "3" || "$PY_MINOR" -lt 11 ]]; then
  echo "❌ 需要 Python 3.11+，当前 ${PY} → ${PY_VER}"
  exit 1
fi
echo "✓ Python：${PY} (${PY_VER})"

RUNTIME_SOURCE_SHA="$(
  ROOT="$ROOT" "$PY" - <<'PY'
import hashlib
import os
from pathlib import Path
root = Path(os.environ["ROOT"])
digest = hashlib.sha256()
paths = [root / "requirements.txt", root / "pyproject.toml"]
for tree in (root / "engine", root / "scripts"):
    paths.extend(path for path in tree.rglob("*") if path.is_file())
for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root)
    if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
        continue
    digest.update(relative.as_posix().encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
)"
VENV_STAMP=".venv/.suying-runtime-fingerprint"
EXPECTED_STAMP="${PY_VER}|${ARCH}|${RUNTIME_SOURCE_SHA}"
if [[ -d .venv && "$(cat "$VENV_STAMP" 2>/dev/null || true)" != "$EXPECTED_STAMP" ]]; then
  echo "→ Python 版本、架构或源码/requirements 已变化，完整重建 .venv"
  rm -rf .venv
fi
if [[ ! -d .venv ]]; then
  echo "→ 创建虚拟环境 .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip wheel >/dev/null
echo "→ 安装引擎依赖（requirements.txt）"
pip install -r requirements.txt
python -m pip check
PYTHONPATH="$ROOT" python -c 'import encodings,fastapi,uvicorn,edge_tts,numpy,engine.main'
printf '%s\n' "$EXPECTED_STAMP" > "$VENV_STAMP"
echo "✓ Python 环境就绪：$(command -v python)"
fi

# --- FFmpeg ---
if command -v ffmpeg >/dev/null 2>&1; then
  echo "✓ FFmpeg：$(command -v ffmpeg)"
else
  echo "⚠ 未找到 FFmpeg。建议：brew install ffmpeg"
  if [[ "${NONINTERACTIVE:-0}" != "1" ]] && command -v brew >/dev/null 2>&1; then
    read -r -p "是否现在用 Homebrew 安装 FFmpeg？[y/N] " ans || true
    if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
      brew install ffmpeg
    fi
  fi
fi

# --- Ollama ---
if ! command -v ollama >/dev/null 2>&1; then
  echo "⚠ 未检测到 Ollama。"
  echo "   将打开下载页：https://ollama.com/download"
  echo "   安装并启动 Ollama 后，重新运行本脚本以拉取模型。"
  if command -v open >/dev/null 2>&1; then
    open "https://ollama.com/download" || true
  fi
  if [[ "${NONINTERACTIVE:-0}" != "1" ]] && command -v brew >/dev/null 2>&1; then
    read -r -p "或用 Homebrew 安装 ollama？[y/N] " ans || true
    if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
      brew install ollama
      brew services start ollama 2>/dev/null || true
      open -a Ollama 2>/dev/null || true
    else
      exit 0
    fi
  else
    exit 0
  fi
fi

echo "✓ Ollama CLI：$(command -v ollama)"
# Wake daemon
ollama list >/dev/null 2>&1 || open -a Ollama 2>/dev/null || true
sleep 2

echo "→ 拉取向量模型 ${EMBED}"
ollama pull "$EMBED"
if [[ -n "$VISION" ]]; then
  echo "→ 拉取视觉模型 ${VISION}（按本机 ${TIER} 档，仅候选按需使用）"
  ollama pull "$VISION"
fi

# Persist hint for engine settings (best-effort if data dir exists)
DATA="${SUYING_DATA_ROOT:-$HOME/Suying/data}"
mkdir -p "$DATA"
HINT="${DATA}/recommended_models.json"
TIER="$TIER" EMBED="$EMBED" VISION="$VISION" RAM_GB="$RAM_GB" CHIP="$CHIP" HINT="$HINT" python - <<'PY'
import json, os
from pathlib import Path
hint = {
  "tier": os.environ["TIER"],
  "ollama_embed_model": os.environ["EMBED"],
  "ollama_vision_model": os.environ["VISION"],
  "ram_gb": int(os.environ["RAM_GB"]),
  "chip": os.environ.get("CHIP") or "",
}
path = Path(os.environ["HINT"])
path.write_text(json.dumps(hint, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"✓ 已写入推荐模型提示：{path}")
PY

# Merge into settings.json if present
if [[ -f "${DATA}/settings.json" ]]; then
  python - <<'PY'
import json, os
from pathlib import Path
data = Path(os.environ.get("SUYING_DATA_ROOT", Path.home() / "Suying" / "data"))
hint = json.loads((data / "recommended_models.json").read_text())
path = data / "settings.json"
raw = json.loads(path.read_text())
raw["ollama_embed_model"] = hint["ollama_embed_model"]
raw["ollama_vision_model"] = hint["ollama_vision_model"]
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
tmp.replace(path)
print("✓ 已写入 settings.json 模型字段")
PY
fi

echo ""
echo "══════════════════════════════════════"
echo " 安装完成"
echo " 启动引擎：./scripts/start-engine.sh"
echo " 然后打开「速影.app」→ 运维 → 确认本地 AI 绿灯"
echo "══════════════════════════════════════"
