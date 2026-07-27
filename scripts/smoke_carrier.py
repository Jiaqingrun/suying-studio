#!/usr/bin/env python3
"""Smoke: T2S carrier layout + update manifest + backup (no media sync)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.app_update import sha256_file, write_latest_manifest  # noqa: E402
from engine.ops.carrier import (  # noqa: E402
    ensure_carrier_layout,
    export_config_backup,
    import_customer_seed,
    list_customer_seeds,
    read_latest_manifest,
    seed_industry_into_carrier,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "速影载体"
        ensure_carrier_layout(root)
        seed_industry_into_carrier(root, ROOT / "configs")
        assert (root / "app").is_dir()
        assert (root / "seed" / "install-defaults.json").is_file()
        seeds = list_customer_seeds(root)
        assert any(s["id"] == "building-supply-starter" for s in seeds)
        customer_root = Path(tmp) / "速影客户" / "演示客户"
        seeded = import_customer_seed(
            seed_id="building-supply-starter",
            customer_root=customer_root,
            carrier_root=root,
        )
        assert seeded["copied"]
        assert (customer_root / "03-词池" / "keyword-pack.json").is_file()
        assert (customer_root / "05-品牌" / "VIDEO_LOCK.json").is_file()
        pkg = root / "app" / "速影-0.0.0-smoke.zip"
        pkg.write_bytes(b"PK\x03\x04smoke")
        digest = sha256_file(pkg)
        write_latest_manifest(
            root,
            version="0.0.0-smoke",
            package=pkg.name,
            sha256=digest,
            force=False,
            notes="smoke",
        )
        man = read_latest_manifest(root)
        assert man and man.get("version") == "0.0.0-smoke"
        bak = export_config_backup(
            carrier_root=root,
            customer_id="smoke",
            customer_name="演示客户",
            payload={"settings": {"api_key": "SECRET"}, "customer": {"name": "演示客户"}},
        )
        assert bak["ok"]
        data = json.loads(Path(bak["path"], "backup.json").read_text(encoding="utf-8"))
        assert "SECRET" not in json.dumps(data)
        print(json.dumps({"ok": True, "carrier": str(root), "backup": bak["path"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
