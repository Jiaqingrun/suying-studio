"""Offline unit checks for cap-free trial entitlement (no Keychain)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from engine.api.app import _enforce_packaged_entitlement
from engine.security.license import is_packaged_runtime
from engine.security.trial import TRIAL_DAYS, entitlement
from engine.security.update_manifest import LicensePayload


def _trial_payload(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "schema_version": 2,
        "product_id": "com.qr.suying",
        "license_id": "trial-license-id-01",
        "device_key_id": "a" * 64,
        "delivery_id": "delivery-id-trial-01",
        "customer_ref": "customer-ref-01",
        "features": ["studio", "pack", "reach"],
        "issued_at": now,
        "issue_seq": 9,
        "perpetual": False,
        "license_kind": "trial",
        "trial_days": TRIAL_DAYS,
        "lock_mode": "hard_all",
        "ops_unlock_allowed": True,
        "expires_at": now + timedelta(days=TRIAL_DAYS),
        "clock_anchor": now - timedelta(minutes=1),
        "key_id": "release-key-1",
    }
    data.update(overrides)
    return LicensePayload.model_validate(data)


def test_trial_active_within_window():
    status = entitlement(_trial_payload())
    assert status["authorized"] is True
    assert status["license_kind"] == "trial"
    assert int(status["trial_remaining_sec"]) > 0


def test_trial_expired_hard_locks_with_ops_unlock_flag():
    now = datetime.now(timezone.utc)
    status = entitlement(
        _trial_payload(
            expires_at=now - timedelta(seconds=5),
            clock_anchor=now - timedelta(days=2),
        ),
        now=now,
    )
    assert status["authorized"] is False
    assert status["locked_reason"] == "体验期已结束"
    assert status["ops_unlock_allowed"] is True


def test_perpetual_still_authorized():
    now = datetime.now(timezone.utc)
    payload = LicensePayload.model_validate(
        {
            "schema_version": 1,
            "product_id": "com.qr.suying",
            "license_id": "perpetual-license-01",
            "device_key_id": "b" * 64,
            "delivery_id": "delivery-id-perp-01",
            "customer_ref": "customer-ref-02",
            "features": ["studio"],
            "issued_at": now,
            "issue_seq": 1,
            "perpetual": True,
            "key_id": "release-key-1",
        }
    )
    status = entitlement(payload)
    assert status["authorized"] is True
    assert status["license_kind"] == "perpetual"


def test_v2_trial_has_no_daily_caps():
    payload = _trial_payload()
    status = entitlement(payload)
    assert status["authorized"] is True
    assert payload.daily_produce_cap is None
    assert payload.daily_upload_cap is None


def test_v1_trial_remains_readable_but_caps_are_not_exposed():
    payload = _trial_payload(
        schema_version=1,
        daily_produce_cap=10,
        daily_upload_cap=10,
    )
    status = entitlement(payload)
    assert status["authorized"] is True
    assert "produce_cap" not in status
    assert "upload_cap" not in status


def test_packaged_entitlement_middleware_locks_business_routes_but_keeps_health():
    async def next_response(_request):
        return Response("ok", status_code=200)

    def request(path: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [],
                "scheme": "http",
                "server": ("127.0.0.1", 8766),
                "client": ("127.0.0.1", 1),
                "root_path": "",
            }
        )

    with patch("engine.security.license.is_packaged_runtime", return_value=True), patch(
        "engine.security.license.current_runtime_license",
        side_effect=RuntimeError("LICENSE_LOCKED: 体验期已结束"),
    ):
        locked = asyncio.run(
            _enforce_packaged_entitlement(request("/customers"), next_response)
        )
        health = asyncio.run(
            _enforce_packaged_entitlement(request("/health"), next_response)
        )
    assert locked.status_code == 423
    assert health.status_code == 200


def test_tauri_bundled_marker_is_authoritative(monkeypatch):
    monkeypatch.setenv("SUYING_BUNDLED_RUNTIME", "1")
    assert is_packaged_runtime() is True
