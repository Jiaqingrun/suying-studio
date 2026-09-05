from __future__ import annotations

import argparse
import base64
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from engine.security.update_manifest import LicensePayload
from engine.security.trial import entitlement
from engine.security import license as runtime_license
from scripts import sign_update_release


def test_runtime_license_uses_explicit_keychain_in_isolated_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    keychain = tmp_path / "login.keychain-db"
    keychain.touch()
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "value\n", "")

    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv(runtime_license.KEYCHAIN_PATH_ENV, str(keychain))
    monkeypatch.setattr(runtime_license.subprocess, "run", fake_run)

    assert runtime_license._security_password("device-state") == "value"
    assert calls == [
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            runtime_license.KEYCHAIN_SERVICE,
            "-a",
            "device-state",
            "-w",
            str(keychain),
        ]
    ]


def test_python_device_secret_unreadable_never_generates(monkeypatch) -> None:
    writes: list[tuple[str, str]] = []

    def unreadable(*args, **kwargs):
        raise RuntimeError("存在但当前会话无法读取")

    monkeypatch.delenv("SUYING_DEVICE_SECRET_B64", raising=False)
    monkeypatch.setattr(runtime_license, "_security_password", unreadable)
    monkeypatch.setattr(
        runtime_license,
        "_set_security_password",
        lambda account, value: writes.append((account, value)),
    )

    with pytest.raises(RuntimeError, match="存在但当前会话无法读取"):
        runtime_license.ensure_device_secret()
    assert writes == []


def test_python_device_secret_missing_generates_once(monkeypatch) -> None:
    generated = b"\x13" * 32
    writes: list[tuple[str, str]] = []
    monkeypatch.delenv("SUYING_DEVICE_SECRET_B64", raising=False)
    monkeypatch.setattr(
        runtime_license,
        "_security_password",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(runtime_license, "_security_item_exists", lambda account: False)
    monkeypatch.setattr(runtime_license.secrets, "token_bytes", lambda size: generated)
    monkeypatch.setattr(
        runtime_license,
        "_set_security_password",
        lambda account, value: writes.append((account, value)),
    )

    encoded = runtime_license.ensure_device_secret()

    assert base64.b64decode(encoded) == generated
    assert writes == [(runtime_license.DEVICE_SECRET_ACCOUNT, encoded)]


def test_python_device_binding_cache_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    runtime_license.remember_device_binding("d" * 64, 9)

    assert runtime_license.cached_device_key_id() == "d" * 64
    assert runtime_license.cached_issue_seq() == 9
    assert (
        runtime_license._cached_device_key_id_path().stat().st_mode & 0o777
    ) == 0o600


def test_release_signer_uses_explicit_keychain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    keychain = tmp_path / "login.keychain-db"
    keychain.touch()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 44, "", "")

    monkeypatch.setenv(sign_update_release.KEYCHAIN_PATH_ENV, str(keychain))
    monkeypatch.setattr(sign_update_release.subprocess, "run", fake_run)

    result = sign_update_release._security(
        "find-generic-password",
        "-s",
        "test-service",
        "-w",
        check=False,
    )
    assert result.returncode == 44
    assert calls[0][-1] == str(keychain)


def test_offline_issuer_signs_machine_bound_perpetual_license(
    tmp_path: Path,
    monkeypatch,
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sign_update_release, "load_keychain_private_key", lambda _: key)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "com.qr.suying",
                "device_key_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "customer.suying-license"

    result = sign_update_release.sign_license(
        argparse.Namespace(
            account="test",
            request=request,
            customer_ref="customer-opaque-1",
            delivery_id="delivery-0123456789abcdef",
            issue_seq=7,
            feature=["core", "publish"],
            output=output,
            license_kind="perpetual",
            trial_days=3,
            term_days=365,
        )
    )

    envelope = json.loads(output.read_text(encoding="utf-8"))
    payload_bytes = base64.b64decode(envelope["payload_b64"])
    key.public_key().verify(base64.b64decode(envelope["signature"]), payload_bytes)
    payload = LicensePayload.model_validate_json(payload_bytes)
    assert payload.perpetual is True
    assert payload.device_key_id == "a" * 64
    assert payload.issue_seq == 7
    assert result["license_id"] == payload.license_id
    assert "private" not in output.read_text(encoding="utf-8").lower()


def test_offline_issuer_signs_fixed_contract_trial_license(
    tmp_path: Path,
    monkeypatch,
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sign_update_release, "load_keychain_private_key", lambda _: key)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "com.qr.suying",
                "device_key_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "trial.suying-license"
    sign_update_release.sign_license(
        argparse.Namespace(
            account="test",
            request=request,
            customer_ref="customer-opaque-1",
            delivery_id="delivery-0123456789abcdef",
            issue_seq=8,
            feature=["core", "publish"],
            output=output,
            license_kind="trial",
            trial_days=3,
            term_days=365,
        )
    )
    envelope = json.loads(output.read_text(encoding="utf-8"))
    payload = LicensePayload.model_validate_json(base64.b64decode(envelope["payload_b64"]))
    assert payload.license_kind == "trial"
    assert payload.schema_version == 2
    assert payload.perpetual is False
    assert payload.trial_days == 3
    assert payload.daily_produce_cap is None
    assert payload.daily_upload_cap is None
    assert payload.lock_mode == "hard_all"
    assert payload.ops_unlock_allowed is True
    assert payload.expires_at is not None and payload.clock_anchor is not None
    assert entitlement(payload)["authorized"] is True


