"""Fail-closed license check for the packaged Python engine."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from engine.security.entitlement import entitlement
from engine.security.update_manifest import (
    LicensePayload,
    load_trusted_keys,
    verify_document,
)

KEYCHAIN_SERVICE = "com.qr.suying.license"
DEVICE_SECRET_ACCOUNT = "device-binding-secret-v1"
ISSUE_SEQ_ACCOUNT = "highest-license-issue-seq"
KEYCHAIN_PATH_ENV = "SUYING_KEYCHAIN_PATH"


def _device_binding_dir() -> Path:
    return Path.home() / "Suying" / "runtime" / "security"


def _cached_device_key_id_path() -> Path:
    return _device_binding_dir() / "device-key-id"


def _cached_issue_seq_path() -> Path:
    return _device_binding_dir() / "highest-license-issue-seq"


def _explicit_keychain_path() -> str:
    raw = os.environ.get(KEYCHAIN_PATH_ENV, "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{KEYCHAIN_PATH_ENV} 必须指向现有的绝对钥匙串文件")
    return str(path)


def is_packaged_runtime() -> bool:
    if os.environ.get("SUYING_BUNDLED_RUNTIME") == "1":
        return True
    normalized = Path(__file__).resolve().as_posix()
    return ".app/Contents/Resources/runtime/studio/" in normalized


def _security_item_exists(account: str) -> bool:
    command = [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
    ]
    keychain_path = _explicit_keychain_path()
    if keychain_path:
        command.append(keychain_path)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _security_password(account: str, *, required: bool = True) -> str:
    command = [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
        "-w",
    ]
    keychain_path = _explicit_keychain_path()
    if keychain_path:
        command.append(keychain_path)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if required:
            raise RuntimeError(f"Keychain 缺少许可证设备状态: {account}")
        return ""
    value = result.stdout.strip()
    # SSH / non-GUI sessions can return exit 0 with an empty password while the
    # item still exists. Treat that as unreadable, never as "missing".
    if not value:
        if _security_item_exists(account):
            raise RuntimeError(
                f"Keychain 设备状态存在但当前会话无法读取: {account} "
                "（请在图形登录会话中打开速影，或通过 launchctl asuser/osascript 执行）"
            )
        if required:
            raise RuntimeError(f"Keychain 缺少许可证设备状态: {account}")
        return ""
    return value


def _set_security_password(account: str, value: str) -> None:
    command = [
        "/usr/bin/security",
        "add-generic-password",
        "-U",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account,
        "-w",
        value,
    ]
    keychain_path = _explicit_keychain_path()
    if keychain_path:
        command.append(keychain_path)
    subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )


def ensure_device_secret() -> str:
    # Remote install may inject the Keychain secret after reading it via a
    # console-capable osascript call. Never invent a replacement secret.
    injected = os.environ.get("SUYING_DEVICE_SECRET_B64", "").strip()
    if injected:
        raw = base64.b64decode(injected, validate=True)
        if len(raw) != 32:
            raise RuntimeError("SUYING_DEVICE_SECRET_B64 长度无效")
        return injected
    try:
        current = _security_password(DEVICE_SECRET_ACCOUNT, required=False)
    except RuntimeError:
        # Unreadable existing secret must never be replaced: that would silently
        # invalidate the already-issued device-bound license.
        raise
    if current:
        return current
    if _security_item_exists(DEVICE_SECRET_ACCOUNT):
        raise RuntimeError(
            "Keychain 设备密钥存在但当前会话无法读取；拒绝重新生成以免作废已签发许可证"
        )
    encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    _set_security_password(DEVICE_SECRET_ACCOUNT, encoded)
    return encoded


def _platform_uuid() -> str:
    result = subprocess.run(
        ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if "IOPlatformUUID" in line:
            value = line.split("=", 1)[-1].strip().strip('"')
            if value:
                return value
    raise RuntimeError("设备缺少 IOPlatformUUID")


def device_key_id() -> str:
    secret = base64.b64decode(
        ensure_device_secret(),
        validate=True,
    )
    if len(secret) != 32:
        raise RuntimeError("Keychain 设备密钥长度损坏")
    digest = hashlib.sha256()
    digest.update(b"suying-device-binding-v1\0")
    digest.update(_platform_uuid().encode())
    digest.update(b"\0")
    digest.update(secret)
    return digest.hexdigest()


def generate_license_request() -> dict[str, Any]:
    identifier = device_key_id()
    return {
        "schema_version": 1,
        "product_id": "com.qr.suying",
        "device_key_id": identifier,
        "machine_hint": identifier[:12],
        "created_at_unix": int(time.time()),
    }


def _write_text_private(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temp_name).unlink(missing_ok=True)
        raise


def remember_device_binding(device_key: str, issue_seq: int) -> None:
    _write_text_private(_cached_device_key_id_path(), device_key.strip() + "\n")
    _write_text_private(_cached_issue_seq_path(), str(int(issue_seq)) + "\n")


def cached_device_key_id() -> str:
    path = _cached_device_key_id_path()
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def cached_issue_seq() -> int:
    path = _cached_issue_seq_path()
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    return int(text) if text.isdigit() else 0


def bootstrap_device_binding_cache_from_license(path: Path) -> dict[str, Any]:
    """Seed on-disk binding from an already-installed license.

    Used when Keychain is unavailable over SSH but the machine already has a
    working licensed install. Does not mint a new device secret.
    """
    envelope: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(envelope) != {"payload_b64", "signature"}:
        raise RuntimeError("许可证 envelope schema 无效")
    payload = base64.b64decode(str(envelope["payload_b64"]), validate=True)
    license_data = LicensePayload.model_validate_json(payload)
    verify_document(
        payload,
        str(envelope["signature"]),
        key_id=license_data.key_id,
        trusted_keys=load_trusted_keys(),
    )
    remember_device_binding(license_data.device_key_id, license_data.issue_seq)
    return {
        "ok": True,
        "device_key_id": license_data.device_key_id,
        "issue_seq": license_data.issue_seq,
        "cache": str(_cached_device_key_id_path()),
    }


def verify_license_file(
    path: Path,
    *,
    allow_cached_device_binding: bool = False,
    enforce_entitlement: bool = True,
) -> LicensePayload:
    envelope: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"payload_b64", "signature"}:
        raise RuntimeError("许可证 envelope schema 无效")
    payload = base64.b64decode(str(envelope["payload_b64"]), validate=True)
    license_data = LicensePayload.model_validate_json(payload)
    verify_document(
        payload,
        str(envelope["signature"]),
        key_id=license_data.key_id,
        trusted_keys=load_trusted_keys(),
    )
    try:
        current_device = device_key_id()
        if license_data.device_key_id != current_device:
            raise RuntimeError("许可证不属于本机")
        remember_device_binding(current_device, license_data.issue_seq)
        highest_text = _security_password(ISSUE_SEQ_ACCOUNT, required=False)
        highest = int(highest_text) if highest_text.isdigit() else cached_issue_seq()
    except RuntimeError:
        if not allow_cached_device_binding:
            raise
        cached = cached_device_key_id()
        if not cached or cached != license_data.device_key_id:
            raise RuntimeError(
                "Keychain 不可用，且本机绑定缓存缺失或不匹配；请先在桌面打开速影"
            )
        highest = cached_issue_seq()
    if license_data.issue_seq < highest:
        raise RuntimeError(
            f"拒绝许可证降级: issue_seq={license_data.issue_seq} < {highest}"
        )
    status = entitlement(license_data)
    ops_unlocked = (
        os.environ.get("SUYING_OPS_LICENSE_UNLOCK") == "1"
        and license_data.license_kind in {"trial", "term"}
        and license_data.ops_unlock_allowed
    )
    if enforce_entitlement and not bool(status.get("authorized")) and not ops_unlocked:
        raise RuntimeError(f"LICENSE_LOCKED: {status.get('locked_reason') or '许可证不可用'}")
    return license_data


def license_status(
    path: Path | None = None,
    *,
    allow_cached_device_binding: bool = False,
) -> dict[str, Any]:
    """Read a license into an API-safe entitlement snapshot."""
    source = path or Path.home() / "Suying" / "runtime" / "security" / "license.suying-license"
    try:
        payload = verify_license_file(
            source,
            allow_cached_device_binding=allow_cached_device_binding,
            enforce_entitlement=False,
        )
    except RuntimeError as exc:
        text = str(exc)
        code = "INVALID"
        if "不属于本机" in text:
            code = "DEVICE_MISMATCH"
        elif "未安装" in text or "不存在" in text:
            code = "MISSING"
        return {
            "authorized": False,
            "license_kind": "",
            "locked_reason": text,
            "code": code,
            "license_id": None,
            "issue_seq": None,
            "features": [],
            "device_key_id": None,
            "delivery_id": None,
            "customer_ref": None,
            "expires_at": None,
            "remaining_sec": None,
            "trial_remaining_sec": None,
            "ops_unlock_allowed": False,
        }
    ent = entitlement(payload)
    return {
        **ent,
        "license_id": payload.license_id,
        "issue_seq": payload.issue_seq,
        "features": payload.features,
        "device_key_id": payload.device_key_id,
        "delivery_id": payload.delivery_id,
        "customer_ref": payload.customer_ref,
        "expires_at": ent.get("expires_at"),
        "remaining_sec": ent.get("remaining_sec"),
        "trial_remaining_sec": ent.get("trial_remaining_sec"),
        "code": ent.get("code") or "",
    }


def current_runtime_license() -> LicensePayload | None:
    """Return the installed active payload, or None for unpackaged development."""
    path = Path.home() / "Suying" / "runtime" / "security" / "license.suying-license"
    if not path.is_file():
        if is_packaged_runtime():
            raise RuntimeError("LICENSE_REQUIRED: 未安装本机许可证")
        return None
    return verify_license_file(
        path,
        allow_cached_device_binding=os.environ.get("SUYING_ALLOW_CACHED_LICENSE_BINDING") == "1",
    )


def install_license_file(
    source: Path,
    *,
    allow_cached_device_binding: bool = False,
) -> Path:
    payload = verify_license_file(
        source,
        allow_cached_device_binding=allow_cached_device_binding,
    )
    target = Path.home() / "Suying" / "runtime" / "security" / "license.suying-license"
    target.parent.mkdir(parents=True, exist_ok=True)
    previous_seq = _security_password(ISSUE_SEQ_ACCOUNT, required=False) or str(
        cached_issue_seq()
    )
    try:
        _set_security_password(ISSUE_SEQ_ACCOUNT, str(payload.issue_seq))
    except Exception:
        # Keychain may be unavailable over SSH; on-disk cache still tracks seq.
        pass
    remember_device_binding(payload.device_key_id, payload.issue_seq)
    fd, temp_name = tempfile.mkstemp(prefix=".license.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            _set_security_password(ISSUE_SEQ_ACCOUNT, previous_seq)
        except Exception:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target


def require_runtime_license() -> None:
    if not is_packaged_runtime():
        return
    path = Path.home() / "Suying" / "runtime" / "security" / "license.suying-license"
    if not path.is_file():
        raise RuntimeError(f"LICENSE_REQUIRED: 未安装本机许可证 ({path})")
    allow_cached = os.environ.get("SUYING_ALLOW_CACHED_LICENSE_BINDING") == "1"
    verify_license_file(path, allow_cached_device_binding=allow_cached)
