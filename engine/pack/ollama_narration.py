"""Ollama-assisted narration: visual hints → spoken copy + theme emoji stickers."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

OLLAMA_URL = "http://127.0.0.1:11434"

def _fallback_emojis(theme: str, n: int = 3) -> list[str]:
    _ = theme
    bank = ["✨", "👍", "✅"]
    return (bank * ((n // len(bank)) + 1))[:n]


def resolve_narration_model(settings: Any) -> str:
    explicit = str(getattr(settings, "ollama_narration_model", "") or "").strip()
    if explicit:
        return explicit
    vision = str(getattr(settings, "ollama_vision_model", "") or "").strip()
    if vision:
        return vision
    try:
        from engine.catalog.ollama_status import active_models_from_settings

        _, vision_m = active_models_from_settings()
        if vision_m:
            return str(vision_m)
    except Exception:
        pass
    return "qwen2.5:7b"


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_response(content: str) -> dict[str, Any] | None:
    text = _strip_json_fence(content)
    # Prefer first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def rewrite_narration_with_ollama(
    *,
    base_script: str,
    visual_hints: list[str],
    theme: str,
    brand: str,
    target_duration_sec: float,
    model: str,
    title: str = "",
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Ask Ollama to turn visual descriptions into spoken copy + emoji cues.

    Returns:
      ok, script, emoji_cues[{at_sec,emoji,label}], model, error?, raw?
    """
    hints = [h for h in (visual_hints or []) if str(h).strip()][:6]
    if not hints and not (base_script or "").strip():
        return {"ok": False, "error": "无视觉描述与底稿", "script": base_script, "emoji_cues": []}

    prompt = (
        "你是短视频口播文案编辑。根据「画面描述」与「主题」，写出有感染力的中文口播，并单独给出字幕表情时间点。\n"
        "硬性要求：\n"
        "1) 口播像热情店员跟老客户说话：有温度、有节奏、带一点开心劲儿；可用「真的」「马上」「稳稳的」等口语；\n"
        "   禁止机械播报腔；禁止「本期主题」「大家好」等开场；\n"
        "2) 不要复述屏幕大标题原文；不要技术规格堆砌；\n"
        "3) 紧扣画面里真实可见的内容，结合主题业务卖点；\n"
        f"4) 口播总字数约适合 {max(8.0, float(target_duration_sec)):.0f} 秒（中文约每秒 3.5–4 字）；\n"
        "5) emoji_cues 选 2–3 个与当前主题匹配的**单字符**表情，禁止复合肤色/性别序列；\n"
        "   这些表情会显示在**字幕行内**，禁止写成「贴在标题旁」；\n"
        "6) 【强制】script 字段禁止出现任何 emoji/表情符号/「表情包」字样——表情只放在 emoji_cues，"
        "旁白朗读绝不读表情；\n"
        "7) 【强制】script 必须用中文句号「。」分成短句，每句 8–16 字，禁止无标点长串连读；"
        "重要停顿用句号，轻顿可用逗号但最终仍应用句号切开；"
        "示例：「真实过程看得见。每个细节都认真。用心服务更放心。」；\n"
        "8) 只输出 JSON，不要其它说明。格式：\n"
        '{"script":"口播全文纯文字句号分隔","emoji_cues":[{"at_sec":0.8,"emoji":"✨","label":"主题"}]}'
        "\n\n"
        f"品牌：{brand or '本地服务'}\n"
        f"主题：{theme or 'default'}\n"
        f"屏幕标题（勿照念）：{title or '（无）'}\n"
        f"画面描述：\n- " + "\n- ".join(hints or ["（无，请按主题写通用口播）"]) + "\n"
        f"底稿参考（可改写）：\n{base_script or '（无）'}\n"
    )

    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0.55},
                },
            )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"ollama HTTP {resp.status_code}",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
            }
        content = (resp.json().get("message") or {}).get("content") or ""
        data = _parse_response(content)
        if not data:
            # Soft fallback: treat whole text as script
            script = content.strip().split("\n")[0].strip()[:220]
            if len(script) < 8:
                return {
                    "ok": False,
                    "error": "无法解析模型输出",
                    "script": base_script,
                    "emoji_cues": [],
                    "model": model,
                    "raw": content[:400],
                }
            data = {"script": script, "emoji_cues": []}

        script = str(data.get("script") or "").strip()
        if len(script) < 8:
            return {
                "ok": False,
                "error": "模型文案过短",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
            }

        raw_cues = data.get("emoji_cues") or data.get("stickers") or []
        from engine.pack.emoji_stickers import sanitize_emoji_cues, strip_emoji_for_speech

        script = strip_emoji_for_speech(script)
        from engine.pack.text_sanitize import ensure_zh_speech_breaks

        # HARD: even if model forgets 「。」, restore short breaths before TTS
        script = ensure_zh_speech_breaks(script, max_chars=18)
        cues = sanitize_emoji_cues(
            raw_cues if isinstance(raw_cues, list) else [],
            video_duration_sec=float(target_duration_sec) or None,
        )
        if not cues:
            fb = _fallback_emojis(theme, 3)
            dur = max(10.0, float(target_duration_sec) or 20.0)
            cues = sanitize_emoji_cues(
                [
                    {
                        "at_sec": round((i + 0.5) * (dur / (len(fb) + 1)), 2),
                        "emoji": em,
                        "label": theme or "",
                        "duration_sec": 1.8,
                    }
                    for i, em in enumerate(fb)
                ],
                video_duration_sec=dur,
            )

        return {
            "ok": True,
            "script": script,
            "emoji_cues": cues,
            "model": model,
            "visual_hints": hints,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e),
            "script": base_script,
            "emoji_cues": [],
            "model": model,
        }
