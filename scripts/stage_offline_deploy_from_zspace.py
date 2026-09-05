#!/usr/bin/env python3
"""Stage offline deploy assets from T2S to ~/Suying/incoming/zspace-offline/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.zspace_offline_deploy import stage_for_deploy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage T2S offline deploy assets locally")
    parser.add_argument("--profile", choices=("lite", "standard", "pro", "max"), required=True)
    parser.add_argument("--arch", default=__import__("platform").machine())
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--skip-tools", action="store_true")
    parser.add_argument("--with-depot", action="store_true", help="Also download 速影/更新包/depot")
    args = parser.parse_args()
    result = stage_for_deploy(
        profile=args.profile,
        arch=args.arch,
        need_models=not args.skip_models,
        need_tools=not args.skip_tools,
        need_depot=args.with_depot,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
