"""Attach TTS narration + burned subtitles to a rendered (or about-to-render) cut."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from engine.config.settings import AppSettings
from engine.pack.expression import (
    build_dual_subtitle_srt,
    resolve_expression_prefs,
    srt_replace_cue_texts,
)
from engine.pack.narration_script import (
    clip_description_hints,
    narration_script_zh,
    scrub_title_from_spoken,
    srt_from_narration_segments,
    text_contains_title,
    tighten_srt_to_voiceover,
)
from engine.pack.tts import narration_bed_from_result, synthesize_script
from engine.render.subtitles_burn import burn_srt_into_video
from engine.template.engine import MontagePlan


def _split_script_to_n(script: str, n: int) -> list[str]:
    """Split narration into ~n cue bodies (timing comes from a base SRT)."""
    import re

    if n <= 0:
        return []
    raw = (script or "").strip()
    if not raw:
        return [""] * n
    parts = [
        p.strip()
        for p in re.split(r"(?<=[。！？.!?…])\s*|\n+", raw)
        if p.strip()
    ]
    if not parts:
        parts = [raw]
    if len(parts) == n:
        return parts
    if len(parts) > n:
        # Merge extras into the last cue
        head, tail = parts[: n - 1], parts[n - 1 :]
        return head + [" ".join(tail)]
    # Fewer sentences than cues: split evenly by length at word/char boundaries
    joined = " ".join(parts)
    if not joined:
        return [""] * n
    chunk_len = max(1, len(joined) // n)
    result: list[str] = []
    start = 0
    for i in range(n):
        if i == n - 1:
            result.append(joined[start:].strip() or joined[-chunk_len:].strip())
            break
        end = min(len(joined), start + chunk_len)
        # Prefer breaking at space / punctuation
        break_at = end
        for j in range(end, start + chunk_len // 2, -1):
            if joined[j - 1] in " 。！？.!?，,、；;":
                break_at = j
                break
        piece = joined[start:break_at].strip()
        result.append(piece or joined[start:end].strip())
        start = break_at if break_at > start else end
    return result


def _cue_count(srt_body: str) -> int:
    chunks = [c.strip() for c in (srt_body or "").strip().split("\n\n") if c.strip()]
    return len(chunks)


def resolve_tts_provider(settings: AppSettings) -> str:
    """Prefer configured provider; Edge 晓晓 → clone (if pack+f5) → macOS say → mock."""
    from engine.pack.voice_clone import f5_available, is_clone_provider

    raw = str(getattr(settings, "tts_provider", "edge") or "edge").lower().strip()
    if is_clone_provider(raw):
        if f5_available():
            return "clone"
        # Missing optional dep: do not silently ship Edge when user asked clone
        return "clone"
    if raw in ("edge", "xiaoxiao"):
        try:
            import edge_tts  # noqa: F401

            return "edge"
        except ImportError:
            return "say" if shutil.which("say") else "mock"
    if raw == "say":
        return "say" if shutil.which("say") else "mock"
    if raw == "mock":
        return "mock"
    # auto / empty: edge first
    if raw in ("auto", ""):
        try:
            import edge_tts  # noqa: F401

            return "edge"
        except ImportError:
            return "say" if shutil.which("say") else "mock"
    return "mock"


def resolve_tts_voice(settings: AppSettings, *, lang: str = "zh", provider: str = "edge") -> str | None:
    explicit = str(getattr(settings, "tts_voice", "") or "").strip()
    if explicit and "Neural" in explicit:
        base = str(lang or "zh").split("-")[0].lower()
        if lang.startswith("zh") and explicit.startswith("zh-"):
            return explicit
        if base and explicit.lower().startswith(base + "-"):
            return explicit
        # Settings CN/EN voice: keep legacy behaviour for those only
        if lang.startswith("zh") or lang == "en":
            if explicit.startswith(("zh-", "en-")):
                return explicit
    if provider == "edge":
        from engine.pack.languages import edge_voice_for_lang
        from engine.pack.tts import DEFAULT_EDGE_VOICE

        return edge_voice_for_lang(lang) or DEFAULT_EDGE_VOICE.get(
            "zh" if str(lang).startswith("zh") else "en", DEFAULT_EDGE_VOICE["zh"]
        )
    return None


def prepare_narration_for_plan(
    settings: AppSettings,
    plan: MontagePlan,
    *,
    profile: dict[str, Any] | None,
    work_dir: Path,
    brand: str = "品牌",
    video_lock: dict[str, Any] | None = None,
    production_rules: dict[str, Any] | None = None,
    variation_seed: int | None = None,
    compliance_policy: dict[str, Any] | None = None,
    tts_token: str | None = None,
    reuse_rewrite: dict[str, Any] | None = None,
    tts_wait_deadline_sec: float | None = None,
    cancel_event: Any | None = None,
    phase_holder: dict[str, str] | None = None,
    exclude_openers: list[str] | None = None,
    industry_lines: list[str] | None = None,
    recent_copy_records: list[Any] | None = None,
) -> dict[str, Any]:
    """
    Synthesize voiceover WAV + SRT for this plan.
    Returns dict with keys: narration_path, srt_path, script, provider, skipped, error?

    When ``tts_token`` is set, the TTS resource-gate slot is acquired only around
    Edge/clone synthesis — not during Ollama narration rewrite — so a hung rewrite
    cannot block the machine TTS slot.

    ``phase_holder`` if provided is mutated with keys ``phase=narration|tts`` so
    worker heartbeats can label which stage is blocked.

    ``reuse_rewrite`` skips a fresh Ollama call and reuses a previously
    successful rewrite (``{"script": ..., "emoji_cues": [...]}``) — used by TTS
    failure retries so a flaky Edge/clone synth does not burn a second LLM call
    (and does not risk the retry failing narration rewrite too).

    ``tts_wait_deadline_sec`` bounds how long this call waits for the shared
    TTS slot before giving up (``None`` waits indefinitely, prior behavior).
    ``cancel_event``, when set, aborts the TTS-slot wait early.
    """
    prefs = resolve_expression_prefs(profile)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    lock = video_lock or {}
    voice_lock = dict(lock.get("voice")) if isinstance(lock.get("voice"), dict) else {}
    narr_lock = lock.get("narration") if isinstance(lock.get("narration"), dict) else {}
    sub_lock = dict(lock.get("subtitle")) if isinstance(lock.get("subtitle"), dict) else {}
    emoji_lock = lock.get("emoji") if isinstance(lock.get("emoji"), dict) else {}
    strip_punct = bool(lock.get("strip_all_punctuation", narr_lock.get("strip_punctuation", True)))
    effective_rules = production_rules if isinstance(production_rules, dict) else {}
    for key in (
        "voice_lang",
        "subtitle_lang",
        "subtitle_burn",
        "dual_secondary_lang",
    ):
        if effective_rules.get(key) is not None:
            prefs[key] = effective_rules[key]
    voice_lock.update(
        {
            "provider": effective_rules.get("tts_provider")
            or voice_lock.get("provider")
            or "edge",
            "voice": effective_rules.get("tts_voice")
            or voice_lock.get("voice")
            or "zh-CN-XiaoxiaoNeural",
            "voice_pack": effective_rules.get("voice_pack")
            or voice_lock.get("voice_pack")
            or "aunt_slow",
            "rate": effective_rules.get("narration_rate")
            or voice_lock.get("rate")
            or "-8%",
            "volume": effective_rules.get("narration_volume")
            or voice_lock.get("volume")
            or "+12%",
            "pitch": effective_rules.get("narration_pitch")
            or voice_lock.get("pitch")
            or "+35Hz",
        }
    )
    sub_lock.update(
        {
            "font_size": effective_rules.get("subtitle_font_size")
            or sub_lock.get("font_size")
            or 64,
            "color": effective_rules.get("subtitle_color")
            or sub_lock.get("color")
            or "#FFFFFF",
            "outline": effective_rules.get("subtitle_stroke_color")
            or sub_lock.get("outline")
            or "#000000",
            "outline_width": (
                0
                if effective_rules.get("subtitle_stroke_enabled") is False
                else (
                    effective_rules.get("subtitle_stroke_width")
                    if effective_rules.get("subtitle_stroke_width") is not None
                    else sub_lock.get("outline_width", 4)
                )
            ),
            "bottom_padding_px": effective_rules.get("subtitle_glyph_bottom_px")
            or sub_lock.get("bottom_padding_px")
            or (180 if getattr(plan, "orientation", "portrait") == "landscape" else 420),
            "effect": effective_rules.get("subtitle_effect") or "none",
            "align": effective_rules.get("subtitle_align") or "center",
        }
    )
    narration_tone = str(effective_rules.get("narration_tone") or "plain")
    # VIDEO_LOCK emotional_delivery may still request 「有劲」; Phase B remaps:
    # product tabletop → professional; loading/ops → warm (slightly colloquial, no fillers).
    requested_emotional = bool(narr_lock.get("emotional_delivery")) and narration_tone in (
        "plain",
        "warm",
        "energetic",
    )
    if requested_emotional:
        narration_tone = "passionate"

    # HARD: 字幕必须烧录才能看见行内表情；禁止标题区浮动贴纸。
    # Preserve explicit burn_dual (still burned); only upgrade external → burn_mono.
    if bool(sub_lock.get("force_burn_mono") or emoji_lock.get("in_subtitle")):
        if prefs.get("subtitle_burn") not in ("burn_mono", "burn_dual"):
            prefs["subtitle_burn"] = "burn_mono"
    if bool(emoji_lock.get("forbid_title_stickers", True)):
        out_forbid_stickers = True
    else:
        out_forbid_stickers = True  # default hard: never title stickers
    _ = out_forbid_stickers

    out: dict[str, Any] = {
        "narration_path": None,
        "srt_path": None,
        "script": "",
        "provider": None,
        "skipped": False,
        "voice_lang": prefs.get("voice_lang"),
        "subtitle_lang": prefs.get("subtitle_lang"),
        "subtitle_burn": prefs.get("subtitle_burn"),
        "dual_secondary_lang": prefs.get("dual_secondary_lang"),
        "strip_punctuation": strip_punct,
        "subtitle_font_size": int(sub_lock.get("font_size") or 64),
        "subtitle_bottom_padding_px": int(sub_lock.get("bottom_padding_px") or 420),
        "subtitle_color": str(sub_lock.get("color") or "#FFFFFF"),
        "subtitle_stroke_color": str(sub_lock.get("outline") or "#000000"),
        "subtitle_stroke_width": int(sub_lock.get("outline_width") or 0),
        "subtitle_effect": str(sub_lock.get("effect") or "none"),
        "subtitle_align": str(sub_lock.get("align") or "center"),
        "narration_tone": narration_tone,
        "variation_seed": variation_seed,
    }
    if phase_holder is not None:
        phase_holder["phase"] = "narration"

    if prefs.get("voice_lang") == "none" and prefs.get("subtitle_lang") == "none":
        out["skipped"] = True
        return out

    brand_name = brand
    if profile:
        brand_name = str((profile.get("brand") or {}).get("display_name") or brand_name)

    plan_dur = sum(float(getattr(c, "duration_sec", 0) or 0) for c in (plan.clips or []))
    if plan_dur <= 0:
        plan_dur = float(getattr(plan, "target_duration", 0) or 0) or 26.0

    cps = float(voice_lock.get("chars_per_sec_zh") or getattr(settings, "tts_chars_per_sec_zh", 3.8) or 3.8)
    title_lock = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    # Locked: on-screen title never enters VO / captions
    speak_title = bool(title_lock.get("spoken", False))
    forbid_title_in_sub = bool(title_lock.get("forbid_in_subtitle", True)) or not speak_title
    on_screen_title = str(plan.title or "")
    out["title_spoken"] = speak_title
    out["title_in_subtitle"] = not forbid_title_in_sub

    from engine.pack.narration_script import (
        collect_scene_tags,
        resolve_delivery_tone,
        visual_context_flags,
    )

    # One hint per slot when possible so product VO order tracks cuts
    visual_hints = clip_description_hints(plan, limit=12)
    theme_key = str(plan.theme or "default")
    scene_tags = collect_scene_tags(plan, limit=12)
    vflags = visual_context_flags(
        visual_hints,
        scene_tags=scene_tags,
        theme=theme_key,
    )
    delivery_tone = resolve_delivery_tone(
        narration_tone,
        theme=theme_key,
        product_display=bool(vflags.get("product_display")),
        loading_ops=bool(vflags.get("loading_ops")),
    )
    out["narration_tone"] = delivery_tone
    out["narration_tone_requested"] = narration_tone
    out["visual_mode"] = {
        "product_display": bool(vflags.get("product_display")),
        "loading_ops": bool(vflags.get("loading_ops")),
    }
    pack_lines = list(industry_lines or [])
    if not pack_lines:
        try:
            from engine.catalog.industry_pack import narration_lines_for_theme, pack_id_for_customer

            customer_name = ""
            if isinstance(profile, dict):
                customer_name = str(profile.get("name") or profile.get("customer_name") or "")
            pack_id = pack_id_for_customer(customer_name or None, profile if isinstance(profile, dict) else None)
            pack_lines = narration_lines_for_theme(pack_id, theme_key)
        except Exception:  # noqa: BLE001
            pack_lines = []
    exclude_list = [str(x).strip() for x in (exclude_openers or []) if str(x).strip()]
    out["exclude_openers"] = exclude_list
    out["industry_lines_count"] = len(pack_lines)
    fp = getattr(plan, "content_fingerprint", None) or {}
    locked_script = ""
    if isinstance(fp, dict) and fp.get("production_mode") == "scene_tour":
        locked_script = str(fp.get("locked_script") or "").strip()
    if locked_script:
        script = locked_script
        out["script"] = script
        out["script_base"] = script
        out["scene_tour"] = True
        out["visual_hints"] = visual_hints
        out["target_duration_sec"] = plan_dur
        from engine.pack.emoji_stickers import ensure_theme_emoji_cues

        out["emoji_cues"] = ensure_theme_emoji_cues(
            [],
            theme="scene_tour",
            video_duration_sec=float(plan_dur or 12.0),
            n=3,
        )
        out["ollama_narration"] = False
        out["ollama_narration_skipped"] = "scene_tour_locked"
        # 跟镜精品：跳过词池金句与 Ollama 改写，直接配音
    else:
        script = narration_script_zh(
            on_screen_title,
            brand=brand_name,
            theme=theme_key,
            clip_hints=visual_hints[:4],
            target_duration_sec=plan_dur,
            cps=cps,
            speak_title=speak_title,
            tone=delivery_tone,
            variation_seed=variation_seed,
            allowed_facts=list(getattr(plan, "allowed_facts", []) or []),
            forbidden_claims=list(getattr(plan, "forbidden_claims", []) or []),
            recipe_components=list(getattr(plan, "copy_components", []) or []),
            exclude_openers=exclude_list,
            industry_lines=pack_lines,
            scene_tags=scene_tags,
            product_display=bool(vflags.get("product_display")),
            loading_ops=bool(vflags.get("loading_ops")),
        )
        if not speak_title:
            script = scrub_title_from_spoken(script, on_screen_title)
        out["script"] = script
        out["script_base"] = script
        out["visual_hints"] = visual_hints
        out["target_duration_sec"] = plan_dur
        out["emoji_cues"] = []
        out["ollama_narration"] = False

    # Ops toggle: Ollama rewrites visual descriptions → spoken copy (+ emoji for subtitles)
    if locked_script:
        pass
    elif reuse_rewrite and reuse_rewrite.get("script"):
        # TTS retry path: narration already succeeded once this job — reuse it
        # verbatim instead of spending another Ollama call (and another chance
        # to fail rewrite on a perfectly fine script).
        script = str(reuse_rewrite["script"])
        if not speak_title:
            script = scrub_title_from_spoken(script, on_screen_title)
        out["script"] = script
        out["script_display"] = reuse_rewrite.get("script_display") or script
        out["emoji_cues"] = list(reuse_rewrite.get("emoji_cues") or [])
        out["ollama_narration"] = True
        out["ollama_narration_model"] = reuse_rewrite.get("ollama_narration_model")
        out["ollama_narration_attempts"] = reuse_rewrite.get("ollama_narration_attempts")
        out["ollama_narration_reused"] = True
    elif bool(getattr(settings, "ollama_narration_enabled", False)) and prefs.get("voice_lang") != "none":
        try:
            from engine.pack.ollama_narration import (
                is_ollama_infra_error,
                resolve_narration_model,
                rewrite_narration_with_ollama,
            )

            model = resolve_narration_model(settings)
            # Feed commercial/speakable clues, never raw long vision captions
            from engine.pack.narration_script import (
                clip_description_hints_as_lines,
                commercial_product_lines_from_hints,
            )

            if bool(vflags.get("product_display")):
                ollama_hints = commercial_product_lines_from_hints(visual_hints, limit=6)
                if not ollama_hints:
                    ollama_hints = clip_description_hints_as_lines(visual_hints, limit=4)
            else:
                ollama_hints = clip_description_hints_as_lines(visual_hints, limit=4) or visual_hints[:4]
            rewritten = rewrite_narration_with_ollama(
                base_script=script,
                visual_hints=ollama_hints or visual_hints,
                theme=str(plan.theme or "default"),
                brand=brand_name,
                target_duration_sec=plan_dur,
                model=model,
                title=on_screen_title,
                tone=delivery_tone,
                variation_seed=variation_seed,
                allowed_facts=list(getattr(plan, "allowed_facts", []) or []),
                forbidden_claims=list(getattr(plan, "forbidden_claims", []) or []),
                recipe_components=list(getattr(plan, "copy_components", []) or []),
                style_notes=str(effective_rules.get("notes") or ""),
                cancel_event=cancel_event,
                exclude_openers=exclude_list,
                product_display=bool(vflags.get("product_display")),
                loading_ops=bool(vflags.get("loading_ops")),
            )
            out["ollama_narration_model"] = model
            out["ollama_narration_attempts"] = rewritten.get("attempts")
            if rewritten.get("ok") and rewritten.get("script"):
                script = str(rewritten["script"])
                if not speak_title:
                    script = scrub_title_from_spoken(script, on_screen_title)
                from engine.pack.emoji_stickers import (
                    ensure_theme_emoji_cues,
                    strip_emoji_for_speech,
                )

                script = strip_emoji_for_speech(script)
                out["script"] = script
                out["script_display"] = script
                out["emoji_cues"] = ensure_theme_emoji_cues(
                    rewritten.get("emoji_cues") or [],
                    theme=str(plan.theme or "default"),
                    video_duration_sec=float(plan_dur or 12.0),
                    n=3,
                )
                out["ollama_narration"] = True
                out["ollama_narration_done"] = True
                if rewritten.get("base_lock") is not None:
                    out["ollama_base_lock"] = bool(rewritten.get("base_lock"))
                if rewritten.get("base_fallback"):
                    out["ollama_base_fallback"] = True
            else:
                err = rewritten.get("error") or "rewrite_failed"
                out["ollama_narration_error"] = err
                out["ollama_narration_infra"] = bool(
                    rewritten.get("infra") if "infra" in rewritten else is_ollama_infra_error(err)
                )
                # READY require_ollama 会打回；用底稿继续 TTS+整轮渲染是空转主因。
                out["voice_lang"] = str(prefs.get("voice_lang") or "zh")
                out["subtitle_lang"] = str(prefs.get("subtitle_lang") or "zh")
                out["skipped"] = True
                out["provider"] = "skipped"
                return out
        except Exception as e:  # noqa: BLE001
            from engine.pack.ollama_narration import is_ollama_infra_error

            err = str(e)
            out["ollama_narration_error"] = err
            out["ollama_narration_infra"] = is_ollama_infra_error(err)
            out["voice_lang"] = str(prefs.get("voice_lang") or "zh")
            out["subtitle_lang"] = str(prefs.get("subtitle_lang") or "zh")
            out["skipped"] = True
            out["provider"] = "skipped"
            return out

    if recent_copy_records and not out.get("scene_tour"):
        from engine.pack.copy_diversity_gate import check_narration

        div_reason = check_narration(script, recent_copy_records)
        if div_reason:
            out["copy_diversity_warning"] = div_reason
            for retry in range(2):
                retry_seed = int((variation_seed or 0) + (retry + 1) * 31)
                retry_script = narration_script_zh(
                    on_screen_title,
                    brand=brand_name,
                    theme=theme_key,
                    clip_hints=visual_hints[:4],
                    target_duration_sec=plan_dur,
                    cps=cps,
                    speak_title=speak_title,
                    tone=delivery_tone,
                    variation_seed=retry_seed,
                    allowed_facts=list(getattr(plan, "allowed_facts", []) or []),
                    forbidden_claims=list(getattr(plan, "forbidden_claims", []) or []),
                    recipe_components=list(getattr(plan, "copy_components", []) or []),
                    exclude_openers=exclude_list,
                    industry_lines=pack_lines,
                    scene_tags=scene_tags,
                    product_display=bool(vflags.get("product_display")),
                    loading_ops=bool(vflags.get("loading_ops")),
                )
                if not speak_title:
                    retry_script = scrub_title_from_spoken(retry_script, on_screen_title)
                if not check_narration(retry_script, recent_copy_records):
                    script = retry_script
                    out["script"] = script
                    out["script_base"] = script
                    out["copy_diversity_retry"] = retry + 1
                    out.pop("copy_diversity_warning", None)
                    break

    # About to synthesize voice — mark phase so heartbeats distinguish TTS hangs.
    if phase_holder is not None:
        phase_holder["phase"] = "tts"

    # Guarantee product/loading VO covers the picture.
    # Product: one VO block per slot (clip-aligned) — NEVER pad with SKUs from other cuts.
    product_slots: list[dict[str, Any]] | None = None
    scene_tour_slots = bool(out.get("scene_tour"))
    if prefs.get("voice_lang") != "none" and bool(vflags.get("product_display")) and plan.clips:
        from engine.pack.narration_script import (
            product_slot_scripts_from_plan,
            script_from_product_slots,
        )

        product_slots = product_slot_scripts_from_plan(
            plan,
            brand=brand_name,
            cps=float(cps or 3.2),
            variation_seed=variation_seed,
        )
        script = script_from_product_slots(product_slots)
        out["script"] = script
        out["script_display"] = script
        out["product_slot_scripts"] = product_slots
        out["product_clip_aligned"] = True
        # Soft warehouse-speak strip (mid-sentence)
        for leak in (
            "日常作业仓内可看",
            "仓内可看",
            "仓内",
            "从外观看到现场操作",
            "这段记录的是实际作业过程",
        ):
            if leak == "仓内":
                script = script.replace("仓内可看", "").replace("仓内", "")
            else:
                script = script.replace(leak, "")
        # Resplit slots after strip only if global script changed — keep slot texts
        out["script"] = script
        out["script_display"] = script
    elif scene_tour_slots and plan.clips and prefs.get("voice_lang") != "none":
        # 跟镜精品：按镜一句，镜切=句切（禁止整段 per_sentence 漂移）
        from engine.pack.emoji_stickers import strip_emoji_for_speech
        from engine.pack.scene_tour_timing import clip_available_sec_for_plan_clip

        product_slots = []
        for i, c in enumerate(plan.clips or []):
            text = strip_emoji_for_speech(str(getattr(c, "description", "") or ""))
            dur = float(getattr(c, "duration_sec", 0) or 0) or 4.0
            avail = clip_available_sec_for_plan_clip(c)
            product_slots.append(
                {
                    "slot": str(getattr(c, "slot", "") or f"s{i}"),
                    "duration_sec": max(2.5, dur),
                    "max_duration_sec": max(2.5, float(avail)),
                    "text": text,
                }
            )
        out["product_slot_scripts"] = product_slots
        out["scene_tour_clip_aligned"] = True
        out["product_clip_aligned"] = True
    elif prefs.get("voice_lang") != "none" and bool(vflags.get("loading_ops")):
        from engine.pack.narration_script import expand_script_to_timeline

        base_for_expand = str(out.get("script_base") or script)
        expanded = expand_script_to_timeline(
            script,
            target_duration_sec=plan_dur,
            base_script=base_for_expand,
            product_display=False,
            loading_ops=True,
            brand=brand_name,
            clip_hints=visual_hints,
            industry_lines=pack_lines,
        )
        if expanded and expanded != script:
            out["script_expanded_for_timeline"] = True
            script = expanded
            out["script"] = script
            if not out.get("script_display"):
                out["script_display"] = script
            elif out.get("ollama_narration"):
                out["script_display"] = script

    if out.get("ollama_narration"):
        out["ollama_narration_done"] = True
    elif not locked_script and prefs.get("voice_lang") != "none":
        # Ollama off / failed-closed base path: never TTS raw vision captions.
        from engine.pack.narration_script import assert_spoken_not_raw_vision

        assert_spoken_not_raw_vision(
            str(out.get("script") or script or ""),
            list(out.get("visual_hints") or visual_hints or []),
            source="base_script",
        )

    # HARD: emoji_cues always available for subtitle injection (even if Ollama off/fail)
    if not out.get("emoji_cues"):
        from engine.pack.emoji_stickers import ensure_theme_emoji_cues

        out["emoji_cues"] = ensure_theme_emoji_cues(
            [],
            theme=str(plan.theme or "default"),
            video_duration_sec=float(plan_dur or 12.0),
            n=3,
        )

    # Locked voice overrides settings
    from engine.pack.voice_clone import is_clone_provider

    if voice_lock.get("provider"):
        provider = str(voice_lock["provider"]).lower()
        if provider in ("edge", "xiaoxiao"):
            try:
                import edge_tts  # noqa: F401

                provider = "edge"
            except ImportError:
                provider = resolve_tts_provider(settings)
        elif is_clone_provider(provider):
            provider = "clone"
        else:
            provider = resolve_tts_provider(settings)
    else:
        provider = resolve_tts_provider(settings)
    out["provider"] = provider
    narr_path = work_dir / "voiceover.wav"
    voice_lang = str(prefs.get("voice_lang") or "zh")
    sub_lang = str(prefs.get("subtitle_lang") or "zh")
    burn_mode = str(prefs.get("subtitle_burn") or "external")
    dual_lang = str(prefs.get("dual_secondary_lang") or "zh")
    srt_tag = sub_lang if sub_lang != "none" else (voice_lang if voice_lang != "none" else "zh")
    srt_path = work_dir / f"subtitle.{srt_tag.replace('-', '_')}.srt"
    voice_srt_body = ""

    if prefs.get("voice_lang") != "none":
        try:
            from engine.pack.languages import edge_voice_for_lang, normalize_lang_code
            from engine.pack.locale import narration_script_for_lang

            lang = normalize_lang_code(str(prefs.get("voice_lang") or "zh"))
            if lang == "none":
                lang = "zh"
            # Prefer locked 晓晓 for zh production; other langs use catalog Edge voices
            if lang not in ("zh", "zh-TW"):
                script = narration_script_for_lang(
                    lang,
                    brand=brand_name,
                    title_zh=on_screen_title,
                    speak_title=False,
                )
                out["script"] = script
            elif lang == "zh-TW":
                script = script.replace("这里是", "這裡是").replace("实拍", "實拍").replace("发货", "出貨")
                out["script"] = script
            locked_voice = str(voice_lock.get("voice") or "").strip()

            def _voice_fits(vid: str, code: str) -> bool:
                if not vid or "Neural" not in vid:
                    return False
                if code.startswith("zh"):
                    return vid.startswith("zh-")
                base = code.split("-")[0].lower()
                return vid.lower().startswith(base + "-")

            if locked_voice and _voice_fits(locked_voice, lang):
                voice = locked_voice
            elif lang.startswith("zh"):
                voice = locked_voice if locked_voice and locked_voice.startswith("zh-") else (
                    edge_voice_for_lang(lang)
                    or resolve_tts_voice(settings, lang=lang, provider=provider)
                )
            else:
                # Never apply zh-locked 晓晓 to foreign packs
                voice = edge_voice_for_lang(lang) or resolve_tts_voice(settings, lang=lang, provider=provider)
            out["voice"] = voice
            rate = str(voice_lock.get("rate") or getattr(settings, "tts_rate", "") or "") or None
            pitch = str(voice_lock.get("pitch") or getattr(settings, "tts_pitch", "") or "") or None
            # Keep locked rate/pitch for zh; neutral defaults for foreign
            if lang.startswith("zh"):
                # HARD: VIDEO_LOCK rate/pitch/volume — 情感播报（音高+音量，语速仍 -8%）
                rate = str(voice_lock.get("rate") or "-8%")
                pitch = str(voice_lock.get("pitch") or "+35Hz")
                volume = str(voice_lock.get("volume") or "+12%")
            elif lang not in ("zh", "zh-TW"):
                rate = "+0%"
                pitch = "+0Hz"
                volume = "+0%"
            else:
                volume = str(voice_lock.get("volume") or "+0%")
            out["tts_rate"] = rate
            out["tts_pitch"] = pitch
            out["tts_volume"] = volume
            # Locked provider: never silently fall back when VIDEO_LOCK pins voice
            lock_requires_edge = str(voice_lock.get("provider") or "").lower() in (
                "edge",
                "xiaoxiao",
            )
            lock_requires_clone = is_clone_provider(str(voice_lock.get("provider") or ""))
            # HARD: strip emoji / sticker-speak before TTS (emoji only in subtitle display)
            from engine.pack.emoji_stickers import strip_emoji_for_speech

            script = strip_emoji_for_speech(script)
            out["script"] = script
            from engine.content.compliance import (
                apply_claim_soft_rewrites,
                check_output_fields,
            )

            compliance = check_output_fields(
                {
                    "title": on_screen_title,
                    "narration": script,
                    "subtitle": script,
                },
                policy=compliance_policy,
                evidence_ids=(
                    {"semantic.strict_v1"}
                    if isinstance(getattr(plan, "content_fingerprint", None), dict)
                    and plan.content_fingerprint.get("strict_semantic_only") is True
                    and plan.content_fingerprint.get("evidence_level") == "semantic.v1"
                    else set()
                ),
            )
            out["compliance"] = compliance
            if not compliance["passed"]:
                rewritten, applied = apply_claim_soft_rewrites(
                    {
                        "title": on_screen_title,
                        "narration": script,
                        "subtitle": script,
                    },
                    compliance,
                    policy=compliance_policy,
                    allow_soft_deny=True,
                )
                if applied:
                    compliance2 = check_output_fields(
                        rewritten,
                        policy=compliance_policy,
                        evidence_ids=(
                            {"semantic.strict_v1"}
                            if isinstance(getattr(plan, "content_fingerprint", None), dict)
                            and plan.content_fingerprint.get("strict_semantic_only") is True
                            and plan.content_fingerprint.get("evidence_level")
                            == "semantic.v1"
                            else set()
                        ),
                    )
                    out["compliance"] = compliance2
                    out["compliance_rewrites"] = applied
                    if compliance2.get("passed"):
                        on_screen_title = str(rewritten.get("title") or on_screen_title)
                        script = str(rewritten.get("narration") or script)
                        out["script"] = script
                        out["title"] = on_screen_title
                        plan.title = on_screen_title
                    else:
                        out["error"] = "文案合规门禁失败，禁止进入 TTS"
                        out["compliance_blocked"] = True
                        return out
                else:
                    out["error"] = "文案合规门禁失败，禁止进入 TTS"
                    out["compliance_blocked"] = True
                    return out
            # Product final authority: rebuild per-slot VO after compliance strips (lock to cuts)
            if bool(vflags.get("product_display")) and plan.clips:
                from engine.pack.narration_script import (
                    product_slot_scripts_from_plan,
                    script_from_product_slots,
                )

                product_slots = product_slot_scripts_from_plan(
                    plan,
                    brand=brand_name,
                    cps=float(cps or 3.2),
                    variation_seed=variation_seed,
                )
                for slot_row in product_slots:
                    slot_row["text"] = strip_emoji_for_speech(str(slot_row.get("text") or ""))
                script = script_from_product_slots(product_slots)
                out["script"] = script
                out["script_display"] = script
                out["product_slot_scripts"] = product_slots
                out["product_clip_aligned"] = True
            # TTS 前统一清洗口播：所有 product_slots（含日更 product_display 重建）+ 无槽汇总 script
            from engine.content.compliance import (
                apply_rewrites_to_text,
                sanitize_script_claims,
            )

            applied_rows = list(out.get("compliance_rewrites") or [])

            def _finalize_spoken_text(raw: str) -> str:
                cleaned = sanitize_script_claims(
                    strip_emoji_for_speech(str(raw or "")),
                    policy=compliance_policy,
                )
                if applied_rows:
                    cleaned = apply_rewrites_to_text(cleaned, applied_rows)
                return cleaned

            if product_slots:
                from engine.pack.narration_script import script_from_product_slots

                for i, slot_row in enumerate(product_slots):
                    cleaned = _finalize_spoken_text(str(slot_row.get("text") or ""))
                    slot_row["text"] = cleaned
                    clips = list(plan.clips or [])
                    if i < len(clips):
                        clips[i].description = cleaned
                script = script_from_product_slots(product_slots)
                out["script"] = script
                out["script_display"] = script
                out["product_slot_scripts"] = product_slots
            else:
                script = _finalize_spoken_text(script)
                out["script"] = script
                out["script_display"] = script
            # Sentence gap: audible breath for 断句; SRT clears during gap (不得拖字)
            gap = float(sub_lock.get("inter_sentence_gap_seconds") or 0.15)
            if gap < 0.1:
                gap = 0.15  # minimum breath so 晓晓不连读
            # HARD: pull cue end before speech ends (default 120ms; was 40ms — too sticky)
            tail_trim = float(sub_lock.get("tail_trim_seconds") or 0.12)
            if tail_trim < 0.08:
                tail_trim = 0.12
            clone_kwargs: dict[str, Any] = {}
            if provider == "clone":
                from engine.pack.voice_clone import resolve_voice_pack

                pack = resolve_voice_pack(voice_lock=voice_lock, settings=settings)
                clone_kwargs = {
                    "clone_pack": pack,
                    "clone_pack_id": pack.id,
                    "clone_speed": pack.speed,
                }
                # Prefer pack cps for duration estimates / script budgeting
                cps = float(pack.chars_per_sec_zh or cps)
                out["clone_pack"] = pack.to_dict()
                out["voice"] = pack.id
                # Clone does not use Edge pitch/volume locks
                out["tts_rate"] = "clone"
                out["tts_pitch"] = "clone"
                out["tts_volume"] = "clone"

            def _synth_under_slot():
                if product_slots:
                    from engine.pack.tts import synthesize_script_clip_slots

                    return synthesize_script_clip_slots(
                        product_slots,
                        work_dir / "narration",
                        lang=lang,
                        provider=provider,  # type: ignore[arg-type]
                        voice=voice if provider != "clone" else None,
                        cps=cps,
                        rate=rate,
                        pitch=pitch,
                        volume=volume,
                        # 跟镜雅述：保留逗号断句，禁止去标点导致一口气连读
                        strip_punctuation=(
                            False
                            if out.get("scene_tour")
                            else (strip_punct and lang.startswith("zh"))
                        ),
                        allow_fallback=not (lock_requires_edge or lock_requires_clone),
                        inter_sentence_gap_sec=gap,
                        pad_to_duration=not bool(out.get("scene_tour")),
                        **clone_kwargs,
                    )
                return synthesize_script(
                    script,
                    work_dir / "narration",
                    lang=lang,
                    provider=provider,  # type: ignore[arg-type]
                    voice=voice if provider != "clone" else None,
                    cps=cps,
                    rate=rate,
                    pitch=pitch,
                    volume=volume,
                    strip_punctuation=strip_punct and lang.startswith("zh"),
                    allow_fallback=not (lock_requires_edge or lock_requires_clone),
                    inter_sentence_gap_sec=gap,
                    **clone_kwargs,
                )

            if tts_token:
                import time as _time

                from engine.runtime.resource_gate import gate as resource_gate

                deadline = (
                    _time.monotonic() + float(tts_wait_deadline_sec)
                    if tts_wait_deadline_sec is not None
                    else None
                )
                while not resource_gate.try_acquire("tts", tts_token):
                    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                        raise RuntimeError("cancelled_waiting_for_tts_slot")
                    if deadline is not None and _time.monotonic() >= deadline:
                        raise TimeoutError(f"tts_slot_wait_timeout ({tts_wait_deadline_sec:.0f}s)")
                    _time.sleep(0.25)
                try:
                    narr = _synth_under_slot()
                finally:
                    resource_gate.release("tts", tts_token)
            else:
                narr = _synth_under_slot()
            if lock_requires_edge and narr.provider != "edge":
                raise RuntimeError(
                    f"VIDEO_LOCK requires Edge TTS, got provider={narr.provider}"
                )
            if lock_requires_clone and narr.provider != "clone":
                raise RuntimeError(
                    f"VIDEO_LOCK requires clone TTS, got provider={narr.provider}"
                )
            bed_gap = float((narr.extras or {}).get("inter_sentence_gap_sec") or gap)
            # Clip-aligned bed already includes per-slot silence; never re-concat segments
            # (each segment's audio_path may point at the full slot oneshot → VO loops).
            if str((narr.extras or {}).get("mode") or "") == "clip_aligned_slots":
                bed_gap = 0.0
            bed = narration_bed_from_result(narr, narr_path, gap_sec=bed_gap)
            # 跟镜精品：按镜配音后，镜长必须等于该镜旁白——禁止再用字数比例重切
            if out.get("scene_tour"):
                from engine.pack.scene_tour_timing import (
                    apply_slot_speech_durations,
                    expand_plan_clips_to_cover_narration,
                )
                from engine.pack.tts import probe_audio_duration

                timing_meta: dict[str, Any]
                slot_secs = list((narr.extras or {}).get("slot_speech_sec") or [])
                if slot_secs and str((narr.extras or {}).get("mode") or "") == "clip_aligned_slots":
                    timing_meta = apply_slot_speech_durations(
                        plan, slot_secs, gap_sec=max(0.08, float(gap))
                    )
                    # 仅当总画面仍短于旁白时，把差额加到最后一镜（不重切前序镜）
                    bed_now = Path(bed)
                    if bed_now.is_file() and plan.clips:
                        ndur = float(probe_audio_duration(bed_now) or 0)
                        pic = sum(float(c.duration_sec or 0) for c in plan.clips)
                        deficit = (ndur + 0.15) - pic
                        if deficit > 0.08:
                            last = plan.clips[-1]
                            from engine.pack.scene_tour_timing import clip_available_sec_for_plan_clip

                            room = max(
                                0.0,
                                clip_available_sec_for_plan_clip(last)
                                - float(last.duration_sec or 0),
                            )
                            add = min(room, deficit)
                            last.duration_sec = round(float(last.duration_sec or 0) + add, 3)
                            timing_meta["tail_pad_sec"] = round(add, 3)
                            if add + 0.05 < deficit:
                                timing_meta["need_fit"] = True
                        timing_meta["reason"] = "slot_aligned_locked"
                    # 硬校验：任一镜旁白仍明显长于画面 → 拒配音（禁止错位出片）
                    overflow = []
                    for row in timing_meta.get("slots") or []:
                        if not isinstance(row, dict):
                            continue
                        sp = float(row.get("speech_sec") or 0)
                        du = float(row.get("duration_sec") or 0)
                        if sp > du + 0.35:
                            overflow.append(
                                f"镜{int(row.get('index', 0)) + 1}旁白{sp:.2f}s>画面{du:.2f}s"
                            )
                    if overflow:
                        raise RuntimeError(
                            "跟镜声画未对齐，已拒绝出片：" + "；".join(overflow[:4])
                        )
                    out["scene_tour_clip_aligned"] = True
                else:
                    timing_meta = expand_plan_clips_to_cover_narration(
                        plan,
                        narration_path=bed,
                        min_exceed_sec=0.15,
                    )
                out["scene_tour_timing"] = timing_meta
                try:
                    plan_dur = sum(float(c.duration_sec or 0) for c in (plan.clips or []))
                    out["target_duration_sec"] = float(plan_dur)
                except Exception:  # noqa: BLE001
                    pass
            # Prefer adaptive tempo over end-frame freeze when VO > picture (L15 still holds).
            fit_meta: dict[str, Any] = {"applied": False, "reason": "disabled"}
            fit_enabled = True
            if isinstance(effective_rules, dict) and "narration_fit_to_picture" in effective_rules:
                fit_enabled = bool(effective_rules.get("narration_fit_to_picture"))
            elif isinstance(voice_lock, dict) and "fit_to_picture" in voice_lock:
                fit_enabled = bool(voice_lock.get("fit_to_picture"))
            else:
                fit_enabled = bool(getattr(settings, "narration_fit_to_picture", True))
            # 跟镜精品按镜对齐后：禁止整段全局 tempo-fit（会毁掉镜切=句切）
            if out.get("scene_tour") and out.get("scene_tour_clip_aligned"):
                fit_enabled = False
            elif out.get("scene_tour"):
                timing = out.get("scene_tour_timing") if isinstance(out.get("scene_tour_timing"), dict) else {}
                fit_enabled = bool(timing.get("need_fit"))
            if fit_enabled and plan_dur > 0.5 and Path(bed).is_file():
                from engine.pack.tts import fit_narration_to_picture_duration

                max_fit = float(
                    getattr(settings, "narration_fit_max_speed", 1.35) or 1.35
                )
                if max_fit < 1.05:
                    max_fit = 1.05
                if max_fit > 1.6:
                    max_fit = 1.6
                total_holder: list[float] = []
                fit_meta = fit_narration_to_picture_duration(
                    Path(bed),
                    picture_duration_sec=float(plan_dur),
                    segments=list(getattr(narr, "segments", None) or []),
                    total_duration_holder=total_holder,
                    max_speed=max_fit,
                    min_tail_sec=0.15,
                )
                if total_holder:
                    try:
                        narr.total_duration_sec = float(total_holder[0])
                    except Exception:  # noqa: BLE001
                        pass
            out["narration_fit_to_picture"] = fit_meta
            out["narration_path"] = str(bed)
            out["provider"] = narr.provider
            out["requested_provider"] = provider
            out["lock_requires_edge"] = lock_requires_edge
            out["lock_requires_clone"] = lock_requires_clone
            out["tts_mode"] = (narr.extras or {}).get("mode")
            out["inter_sentence_gap_sec"] = bed_gap
            if (narr.extras or {}).get("clone_pack"):
                out["clone_pack"] = narr.extras["clone_pack"]
            voice_srt_body = srt_from_narration_segments(
                narr.segments,
                bottom_dual_line=False,
                tail_trim_seconds=tail_trim,
                inter_sentence_gap_seconds=bed_gap,
                forbid_title=on_screen_title if forbid_title_in_sub else None,
            )
            # Nuclear post-pass against the real VO bed — 话说完字幕必须消失
            if voice_srt_body.strip() and Path(bed).is_file():
                voice_srt_body = tighten_srt_to_voiceover(
                    voice_srt_body,
                    bed,
                    tail_trim_seconds=tail_trim,
                )
            out["subtitle_tail_trim_sec"] = tail_trim
            out["subtitle_aligned_to_voice"] = True
            # Display script without punctuation for sidecar clarity
            if strip_punct:
                from engine.pack.text_sanitize import strip_all_punctuation

                out["script_display"] = strip_all_punctuation(script) if lang.startswith("zh") else script
        except Exception as e:  # noqa: BLE001 — render must continue with BGM-only
            out["error"] = str(e)
            out["narration_path"] = None

    if prefs.get("subtitle_lang") != "none":
        from engine.pack.languages import normalize_lang_code
        from engine.pack.locale import narration_script_for_lang, subtitle_cue_text
        from engine.pack.publish import build_subtitle_srt

        primary_lang = normalize_lang_code(sub_lang if sub_lang != "none" else "zh")
        base_srt = voice_srt_body
        if not base_srt.strip():
            dur = sum(float(c.duration_sec or 0) for c in plan.clips) or 6.0
            fallback_cap = subtitle_cue_text(primary_lang, brand=brand_name)
            if forbid_title_in_sub and text_contains_title(fallback_cap, on_screen_title):
                fallback_cap = subtitle_cue_text("zh", brand=brand_name)
            base_srt = build_subtitle_srt(
                fallback_cap, duration_sec=min(8.0, max(4.0, dur * 0.35))
            )

        # Align primary cue text to subtitle_lang (may differ from VO language)
        n_cues = _cue_count(base_srt)
        # burn_mono HARD: captions = VO wording + timing. Never replace with brand
        # short-cycle templates when subtitle_lang ≠ voice_lang (customer incident
        # 2026-08-02: voice=zh + subtitle=zh-TW → traditional brand line ≠ spoken).
        if burn_mode == "burn_mono" and voice_srt_body.strip():
            if primary_lang != voice_lang and voice_lang != "none":
                out["subtitle_forced_to_voice"] = {
                    "requested": primary_lang,
                    "effective": voice_lang,
                    "reason": "burn_mono_requires_voice_alignment",
                }
                primary_lang = voice_lang if voice_lang != "none" else primary_lang
            primary_srt = voice_srt_body
        elif voice_lang == primary_lang and voice_srt_body.strip():
            primary_srt = voice_srt_body
        else:
            use_short_cycle = False
            if primary_lang == "zh":
                if voice_lang.startswith("zh") and out.get("script"):
                    primary_script = str(out["script"])
                elif not str(voice_lang).startswith("zh"):
                    # Foreign VO timing + zh captions: short curated lines (not duration-filled bed)
                    primary_script = narration_script_for_lang(
                        "zh", brand=brand_name, title_zh=on_screen_title, speak_title=False
                    )
                    use_short_cycle = True
                else:
                    primary_script = narration_script_zh(
                        on_screen_title,
                        brand=brand_name,
                        theme=str(plan.theme or "default"),
                        clip_hints=clip_description_hints(plan, limit=4),
                        target_duration_sec=plan_dur,
                        cps=cps,
                        speak_title=False,
                        exclude_openers=list(out.get("exclude_openers") or []),
                        industry_lines=pack_lines,
                    )
                    primary_script = scrub_title_from_spoken(primary_script, on_screen_title)
            elif primary_lang == "zh-TW":
                # Prefer real VO script timing when VO is also Chinese family
                if voice_lang.startswith("zh") and voice_srt_body.strip() and out.get("script"):
                    primary_script = str(out["script"])
                    use_short_cycle = False
                else:
                    primary_script = narration_script_for_lang(
                        "zh-TW", brand=brand_name, title_zh=on_screen_title, speak_title=False
                    )
                    use_short_cycle = not str(voice_lang).startswith("zh")
            else:
                primary_script = narration_script_for_lang(
                    primary_lang,
                    brand=brand_name,
                    title_zh=on_screen_title,
                    speak_title=False,
                )
                use_short_cycle = True
            if use_short_cycle:
                import re as _re

                sentences = [
                    p.strip()
                    for p in _re.split(r"(?<=[。！？.!?…])\s*", primary_script)
                    if p.strip()
                ] or [primary_script]
                n = n_cues or 1
                if len(sentences) < n:
                    parts = [sentences[i % len(sentences)] for i in range(n)]
                else:
                    parts = sentences[:n]
                primary_srt = srt_replace_cue_texts(base_srt, parts)
            else:
                primary_srt = srt_replace_cue_texts(
                    base_srt, _split_script_to_n(primary_script, n_cues or 1)
                )

        final_srt = primary_srt
        if burn_mode == "burn_dual":
            sec_code = normalize_lang_code(dual_lang)
            if sec_code == primary_lang:
                sec_code = "en" if primary_lang.startswith("zh") else "zh"
            # Dual second line: short curated template (not duration-filled VO bed)
            sec_script = narration_script_for_lang(
                sec_code,
                brand=brand_name,
                title_zh=on_screen_title,
                speak_title=False,
            )
            from engine.pack.text_sanitize import subtitle_display_text

            n_sec = _cue_count(primary_srt) or 1
            sec_parts = _split_script_to_n(sec_script, n_sec)
            # Cycle short templates instead of repeating only the last sentence
            if sec_code.startswith("zh"):
                # Prefer sentence list cycling for readability
                import re as _re

                sentences = [
                    p.strip()
                    for p in _re.split(r"(?<=[。！？.!?…])\s*", sec_script)
                    if p.strip()
                ] or [sec_script]
                if len(sentences) < n_sec:
                    sec_parts = [sentences[i % len(sentences)] for i in range(n_sec)]
                else:
                    sec_parts = sentences[:n_sec]
            sec_parts = [subtitle_display_text(t, keep_newlines=False) for t in sec_parts]
            secondary_srt = srt_replace_cue_texts(primary_srt, sec_parts)
            final_srt = build_dual_subtitle_srt(primary_srt, secondary_srt)
            dual_path = work_dir / "subtitle.dual.srt"
            dual_path.write_text(final_srt, encoding="utf-8")
            srt_path = dual_path
            out["dual_secondary_lang"] = sec_code
            out["subtitle_primary_lang"] = primary_lang
        else:
            srt_path = work_dir / f"subtitle.{primary_lang.replace('-', '_')}.srt"

        srt_path.write_text(final_srt, encoding="utf-8")
        out["srt_path"] = str(srt_path)
        out["subtitle_burn"] = burn_mode

    # HARD: inject emoji into subtitle SRT (display); never into VO
    from engine.pack.emoji_stickers import inject_emojis_into_srt, text_has_emoji

    srt_file = out.get("srt_path")
    if srt_file and Path(srt_file).is_file() and out.get("emoji_cues"):
        raw_srt = Path(srt_file).read_text(encoding="utf-8")
        injected = inject_emojis_into_srt(raw_srt, list(out.get("emoji_cues") or []))
        Path(srt_file).write_text(injected, encoding="utf-8")
        out["subtitle_has_emoji"] = text_has_emoji(injected)
        out["emoji_in_subtitle"] = True
        out["title_stickers_disabled"] = True

    # Homology: final on-disk SRT must clamp to the same VO bed READY_GATE will hear.
    # Emoji inject / dual rebuild can leave cue ends past audible speech; re-tighten once.
    narr_for_align = out.get("narration_path")
    srt_for_align = out.get("srt_path")
    if (
        narr_for_align
        and srt_for_align
        and Path(str(narr_for_align)).is_file()
        and Path(str(srt_for_align)).is_file()
        and prefs.get("subtitle_lang") != "none"
    ):
        try:
            trim_final = float(out.get("subtitle_tail_trim_sec") or 0.12)
            if trim_final < 0.08:
                trim_final = 0.12
            body = Path(str(srt_for_align)).read_text(encoding="utf-8")
            tightened = tighten_srt_to_voiceover(
                body,
                Path(str(narr_for_align)),
                tail_trim_seconds=trim_final,
            )
            Path(str(srt_for_align)).write_text(tightened, encoding="utf-8")
            out["subtitle_aligned_to_voice"] = True
            out["subtitle_tail_trim_sec"] = trim_final
            out["subtitle_align_pass"] = "final_bed"
        except Exception as exc:  # noqa: BLE001
            out["subtitle_align_error"] = str(exc)[:240]

    return out


def burn_subtitles_inplace(
    video: Path,
    srt: Path,
    *,
    work_dir: Path | None = None,
    font_size: int = 64,
    bottom_padding_px: int = 420,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 3,
    align: str = "center",
    subtitle_layout: str = "horizontal",
    subtitle_vertical_side: str = "left",
    narration_text_effect: str = "none",
    font_family: str | None = None,
    center_x_pct: float | None = None,
    center_y_pct: float | None = None,
) -> dict[str, Any]:
    """Burn SRT into video, replacing file on success."""
    video = Path(video)
    srt = Path(srt)
    if not video.is_file() or not srt.is_file():
        return {"ok": False, "error": "missing video or srt"}
    tmp = (work_dir or video.parent) / f"{video.stem}.burned_tmp.mp4"
    result = burn_srt_into_video(
        video,
        srt,
        tmp,
        font_size=font_size,
        bottom_padding_px=bottom_padding_px,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        align=align,
        subtitle_layout=subtitle_layout,
        subtitle_vertical_side=subtitle_vertical_side,
        narration_text_effect=narration_text_effect,
        font_family=font_family,
        center_x_pct=center_x_pct,
        center_y_pct=center_y_pct,
    )
    if not result.get("ok"):
        return result
    try:
        video.unlink(missing_ok=True)
        tmp.replace(video)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "out": str(video), "method": result.get("method")}