def test_legacy_v1_trial_caps_are_signature_compatibility_only() -> None:
    now = datetime.now(timezone.utc)
    payload = LicensePayload(
        schema_version=1,
        license_id="license-" + "a" * 32,
        device_key_id="b" * 64,
        delivery_id="delivery-0123456789abcdef",
        customer_ref="customer-opaque-1",
        features=["core"],
        issued_at=now,
        issue_seq=1,
        perpetual=False,
        license_kind="trial",
        trial_days=3,
        daily_produce_cap=10,
        daily_upload_cap=10,
        lock_mode="hard_all",
        ops_unlock_allowed=True,
        expires_at=now + timedelta(days=3),
        clock_anchor=now - timedelta(seconds=1),
        key_id="ed25519-test-key",
    )
    status = entitlement(payload, now=now)
    assert status["authorized"] is True
    assert "produce_cap" not in status
    assert "upload_cap" not in status


def test_python_runtime_rejects_license_for_another_machine(
    tmp_path: Path,
    monkeypatch,
) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sign_update_release, "load_keychain_private_key", lambda _: key)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "com.qr.suying",
                "device_key_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "customer.suying-license"
    sign_update_release.sign_license(
        argparse.Namespace(
            account="test",
            request=request,
            customer_ref="customer-opaque-1",
            delivery_id="delivery-0123456789abcdef",
            issue_seq=1,
            feature=["core"],
            output=output,
            license_kind="perpetual",
            trial_days=3,
            term_days=365,
        )
    )
    monkeypatch.setattr(
        runtime_license,
        "load_trusted_keys",
        lambda: {sign_update_release.public_key_id(key.public_key()): key.public_key()},
    )
    monkeypatch.setattr(runtime_license, "_security_password", lambda *args, **kwargs: "1")
    monkeypatch.setattr(runtime_license, "device_key_id", lambda: "b" * 64)

    with pytest.raises(RuntimeError, match="不属于本机"):
        runtime_license.verify_license_file(output)


def test_offline_issuer_signs_term_license_365_days(tmp_path: Path, monkeypatch) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sign_update_release, "load_keychain_private_key", lambda _: key)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "com.qr.suying",
                "device_key_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "term.suying-license"
    sign_update_release.sign_license(
        argparse.Namespace(
            account="test",
            request=request,
            customer_ref="customer-opaque-1",
            delivery_id="delivery-0123456789abcdef",
            issue_seq=2,
            feature=["core"],
            output=output,
            license_kind="term",
            trial_days=3,
            term_days=365,
        )
    )
    envelope = json.loads(output.read_text(encoding="utf-8"))
    payload = LicensePayload.model_validate_json(base64.b64decode(envelope["payload_b64"]))
    assert payload.license_kind == "term"
    assert payload.term_days == 365
    assert payload.perpetual is False
    assert payload.lock_mode == "hard_all"
    assert payload.ops_unlock_allowed is True
    status = entitlement(payload)
    assert status["authorized"] is True
    assert status["code"] == ""
    assert int(status["remaining_sec"]) > 3600 * 24 * 300


def test_term_entitlement_expires(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    payload = LicensePayload(
        schema_version=2,
        license_id="license-" + "c" * 32,
        device_key_id="b" * 64,
        delivery_id="delivery-0123456789abcdef",
        customer_ref="customer-opaque-1",
        features=["core"],
        issued_at=now - timedelta(days=400),
        issue_seq=3,
        perpetual=False,
        license_kind="term",
        term_days=365,
        lock_mode="hard_all",
        ops_unlock_allowed=True,
        expires_at=now - timedelta(days=1),
        clock_anchor=now - timedelta(days=400),
        key_id="ed25519-test-key",
    )
    status = entitlement(payload, now=now)
    assert status["authorized"] is False
    assert status["code"] == "TERM_EXPIRED"
    assert "运维" in str(status["locked_reason"])


def test_term_non_365_rejected_without_flex(tmp_path: Path, monkeypatch) -> None:
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sign_update_release, "load_keychain_private_key", lambda _: key)
    monkeypatch.delenv("SUYING_LICENSE_TEST_FLEX_TERM", raising=False)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product_id": "com.qr.suying",
                "device_key_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="365"):
        sign_update_release.sign_license(
            argparse.Namespace(
                account="test",
                request=request,
                customer_ref="customer-opaque-1",
                delivery_id="delivery-0123456789abcdef",
                issue_seq=1,
                feature=["core"],
                output=tmp_path / "bad.suying-license",
                license_kind="term",
                trial_days=3,
                term_days=30,
            )
        )
