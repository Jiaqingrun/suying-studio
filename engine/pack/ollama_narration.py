"""Ollama-assisted narration: visual hints → spoken copy + theme emoji stickers.

HARD_LOCKS L20: life-service path is golden-base + cadence-only polish.
See docs/SERVICE_NARRATION_LOCK.md — do not change base_lock without user auth.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from engine.catalog.ollama_runtime import (
    OLLAMA_NARRATION_KEEP_ALIVE,
    chat_completion,
    circuit_allows_request,
    circuit_snapshot,
)

# Cap completion so thinking/runaway models cannot burn the whole read timeout.
_NARRATION_NUM_PREDICT = 640
_NARRATION_ATTEMPTS = 2
_NARRATION_RETRY_SLEEP_SEC = 1.0
# Single total deadline across attempts (retries consume remaining budget).
_NARRATION_WALL_SEC = 90.0


def _fallback_emojis(theme: str, n: int = 3) -> list[str]:
    _ = theme
    bank = ["✨", "👍", "✅"]
    return (bank * ((n // len(bank)) + 1))[:n]


def is_ollama_infra_error(error: str | None) -> bool:
    """Timeouts / connection / HTTP — not content-quality rejects."""
    text = str(error or "").strip().lower()
    if not text:
        return False
    needles = (
        "timed out",
        "timeout",
        "readtimeout",
        "connect",
        "connection",
        "refused",
        "unreachable",
        "ollama http",
        "slot_busy",
        "host_pressure",
        "resource_gate",
        "wall-clock",
        "wall clock",
        "lease_expired",
        "circuit_open",
        "daemon",
        "runner",
        "budget_exhausted",
        "cancelled",
    )
    return any(n in text for n in needles)


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
    except Exception:
        vision_m = None
    if vision_m:
        return str(vision_m)
    # Prefer thinking-capable-but-disabled ship default (see think:false below).
    return "qwen3.5:9b"


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_response(content: str) -> dict[str, Any] | None:
    text = _strip_json_fence(content)
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


def _chat_payload(
    *,
    model: str,
    prompt: str,
    tone: str,
    variation_seed: int | None,
    temperature: float | None = None,
) -> dict[str, Any]:
    if temperature is None:
        temperature = (
            0.55 if tone in ("professional", "plain") else (0.74 if tone == "warm" else 0.68)
        )
    options: dict[str, Any] = {
        "temperature": float(temperature),
        "num_predict": _NARRATION_NUM_PREDICT,
    }
    if variation_seed is not None:
        options["seed"] = int(variation_seed)
    return {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_NARRATION_KEEP_ALIVE,
        "messages": [{"role": "user", "content": prompt}],
        "options": options,
    }


def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[，,。．！？!?、；;：:…·\-—_「」\"'（）()【】\[\]]", "", str(text or "")))


def _script_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    na = _normalize_for_similarity(a)
    nb = _normalize_for_similarity(b)
    if not na or not nb:
        return 0.0
    return float(SequenceMatcher(None, na, nb).ratio())


def _base_sentence_cores(base_script: str) -> list[str]:
    """Substantial Han cores from golden draft sentences (for retention check)."""
    cores: list[str] = []
    for part in re.split(r"[。！？!?]+", str(base_script or "")):
        c = re.sub(r"[^\u4e00-\u9fff]", "", part)
        if len(c) >= 6:
            cores.append(c)
    return cores


def _core_retained(core: str, script_compact: str, *, min_ratio: float = 0.86) -> bool:
    """True if core appears, or is nearly matching a window of equal length."""
    if not core or not script_compact:
        return False
    if core in script_compact:
        return True
    from difflib import SequenceMatcher

    n = len(core)
    if n > len(script_compact):
        return SequenceMatcher(None, core, script_compact).ratio() >= min_ratio
    best = 0.0
    # Step windows; n can be ~20 so loop is fine
    for i in range(0, len(script_compact) - n + 1):
        best = max(best, SequenceMatcher(None, core, script_compact[i : i + n]).ratio())
        if best >= min_ratio:
            return True
    return False


def _base_retention_ratio(script: str, base_script: str) -> float:
    cores = _base_sentence_cores(base_script)
    if not cores:
        return 1.0
    compact = re.sub(r"[^\u4e00-\u9fff]", "", str(script or ""))
    hit = sum(1 for c in cores if _core_retained(c, compact))
    return hit / len(cores)


def use_golden_base_lock(
    *,
    product_display: bool = False,
    loading_ops: bool = False,
    base_script: str = "",
) -> bool:
    """L20 v2（2026-08-29 用户授权）：生活服务走受控多样改写，关闭黄金底稿硬锁。

    商品桌面与仓配/装车仍保持自由改写；服务向保留 ``_address_drift_blocked`` 禁词。
    """
    _ = base_script
    if product_display or loading_ops:
        return False
    return False


def _address_drift_blocked(script: str, base_script: str) -> str | None:
    """Ban casual address / chat pads that gold life-service drafts never used."""
    sc = re.sub(r"\s+", "", str(script or ""))
    base = re.sub(r"\s+", "", str(base_script or ""))
    if not sc or not base:
        return None
    # Base uses 您-only; rewrite must not introduce 咱们
    if "您" in base and "咱们" not in base and "咱们" in sc:
        return "rewrite_address_咱们"
    # Soft pad the user already rejected
    for banned in ("聊聊天", "聊两句", "路过", "安排到店服务", "先请听完", "请听完您的想法"):
        if banned in sc and banned not in base:
            return f"rewrite_banned_{banned}"
    return None


def _hint_grounding_hits(script: str, hints: list[str]) -> int:
    """Count how many visual hints contribute a distinct 2-han gram into script."""
    compact = re.sub(r"[^\u4e00-\u9fff]", "", str(script or ""))
    if not compact:
        return 0
    hits = 0
    for h in hints:
        cleaned = re.sub(r"[^\u4e00-\u9fff]", "", str(h or ""))
        if len(cleaned) < 2:
            continue
        found = False
        for i in range(0, len(cleaned) - 1):
            gram = cleaned[i : i + 2]
            if gram and gram in compact:
                found = True
                break
        if found:
            hits += 1
    return hits


def _finalize_ok(
    *,
    data: dict[str, Any],
    base_script: str,
    model: str,
    theme: str,
    target_duration_sec: float,
    forbidden_claims: list[str] | None,
    hints: list[str],
    attempts: int,
    exclude_openers: list[str] | None = None,
    product_display: bool = False,
    loading_ops: bool = False,
    base_lock: bool = False,
) -> dict[str, Any]:
    script = str(data.get("script") or "").strip()
    if len(script) < 8:
        return {
            "ok": False,
            "error": "模型文案过短",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    raw_cues = data.get("emoji_cues") or data.get("stickers") or []
    from engine.pack.emoji_stickers import sanitize_emoji_cues, strip_emoji_for_speech
    from engine.pack.narration_script import (
        opener_key,
        scrub_video_meta_sentences,
        script_has_camera_direction,
        script_has_oral_filler,
        script_has_packaging_meta,
        script_has_questionnaire_speak,
        script_has_shipping_claim,
        script_has_stock_ban,
        script_has_video_meta_speak,
        script_is_telegram_choppy,
        to_spoken_line,
    )

    script = strip_emoji_for_speech(script)
    from engine.pack.text_sanitize import ensure_zh_speech_breaks
    from engine.content.compliance import sanitize_script_claims

    # Oral fillers / shipping leaks are never recoverable (must not soft-fall to base draft).
    if script_has_oral_filler(script):
        return {
            "ok": False,
            "error": "rewrite_stock_ban",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }
    if product_display and script_has_shipping_claim(script):
        return {
            "ok": False,
            "error": "rewrite_shipping_on_product",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }
    if product_display and script_has_packaging_meta(script):
        return {
            "ok": False,
            "error": "rewrite_packaging_meta",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    # Detect dry telegram prose on model wording *before* breath re-break.
    if loading_ops and script_is_telegram_choppy(script):
        return {
            "ok": False,
            "error": "rewrite_telegram_choppy",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    # Rewrite meta/direction/questionnaire fragments before breath splits
    fixed_parts: list[str] = []
    for part in re.split(r"(?<=[。！？])", str(script or "")):
        piece = part.strip()
        if not piece:
            continue
        spoken = to_spoken_line(piece)
        if spoken is None:
            continue
        fixed_parts.append(spoken.rstrip("。") + "。")
    if fixed_parts:
        script = "".join(fixed_parts)
    script = scrub_video_meta_sentences(script)

    script = ensure_zh_speech_breaks(script, max_chars=18)
    script = sanitize_script_claims(
        script,
        extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
    )

    # Always peel residual shot-description sentences (product/service intro only)
    from engine.pack.narration_script import filter_product_commercial_script

    script = scrub_video_meta_sentences(script)
    min_han = int(max(8.0, float(target_duration_sec) or 20.0) * 2.6)
    if product_display:
        # Re-filter AFTER breath splits — short fragments must still be commercial
        script = filter_product_commercial_script(script)
        clean_base = filter_product_commercial_script(
            scrub_video_meta_sentences(str(base_script or ""))
        )
        clean_base = filter_product_commercial_script(
            ensure_zh_speech_breaks(clean_base, max_chars=18)
        ) if clean_base else ""
        if len(re.sub(r"[^\u4e00-\u9fff]", "", script)) < min_han and clean_base:
            script = clean_base
        if (
            script_has_video_meta_speak(script)
            or script_has_packaging_meta(script)
            or len(re.sub(r"[^\u4e00-\u9fff]", "", script))
            < max(20, len(re.sub(r"[^\u4e00-\u9fff]", "", clean_base)) // 2)
        ) and clean_base:
            script = clean_base
        # One more pass after any ensure_zh later in this function
        script = filter_product_commercial_script(script)

    # Soft fallback: prefer longer base draft over ultra-short model output
    han = len(re.sub(r"[^\u4e00-\u9fff]", "", script))
    if (product_display or loading_ops) and han < min_han:
        base_han = len(re.sub(r"[^\u4e00-\u9fff]", "", base_script or ""))
        if base_han > han:
            if product_display:
                script = filter_product_commercial_script(
                    scrub_video_meta_sentences(str(base_script or "").strip())
                )
            else:
                script = scrub_video_meta_sentences(str(base_script or "").strip())
            script = ensure_zh_speech_breaks(script, max_chars=18)
            script = sanitize_script_claims(
                script,
                extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
            )
            han = len(re.sub(r"[^\u4e00-\u9fff]", "", script))

    if (
        script_has_stock_ban(script)
        or script_has_camera_direction(script)
        or script_has_video_meta_speak(script)
    ):
        # Oral-filler rewrites are never acceptable — hard fail (do not soft-fall to base).
        if script_has_oral_filler(script):
            return {
                "ok": False,
                "error": "rewrite_stock_ban",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": False,
                "attempts": attempts,
            }
        # Soft scrub base and use it when model still leaked composition speak
        clean_base = scrub_video_meta_sentences(str(base_script or ""))
        clean_base = ensure_zh_speech_breaks(clean_base, max_chars=18) if clean_base else ""
        if (
            clean_base
            and len(re.sub(r"[^\u4e00-\u9fff]", "", clean_base)) >= 12
            and not script_has_video_meta_speak(clean_base)
            and not script_has_stock_ban(clean_base)
        ):
            script = sanitize_script_claims(
                clean_base,
                extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
            )
        else:
            return {
                "ok": False,
                "error": "rewrite_stock_ban",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": False,
                "attempts": attempts,
            }

    if product_display and script_has_shipping_claim(script):
        return {
            "ok": False,
            "error": "rewrite_shipping_on_product",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    if product_display and script_has_packaging_meta(script):
        # Prefer clean base draft over packaging vision prose so product still ships
        if (
            base_script
            and not script_has_packaging_meta(base_script)
            and not script_has_shipping_claim(base_script)
            and not script_has_stock_ban(base_script)
        ):
            script = ensure_zh_speech_breaks(str(base_script).strip(), max_chars=18)
            script = sanitize_script_claims(
                script,
                extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
            )
        else:
            return {
                "ok": False,
                "error": "rewrite_packaging_meta",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": False,
                "attempts": attempts,
            }

    # Residual questionnaire: soft-map common cliché without hard-fail entire rewrite
    if script_has_questionnaire_speak(script):
        script = re.sub(
            r"可见细节帮助进一步确认需求",
            "细节当面看清楚",
            script,
        )
        script = re.sub(r"我们展示细节来满足您的需求", "细节当面看清楚", script)
        script = re.sub(r"进一步确认需求", "当面看清楚", script)
        script = re.sub(r"进一步确认", "当面沟通", script)

    exclude_keys = {opener_key(x) for x in (exclude_openers or []) if str(x).strip()}
    if opener_key(script) in exclude_keys:
        return {
            "ok": False,
            "error": "rewrite_opener_reused",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    if not product_display and not loading_ops and (base_script or "").strip():
        drift = _address_drift_blocked(script, base_script)
        if drift:
            return {
                "ok": False,
                "error": drift,
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": False,
                "attempts": attempts,
            }

    # Base-lock life-service: visual-hint grounding is soft (gold copy is the spine).
    if hints and not base_lock and _hint_grounding_hits(script, hints) < min(2, len(hints)):
        return {
            "ok": False,
            "error": "rewrite_not_grounded",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": False,
            "attempts": attempts,
        }

    used_base_fallback = False
    if base_lock and (base_script or "").strip():
        drift = _address_drift_blocked(script, base_script)
        sim = _script_similarity(script, base_script)
        retain = _base_retention_ratio(script, base_script)
        # Cadence polish only: need high overlap; if model drifts, fall back to gold base
        # so ready production still ships (voice path must not hard-fail READY).
        if drift or sim < 0.78 or retain < 0.85:
            script = ensure_zh_speech_breaks(str(base_script).strip(), max_chars=18)
            script = sanitize_script_claims(
                script,
                extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
            )
            used_base_fallback = True

    if base_script and _script_similarity(script, base_script) >= 0.92:
        # Near-identical rewrite is still a valid timeline VO; use base and continue
        # instead of failing READY-gated production (circuit_open).
        script = ensure_zh_speech_breaks(str(base_script).strip(), max_chars=18)
        script = sanitize_script_claims(
            script,
            extra_terms=[str(x) for x in (forbidden_claims or []) if str(x).strip()],
        )

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
        "infra": False,
        "attempts": attempts,
        "base_lock": bool(base_lock),
        "base_fallback": bool(used_base_fallback),
    }


def rewrite_narration_with_ollama(
    *,
    base_script: str,
    visual_hints: list[str],
    theme: str,
    brand: str,
    target_duration_sec: float,
    model: str,
    title: str = "",
    tone: str = "plain",
    variation_seed: int | None = None,
    allowed_facts: list[str] | None = None,
    forbidden_claims: list[str] | None = None,
    recipe_components: list[str] | None = None,
    style_notes: str = "",
    timeout: float = 60.0,
    cancel_event: Any | None = None,
    exclude_openers: list[str] | None = None,
    product_display: bool = False,
    loading_ops: bool = False,
) -> dict[str, Any]:
    """Ask Ollama to turn visual descriptions into spoken copy + emoji cues."""
    from engine.pack.narration_script import (
        ORAL_FILLER_BANS,
        PRODUCT_PACKAGING_BANS,
        SHIPPING_CLAIM_TERMS,
        STOCK_BANNED_PHRASES,
    )

    hints = [h for h in (visual_hints or []) if str(h).strip()][:6]
    if not hints and not (base_script or "").strip():
        return {
            "ok": False,
            "error": "无视觉描述与底稿",
            "script": base_script,
            "emoji_cues": [],
            "infra": False,
        }

    if not circuit_allows_request(for_probe=False):
        return {
            "ok": False,
            "error": "ollama circuit_open",
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": True,
            "attempts": 0,
            "circuit": circuit_snapshot(),
        }

    dense = len(hints) >= 2 or bool(allowed_facts) or bool(recipe_components)
    target_sec = max(8.0, float(target_duration_sec) or 20.0)
    # Cover most of the picture timeline (user: 铺满口播，勿说到一半就停)
    if product_display or loading_ops:
        char_lo = int(target_sec * 3.3)
        char_hi = int(target_sec * 3.9)
    else:
        char_lo = int(target_sec * (3.2 if dense else 2.0))
        char_hi = int(target_sec * (3.8 if dense else 2.8))

    base_lock = use_golden_base_lock(
        product_display=product_display,
        loading_ops=loading_ops,
        base_script=base_script,
    )

    tone_guidance = {
        "plain": "正式平静说明，像现场讲解，不用喊口号与表演口语",
        "warm": (
            "礼貌温润、称「您」；短句有呼吸；"
            if base_lock
            else "礼貌温润、称「您」；短句有呼吸；"
        )
        + (
            "在黄金底稿上只调节奏与断句，不换金句、不改称谓；"
            if base_lock
            else "禁止换成随意的「咱们/聊两句」破坏品牌口播；"
        )
        + "禁止合规口号串：可核验、重点在、不夸大、可见执行细节、照实说明、仅介绍；"
        "禁止说明书腔与电报碎句",
        "energetic": "节奏稍紧，仍保持正式讲解，禁止催促喊看",
        "passionate": "过程型口语、有起伏但连贯，禁止口水词与表演腔",
        "professional": "专业克制说明，突出可见摆放/品类/工序，像正式产品介绍",
    }.get(tone, "正式平静说明，像现场讲解")

    # Universal VO rules for every selected video type (product/ops/overview)
    voice_universal = (
        "【通用口播硬规则·全部片型】"
        "① 这是**介绍商品或介绍服务**的口播，不是影评/构图解说；"
        "只讲「货是什么、怎么用/干什么、现场在干什么」；"
        "禁止念运镜编导语与**画面描写腔**："
        "「画面主体为…」「视频展示了…」「背景停放/背景为…」「容器内部为…」"
        "「静止放置」「镜头轻微移动」「可见中心搅拌结构」「颜色平面/纸面/底板」等。"
        "② 禁止问卷纸质腔「进一步确认需求」；"
        "价值句用仓配人话，例：「细节当面看清楚」「按采购需要挑对应品类」。"
        "③ 不要复述「可用组件」或「画面描述」里的机器视觉原文，应改成自然推销/服务句。"
        "④ 文案必须跟本条镜头里的真实货品/服务动作对得上，禁止张冠李戴。"
    )
    if product_display:
        scene_rule = (
            voice_universal
            + "【模式=商品仓配介绍】写短句人话：点名可见品类"
            "（扳手、卷尺、板材、料斗等），"
            "只说「是什么、工地/门店怎么用得上」一两句，禁止说明书式详细用处罗列。"
            f"【时长】口播总字数约 {char_lo}–{char_hi} 字，基本铺满，禁止写完三句就收。"
            "【必须】收尾一句采购向（例：看中的到店拿、缺的跟我们配）；"
            "可用「细节当面看清楚」。"
            "【严禁】包装字样/构图/相对方位排版/刻字读数/颜色桌面等视觉描写；"
            "禁止「最上方/依次变小/手柄上有刻字/身穿/堆放着」类构图念白。"
            "每句点品类与场景，像仓里店员说话，不解说镜头怎么拍。"
            "严禁装车物流叙事与保证词（放心/最好/第一/厂家直销/马上发货）。"
            "严禁「陈列/说明/展示细节满足需求/完整服务」套话。"
        )
    elif loading_ops:
        scene_rule = (
            voice_universal
            + "【模式=装车/仓配过程】写**连贯过程旁白**："
            "像人介绍现场干活（备货、码货、装车），"
            "把装载/核对/码放/车门等动作串成顺口整段；"
            "可用「跟着装车走一趟」开场；收尾用「要哪一类跟我们说一声」类采购句；"
            "禁止「完整服务/来店感受服务」生活服务腔；"
            "禁止念构图描写；【严禁电报体】与口水词。"
        )
    elif base_lock:
        scene_rule = (
            "【模式=黄金底稿·节奏润色】"
            "底稿已是终审服务金句链，你只能：① 调整标点断句与轻微连接词使更顺口；"
            "② 保持每一句原有专名、承诺与流程信息（怎样为您服务 / 有预约则提前安排 / "
            "倾听想法再安排项目 / 统一标准 / 来店体验详聊 等）；"
            "③ 禁止换称谓：勿用咱们、我们先看看、聊聊天、聊两句；禁删预约句；"
            "④ 禁止新增功效/推销；禁止画面描写腔与合规口号。"
            "若把握不大，几乎原句输出并只断句。"
        )
    else:
        scene_rule = (
            voice_universal
            + "默认正式服务/商品说明，禁止空转与画面描写腔。"
        )

    filler_ban = "、".join(ORAL_FILLER_BANS[:24])
    pkg_extra = list(PRODUCT_PACKAGING_BANS[:14]) if product_display else []
    stock_ban = "、".join(
        list(STOCK_BANNED_PHRASES[:8])
        + list(SHIPPING_CLAIM_TERMS[:8] if product_display else [])
        + pkg_extra
    )
    extra_style = str(style_notes or "").strip()[:600]
    if product_display:
        breath_rule = (
            "7) 【强制】script 用中文句号「。」分成多句（约 5–9 句）；每句 8–16 字；"
            "要覆盖多档品类，勿写完三句就停；示例："
            "「本期始峰五金上架几样实用货。扳手套装紧固拆装好搭档。"
            "钢卷尺现场量尺好带。钳类剪切夹持常用。料斗转运投料也省力。"
            "多型号对照好挑选。细节当面看清楚。看中的到店直接拿。」\n"
        )
    elif loading_ops:
        breath_rule = (
            "7) 【强制】写 2–4 个句号句的**连贯口播**；句内用逗号串过程，"
            "每句总宽约 16–40 字（可含两三个分句），听起来像人在说，不要电报碎句；"
            "TTS 会再按逗号呼吸，你负责润色与情绪。示例："
            "「跟着装车走一趟，仓里这批备货正在码上车，配件都仔细核对。"
            "车门敞开往里码，节奏清楚，要哪一类跟我们说一声。」\n"
        )
    elif base_lock:
        breath_rule = (
            "7) 【强制】保留底稿句序与语义；用「。」短句断气（每句约 8–18 字）；"
            "可在原句内加逗号呼吸，勿另起与底稿无关的句子。\n"
        )
    else:
        breath_rule = (
            "7) 【强制】script 必须用中文句号「。」分成短句；每句 8–16 字；"
            "禁止无标点长串；示例："
            "「货往车斗码稳，工人动作清楚。装完再核对件数，细节都交待到位。」\n"
        )
    recent_ban = "、".join(
        str(x).strip()[:18] for x in (exclude_openers or []) if str(x).strip()
    )[:240]
    style_line = (
        f"本条风格补充（优先落实，不得新增事实）：{extra_style}\n" if extra_style else ""
    )
    # Pre-map recipe/facts/vision so model is never fed raw camera/composition prose
    from engine.pack.narration_script import (
        clip_description_hints_as_lines,
        commercial_product_lines_from_hints,
        filter_grounding_for_speech,
    )

    spoken_components = filter_grounding_for_speech(list(recipe_components or []))
    spoken_facts = filter_grounding_for_speech(list(allowed_facts or []))
    if product_display:
        speak_hints = commercial_product_lines_from_hints(hints, limit=6)
        if not speak_hints:
            speak_hints = clip_description_hints_as_lines(hints, limit=4)
    elif loading_ops:
        speak_hints = clip_description_hints_as_lines(hints, limit=4) or [
            h for h in hints if len(re.sub(r"\s+", "", str(h))) <= 36
        ][:4]
    else:
        speak_hints = clip_description_hints_as_lines(hints, limit=4) or hints[:4]
    # Grounding check / prompt use speakable product lines, not CV caption dump
    hints = speak_hints or hints[:4]
    if base_lock:
        ground_rule = (
            "9) 视觉线索仅作点缀理解，**不得**据此改写或覆盖黄金底稿主线；\n"
        )
        length_rule = (
            "5) 字数贴近底稿（±15% 为宜），勿为铺满硬注水，也勿删短关键中段；\n"
        )
        role_line = (
            "你是口播节奏编辑。任务：在**黄金底稿**上润色断句与气息，不改事实与金句。"
            "并单独给出字幕表情时间点。\n"
        )
        invent_rule = (
            "4) 禁止新增任何事实；禁止替换底稿金句；允许仅断句与极轻语气顺滑；\n"
        )
        base_line = f"\n【黄金底稿·必须保留核心表述】\n{base_script or '（无）'}\n"
        change_line = (
            f"变化编号：{variation_seed if variation_seed is not None else '自动'}"
            "（仅可轻微换断句节奏，不得另起一套话）\n"
        )
    else:
        ground_rule = (
            "9) 若有货品/服务线索：至少把 2 条线索里的具体货品或动作写进口播（不得空转套话）；"
            "线索若是机器视觉原句，只取货名与用途，禁止念「画面主体/背景/颜色平面」；\n"
            if hints
            else "9) 线索不足时写短：少说通用词，不要为了时长注水；\n"
        )
        length_rule = (
            f"5) 口播总字数约 {char_lo}–{char_hi} 字"
            + (
                "（须基本铺满画面时长，禁止过短半途收尾；事实不足可细化可见用途与过程，仍禁空口保证）；\n"
                if (product_display or loading_ops)
                else "（宁可短，不可空话填满）；\n"
            )
        )
        role_line = (
            "你是短视频口播文案编辑。根据「货品/服务线索」写正式、顺的中文商品或服务介绍口播，"
            "并单独给出字幕表情时间点。禁止构图/影评式画面对白。\n"
        )
        invent_rule = (
            "4) 只能改写「允许事实」与「可用组件」，不得新增产品、用途、参数、库存、价格、时效、授权或效果事实；\n"
            "   严禁：放心、库存、规格、保证、最好、第一、厂家直销、马上发货、当天达；\n"
        )
        base_line = f"\n底稿参考（可改写，勿整段照抄）：\n{base_script or '（无）'}\n"
        change_line = (
            f"变化编号：{variation_seed if variation_seed is not None else '自动'}"
            "（不同编号换句式，事实不变）\n"
        )
    recent_line = f"12) 禁止与近期开场雷同：{recent_ban}；\n" if recent_ban else ""
    prompt = "".join(
        [
            role_line,
            "硬性要求：\n",
            "1) 口播像人在现场讲解，禁止机械播报；禁止「本期主题」「大家好」；\n",
            f"2) {scene_rule}\n",
            "3) 不要复述屏幕大标题原文；不要技术规格堆砌；\n",
            invent_rule,
            length_rule,
            "6) emoji_cues 选 2–3 个单字符表情；表情只放 emoji_cues，script 禁止任何 emoji；\n",
            breath_rule,
            "8) 只输出 JSON。格式：",
            '{"script":"口播全文纯文字句号分隔","emoji_cues":[{"at_sec":0.8,"emoji":"✨","label":"主题"}]}\n',
            ground_rule,
            f"10) 严禁语气口水词/表演腔：{filler_ban}；\n",
            f"11) 另禁：{stock_ban}；严禁咱们、聊聊天、聊两句、先请听完；\n",
            recent_line,
            "\n",
            f"品牌：{brand or '本地服务'}\n",
            f"主题：{theme or 'default'}\n",
            f"本条语气规则：{tone_guidance}\n",
            style_line,
            change_line,
            f"屏幕标题（勿照念，不要据此脑补画面外动作）：{title or '（无）'}\n",
            (
                ""
                if base_lock
                else (
                    "货品/服务线索（已清洗，只作点货依据，禁止念视觉原句）：\n- "
                    + "\n- ".join(hints or ["（无线索，请写短而具体的商品或服务介绍）"])
                    + "\n允许事实：\n- "
                    + "\n- ".join(spoken_facts or ["仅可介绍可见货品/服务，勿形容构图"])
                    + "\n可用组件（已转为口播语，禁止退回运镜/构图/问卷原句）：\n- "
                    + "\n- ".join(spoken_components or ["细节当面看清楚。"])
                    + "\n禁止声明/未知项：\n- "
                    + "\n- ".join(forbidden_claims or ["未提供证据的事实"])
                )
            ),
            base_line,
        ]
    )

    payload = _chat_payload(
        model=model,
        prompt=prompt,
        tone=tone,
        variation_seed=variation_seed,
        temperature=0.32 if base_lock else None,
    )
    wall = min(float(_NARRATION_WALL_SEC), max(5.0, float(timeout) * _NARRATION_ATTEMPTS + 15.0))
    deadline = time.monotonic() + wall
    last_error = "rewrite_failed"
    for attempt in range(1, _NARRATION_ATTEMPTS + 1):
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return {
                "ok": False,
                "error": "cancelled",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": True,
                "attempts": attempt - 1,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            return {
                "ok": False,
                "error": f"narration wall-clock timeout ({wall:.0f}s)",
                "script": base_script,
                "emoji_cues": [],
                "model": model,
                "infra": True,
                "attempts": attempt - 1,
            }
        attempts_left = _NARRATION_ATTEMPTS - attempt + 1
        slice_budget = max(5.0, remaining / attempts_left)
        result = chat_completion(
            model=model,
            messages=list(payload["messages"]),
            timeout_sec=slice_budget,
            keep_alive=payload["keep_alive"],
            think=bool(payload.get("think", False)),
            options=dict(payload.get("options") or {}),
            cancel_event=cancel_event,
            kind="narration",
            budget_sec=remaining,
        )
        if result.get("ok"):
            body = result.get("body") or {}
            content = (
                ((body.get("message") or {}).get("content") or "")
                if isinstance(body, dict)
                else ""
            )
            data = _parse_response(content)
            if not data:
                script = content.strip().split("\n")[0].strip()[:220]
                if len(script) < 8:
                    last_error = "无法解析模型输出"
                    if attempt < _NARRATION_ATTEMPTS:
                        time.sleep(_NARRATION_RETRY_SLEEP_SEC)
                        continue
                    return {
                        "ok": False,
                        "error": last_error,
                        "script": base_script,
                        "emoji_cues": [],
                        "model": model,
                        "raw": content[:400],
                        "infra": not bool(content.strip()),
                        "attempts": attempt,
                    }
                data = {"script": script, "emoji_cues": []}
            finalized = _finalize_ok(
                data=data,
                base_script=base_script,
                model=model,
                theme=theme,
                target_duration_sec=target_duration_sec,
                forbidden_claims=forbidden_claims,
                hints=hints,
                attempts=attempt,
                exclude_openers=exclude_openers,
                product_display=product_display,
                loading_ops=loading_ops,
                base_lock=base_lock,
            )
            if finalized.get("ok"):
                return finalized
            last_error = str(finalized.get("error") or "rewrite_quality")
            if attempt < _NARRATION_ATTEMPTS:
                time.sleep(_NARRATION_RETRY_SLEEP_SEC)
                continue
            return finalized

        last_error = str(result.get("error") or "rewrite_failed")
        infra = bool(result.get("infra")) or is_ollama_infra_error(last_error)
        if attempt < _NARRATION_ATTEMPTS and infra:
            time.sleep(_NARRATION_RETRY_SLEEP_SEC)
            continue
        return {
            "ok": False,
            "error": last_error,
            "script": base_script,
            "emoji_cues": [],
            "model": model,
            "infra": infra,
            "attempts": attempt,
            "error_kind": result.get("error_kind"),
        }
    return {
        "ok": False,
        "error": last_error,
        "script": base_script,
        "emoji_cues": [],
        "model": model,
        "infra": is_ollama_infra_error(last_error),
        "attempts": _NARRATION_ATTEMPTS,
    }
