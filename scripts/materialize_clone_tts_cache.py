#!/usr/bin/env python3
"""Materialize / verify F5-TTS + Vocos HuggingFace hub caches for offline clone TTS.

Ops Mac (copy onto USB / LAN then to customer):
  python3 scripts/materialize_clone_tts_cache.py --check
  python3 scripts/materialize_clone_tts_cache.py --export /path/to/clone-hf-hub

Customer Mac (import):
  python3 scripts/materialize_clone_tts_cache.py --import-from /path/to/clone-hf-hub
  # then set in ~/Suying/runtime/local.env:
  #   HF_HUB_OFFLINE=1
  #   TRANSFORMERS_OFFLINE=1
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REQUIRED = (
    "models--SWivid--F5-TTS",
    "models--charactr--vocos-mel-24khz",
)


def hf_hub_root() -> Path:
    raw = os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if raw:
        base = Path(raw).expanduser()
        hub = base / "hub" if (base / "hub").is_dir() or not base.exists() else base
        return hub if hub.name == "hub" else base / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def status(hub: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name in REQUIRED:
        out[name] = any(hub.glob(f"{name}*")) if hub.is_dir() else False
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit 0 only if all weights present")
    parser.add_argument("--export", type=Path, help="Copy required model dirs to destination")
    parser.add_argument("--import-from", type=Path, dest="import_from", help="Copy model dirs into local HF hub")
    args = parser.parse_args()
    hub = hf_hub_root()
    st = status(hub)
    print(f"HF hub: {hub}")
    for name, ok in st.items():
        print(f"  {'OK' if ok else 'MISSING'}: {name}")

    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        for name, ok in st.items():
            if not ok:
                print(f"ERROR: cannot export missing {name}", file=sys.stderr)
                return 2
            matches = sorted(hub.glob(f"{name}*"))
            for src in matches:
                dst = args.export / src.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                print(f"exported {src.name}")
        return 0

    if args.import_from:
        src_root = args.import_from.expanduser()
        if not src_root.is_dir():
            print(f"ERROR: import source missing: {src_root}", file=sys.stderr)
            return 2
        hub.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED:
            matches = sorted(src_root.glob(f"{name}*"))
            if not matches:
                print(f"ERROR: import source missing {name}", file=sys.stderr)
                return 2
            for src in matches:
                dst = hub / src.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                print(f"imported {src.name} -> {dst}")
        st = status(hub)
        print("after import:")
        for name, ok in st.items():
            print(f"  {'OK' if ok else 'MISSING'}: {name}")

    if args.check or args.import_from or args.export:
        return 0 if all(st.values()) else 1
    return 0 if all(st.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
