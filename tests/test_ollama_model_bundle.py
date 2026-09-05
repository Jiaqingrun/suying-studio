from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ollama_model_bundle", ROOT / "scripts/ollama_model_bundle.py")
assert SPEC and SPEC.loader
bundle_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_tool)


def add_model(root: Path, name: str, payload: bytes) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    blob = root / "blobs" / f"sha256-{digest}"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": f"sha256:{digest}", "size": len(payload)},
        "layers": [],
    }
    target = root / bundle_tool.manifest_relative(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest), encoding="utf-8")


def test_profile_bundle_contains_exact_manifests_and_imports_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    add_model(source, "nomic-embed-text", b"embed")
    add_model(source, "qwen3.5:9b", b"vision")
    monkeypatch.setattr(bundle_tool.platform, "machine", lambda: "arm64")

    output = tmp_path / "bundles" / "pro"
    metadata = bundle_tool.create_bundle("pro", output, source)
    assert [item["name"] for item in metadata["models"]] == ["nomic-embed-text", "qwen3.5:9b"]
    assert metadata["narration_included"] is False

    target = tmp_path / "target"
    result = bundle_tool.import_bundle(output, target, expected_profile="pro", expected_arch="arm64")
    assert result["ok"] is True
    assert (target / bundle_tool.manifest_relative("qwen3.5:9b")).is_file()
    assert not list(target.rglob("*.importing-*"))


def test_bundle_rejects_tampered_blob_and_wrong_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    add_model(source, "nomic-embed-text", b"embed")
    monkeypatch.setattr(bundle_tool.platform, "machine", lambda: "arm64")
    output = tmp_path / "lite"
    bundle_tool.create_bundle("lite", output, source)

    with pytest.raises(ValueError, match="档位不匹配"):
        bundle_tool.verify_bundle(output, expected_profile="pro", expected_arch="arm64")
    blob = next((output / "blobs").iterdir())
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA256"):
        bundle_tool.verify_bundle(output, expected_profile="lite", expected_arch="arm64")


@pytest.mark.parametrize(
    "malicious",
    [
        "/tmp/escape",
        "../escape",
        "blobs/../escape",
        r"blobs\sha256-" + "0" * 64,
    ],
)
def test_bundle_rejects_unsafe_file_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malicious: str,
) -> None:
    source = tmp_path / "source"
    add_model(source, "nomic-embed-text", b"embed")
    monkeypatch.setattr(bundle_tool.platform, "machine", lambda: "arm64")
    output = tmp_path / "lite"
    bundle_tool.create_bundle("lite", output, source)
    metadata_path = output / "bundle.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["files"][malicious] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="非法|非规范"):
        bundle_tool.verify_bundle(output)


def test_bundle_rejects_symlink_and_unreferenced_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    add_model(source, "nomic-embed-text", b"embed")
    monkeypatch.setattr(bundle_tool.platform, "machine", lambda: "arm64")
    output = tmp_path / "lite"
    bundle_tool.create_bundle("lite", output, source)

    link = output / "linked"
    link.symlink_to(output / "bundle.json")
    with pytest.raises(ValueError, match="符号链接"):
        bundle_tool.verify_bundle(output)
    link.unlink()

    extra = output / "blobs" / f"sha256-{'0' * 64}"
    extra.write_bytes(b"extra")
    metadata_path = output / "bundle.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["files"][extra.relative_to(output).as_posix()] = hashlib.sha256(b"extra").hexdigest()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="未引用"):
        bundle_tool.verify_bundle(output)


def test_import_rolls_back_all_new_files_on_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    add_model(source, "nomic-embed-text", b"embed")
    monkeypatch.setattr(bundle_tool.platform, "machine", lambda: "arm64")
    output = tmp_path / "lite"
    bundle_tool.create_bundle("lite", output, source)
    target = tmp_path / "target"

    real_replace = os.replace
    commits = 0

    def fail_second_commit(src: str | Path, dst: str | Path) -> None:
        nonlocal commits
        if str(src).startswith(str(target / ".suying-model-import-")):
            commits += 1
            if commits == 2:
                raise OSError("simulated commit failure")
        real_replace(src, dst)

    monkeypatch.setattr(bundle_tool.os, "replace", fail_second_commit)
    with pytest.raises(OSError, match="simulated"):
        bundle_tool.import_bundle(output, target, expected_profile="lite", expected_arch="arm64")

    assert not list((target / "blobs").glob("*"))
    assert not list((target / "manifests").rglob("*"))
    assert not list(target.glob(".suying-model-import-*"))
