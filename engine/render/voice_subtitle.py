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
    """Prefer Edge 晓晓 → macOS say → mock."""
    raw = str(getattr(settings, "tts_provider", "edge") or "edge").lower().strip()
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
    if explicit and (lang.startswith("zh") or lang == "en"):
        # Settings voice is usually CN/EN; don't force it onto JA/KO/…
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
) -> dict[str, Any]:
    """
    Synthesize voiceover WAV + SRT for this plan.
    Returns dict with keys: narration_path, srt_path, script, provider, skipped, error?
    """
    prefs = resolve_expression_prefs(profile)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    lock = video_lock or {}
    voice_lock = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    narr_lock = lock.get("narration") if isinstance(lock.get("narration"), dict) else {}
    sub_lock = lock.get("subtitle") if isinstance(lock.get("subtitle"), dict) else {}
    strip_punct = bool(lock.get("strip_all_punctuation", narr_lock.get("strip_punctuation", True)))

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
        "subtitle_bottom_padding_px": int(sub_lock.get("bottom_padding_px") or 400),
    }

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

    script = narration_script_zh(
        on_screen_title,
        brand=brand_name,
        theme=str(plan.theme or "default"),
        clip_hints=clip_description_hints(plan, limit=4),
        target_duration_sec=plan_dur,
        cps=cps,
        speak_title=speak_title,
    )
    if not speak_title:
        script = scrub_title_from_spoken(script, on_screen_title)
    out["script"] = script
    out["target_duration_sec"] = plan_dur

    # Locked voice overrides settings
    if voice_lock.get("provider"):
        provider = str(voice_lock["provider"]).lower()
        if provider in ("edge", "xiaoxiao"):
            try:
                import edge_tts  # noqa: F401

                provider = "edge"
            except ImportError:
                provider = resolve_tts_provider(settings)
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
            if lang == "zh" and voice_lock.get("voice"):
                voice = str(voice_lock["voice"])
            elif lang.startswith("zh"):
                voice = str(voice_lock.get("voice") or "") or edge_voice_for_lang(lang) or resolve_tts_voice(
                    settings, lang=lang, provider=provider
                )
            else:
                # Never apply zh-locked 晓晓 to foreign packs
                voice = edge_voice_for_lang(lang) or resolve_tts_voice(settings, lang=lang, provider=provider)
            out["voice"] = voice
            rate = str(voice_lock.get("rate") or getattr(settings, "tts_rate", "") or "") or None
            pitch = str(voice_lock.get("pitch") or getattr(settings, "tts_pitch", "") or "") or None
            # Keep locked rate/pitch for zh; neutral defaults for foreign
            if lang not in ("zh", "zh-TW"):
                rate = "+0%"
                pitch = "+0Hz"
            # Locked Edge: never silently fall back to macOS say (zh = 晓晓; foreign = catalog Edge)
            lock_requires_edge = str(voice_lock.get("provider") or "").lower() in (
                "edge",
                "xiaoxiao",
            )
            # Sentence gap: audible breath for 断句; keep SRT in lockstep with audio bed
            gap = float(sub_lock.get("inter_sentence_gap_seconds") or 0.15)
            if gap < 0.1:
                gap = 0.15  # minimum breath so 晓晓不连读
            narr = synthesize_script(
                script,
                work_dir / "narration",
                lang=lang,
                provider=provider,  # type: ignore[arg-type]
                voice=voice,
                rate=rate,
                pitch=pitch,
                strip_punctuation=strip_punct and lang.startswith("zh"),
                allow_fallback=not lock_requires_edge,
                inter_sentence_gap_sec=gap,
            )
            if lock_requires_edge and narr.provider != "edge":
                raise RuntimeError(
                    f"VIDEO_LOCK requires Edge TTS, got provider={narr.provider}"
                )
            bed_gap = float((narr.extras or {}).get("inter_sentence_gap_sec") or gap)
            bed = narration_bed_from_result(narr, narr_path, gap_sec=bed_gap)
            out["narration_path"] = str(bed)
            out["provider"] = narr.provider
            out["requested_provider"] = provider
            out["lock_requires_edge"] = lock_requires_edge
            out["tts_mode"] = (narr.extras or {}).get("mode")
            out["inter_sentence_gap_sec"] = bed_gap
            voice_srt_body = srt_from_narration_segments(
                narr.segments,
                bottom_dual_line=False,
                tail_trim_seconds=float(sub_lock.get("tail_trim_seconds") or 0.04),
                inter_sentence_gap_seconds=bed_gap,
                forbid_title=on_screen_title if forbid_title_in_sub else None,
            )
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
        if voice_lang == primary_lang and voice_srt_body.strip():
            primary_srt = voice_srt_body
        else:
            if primary_lang == "zh":
                if voice_lang.startswith("zh") and out.get("script"):
                    primary_script = str(out["script"])
                else:
                    primary_script = narration_script_zh(
                        on_screen_title,
                        brand=brand_name,
                        theme=str(plan.theme or "default"),
                        clip_hints=clip_description_hints(plan, limit=4),
                        target_duration_sec=plan_dur,
                        cps=cps,
                        speak_title=False,
                    )
                    primary_script = scrub_title_from_spoken(primary_script, on_screen_title)
            elif primary_lang == "zh-TW":
                primary_script = narration_script_for_lang(
                    "zh-TW", brand=brand_name, title_zh=on_screen_title, speak_title=False
                )
            else:
                primary_script = narration_script_for_lang(
                    primary_lang,
                    brand=brand_name,
                    title_zh=on_screen_title,
                    speak_title=False,
                )
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

    return out


def burn_subtitles_inplace(
    video: Path,
    srt: Path,
    *,
    work_dir: Path | None = None,
    font_size: int = 64,
    bottom_padding_px: int = 400,
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
    )
    if not result.get("ok"):
        return result
    try:
        video.unlink(missing_ok=True)
        tmp.replace(video)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "out": str(video), "method": result.get("method")}
