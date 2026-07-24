#!/usr/bin/env python3
"""DEPRECATED: use scripts/fixtures/bootstrap_shifeng.py (sample customer fixture)."""
from __future__ import annotations
import runpy
import sys
from pathlib import Path

print("WARNING: bootstrap_shifeng.py 已迁到 scripts/fixtures/（样板客户，非产品入口）", file=sys.stderr)
runpy.run_path(str(Path(__file__).resolve().parent / "fixtures" / "bootstrap_shifeng.py"), run_name="__main__")
