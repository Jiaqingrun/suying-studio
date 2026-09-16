"""Shared Ops-Token gate for destructive /ops write endpoints.

PL-07 scheme A (mild): only high-risk writes on the whitelist call
``require_ops_token``. Daily production buttons must not be gated here.

Dev bypass (documented in docs/legal/OPS_AUTH_WHITELIST.md):
  - ``SUYING_OPS_DEV=1`` (or true/yes)
  - loopback client (127.0.0.1 / ::1 / localhost / 127.*)

Tightening (drop bare localhost bypass) awaits human confirmation.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import HTTPException, Request

# Documented high-risk write surface (scheme A). Do not expand without product cut.
OPS_TOKEN_WRITE_WHITELIST: frozenset[str] = frozenset(
    {
        "POST /ops/resource-gate/force-release",
        "POST /ops/backups",
        "PUT /ops/backups/policy",
        "POST /ops/backups/prune",
        "PUT /ops/published-cleanup/policy",
        "POST /ops/published-cleanup",
        "PUT /ops/disk-cleanup/policy",
        "POST /ops/disk-cleanup/run",
    }
)


def runtime_dir() -> Path:
    return Path(
        os.environ.get("SUYING_APP_RUNTIME_DIR")
        or Path.home() / "Library/Application Support/com.qr.suying/runtime"
    )


def _env_flag_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip().lower().split("%", 1)[0]
    if not h:
        return False
    if h in {"127.0.0.1", "::1", "localhost"}:
        return True
    return h.startswith("127.")


def ops_dev_bypass(*, client_host: str | None = None) -> bool:
    """True when documented local-dev bypass applies."""
    if _env_flag_true("SUYING_OPS_DEV"):
        return True
    return is_loopback_host(client_host)


def client_host_from_request(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def require_ops_token(
    token: str | None,
    *,
    request: Request | None = None,
    client_host: str | None = None,
) -> None:
    """Fail-closed for whitelist writes unless token matches or dev bypass applies."""
    host = client_host if client_host is not None else client_host_from_request(request)
    if ops_dev_bypass(client_host=host):
        return
    try:
        expected = (runtime_dir() / "system_token.txt").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(503, "高级操作验证暂不可用，请重启速影") from exc
    got = (token or "").strip()
    # Length gate first — compare_digest raises on mismatch on some Python builds.
    if (
        len(expected) != 64
        or len(got) != 64
        or not hmac.compare_digest(expected, got)
    ):
        raise HTTPException(403, "高级功能已锁定，请先输入密码")
