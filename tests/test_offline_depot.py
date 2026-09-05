from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine.security import offline_depot
from engine.security.update_manifest import public_key_id
from scripts.build_offline_depot import build_depot


def _legal_component(tmp_path: Path, component_ids: list[str]) -> str:
    legal = tmp_path / "legal"
    (legal / "licenses").mkdir(parents=True)
    (legal / "licenses" / "test.txt").write_text("test license\n", encoding="utf-8")
    (legal / "DELIVERY_NOTICE.zh-CN.md").write_text("测试交付说明\n", encoding="utf-8")
    (legal / "THIRD_PARTY_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": "suying.third-party.v1",
                "distribution_status": "ready",
                "covered_components": component_ids,
                "packages": [
                    {
                        "name": "test-package",
                        "version": "1.0",
                        "license_expression": "MIT",
                        "source_url": "https://example.invalid/source",
                        "component_ids": component_ids,
                        "license_paths": ["licenses/test.txt"],
                        "source_required": False,
                        "corresponding_source_paths": [],
                    }
                ],
                "gaps": [],
            }
        ),
        encoding="utf-8",
    )
    return f"legal:legal:any:{legal}"


def test_offline_depot_deduplicates_and_detects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Ed25519PrivateKey.generate()
    trusted = {public_key_id(key.public_key()): key.public_key()}
    monkeypatch.setattr(offline_depot, "load_trusted_keys", lambda: trusted)
    source = tmp_path / "source"
    source.mkdir()
    app = source / "suying.zip"
    ffmpeg_bundle = source / "ffmpeg-bundle"
    (ffmpeg_bundle / "bin").mkdir(parents=True)
    (ffmpeg_bundle / "legal").mkdir()
    ffmpeg = ffmpeg_bundle / "bin" / "ffmpeg"
    ffprobe = ffmpeg_bundle / "bin" / "ffprobe"
    app.write_bytes(b"app")
    ffmpeg.write_bytes(b"same-static-binary")
    ffprobe.write_bytes(b"same-static-binary")
    ffmpeg.chmod(0o755)
    ffprobe.chmod(0o755)
    (ffmpeg_bundle / "legal" / "FFMPEG_BUNDLE_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": "suying.ffmpeg-bundle-legal.v1",
                "distribution_status": "ready",
                "packages": [{"name": "test"}],
                "gaps": [],
            }
        ),
        encoding="utf-8",
    )
    depot = tmp_path / "depot"

    manifest = build_depot(
        depot,
        depot_seq=1,
        component_specs=[
            f"app:app:arm64:{app}",
            f"ffmpeg:ffmpeg:arm64:{ffmpeg_bundle}",
            _legal_component(tmp_path, ["app", "ffmpeg"]),
        ],
        private_key=key,
    )

    assert len(manifest.objects) < sum(len(item.files) for item in manifest.components)
    verified = offline_depot.verify_depot(depot)
    ffmpeg_component = next(item for item in verified.components if item.component_id == "ffmpeg")
    target = offline_depot.materialize_component(
        depot,
        verified,
        ffmpeg_component,
        tmp_path / "materialized",
    )
    assert (target / "bin" / "ffmpeg").read_bytes() == b"same-static-binary"
    assert (target / "bin" / "ffmpeg").stat().st_mode & 0o111

    object_path = depot / next(iter(verified.objects.values())).object_path
    object_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="CAS 对象损坏"):
        offline_depot.verify_depot(depot)


def test_offline_depot_rejects_missing_legal_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "app.zip"
    app.write_bytes(b"app")
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        offline_depot,
        "load_trusted_keys",
        lambda: {public_key_id(key.public_key()): key.public_key()},
    )
    with pytest.raises(ValueError, match="legal 组件"):
        build_depot(
            tmp_path / "depot",
            depot_seq=1,
            component_specs=[f"app:app:arm64:{app}"],
            private_key=key,
        )


def test_offline_depot_rejects_missing_corresponding_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "app.zip"
    app.write_bytes(b"app")
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        offline_depot,
        "load_trusted_keys",
        lambda: {public_key_id(key.public_key()): key.public_key()},
    )
    legal_spec = _legal_component(tmp_path, ["app"])
    legal_root = Path(legal_spec.split(":", 3)[-1])
    manifest_path = legal_root / "THIRD_PARTY_MANIFEST.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["packages"][0]["license_expression"] = "GPL-3.0-or-later"
    document["packages"][0]["source_required"] = True
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="corresponding_source_paths"):
        build_depot(
            tmp_path / "depot",
            depot_seq=1,
            component_specs=[f"app:app:arm64:{app}", legal_spec],
            private_key=key,
        )


