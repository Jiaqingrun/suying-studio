"""G3 publish_pack — export ready output into a takeaway publish folder."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLATFORMS = ("douyin", "channels", "xhs", "wechat_mp")


def _safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os_link = getattr(__import__("os"), "link")
        os_link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _load_sidecar(sidecar_path: Path | None) -> dict[str, Any]:
    if not sidecar_path or not sidecar_path.is_file():
        return {}
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _split_title_lines(title: str) -> list[str]:
    parts = [p.strip() for p in (title or "").replace("|", "｜").split("｜") if p.strip()]
    if not parts:
        parts = [p.strip() for p in (title or "").split("\n") if p.strip()]
    if not parts and title:
        parts = [title.strip()]
    return parts[:2] or ["速影成片"]


def build_platform_copy(
    *,
    title: str,
    brand: str,
    theme: str,
    hashtags: list[str],
    music_credit: str,
    description: str = "",
) -> dict[str, Any]:
    """Same selling point, different tone per platform."""
    lines = _split_title_lines(title)
    hook = lines[0]
    sub = lines[1] if len(lines) > 1 else f"{brand}本地实拍"
    tags = hashtags[:6] if hashtags else [f"#{theme}", "#短视频", f"#{brand}"]
    tag_line = " ".join(tags)

    copies = {
        "douyin": {
            "platform": "douyin",
            "title": hook[:30],
            "body": f"{hook}\n{sub}\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags,
        },
        "channels": {
            "platform": "channels",
            "title": f"{hook} · {brand}"[:64],
            "body": f"{sub}。{brand}真实现场记录，欢迎咨询。\n{tag_line}",
            "hashtags": tags,
        },
        "xhs": {
            "platform": "xhs",
            "title": f"「{hook}」{sub}"[:40],
            "body": f"{hook}｜{sub}\n实测本地发货体验分享～\n{tag_line}\n🎵 {music_credit}",
            "hashtags": tags,
        },
        "wechat_mp": {
            "platform": "wechat_mp",
            "title": f"{brand}｜{hook}",
            "body": (
                f"【导语】{hook}。\n\n"
                f"{sub}。本稿配套短视频为本地实拍混剪，适合公众号推送与朋友圈二次传播。\n\n"
                f"{description or ''}\n\n"
                f"（音乐：{music_credit}）"
            ).strip(),
            "hashtags": tags[:3],
        },
    }
    return {
        "version": "1.0",
        "locale": "zh-CN",
        "brand": brand,
        "theme": theme,
        "source_title": title,
        "platforms": copies,
    }


def build_subtitle_srt(title: str, *, duration_sec: float = 6.0) -> str:
    """Simple dual-line title SRT for the opening window."""
    lines = _split_title_lines(title)
    # Ensure each cue line is short (dual_chip style ≤6 chars when possible)
    cues: list[str] = []
    t0 = 0.0
    span = max(2.0, min(4.0, duration_sec / max(1, len(lines))))
    for i, line in enumerate(lines, start=1):
        start = t0 + (i - 1) * span
        end = start + span
        cues.append(
            f"{i}\n{_ts(start)} --> {_ts(end)}\n{line}\n"
        )
    return "\n".join(cues).strip() + "\n"


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def scan_banned_terms(texts: list[str], banned: list[str]) -> list[str]:
    hits: list[str] = []
    blob = "\n".join(texts)
    for term in banned:
        t = (term or "").strip()
        if t and t in blob and t not in hits:
            hits.append(t)
    return hits


def collect_banned_terms(
    *,
    profile: dict[str, Any] | None = None,
    pack_data: dict[str, Any] | None = None,
) -> list[str]:
    terms: list[str] = []
    if profile:
        terms.extend((profile.get("compliance") or {}).get("banned_terms") or [])
    if pack_data:
        terms.extend((pack_data.get("compliance") or {}).get("blocked_terms") or [])
        terms.extend((pack_data.get("compliance") or {}).get("banned_terms") or [])
    # de-dupe preserve order
    out: list[str] = []
    for t in terms:
        if t and t not in out:
            out.append(str(t))
    return out


def export_publish_pack(
    *,
    output_path: Path,
    sidecar_path: Path | None = None,
    pack_dir: Path | None = None,
    profile: dict[str, Any] | None = None,
    pack_data: dict[str, Any] | None = None,
    brand: str | None = None,
    foreign_locales: list[str] | None = None,
    include_narration: bool = True,
    tts_provider: str = "mock",
    expression_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create `<stem>.publish_pack/` beside the ready mp4.
    Returns manifest dict + paths.

    P2: ``profile.expression`` / ``expression_overrides`` control
    ``voice_lang`` / ``subtitle_lang`` / ``subtitle_burn``.
    G4 default still emits English when prefs ask for it (or legacy foreign_locales).
    """
    from engine.pack.expression import (
        build_dual_subtitle_srt,
        foreign_locales_for_prefs,
        resolve_expression_prefs,
    )

    output_path = Path(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(f"成片不存在: {output_path}")

    prefs = resolve_expression_prefs(profile, overrides=expression_overrides)
    if foreign_locales is not None:
        locales = list(foreign_locales)
    else:
        locales = foreign_locales_for_prefs(prefs)

    side = _load_sidecar(Path(sidecar_path) if sidecar_path else output_path.with_suffix(".json"))
    title = str(side.get("title") or output_path.stem)
    theme = str(side.get("theme") or "default")
    copy0 = side.get("copywriting") or {}
    hashtags = list(copy0.get("hashtags") or [])
    music = str(copy0.get("music_credit") or "Music: generated")
    brand_name = brand or (pack_data or {}).get("company_info", {}).get("display_name_preferred") or "品牌"
    if profile:
        brand_name = (profile.get("brand") or {}).get("display_name") or brand_name

    dest = pack_dir or output_path.with_suffix("").parent / f"{output_path.stem}.publish_pack"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    video_dst = dest / "video.mp4"
    _safe_copy(output_path, video_dst)

    covers = [Path(c) for c in (side.get("covers") or []) if c]
    cover_dst = None
    for i, c in enumerate(covers):
        if c.is_file():
            name = "cover.jpg" if i == 0 else f"cover_{i+1}{c.suffix or '.jpg'}"
            _safe_copy(c, dest / name)
            if cover_dst is None:
                cover_dst = dest / name

    # also copy sidecar snapshot
    if side:
        (dest / "source.sidecar.json").write_text(
            json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    copy_doc = build_platform_copy(
        title=title,
        brand=brand_name,
        theme=theme,
        hashtags=hashtags,
        music_credit=music,
        description=str(copy0.get("description") or ""),
    )
    (dest / "copy.zh.json").write_text(
        json.dumps(copy_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # duration hint from meta if present
    dur = float((side.get("meta") or {}).get("duration_sec") or 6.0)
    srt = build_subtitle_srt(title, duration_sec=min(8.0, max(4.0, dur * 0.25)))
    (dest / "subtitle.zh.srt").write_text(srt, encoding="utf-8")

    files: dict[str, Any] = {
        "video": "video.mp4",
        "cover": cover_dst.name if cover_dst else None,
        "copy": "copy.zh.json",
        "subtitle": "subtitle.zh.srt",
        "compliance": "compliance.json",
        "sidecar": "source.sidecar.json" if side else None,
    }

    if include_narration and prefs["voice_lang"] in ("zh", "zh-TW"):
        try:
            from engine.pack.narration_script import narration_script_zh
            from engine.pack.tts import narration_bed_from_result, synthesize_script
            from engine.render.voice_subtitle import resolve_tts_provider
            from engine.config.settings import load_settings as _ls
            from engine.pack.languages import edge_voice_for_lang

            script_zh = narration_script_zh(
                title,
                brand=brand_name,
                theme=theme,
                description=str(copy0.get("description") or ""),
                speak_title=False,
            )
            if prefs["voice_lang"] == "zh-TW":
                # Soft traditional cue — keep structure, TTS uses TW voice
                script_zh = script_zh.replace("这里是", "這裡是").replace("实拍", "實拍").replace("发货", "出貨")
            provider = resolve_tts_provider(_ls())
            vlang = prefs["voice_lang"]
            narr_zh = synthesize_script(
                script_zh,
                dest / f"narration.{vlang}",
                lang=vlang,
                provider=provider,  # type: ignore[arg-type]
                voice=edge_voice_for_lang(vlang),
            )
            bed_zh = narration_bed_from_result(narr_zh, dest / f"voiceover.{vlang}.wav")
            files["voiceover"] = bed_zh.name
            files["narration"] = f"narration.{vlang}"
            from engine.pack.narration_script import srt_from_narration_segments

            timed = srt_from_narration_segments(narr_zh.segments, video_duration_sec=dur or None)
            if timed.strip():
                srt_name = f"subtitle.{vlang}.srt"
                (dest / srt_name).write_text(timed, encoding="utf-8")
                if vlang == "zh":
                    (dest / "subtitle.zh.srt").write_text(timed, encoding="utf-8")
                else:
                    files["subtitle_zh_TW"] = srt_name
        except Exception:  # noqa: BLE001
            pass

    variants: dict[str, Any] = {}
    foreign_srts: dict[str, str] = {}

    # Build variants for every requested foreign locale (+ zh-TW)
    needed_locales = list(locales)
    for key in ("voice_lang", "subtitle_lang"):
        lang = prefs.get(key) or ""
        if lang and lang not in ("zh", "none") and lang not in needed_locales:
            needed_locales.append(lang)

    for loc in needed_locales:
        from engine.pack.locale import (
            build_platform_copy_for_lang,
            glossary_from_pack,
            narration_script_for_lang,
            subtitle_cue_text,
        )
        from engine.pack.languages import edge_voice_for_lang
        from engine.pack.tts import narration_bed_from_result, synthesize_script

        gloss = glossary_from_pack(pack_data, lang=loc if loc == "en" else "en")
        copy_loc = build_platform_copy_for_lang(
            loc,
            title_zh=title,
            brand=brand_name,
            theme=theme,
            hashtags=hashtags,
            music_credit=music,
            glossary=gloss,
            description=str(copy0.get("description") or ""),
        )
        copy_name = f"copy.{loc}.json"
        (dest / copy_name).write_text(json.dumps(copy_loc, ensure_ascii=False, indent=2), encoding="utf-8")
        cue = str(copy_loc.get("title_localized") or subtitle_cue_text(loc, brand=brand_name))
        srt_loc = build_subtitle_srt(cue.replace(" — ", "｜"), duration_sec=min(8.0, max(4.0, dur * 0.25)))
        srt_name = f"subtitle.{loc}.srt"
        (dest / srt_name).write_text(srt_loc, encoding="utf-8")
        foreign_srts[loc] = srt_loc
        files[f"copy_{loc.replace('-', '_')}"] = copy_name
        files[f"subtitle_{loc.replace('-', '_')}"] = srt_name
        variant_meta: dict[str, Any] = {
            "locale": loc,
            "copy": copy_name,
            "subtitle": srt_name,
        }
        # G4: foreign pack variants always include VO when narration is on
        # (not only when primary voice_lang equals that locale).
        if include_narration and loc not in ("zh", "zh-TW"):
            try:
                script_loc = narration_script_for_lang(
                    loc, brand=brand_name, title_zh=title, speak_title=False
                )
                narr_dir = dest / f"narration.{loc}"
                from engine.render.voice_subtitle import resolve_tts_provider
                from engine.config.settings import load_settings as _ls

                provider = resolve_tts_provider(_ls())
                if tts_provider in ("mock", "say"):
                    provider = tts_provider  # type: ignore[assignment]
                narr = synthesize_script(
                    script_loc,
                    narr_dir,
                    lang=loc,
                    provider=provider,  # type: ignore[arg-type]
                    voice=edge_voice_for_lang(loc),
                )
                bed = narration_bed_from_result(narr, dest / f"voiceover.{loc}.wav")
                variant_meta["narration_dir"] = f"narration.{loc}"
                variant_meta["voiceover"] = f"voiceover.{loc}.wav"
                variant_meta["script"] = script_loc
                variant_meta["duration_sec"] = narr.total_duration_sec
                files[f"voiceover_{loc.replace('-', '_')}"] = bed.name
                files[f"narration_{loc.replace('-', '_')}"] = f"narration.{loc}"
                from engine.pack.narration_script import srt_from_narration_segments

                timed = srt_from_narration_segments(narr.segments, video_duration_sec=dur or None)
                if timed.strip():
                    (dest / srt_name).write_text(timed, encoding="utf-8")
                    foreign_srts[loc] = timed
            except Exception:  # noqa: BLE001
                pass
        variants[loc] = variant_meta

    # Primary subtitle pointer by subtitle_lang
    sub_lang = prefs["subtitle_lang"]
    if sub_lang == "none":
        files["subtitle_primary"] = None
    elif sub_lang == "zh":
        files["subtitle_primary"] = files.get("subtitle")
    else:
        key = f"subtitle_{sub_lang.replace('-', '_')}"
        files["subtitle_primary"] = files.get(key) or files.get("subtitle")

    if prefs["subtitle_burn"] == "burn_dual":
        # Dual = subtitle_lang (primary) + dual_secondary_lang
        primary_code = sub_lang if sub_lang != "none" else "zh"
        secondary_code = prefs.get("dual_secondary_lang") or (
            "zh" if primary_code not in ("zh", "zh-TW") else "en"
        )
        srt_primary = (
            srt
            if primary_code in ("zh", "zh-TW")
            else foreign_srts.get(primary_code, srt)
        )
        srt_secondary = (
            srt
            if secondary_code in ("zh", "zh-TW")
            else foreign_srts.get(secondary_code, "")
        )
        if primary_code not in ("zh", "zh-TW") and foreign_srts.get(primary_code):
            srt_primary = foreign_srts[primary_code]
        if secondary_code in ("zh", "zh-TW"):
            srt_secondary = srt
        elif foreign_srts.get(secondary_code):
            srt_secondary = foreign_srts[secondary_code]
        if srt_primary and srt_secondary:
            dual = build_dual_subtitle_srt(srt_primary, srt_secondary)
            (dest / "subtitle.dual.srt").write_text(dual, encoding="utf-8")
            files["subtitle_dual"] = "subtitle.dual.srt"
            files["subtitle_primary"] = "subtitle.dual.srt"
            files["dual_pair"] = f"{primary_code}+{secondary_code}"

    # Render: external = ship SRT only; burn_* = ffmpeg soft-burn into pack video
    burn_source = files.get("subtitle_primary")
    if prefs["subtitle_burn"] == "burn_mono":
        if sub_lang in ("zh", "none"):
            burn_source = files.get("subtitle")
        else:
            burn_source = files.get(f"subtitle_{sub_lang.replace('-', '_')}") or files.get("subtitle")
    elif prefs["subtitle_burn"] == "burn_dual":
        burn_source = files.get("subtitle_dual") or burn_source
    burn_intent: dict[str, Any] = {
        "mode": prefs["subtitle_burn"],
        "source_srt": burn_source,
        "burn_in_video": prefs["subtitle_burn"] in ("burn_mono", "burn_dual"),
        "note": (
            "外挂字幕，不烧录"
            if prefs["subtitle_burn"] == "external"
            else "已请求烧录"
        ),
        "burned": False,
        "burn_error": None,
    }
    if burn_intent["burn_in_video"] and burn_source:
        from engine.render.subtitles_burn import burn_srt_into_video

        srt_path = dest / str(burn_source)
        burned_name = "video.burned.mp4"
        burned_path = dest / burned_name
        result = burn_srt_into_video(dest / "video.mp4", srt_path, burned_path)
        if result.get("ok"):
            burn_intent["burned"] = True
            burn_intent["method"] = result.get("method")
            burn_intent["note"] = "已烧录到 video.burned.mp4（原片 video.mp4 保留）"
            files["video_burned"] = burned_name
        else:
            burn_intent["burn_error"] = str(result.get("error") or "burn failed")
            burn_intent["method"] = result.get("method")
            burn_intent["note"] = f"烧录失败，仍交付外挂 SRT：{burn_intent['burn_error'][:200]}"
    files["subtitle_burn_intent"] = burn_intent

    banned = collect_banned_terms(profile=profile, pack_data=pack_data)
    scan_texts = [title, json.dumps(copy_doc, ensure_ascii=False), srt]
    for loc in needed_locales:
        cp = dest / f"copy.{loc}.json"
        if cp.is_file():
            scan_texts.append(cp.read_text(encoding="utf-8"))
    hits = scan_banned_terms(scan_texts, banned)
    compliance = {
        "passed": len(hits) == 0,
        "banned_terms_checked": banned,
        "hits": hits,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    (dest / "compliance.json").write_text(
        json.dumps(compliance, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "version": "1.3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_output": str(output_path),
        "pack_dir": str(dest),
        "files": files,
        "title": title,
        "theme": theme,
        "brand": brand_name,
        "compliance_passed": compliance["passed"],
        "platforms": list(PLATFORMS),
        "locales": ["zh", *needed_locales],
        "variants": variants,
        "expression": prefs,
        "subtitle_burn_intent": burn_intent,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
