#!/usr/bin/env python3
"""Build and sign a complete manifest for an embedded app runtime."""

from __future__ import annotations

import argparse
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from engine.security.update_manifest import (  # noqa: E402
    RuntimeFile,
    RuntimeManifest,
    public_key_id,
    sha256_file,
    write_signed_document,
)
from scripts.sign_update_release import (  # noqa: E402
    DEFAULT_ACCOUNT,
    load_keychain_private_key,
)

MANIFEST_NAME = "RUNTIME_MANIFEST.json"
SIGNATURE_NAME = f"{MANIFEST_NAME}.sig"


def build_manifest(
    runtime_root: Path,
    *,
    bundle_version: str,
    arch: str,
    account: str = DEFAULT_ACCOUNT,
    private_key: Ed25519PrivateKey | None = None,
) -> RuntimeManifest:
    root = runtime_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"runtime 根目录不存在: {root}")
    signing_key = private_key or load_keychain_private_key(account)
    files: list[RuntimeFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() and path.is_dir():
            raise ValueError(f"runtime 禁止目录符号链接: {path}")
        if path.name in {MANIFEST_NAME, SIGNATURE_NAME} or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"runtime 符号链接越界: {path}")
        files.append(
            RuntimeFile(
                path=path.relative_to(root).as_posix(),
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return RuntimeManifest(
        bundle_version=bundle_version,
        arch=arch,
        created_at=datetime.now(timezone.utc),
        key_id=public_key_id(signing_key.public_key()),
        files=files,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--bundle-version", required=True)
    parser.add_argument(
        "--arch",
        choices=("arm64", "x86_64"),
        default=("arm64" if platform.machine() == "arm64" else "x86_64"),
    )
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    args = parser.parse_args()

    manifest = build_manifest(
        args.runtime_root,
        bundle_version=args.bundle_version,
        arch=args.arch,
        account=args.account,
    )
    private_key = load_keychain_private_key(args.account)
    path, signature = write_signed_document(
        manifest,
        private_key=private_key,
        path=args.runtime_root / MANIFEST_NAME,
    )
    print(f"{path}\n{signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
