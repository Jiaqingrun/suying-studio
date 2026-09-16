"""R2: Ops-Token gate for force-release / shared require_ops_token."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from engine.api.ops_auth import require_ops_token


def test_require_ops_token_rejects_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "system_token.txt").write_text("a" * 64, encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        require_ops_token(None)
    assert exc.value.status_code == 403


def test_require_ops_token_accepts_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    token = "b" * 64
    (tmp_path / "system_token.txt").write_text(token, encoding="utf-8")
    require_ops_token(token)


def test_require_ops_token_unavailable_without_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        require_ops_token("x" * 64)
    assert exc.value.status_code == 503
