"""Shared Ops-Token gate for destructive /ops write endpoints."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import HTTPException


def runtime_dir() -> Path:
    return Path(
        os.environ.get("SUYING_APP_RUNTIME_DIR")
        or Path.home() / "Library/Application Support/com.qr.suying/runtime"
    )


def require_ops_token(token: str | None) -> None:
    """Fail-closed: advanced ops writes need the runtime system token header."""
    try:
        expected = (runtime_dir() / "system_token.txt").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(503, "高级操作验证暂不可用，请重启速影") from exc
    if (
        len(expected) != 64
        or not token
        or not hmac.compare_digest(expected, token.strip())
    ):
        raise HTTPException(403, "高级功能已锁定，请先输入密码")
