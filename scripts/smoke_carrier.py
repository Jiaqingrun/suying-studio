#!/usr/bin/env python3
"""Smoke: T2S carrier layout + update manifest + backup (no media sync)."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.carrier import (  # noqa: E402
    ensure_carrier_layout,
    export_config_backup,
    import_customer_seed,
    list_customer_seeds,
    read_latest_manifest,
    seed_industry_into_carrier,
)
from engine.security.update_manifest import (  # noqa: E402
    Artifact,
    LatestPointer,
    ReleaseManifest,
    public_key_id,
    sha256_file,
    write_signed_document,
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
        key = Ed25519PrivateKey.generate()
        trusted = {public_key_id(key.public_key()): key.public_key()}
        release_dir = root / "app" / "releases" / "0.0.0-smoke" / "smoke"
        release_dir.mkdir(parents=True)
        pkg = release_dir / "速影-0.0.0-smoke.zip"
        pkg.write_bytes(b"PK\x03\x04smoke")
        digest = sha256_file(pkg)
        release = ReleaseManifest(
            release_seq=1,
            version="0.0.0-smoke",
            arch="arm64",
            artifact=Artifact(path=pkg.name, size=pkg.stat().st_size, sha256=digest),
            runtime_manifest_sha256="a" * 64,
            delivery_id="delivery-smoke-0123456789",
            customer_ref="customer-smoke",
            created_at=datetime.now(timezone.utc),
            key_id=public_key_id(key.public_key()),
            notes="smoke",
        )
        release_path, _ = write_signed_document(
            release,
            private_key=key,
            path=release_dir / "release.json",
        )
        latest = LatestPointer(
            release_seq=1,
            release_path="releases/0.0.0-smoke/smoke/release.json",
            release_sha256=sha256_file(release_path),
            key_id=release.key_id,
            updated_at=datetime.now(timezone.utc),
        )
        write_signed_document(
            latest,
            private_key=key,
            path=root / "app" / "latest.json",
        )
        man = read_latest_manifest(root, trusted_keys=trusted)
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
