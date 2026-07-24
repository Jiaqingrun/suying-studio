#!/usr/bin/env python3
"""DEPRECATED: use scripts/fixtures/convert_shifeng_keyword_pack.py."""
from __future__ import annotations
import runpy
import sys
from pathlib import Path

print("WARNING: convert_shifeng_keyword_pack.py 已迁到 scripts/fixtures/", file=sys.stderr)
runpy.run_path(str(Path(__file__).resolve().parent / "fixtures" / "convert_shifeng_keyword_pack.py"), run_name="__main__")
