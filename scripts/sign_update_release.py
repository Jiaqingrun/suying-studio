#!/usr/bin/env python3
"""Create and sign immutable 速影 release metadata using a Keychain key."""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from engine.security.update_manifest import (  # noqa: E402
    DEFAULT_TRUSTED_KEYS,
    PRODUCT_ID,
    Artifact,
    LicensePayload,
    LatestPointer,
    ReleaseManifest,
    canonical_json_bytes,
    encode_private_key,
    encode_public_key,
    public_key_id,
    sha256_file,
    write_signed_document,
)

KEYCHAIN_SERVICE = "com.qr.suying.release-signing"
DEFAULT_ACCOUNT = "release-v1"
KEYCHAIN_PATH_ENV = "SUYING_KEYCHAIN_PATH"


def _explicit_keychain_path() -> str:
    raw = os.environ.get(KEYCHAIN_PATH_ENV, "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{KEYCHAIN_PATH_ENV} 必须指向现有的绝对钥匙串文件")
    return str(path)


def _security(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["/usr/bin/security", *args]
    keychain_path = _explicit_keychain_path()
    if keychain_path:
        command.append(keychain_path)
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def load_keychain_private_key(account: str) -> Ed25519PrivateKey:
    result = _security(
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
        "-w",
    )
    from engine.security.update_manifest import decode_private_key

    return decode_private_key(result.stdout.strip())


def init_key(*, account: str, trusted_keys_path: Path, rotate: bool = False) -> dict[str, str]:
    existing = _security(
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
        "-w",
        check=False,
    )
    if existing.returncode == 0 and not rotate:
        private_key = load_keychain_private_key(account)
    else:
        private_key = Ed25519PrivateKey.generate()
        encoded = encode_private_key(private_key)
        _security(
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            account,
            "-w",
            encoded,
        )
    public_key = private_key.public_key()
    key_id = public_key_id(public_key)
    path = trusted_keys_path.expanduser().resolve()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"schema_version": 1, "product_id": PRODUCT_ID, "keys": {}}
    if data.get("schema_version") != 1 or data.get("product_id") != PRODUCT_ID:
        raise ValueError("trusted keys 文件产品或 schema 不匹配")
    keys = data.setdefault("keys", {})
    if not isinstance(keys, dict):
        raise ValueError("trusted keys 的 keys 字段无效")
    keys[key_id] = encode_public_key(public_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"key_id": key_id, "trusted_keys": str(path), "account": account}


def sign_release(args: argparse.Namespace) -> dict[str, object]:
    package = args.package.expanduser().resolve()
    runtime_manifest = args.runtime_manifest.expanduser().resolve()
    if not package.is_file():
        raise ValueError(f"更新包不存在: {package}")
    if not runtime_manifest.is_file():
        raise ValueError(f"runtime manifest 不存在: {runtime_manifest}")
    private_key = load_keychain_private_key(args.account)
    key_id = public_key_id(private_key.public_key())
    trusted = json.loads(args.trusted_keys.read_text(encoding="utf-8"))
    if key_id not in (trusted.get("keys") or {}):
        raise ValueError(f"签名密钥 {key_id} 未写入 trusted keys，拒绝生成不可验证发布")

    now = datetime.now(timezone.utc)
    delivery_id = args.delivery_id or f"delivery-{secrets.token_hex(16)}"
    release_path = (
        args.release_path
        or f"releases/{args.version}/{now.strftime('%Y%m%dT%H%M%SZ')}/release.json"
    )
    manifest = ReleaseManifest(
        release_seq=args.release_seq,
        version=args.version,
        arch=args.arch,
        artifact=Artifact(
            path=package.name,
            size=package.stat().st_size,
            sha256=sha256_file(package),
        ),
        runtime_manifest_sha256=sha256_file(runtime_manifest),
        delivery_id=delivery_id,
        customer_ref=args.customer_ref,
        created_at=now,
        key_id=key_id,
        notes=args.notes,
    )
    output = args.output.expanduser().resolve()
    release_file, release_sig = write_signed_document(
        manifest,
        private_key=private_key,
        path=output / release_path,
    )
    latest = LatestPointer(
        release_seq=manifest.release_seq,
        release_path=release_path,
        release_sha256=sha256_file(release_file),
        key_id=key_id,
        updated_at=now,
    )
    latest_file, latest_sig = write_signed_document(
        latest,
        private_key=private_key,
        path=output / "latest.json",
    )
    return {
        "ok": True,
        "release": str(release_file),
        "release_signature": str(release_sig),
        "latest": str(latest_file),
        "latest_signature": str(latest_sig),
        "release_seq": manifest.release_seq,
        "delivery_id": delivery_id,
        "key_id": key_id,
    }


