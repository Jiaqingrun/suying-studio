from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.build_runtime_manifest import build_manifest
from engine.security import runtime_integrity
from engine.security.update_manifest import public_key_id, write_signed_document


def test_runtime_manifest_covers_all_regular_files(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    (tmp_path / "studio" / "engine").mkdir(parents=True)
    (tmp_path / "studio" / "engine" / "main.py").write_text("print('ok')\n")
    (tmp_path / "python" / "bin").mkdir(parents=True)
    (tmp_path / "python" / "bin" / "python3").write_bytes(b"python")

    manifest = build_manifest(
        tmp_path,
        bundle_version="0.2.0",
        arch="arm64",
        private_key=key,
    )

    assert [item.path for item in manifest.files] == [
        "python/bin/python3",
        "studio/engine/main.py",
    ]


def test_runtime_manifest_rejects_symlink_escape(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    outside = tmp_path.parent / "outside-runtime-file"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "escape").symlink_to(outside)

    with pytest.raises(ValueError, match="符号链接越界"):
        build_manifest(
            tmp_path,
            bundle_version="0.2.0",
            arch="arm64",
            private_key=key,
        )


def test_signed_runtime_verifier_rejects_unlisted_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Ed25519PrivateKey.generate()
    (tmp_path / "studio").mkdir()
    (tmp_path / "studio" / "engine.py").write_text("ok = True\n", encoding="utf-8")
    manifest = build_manifest(
        tmp_path,
        bundle_version="0.2.0",
        arch="arm64",
        private_key=key,
    )
    write_signed_document(
        manifest,
        private_key=key,
        path=tmp_path / runtime_integrity.MANIFEST_NAME,
    )
    monkeypatch.setattr(
        runtime_integrity,
        "load_trusted_keys",
        lambda: {public_key_id(key.public_key()): key.public_key()},
    )
    runtime_integrity.verify_runtime(tmp_path, expected_arch="arm64")

    (tmp_path / "studio" / "injected.py").write_text("bad = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="文件集合不匹配"):
        runtime_integrity.verify_runtime(tmp_path, expected_arch="arm64")
