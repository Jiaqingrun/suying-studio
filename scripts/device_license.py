#!/usr/bin/env python3
"""Generate a machine request or install a signed 速影 license."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.security.license import (  # noqa: E402
    generate_license_request,
    install_license_file,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request")
    request.add_argument("--output", type=Path, required=True)
    install = sub.add_parser("install")
    install.add_argument("--license", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "request":
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = generate_license_request()
        output.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"ok": True, "request": str(output)}, ensure_ascii=False))
        return 0

    target = install_license_file(args.license.expanduser().resolve())
    print(json.dumps({"ok": True, "license": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
