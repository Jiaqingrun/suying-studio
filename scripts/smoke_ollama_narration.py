#!/usr/bin/env python3
"""Smoke: Ollama narration rewrite helpers (offline parse + fallback)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pack.ollama_narration import (  # noqa: E402
    _fallback_emojis,
    _parse_response,
    resolve_narration_model,
)
from engine.config.settings import AppSettings  # noqa: E402


def main() -> None:
    raw = '```json\n{"script":"真实过程看得见。每个细节都认真。","emoji_cues":[{"at_sec":1.2,"emoji":"✨","label":"主题"}]}\n```'
    data = _parse_response(raw)
    assert data and "真实过程" in data["script"]
    assert data["emoji_cues"][0]["emoji"] == "✨"
    em = _fallback_emojis("任意行业", 3)
    assert em == ["✨", "👍", "✅"]
    s = AppSettings()
    s.ollama_narration_model = "qwen2.5:3b"
    assert resolve_narration_model(s) == "qwen2.5:3b"
    print(json.dumps({"ok": True, "emojis": em, "script_len": len(data["script"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
