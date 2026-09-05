"""Lab toggle for semantic full backfill via local.env."""

from __future__ import annotations

import os
from pathlib import Path

from engine.config.settings import (
    LAB_ENV_SEMANTIC_FULL_BACKFILL,
    LAB_ENV_VISION_CASCADE,
    allow_semantic_full_backfill_env,
    set_semantic_full_backfill_lab,
)


def test_set_semantic_full_backfill_lab_roundtrip(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / "local.env"
    env_path.write_text(f"{LAB_ENV_VISION_CASCADE}=1\nFOO=bar\n", encoding="utf-8")
    monkeypatch.setenv("SUYING_LOCAL_ENV", str(env_path))
    monkeypatch.delenv(LAB_ENV_SEMANTIC_FULL_BACKFILL, raising=False)
    monkeypatch.setenv(LAB_ENV_VISION_CASCADE, "1")

    out = set_semantic_full_backfill_lab(True)
    assert out["enabled"] is True
    assert allow_semantic_full_backfill_env() is True
    assert os.environ.get(LAB_ENV_SEMANTIC_FULL_BACKFILL) == "1"
    assert LAB_ENV_VISION_CASCADE not in os.environ
    text = env_path.read_text(encoding="utf-8")
    assert f"{LAB_ENV_SEMANTIC_FULL_BACKFILL}=1" in text
    assert LAB_ENV_VISION_CASCADE not in text
    assert "FOO=bar" in text

    out2 = set_semantic_full_backfill_lab(False)
    assert out2["enabled"] is False
    assert allow_semantic_full_backfill_env() is False
    assert LAB_ENV_SEMANTIC_FULL_BACKFILL not in os.environ
    text2 = env_path.read_text(encoding="utf-8")
    assert LAB_ENV_SEMANTIC_FULL_BACKFILL not in text2
    assert "FOO=bar" in text2
