"""R2 / PL-07: Ops-Token gate + mild local-dev bypass."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from engine.api.ops_auth import (
    OPS_TOKEN_WRITE_WHITELIST,
    is_loopback_host,
    ops_dev_bypass,
    require_ops_token,
)


def test_whitelist_covers_high_risk_only() -> None:
    assert "POST /ops/resource-gate/force-release" in OPS_TOKEN_WRITE_WHITELIST
    assert "POST /ops/services/watcher/start" not in OPS_TOKEN_WRITE_WHITELIST
    assert len(OPS_TOKEN_WRITE_WHITELIST) >= 5


def test_loopback_detection() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("testclient")
    assert not is_loopback_host("192.168.1.10")


def test_require_ops_token_rejects_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("SUYING_OPS_DEV", raising=False)
    (tmp_path / "system_token.txt").write_text("a" * 64, encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        require_ops_token(None, client_host="testclient")
    assert exc.value.status_code == 403


def test_require_ops_token_accepts_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("SUYING_OPS_DEV", raising=False)
    token = "b" * 64
    (tmp_path / "system_token.txt").write_text(token, encoding="utf-8")
    require_ops_token(token, client_host="testclient")


def test_require_ops_token_unavailable_without_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("SUYING_OPS_DEV", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_ops_token("x" * 64, client_host="testclient")
    assert exc.value.status_code == 503


def test_suying_ops_dev_bypass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("SUYING_OPS_DEV", "1")
    require_ops_token(None, client_host="testclient")
    assert ops_dev_bypass(client_host="testclient") is True


def test_loopback_bypass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("SUYING_OPS_DEV", raising=False)
    require_ops_token(None, client_host="127.0.0.1")
