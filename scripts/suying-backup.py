#!/usr/bin/env python3
"""Run one policy-controlled Suying backup from LaunchAgent or terminal."""

from __future__ import annotations

import json

from engine.ops.backup_service import load_policy, run_backup_now


def main() -> int:
    policy = load_policy()
    if not policy.get("enabled"):
        print(json.dumps({"ok": True, "skipped": "disabled"}, ensure_ascii=False))
        return 0
    result = run_backup_now("launch_agent")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") and result.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