def test_offline_depot_rejects_blocked_ffmpeg_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "ffmpeg"
    (bundle / "legal").mkdir(parents=True)
    (bundle / "bin").mkdir()
    (bundle / "bin" / "ffmpeg").write_bytes(b"ffmpeg")
    (bundle / "legal" / "FFMPEG_BUNDLE_MANIFEST.json").write_text(
        json.dumps(
            {
                "schema_version": "suying.ffmpeg-bundle-legal.v1",
                "distribution_status": "blocked",
                "packages": [{"name": "ffmpeg"}],
                "gaps": [{"material": "source"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ffmpeg bundle 法务材料未达到 ready"):
        build_depot(
            tmp_path / "depot",
            depot_seq=1,
            component_specs=[f"ffmpeg:ffmpeg:arm64:{bundle}"],
            private_key=Ed25519PrivateKey.generate(),
        )


def test_offline_depot_rejects_unsafe_component_id(tmp_path: Path) -> None:
    source = tmp_path / "app.zip"
    source.write_bytes(b"app")
    with pytest.raises(ValueError, match="pattern"):
        build_depot(
            tmp_path / "depot",
            depot_seq=1,
            component_specs=[f"../escape:app:arm64:{source}"],
            private_key=Ed25519PrivateKey.generate(),
        )


def test_repository_legal_manifest_is_ready_without_retired_runtimes() -> None:
    docs = Path(__file__).resolve().parents[1] / "docs"
    document = json.loads((docs / "THIRD_PARTY_MANIFEST.json").read_text(encoding="utf-8"))

    assert document["distribution_status"] == "ready"
    unresolved = {item["package"] for item in document["gaps"]}
    assert not {
        "FFmpeg",
        "x264",
        "x265",
        "LAME",
        "mpg123",
    }.intersection(unresolved)
    assert "nomic-embed-text model" not in unresolved
    assert "qwen3.5:9b model" not in unresolved
    assert unresolved == set()
    package_names = {item["name"] for item in document["packages"]}
    assert "Remotion runtime family" not in package_names
    assert "OpenMontage creative runtime" not in package_names
    assert "Cursor SDK" not in package_names

    source_hashes = {
        "FFmpeg": "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c",
        "x265": "40b1ea0453e0309f0eba934e0ddf533f8f6295966679e8894e8f1c1c8d5e1210",
        "LAME": "7578af6eebd578b2bd64e468fac4ae1f03670a7e028166e67f855674b9b6aeac",
        "mpg123": "929a7c18ba662b8927aed4de229ad9ae8ab2b4806dd0f30b90113eb1b4e2195a",
    }
    packages = {item["name"]: item for item in document["packages"]}
    for name, digest in source_hashes.items():
        package = packages[name]
        assert package["source_sha256"] == digest
        [relative] = package["corresponding_source_paths"]
        assert hashlib.sha256((docs / relative).read_bytes()).hexdigest() == digest

    x264 = packages["x264"]
    assert x264["source_revision"] == "b35605ace3ddf7c1a5d67a2eb553f034aef41d55"
    x264_archive = docs / x264["corresponding_source_paths"][0]
    assert hashlib.sha256(x264_archive.read_bytes()).hexdigest() == (
        x264["source_archive_sha256"]
    )

    for name in {"FFmpeg", "x264", "x265", "LAME", "mpg123"}:
        package = packages[name]
        assert (docs / package["formula_path"]).is_file()
        receipt = json.loads(
            (docs / package["install_receipt_path"]).read_text(encoding="utf-8")
        )
        assert receipt["source"]["spec"] == "stable"
    x264_formula = (docs / x264["formula_path"]).read_text(encoding="utf-8")
    assert 'version "r3222"' in x264_formula
    assert 'revision: "b35605ace3ddf7c1a5d67a2eb553f034aef41d55"' in x264_formula

    models = {
        item["name"]: item
        for item in document["packages"]
        if item["name"] in {"nomic-embed-text model", "qwen3.5:9b model"}
    }
    assert models["nomic-embed-text model"]["local_provenance"]["manifest_sha256"] == (
        "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
    )
    assert models["qwen3.5:9b model"]["local_provenance"]["manifest_sha256"] == (
        "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
    )
    for package in models.values():
        assert package["local_provenance"]["notice_layer_present"] is False
        for relative in package["license_paths"]:
            material = (docs / relative).read_bytes()
            assert hashlib.sha256(material).hexdigest() == (
                package["local_provenance"]["license_file_sha256"]
            )
            text = material.decode("utf-8")
            assert "Apache License" in text
            assert "Version 2.0, January 2004" in text


def test_offline_depot_validates_model_bundle_before_cas(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "lite"
    bundle.mkdir()
    (bundle / "bundle.json").write_text(
        json.dumps(
            {
                "schema_version": "suying.ollama.offline-bundle.v1",
                "profile_id": "lite",
                "source_arch": "arm64",
                "files": {"blobs/sha256-" + "0" * 64: "0" * 64},
                "models": [{"manifest": "manifests/registry.ollama.ai/library/x/latest"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="文件清单不闭合"):
        build_depot(
            tmp_path / "depot",
            depot_seq=1,
            component_specs=[f"models-lite:model_profile:arm64:{bundle}"],
            private_key=Ed25519PrivateKey.generate(),
        )
