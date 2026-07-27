#!/usr/bin/env python3
"""Strict ready-montage QA CLI — delegates to engine.qc.ready_gate (READY_GATE).

Checks one montage_*.mp4 (+ sidecar / srt / voice) and prints JSON + exit code.
Exit 0 = pass, 1 = fail, 2 = skip/incomplete.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.qc.ready_gate import evaluate_ready_gate, qa_montage  # noqa: E402,F401


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: qa_ready_montage.py <montage.mp4>", file=sys.stderr)
        return 2
    result = evaluate_ready_gate(Path(sys.argv[1]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not Path(sys.argv[1]).is_file():
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
