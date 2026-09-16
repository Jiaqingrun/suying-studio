#!/usr/bin/env bash
# S7 / PL-14: every production try_acquire(ollama_heavy|tts) must heartbeat the same slot.
# Usage: ./scripts/check-resource-gate-heartbeat.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail=0
echo "==> resource-gate heartbeat: scan engine/ for ollama_heavy|tts acquires"

python3 - <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(".")
ENGINE = ROOT / "engine"
SKIP_PARTS = {".venv", "__pycache__", "tests"}
# Files that only define the gate / helpers, not long holders.
SKIP_FILES = {
    "engine/runtime/resource_gate.py",
}

acquire_re = re.compile(
    r"""try_acquire\(\s*[\"'](ollama_heavy|tts)[\"']""",
)
heartbeat_re = re.compile(
    r"""heartbeat\(\s*[\"'](ollama_heavy|tts)[\"']""",
)

missing: list[str] = []
ok: list[str] = []

for path in sorted(ENGINE.rglob("*.py")):
    rel = path.as_posix()
    if any(part in SKIP_PARTS for part in path.parts):
        continue
    if rel in SKIP_FILES:
        continue
    text = path.read_text(encoding="utf-8")
    slots = set(acquire_re.findall(text))
    if not slots:
        continue
    hb = set(heartbeat_re.findall(text))
    for slot in sorted(slots):
        if slot not in hb:
            missing.append(f"{rel}: try_acquire({slot!r}) without heartbeat({slot!r})")
        else:
            ok.append(f"{rel}: {slot}")

for line in ok:
    print(f"    ok {line}")
if missing:
    for line in missing:
        print(f"FAIL: {line}", file=sys.stderr)
    sys.exit(1)
if not ok:
    print("FAIL: no ollama_heavy/tts acquire sites found (unexpected)", file=sys.stderr)
    sys.exit(1)
print(f"    checked {len(ok)} holder site(s)")
PY

echo "==> check-resource-gate-heartbeat: OK"
