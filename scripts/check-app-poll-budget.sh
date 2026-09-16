#!/usr/bin/env bash
# S4 / PL-25: App poll budget + interval inventory gate.
# - messages floor ≥30s
# - no new ≤1s full-payload polls outside allowlist
# Usage: ./scripts/check-app-poll-budget.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail=0

echo "==> poll-budget: POLL_BUDGET_MS floors"
python3 - <<'PY' || fail=1
from pathlib import Path
import re
import sys

text = Path("apps/desktop/src/pollBudget.ts").read_text(encoding="utf-8")

def ms(name: str) -> int:
    m = re.search(rf"{name}:\s*([\d_]+)", text)
    if not m:
        print(f"FAIL: missing POLL_BUDGET_MS.{name}", file=sys.stderr)
        sys.exit(1)
    return int(m.group(1).replace("_", ""))

checks = {
    "messages": 30_000,
    "health": 5_000,
    "services": 30_000,
    "semanticIdle": 15_000,
    "semanticFast": 3_000,
}
for name, floor in checks.items():
    got = ms(name)
    if got < floor:
        print(f"FAIL: {name}={got} < floor {floor}", file=sys.stderr)
        sys.exit(1)
    print(f"    {name}={got} ok")

# servicesEvery must exist and be ≥6 (≈30s with 5s health)
m = re.search(r"servicesEvery:\s*(\d+)", text)
if not m or int(m.group(1)) < 6:
    print("FAIL: POLL_TICK.servicesEvery must be ≥6", file=sys.stderr)
    sys.exit(1)
print(f"    servicesEvery={m.group(1)} ok")
PY

echo "==> poll-budget: inventory doc present"
if [[ ! -f docs/APP_INTERVAL_INVENTORY.md ]]; then
  echo "FAIL: missing docs/APP_INTERVAL_INVENTORY.md" >&2
  fail=1
fi

echo "==> poll-budget: ban literal ≤1s setInterval full polls (allowlist)"
# Allowlist: VectorControl (vectorBusy), PublishBatchPanel (active publish due),
# ActivityTicker (UI-only rotation, no API).
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if command -v rg >/dev/null 2>&1; then
  rg -n --glob '*.tsx' --glob '*.ts' \
    'setInterval\([^,]*,\s*(1000|500|250|100)\s*\)' \
    apps/desktop/src \
    >"$tmp" 2>/dev/null || true
else
  grep -RInE 'setInterval\([^,]*,\s*(1000|500|250|100)\s*\)' apps/desktop/src \
    --include='*.ts' --include='*.tsx' >"$tmp" 2>/dev/null || true
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" ]] && continue
  case "$line" in
    *PublishBatchPanel.tsx*|*VectorControl.tsx*|*ActivityTicker.tsx*|*pollBudget.ts*)
      continue
      ;;
  esac
  echo "FAIL: ≤1s setInterval outside allowlist: $line" >&2
  fail=1
done <"$tmp"

# VectorControl must gate 1s on busy flag / vectorBusy constant
if ! rg -n 'POLL_BUDGET_MS\.vectorBusy|vectorBusy' apps/desktop/src/VectorControl.tsx >/dev/null 2>&1; then
  echo "FAIL: VectorControl must use POLL_BUDGET_MS.vectorBusy for 1s cadence" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "ERROR: check-app-poll-budget FAILED" >&2
  exit 1
fi
echo "==> check-app-poll-budget: OK"