def sign_license(args: argparse.Namespace) -> dict[str, object]:
    request = json.loads(args.request.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1 or request.get("product_id") != PRODUCT_ID:
        raise ValueError("许可请求产品或 schema 不匹配")
    device_key_id = str(request.get("device_key_id") or "")
    if len(device_key_id) != 64:
        raise ValueError("device_key_id 必须为 64 位十六进制摘要")
    private_key = load_keychain_private_key(args.account)
    key_id = public_key_id(private_key.public_key())
    license_kind = str(getattr(args, "license_kind", "term") or "term")
    trial_days = int(getattr(args, "trial_days", 3))
    term_days = int(getattr(args, "term_days", 365))
    flex = os.environ.get("SUYING_LICENSE_TEST_FLEX_TERM", "").strip() == "1"
    if license_kind == "trial" and trial_days != 3:
        raise ValueError("体验期固定为 3×24 小时")
    if license_kind == "term" and term_days != 365 and not flex:
        raise ValueError("年期固定为 365 天（测试可用 SUYING_LICENSE_TEST_FLEX_TERM=1）")
    if license_kind not in {"trial", "perpetual", "term"}:
        raise ValueError(f"不支持的 license_kind: {license_kind}")
    now = datetime.now(timezone.utc)
    trial = license_kind == "trial"
    term = license_kind == "term"
    time_bound = trial or term
    payload = LicensePayload(
        license_id=f"license-{secrets.token_hex(16)}",
        device_key_id=device_key_id,
        delivery_id=args.delivery_id or f"delivery-{secrets.token_hex(16)}",
        customer_ref=args.customer_ref,
        features=sorted(set(args.feature or ["core"])),
        issued_at=now,
        issue_seq=args.issue_seq,
        perpetual=not time_bound,
        license_kind=license_kind,
        trial_days=3 if trial else None,
        term_days=term_days if term else None,
        lock_mode="hard_all" if time_bound else None,
        ops_unlock_allowed=time_bound,
        # Signed issuance is the activation anchor; late import never extends the window.
        expires_at=(now + timedelta(days=(3 if trial else term_days))) if time_bound else None,
        clock_anchor=now if time_bound else None,
        key_id=key_id,
    )
    payload_bytes = canonical_json_bytes(payload)
    envelope = {
        "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        "signature": base64.b64encode(private_key.sign(payload_bytes)).decode("ascii"),
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "license": str(output),
        "license_id": payload.license_id,
        "device_key_id": payload.device_key_id,
        "delivery_id": payload.delivery_id,
        "issue_seq": payload.issue_seq,
        "license_kind": payload.license_kind,
        "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
        "key_id": key_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-key", help="生成/读取 Keychain 发布密钥并写入公钥")
    init.add_argument("--account", default=DEFAULT_ACCOUNT)
    init.add_argument("--trusted-keys", type=Path, default=DEFAULT_TRUSTED_KEYS)
    init.add_argument("--rotate", action="store_true")

    sign = sub.add_parser("sign-release", help="签名 release.json 和 latest.json")
    sign.add_argument("--account", default=DEFAULT_ACCOUNT)
    sign.add_argument("--trusted-keys", type=Path, default=DEFAULT_TRUSTED_KEYS)
    sign.add_argument("--package", type=Path, required=True)
    sign.add_argument("--runtime-manifest", type=Path, required=True)
    sign.add_argument("--release-seq", type=int, required=True)
    sign.add_argument("--version", required=True)
    sign.add_argument(
        "--arch",
        choices=("arm64", "x86_64"),
        default=("arm64" if platform.machine() == "arm64" else "x86_64"),
    )
    sign.add_argument("--customer-ref", required=True)
    sign.add_argument("--delivery-id")
    sign.add_argument("--release-path")
    sign.add_argument("--notes", default="")
    sign.add_argument("--output", type=Path, required=True)

    license_parser = sub.add_parser("sign-license", help="签发单机年期、永久或三日体验许可证")
    license_parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    license_parser.add_argument("--request", type=Path, required=True)
    license_parser.add_argument("--customer-ref", required=True)
    license_parser.add_argument("--delivery-id")
    license_parser.add_argument("--issue-seq", type=int, required=True)
    license_parser.add_argument(
        "--license-kind",
        choices=("term", "perpetual", "trial"),
        default="term",
        help="正式默认 term（365 天）；perpetual 为特批/祖父；trial 为三日体验",
    )
    license_parser.add_argument("--trial-days", type=int, default=3)
    license_parser.add_argument("--term-days", type=int, default=365)
    license_parser.add_argument("--feature", action="append")
    license_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "init-key":
        result = init_key(
            account=args.account,
            trusted_keys_path=args.trusted_keys,
            rotate=args.rotate,
        )
    elif args.command == "sign-release":
        result = sign_release(args)
    else:
        result = sign_license(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
