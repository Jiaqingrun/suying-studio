"""跟镜精品 · 热点收件箱（第二版占位）。

第一版：仅提供数据结构与中文说明，不联网、不进旁白。
后续：收录 → 本地清洗 → 禁用词沉淀 → 参考提示。
"""

from __future__ import annotations

from typing import Any


def hot_inbox_status() -> dict[str, Any]:
    return {
        "ok": True,
        "enabled": False,
        "label_zh": "行业热点参考（第二版）",
        "message": "热点收录与清洗将在第二版开放：违规写入禁用词，有用内容仅作参考提示，不会直接写进旁白。",
        "pending_count": 0,
        "reference_hooks": [],
    }


def ingest_hot_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "message": "热点技能尚未启用（第二版）。",
    }
