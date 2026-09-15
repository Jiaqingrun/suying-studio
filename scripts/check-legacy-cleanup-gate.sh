#!/usr/bin/env bash
# P3 防回归：遗留清理合同不得静默回潮。
# Usage: ./scripts/check-legacy-cleanup-gate.sh
# 排除 docs/archive/**；变更表里「删/废止/过时」叙述允许保留禁句原文。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail=0
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

echo "==> legacy-cleanup gate: 仅节奏润色（现行入口）"
# 现行合同入口不得把「仅节奏润色」写成有效底线；允许「删/废止/过时」变更叙述。
if command -v rg >/dev/null 2>&1; then
  rg -n --no-heading '仅节奏润色' \
    docs AGENTS.md PROJECT.md README.md .cursor/rules \
    --glob '!docs/archive/**' \
    >"$tmp" 2>/dev/null || true
else
  grep -RIn '仅节奏润色' docs AGENTS.md PROJECT.md README.md .cursor/rules 2>/dev/null \
    | grep -v '/docs/archive/' >"$tmp" || true
fi
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" ]] && continue
  if echo "$line" | grep -qE '删|废止|过时|历史|不再|不得再写|禁止回潮|不得再写'; then
    continue
  fi
  echo "FAIL: $line" >&2
  fail=1
done <"$tmp"

echo "==> legacy-cleanup gate: Desktop/速影.app 交付落点（README / 安装文）"
: >"$tmp"
if command -v rg >/dev/null 2>&1; then
  rg -n --no-heading 'Desktop/速影\.app|~/Desktop/速影\.app' \
    README.md docs/CUSTOMER_INSTALL.md docs/INSTALL.md \
    >"$tmp" 2>/dev/null || true
else
  grep -nE 'Desktop/速影\.app|~/Desktop/速影\.app' \
    README.md docs/CUSTOMER_INSTALL.md docs/INSTALL.md 2>/dev/null >"$tmp" || true
fi
# 允许「清理桌面残留 / 禁止落桌面」叙述；禁止把 Desktop 写成正式交付路径。
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" ]] && continue
  if echo "$line" | grep -qE '禁止|不得|清理|删除|rm |残留|勿|不要落|禁落'; then
    continue
  fi
  # 离线模型暂存等非 App 交付说明：须同时写明「暂存/缓存/临时」且非「正式包/交付」
  if echo "$line" | grep -qE '暂存|缓存|临时|模型' && ! echo "$line" | grep -qE '正式包|交付落|安装到'; then
    continue
  fi
  echo "FAIL: $line" >&2
  fail=1
done <"$tmp"

echo "==> legacy-cleanup gate: Agent 入口文件存在"
for f in docs/README.md docs/DEV_LOCK.md docs/HARD_LOCKS.md AGENTS.md PROJECT.md; do
  if [[ ! -f "$f" ]]; then
    echo "FAIL: missing $f" >&2
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "ERROR: check-legacy-cleanup-gate FAILED（见上 FAIL）" >&2
  echo "       清单：docs 旁 Store checklist / 仓内遗留清理计划 P3" >&2
  exit 1
fi

echo "==> check-legacy-cleanup-gate: OK"
