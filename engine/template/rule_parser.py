"""Local Ollama parser: natural language → structured production rules (strict)."""

from __future__ import annotations

import json
import re
from typing import Any

from engine.catalog.ollama_runtime import OLLAMA_KEEP_ALIVE, heavy_request
from engine.template.rule_schema import parse_json_schema, validate_and_clamp


def resolve_rule_model(settings: Any) -> str:
    explicit = str(getattr(settings, "ollama_rule_model", "") or "").strip()
    if explicit:
        return explicit
    narr = str(getattr(settings, "ollama_narration_model", "") or "").strip()
    if narr and "embed" not in narr.lower() and "nomic" not in narr.lower():
        # Prefer text models over vision for rule parsing
        if not any(x in narr.lower() for x in ("vl", "vision", "llava", "minicpm-v")):
            return narr
    try:
        from engine.catalog.ollama_status import active_models_from_settings

        _, vision_m = active_models_from_settings()
        # Prefer qwen text; fall back carefully
    except Exception:
        vision_m = ""
    _ = vision_m
    return "qwen2.5:7b"


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_object(content: str) -> dict[str, Any] | None:
    text = _strip_json_fence(content)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_production_rules_with_ollama(
    *,
    source_text: str,
    model: str,
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Strict parse. Fail closed on timeout / malformed / empty — never soft-fallback to free text."""
    text = (source_text or "").strip()
    if len(text) < 4:
        return {"ok": False, "error": "描述过短，请用自己的话写清节奏、镜头、时长等要求"}

    schema = parse_json_schema(require_complete=True)
    prompt = (
        "你是短视频生产规则助手。把客户的中文要求翻译成结构化规则 JSON。\n"
        "只输出符合 schema 的 JSON，不要解释。\n"
        "约束：\n"
        "1) 不要输出任何绕过质检/成品门禁/改标题颜色/改旁白语速的字段；\n"
        "2) 必须补齐所有字段，禁止 null、空字符串；无法判断 theme/category 时填 default，"
        "列表可为空数组，布尔值必须明确 true/false；\n"
        "3) pace 只能是 slow/normal/fast；narration_tone 只能是 plain/warm/energetic/passionate/professional；\n"
        "4) template_preference 只能是 default-vertical / fast-ship / stable-product 或 null；\n"
        "5) target_duration_sec 在 8–90，用户未说明则根据内容选择合理值（通常 25）；\n"
        "6) prefer/exclude_semantic_labels 用英文场景标签如 product_closeup、warehouse、delivery；\n"
        "7) min_cliplet_quality 未要求加严时填硬锁 0.35；\n"
        "8) item_label_enabled 仅表示希望显示物品名；无官方目录证据和 strict 画面证据时系统仍会隐藏；"
        "item_label_side 只能 left/right，默认 left；字号默认 56，安全区默认 360–1320；\n"
        "9) notes 必须用一两句中文复述理解，并说明你补齐的默认项；confidence 0–1。\n\n"
        f"客户原话：\n{text[:4000]}\n"
    )

    result = heavy_request(
        kind="rule",
        path="/api/chat",
        payload={
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "format": schema,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0},
        },
        timeout_sec=timeout,
        model=model,
    )
    if not result.get("ok"):
        if str(result.get("error_kind") or "") == "timeout":
            return {"ok": False, "error": "本地 AI 解析超时", "model": model}
        return {
            "ok": False,
            "error": f"本地 AI 不可用: {result.get('error') or 'request_failed'}",
            "model": model,
        }

    body = result.get("body") or {}
    content = ((body.get("message") or {}) if isinstance(body, dict) else {}).get("content") or ""
    data = _extract_object(content)
    if not data:
        return {
            "ok": False,
            "error": "模型输出不是合法 JSON，解析失败（不回退为自由文本）",
            "model": model,
            "raw": content[:500],
        }

    # Prompt-injection / schema pollution: reject forbidden keys loudly
    report = validate_and_clamp(data)
    effective = report.get("effective_rules") or {}
    required_values = (
        "theme",
        "category",
        "template_preference",
        "target_duration_sec",
        "pace",
        "narration_tone",
        "min_cliplet_quality",
        "notes",
    )
    missing = [key for key in required_values if effective.get(key) in (None, "")]
    if missing:
        return {
            "ok": False,
            "error": f"本地 AI 未补齐结构化字段：{', '.join(missing)}，请重试",
            "model": model,
            "raw": content[:500],
        }
    conf = data.get("confidence")
    try:
        confidence = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0
        report["warnings"] = list(report.get("warnings") or []) + ["confidence 无效，按 0 处理"]

    return {
        "ok": True,
        "model": model,
        "confidence": max(0.0, min(1.0, confidence)),
        "raw": content[:800],
        **report,
    }
