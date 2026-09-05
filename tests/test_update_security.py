from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine.security.update_manifest import (
    Artifact,
    LatestPointer,
    ReleaseManifest,
    UpdateState,
    canonical_json_bytes,
    public_key_id,
    sha256_bytes,
    verify_release,
    write_signed_document,
)


def _manifest(
    key: Ed25519PrivateKey,
    *,
    release_seq: int = 1,
    artifact_sha: str = "a" * 64,
) -> ReleaseManifest:
    return ReleaseManifest(
        release_seq=release_seq,
        version="0.2.0",
        arch="arm64",
        artifact=Artifact(path="suying.zip", size=123, sha256=artifact_sha),
        runtime_manifest_sha256="b" * 64,
        delivery_id="delivery-0123456789abcdef",
        customer_ref="customer-opaque-0001",
        created_at=datetime.now(timezone.utc),
        key_id=public_key_id(key.public_key()),
    )


def _write_release(
    root: Path,
    key: Ed25519PrivateKey,
    manifest: ReleaseManifest,
) -> tuple[Path, Path]:
    return write_signed_document(
        manifest,
        private_key=key,
        path=root / "release.json",
    )


def test_signed_release_verifies_and_binds_arch_and_delivery(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    manifest_path, signature_path = _write_release(tmp_path, key, _manifest(key))
    trusted = {public_key_id(key.public_key()): key.public_key()}

    verified, digest = verify_release(
        manifest_path,
        signature_path,
        trusted_keys=trusted,
        expected_arch="arm64",
        expected_delivery_id="delivery-0123456789abcdef",
        state_path=None,
    )

    assert verified.release_seq == 1
    assert digest == sha256_bytes(manifest_path.read_bytes())
    with pytest.raises(ValueError, match="交付水印"):
        verify_release(
            manifest_path,
            signature_path,
            trusted_keys=trusted,
            expected_delivery_id="delivery-other-0123456789",
            state_path=None,
        )


def test_release_tamper_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    wrong = Ed25519PrivateKey.generate()
    manifest_path, signature_path = _write_release(tmp_path, key, _manifest(key))
    trusted = {public_key_id(key.public_key()): wrong.public_key()}

    with pytest.raises(ValueError, match="签名无效"):
        verify_release(
            manifest_path,
            signature_path,
            trusted_keys=trusted,
            state_path=None,
        )

    trusted = {public_key_id(key.public_key()): key.public_key()}
    manifest_path.write_bytes(manifest_path.read_bytes().replace(b"0.2.0", b"0.2.1"))
    with pytest.raises(ValueError, match="签名无效"):
        verify_release(
            manifest_path,
            signature_path,
            trusted_keys=trusted,
            state_path=None,
        )


def test_release_downgrade_and_same_sequence_replacement_are_rejected(
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    trusted = {public_key_id(key.public_key()): key.public_key()}
    installed = _manifest(key, release_seq=3)
    installed_payload = canonical_json_bytes(installed)
    state = UpdateState(
        highest_release_seq=3,
        release_digest=sha256_bytes(installed_payload),
        installed_at=datetime.now(timezone.utc),
    )
    state_path = tmp_path / "state.json"
    state_path.write_bytes(canonical_json_bytes(state))

    old_path, old_sig = _write_release(
        tmp_path / "old",
        key,
        _manifest(key, release_seq=2),
    )
    with pytest.raises(ValueError, match="拒绝降级"):
        verify_release(
            old_path,
            old_sig,
            trusted_keys=trusted,
            state_path=state_path,
        )

    swapped_path, swapped_sig = _write_release(
        tmp_path / "swapped",
        key,
        _manifest(key, release_seq=3, artifact_sha="c" * 64),
    )
    with pytest.raises(ValueError, match="同一 release_seq"):
        verify_release(
            swapped_path,
            swapped_sig,
            trusted_keys=trusted,
            state_path=state_path,
        )


def test_app_update_accepts_only_complete_signed_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.ops import app_update
    from engine.security import update_manifest

    key = Ed25519PrivateKey.generate()
    trusted = {public_key_id(key.public_key()): key.public_key()}
    root = tmp_path / "carrier"
    release_dir = root / "app" / "releases" / "0.2.0" / "build"
    package = release_dir / "suying.zip"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"signed package")
    manifest = _manifest(
        key,
        artifact_sha=update_manifest.sha256_file(package),
    ).model_copy(
        update={
            "artifact": Artifact(
                path=package.name,
                size=package.stat().st_size,
                sha256=update_manifest.sha256_file(package),
            )
        }
    )
    release_path, _ = _write_release(release_dir, key, manifest)
    latest = LatestPointer(
        release_seq=manifest.release_seq,
        release_path="releases/0.2.0/build/release.json",
        release_sha256=update_manifest.sha256_file(release_path),
        key_id=manifest.key_id,
        updated_at=datetime.now(timezone.utc),
    )
    write_signed_document(
        latest,
        private_key=key,
        path=root / "app" / "latest.json",
    )
    monkeypatch.setattr(app_update, "discover_carrier_roots", lambda: [root])
    monkeypatch.setattr(app_update.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(update_manifest, "load_trusted_keys", lambda: trusted)

    verified, error = app_update._verified_update(state_path=None, license_path=None)

    assert error is None
    assert verified
    assert verified["package_path"] == package.resolve()


def test_app_update_rejects_unsigned_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from engine.ops import app_update

    root = tmp_path / "carrier"
    app = root / "app"
    app.mkdir(parents=True)
    (app / "latest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(app_update, "discover_carrier_roots", lambda: [root])

    verified, error = app_update._verified_update(state_path=None, license_path=None)

    assert verified is None
    assert error and "latest.json.sig" in error
