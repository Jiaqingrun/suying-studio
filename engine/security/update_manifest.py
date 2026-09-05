"""Signed release manifests and persistent anti-downgrade state."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

PRODUCT_ID = "com.qr.suying"
SCHEMA_VERSION = 1
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$"
DEFAULT_TRUSTED_KEYS = Path(__file__).with_name("trusted_release_keys.json")
DEFAULT_UPDATE_STATE = Path.home() / "Suying" / "runtime" / "security" / "update-state.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Artifact(StrictModel):
    path: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("artifact path 必须是安全的 POSIX 相对路径")
        return value


class ReleaseManifest(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    release_seq: int = Field(ge=1)
    version: str = Field(pattern=SEMVER_PATTERN)
    arch: Literal["arm64", "x86_64"]
    artifact: Artifact
    runtime_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    delivery_id: str = Field(min_length=16, max_length=128)
    customer_ref: str = Field(min_length=8, max_length=128)
    created_at: datetime
    expires_at: datetime | None = None
    key_id: str = Field(min_length=8, max_length=64)
    notes: str = Field(default="", max_length=4000)

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return value


class LatestPointer(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    release_seq: int = Field(ge=1)
    release_path: str = Field(min_length=1, max_length=255)
    release_sha256: str = Field(pattern=SHA256_PATTERN)
    key_id: str = Field(min_length=8, max_length=64)
    updated_at: datetime

    @field_validator("release_path")
    @classmethod
    def validate_release_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("release_path 必须是安全的 POSIX 相对路径")
        return value

    @field_validator("updated_at")
    @classmethod
    def require_updated_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("updated_at 必须包含时区")
        return value


class RuntimeFile(StrictModel):
    path: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def validate_runtime_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("runtime 文件必须是安全的 POSIX 相对路径")
        return value


class RuntimeManifest(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    bundle_version: str = Field(pattern=SEMVER_PATTERN)
    arch: Literal["arm64", "x86_64"]
    created_at: datetime
    key_id: str = Field(min_length=8, max_length=64)
    files: list[RuntimeFile] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_created_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须包含时区")
        return value


class LicensePayload(StrictModel):
    # v1 included daily production/upload caps. v2 removes them while keeping
    # v1 readable so already-issued offline licenses remain valid.
    schema_version: Literal[1, 2] = 2
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    license_id: str = Field(min_length=16, max_length=128)
    device_key_id: str = Field(pattern=SHA256_PATTERN)
    delivery_id: str = Field(min_length=16, max_length=128)
    customer_ref: str = Field(min_length=8, max_length=128)
    features: list[str] = Field(min_length=1)
    issued_at: datetime
    issue_seq: int = Field(ge=1)
    # Keep this legacy field optional-by-default so schema-v1 perpetual
    # licenses issued before trials continue to validate unchanged.
    perpetual: bool = True
    license_kind: Literal["trial", "perpetual", "term"] = "perpetual"
    trial_days: int | None = Field(default=None, ge=1, le=31)
    term_days: int | None = Field(default=None, ge=1, le=3660)
    # Legacy v1 signature fields. New v2 issuers must leave both absent.
    daily_produce_cap: int | None = Field(default=None, ge=1, le=1000)
    daily_upload_cap: int | None = Field(default=None, ge=1, le=1000)
    lock_mode: Literal["hard_all"] | None = None
    ops_unlock_allowed: bool = False
    expires_at: datetime | None = None
    clock_anchor: datetime | None = None
    key_id: str = Field(min_length=8, max_length=64)

    @field_validator("issued_at", "expires_at", "clock_anchor")
    @classmethod
    def require_issued_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("许可证时间必须包含时区")
        return value


class DepotObject(StrictModel):
    size: int = Field(gt=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    object_path: str = Field(pattern=r"^objects/sha256/[0-9a-f]{64}$")


class DepotComponent(StrictModel):
    component_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
    kind: Literal["app", "ollama", "ffmpeg", "ffprobe", "model_profile", "legal"]
    arch: Literal["arm64", "x86_64", "any"]
    files: dict[str, str] = Field(min_length=1)
    executable_files: list[str] = Field(default_factory=list)

    @field_validator("files")
    @classmethod
    def validate_component_files(cls, value: dict[str, str]) -> dict[str, str]:
        for relative, digest in value.items():
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in relative
                or not re.fullmatch(SHA256_PATTERN, digest)
            ):
                raise ValueError("depot component 文件映射无效")
        return value


class OfflineDepotManifest(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    depot_seq: int = Field(ge=1)
    created_at: datetime
    key_id: str = Field(min_length=8, max_length=64)
    objects: dict[str, DepotObject] = Field(min_length=1)
    components: list[DepotComponent] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_depot_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须包含时区")
        return value


class UpdateState(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    product_id: Literal["com.qr.suying"] = PRODUCT_ID
    highest_release_seq: int = Field(ge=1)
    release_digest: str = Field(pattern=SHA256_PATTERN)
    installed_at: datetime


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes_raw()
    return f"ed25519-{hashlib.sha256(raw).hexdigest()[:16]}"


def encode_public_key(public_key: Ed25519PublicKey) -> str:
    return base64.b64encode(public_key.public_bytes_raw()).decode("ascii")


def decode_public_key(value: str) -> Ed25519PublicKey:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise ValueError("Ed25519 公钥长度无效")
    return Ed25519PublicKey.from_public_bytes(raw)


def encode_private_key(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.private_bytes_raw()).decode("ascii")


def decode_private_key(value: str) -> Ed25519PrivateKey:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise ValueError("Ed25519 私钥长度无效")
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_trusted_keys(path: Path = DEFAULT_TRUSTED_KEYS) -> dict[str, Ed25519PublicKey]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(data) != {"schema_version", "product_id", "keys"}:
        raise ValueError("trusted keys schema 无效")
    if data["schema_version"] != SCHEMA_VERSION or data["product_id"] != PRODUCT_ID:
        raise ValueError("trusted keys 产品或版本不匹配")
    keys = data["keys"]
    if not isinstance(keys, dict) or not keys:
        raise ValueError("trusted keys 为空")
    return {str(key_id): decode_public_key(str(value)) for key_id, value in keys.items()}


def sign_document(document: BaseModel, private_key: Ed25519PrivateKey) -> tuple[bytes, str]:
    payload = canonical_json_bytes(document)
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    return payload, signature


def verify_document(
    payload: bytes,
    signature_text: str,
    *,
    key_id: str,
    trusted_keys: dict[str, Ed25519PublicKey],
) -> str:
    key = trusted_keys.get(key_id)
    if key is None:
        raise ValueError(f"不受信任的发布密钥: {key_id}")
    try:
        signature = base64.b64decode(signature_text.strip(), validate=True)
        key.verify(signature, payload)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("发布清单签名无效") from exc
    return sha256_bytes(payload)


def write_signed_document(
    document: BaseModel,
    *,
    private_key: Ed25519PrivateKey,
    path: Path,
) -> tuple[Path, Path]:
    payload, signature = sign_document(document, private_key)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    signature_path = path.with_name(f"{path.name}.sig")
    signature_path.write_text(signature + "\n", encoding="ascii")
    return path, signature_path


def verify_release(
    manifest_path: Path,
    signature_path: Path,
    *,
    trusted_keys: dict[str, Ed25519PublicKey] | None = None,
    expected_arch: str | None = None,
    expected_delivery_id: str | None = None,
    state_path: Path | None = DEFAULT_UPDATE_STATE,
    now: datetime | None = None,
) -> tuple[ReleaseManifest, str]:
    payload = Path(manifest_path).read_bytes()
    manifest = ReleaseManifest.model_validate_json(payload)
    digest = verify_document(
        payload,
        Path(signature_path).read_text(encoding="ascii"),
        key_id=manifest.key_id,
        trusted_keys=trusted_keys or load_trusted_keys(),
    )
    current_time = now or datetime.now(timezone.utc)
    if manifest.expires_at and current_time > manifest.expires_at:
        raise ValueError("发布清单已过期")
    if expected_arch and manifest.arch != expected_arch:
        raise ValueError(f"更新架构不匹配: expect={expected_arch}, got={manifest.arch}")
    if expected_delivery_id and manifest.delivery_id != expected_delivery_id:
        raise ValueError("更新交付水印与本机不匹配")
    if state_path:
        _enforce_anti_downgrade(manifest, digest, Path(state_path))
    return manifest, digest


def verify_latest(
    pointer_path: Path,
    signature_path: Path,
    *,
    trusted_keys: dict[str, Ed25519PublicKey] | None = None,
) -> tuple[LatestPointer, str]:
    payload = Path(pointer_path).read_bytes()
    pointer = LatestPointer.model_validate_json(payload)
    digest = verify_document(
        payload,
        Path(signature_path).read_text(encoding="ascii"),
        key_id=pointer.key_id,
        trusted_keys=trusted_keys or load_trusted_keys(),
    )
    return pointer, digest


def _enforce_anti_downgrade(
    manifest: ReleaseManifest,
    digest: str,
    state_path: Path,
) -> None:
    if not state_path.is_file():
        return
    state = UpdateState.model_validate_json(state_path.read_bytes())
    if manifest.release_seq < state.highest_release_seq:
        raise ValueError(
            f"拒绝降级: release_seq={manifest.release_seq} < {state.highest_release_seq}"
        )
    if (
        manifest.release_seq == state.highest_release_seq
        and digest != state.release_digest
    ):
        raise ValueError("拒绝同一 release_seq 替换为不同内容")


def record_installed_release(
    manifest: ReleaseManifest,
    release_digest: str,
    *,
    state_path: Path = DEFAULT_UPDATE_STATE,
) -> Path:
    state = UpdateState(
        highest_release_seq=manifest.release_seq,
        release_digest=release_digest,
        installed_at=datetime.now(timezone.utc),
    )
    target = Path(state_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(canonical_json_bytes(state))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temp_name).unlink(missing_ok=True)
        raise
    return target
