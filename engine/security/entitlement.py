"""Signed license entitlement evaluation (trial + term + perpetual).

Daily production and upload caps were removed by explicit product decision.
"""

from __future__ import annotations

from datetime import datetime, timezone

from engine.security.update_manifest import LicensePayload

TRIAL_DAYS = 3
TERM_DAYS = 365


def _now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _time_bound_status(
    payload: LicensePayload,
    *,
    kind: str,
    expired_reason: str,
    expired_code: str,
    invalid_reason: str,
    now: datetime | None = None,
) -> dict[str, object]:
    current = _now(now)
    if (
        payload.perpetual
        or payload.lock_mode != "hard_all"
        or payload.expires_at is None
        or payload.clock_anchor is None
    ):
        return {
            "authorized": False,
            "license_kind": kind,
            "locked_reason": invalid_reason,
            "code": "INVALID",
            "ops_unlock_allowed": bool(payload.ops_unlock_allowed),
        }
    if current < payload.clock_anchor.astimezone(timezone.utc):
        return {
            "authorized": False,
            "license_kind": kind,
            "locked_reason": "系统时钟早于授权锚点",
            "code": "CLOCK_ROLLBACK",
            "ops_unlock_allowed": bool(payload.ops_unlock_allowed),
        }
    expires_at = payload.expires_at.astimezone(timezone.utc)
    remaining = max(0, int((expires_at - current).total_seconds()))
    if current >= expires_at:
        return {
            "authorized": False,
            "license_kind": kind,
            "locked_reason": expired_reason,
            "code": expired_code,
            "expires_at": expires_at.isoformat(),
            "remaining_sec": 0,
            "trial_remaining_sec": 0 if kind == "trial" else 0,
            "ops_unlock_allowed": bool(payload.ops_unlock_allowed),
        }
    out: dict[str, object] = {
        "authorized": True,
        "license_kind": kind,
        "locked_reason": "",
        "code": "",
        "expires_at": expires_at.isoformat(),
        "remaining_sec": remaining,
        "ops_unlock_allowed": bool(payload.ops_unlock_allowed),
    }
    if kind == "trial":
        out["trial_remaining_sec"] = remaining
    return out


def entitlement(payload: LicensePayload, *, now: datetime | None = None) -> dict[str, object]:
    """Return a display-safe entitlement snapshot; malformed contracts fail closed."""
    kind = str(payload.license_kind or ("perpetual" if payload.perpetual else "")).strip()
    if kind == "perpetual" or (not kind and payload.perpetual):
        if not payload.perpetual:
            return {
                "authorized": False,
                "license_kind": "perpetual",
                "locked_reason": "许可证永久标记无效",
                "code": "INVALID",
                "ops_unlock_allowed": False,
            }
        return {
            "authorized": True,
            "license_kind": "perpetual",
            "locked_reason": "",
            "code": "",
            "ops_unlock_allowed": False,
            "remaining_sec": None,
        }

    if kind == "trial":
        if payload.trial_days != TRIAL_DAYS or not payload.ops_unlock_allowed:
            return {
                "authorized": False,
                "license_kind": "trial",
                "locked_reason": "体验许可证合同字段无效",
                "code": "INVALID",
                "ops_unlock_allowed": False,
            }
        # Schema v1 trials were signed with fixed 10/10 cap fields. Keep validating
        # those immutable old envelopes, but never enforce or expose the counters.
        if int(payload.schema_version) == 1 and (
            payload.daily_produce_cap != 10 or payload.daily_upload_cap != 10
        ):
            return {
                "authorized": False,
                "license_kind": "trial",
                "locked_reason": "旧版体验许可证合同字段无效",
                "code": "INVALID",
                "ops_unlock_allowed": False,
            }
        return _time_bound_status(
            payload,
            kind="trial",
            expired_reason="体验期已结束",
            expired_code="TRIAL_EXPIRED",
            invalid_reason="体验许可证合同字段无效",
            now=now,
        )

    if kind == "term":
        if payload.term_days != TERM_DAYS or not payload.ops_unlock_allowed:
            return {
                "authorized": False,
                "license_kind": "term",
                "locked_reason": "年期许可证合同字段无效",
                "code": "INVALID",
                "ops_unlock_allowed": False,
            }
        return _time_bound_status(
            payload,
            kind="term",
            expired_reason="授权已到期，请联系运维人员",
            expired_code="TERM_EXPIRED",
            invalid_reason="年期许可证合同字段无效",
            now=now,
        )

    return {
        "authorized": False,
        "license_kind": kind or "",
        "locked_reason": "未知许可证类型",
        "code": "INVALID",
        "ops_unlock_allowed": False,
    }
