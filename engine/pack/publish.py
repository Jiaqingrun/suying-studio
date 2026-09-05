"""G3 publish_pack — export ready output into a takeaway publish folder."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.reach.business_scope import VIDEO_PLATFORMS


PLATFORMS = tuple(sorted(VIDEO_PLATFORMS))
OPTIONAL_ARTICLE_PLATFORMS = ("wechat_mp",)
PACK_VERSION = "video-publish-assets-v1"
KUAISHOU_HASHTAG_LIMIT = 4
AI_GENERATED_DISCLOSURE = "本作品由AI生成"
# 2026-08-13 用户明确：成片物料禁止再出现「本作品由AI生成」；下列别名一并剔除。
_AI_DISCLOSURE_ALIASES = (
    "本作品由AI生成",
    "本视频由AI生成",
    "本内容由AI生成",
    "本片由AI生成",
)
_HASHTAG_RE = re.compile(r"#[^\s#，。！？；：、,.!?;:()\[\]{}<>《》“”\"'‘’|｜]+")


def strip_ai_generated_disclosure(body: str) -> str:
    """Remove AI disclosure lines from publish copy (user lock 2026-08-13)."""
    text = str(body or "").replace("\r\n", "\n")
    lines = [line for line in text.split("\n") if line.strip() not in _AI_DISCLOSURE_ALIASES]
    return "\n".join(lines).strip()


def ensure_ai_generated_disclosure(body: str) -> str:
    """Compatibility shim: strip AI disclosure instead of appending (L14 revoked)."""
    return strip_ai_generated_disclosure(body)


def has_ai_generated_disclosure(body: str) -> bool:
    """True if any AI disclosure alias remains (should be False after strip)."""
    lines = [
        line.strip()
        for line in str(body or "").replace("\r\n", "\n").split("\n")
        if line.strip()
    ]
    return any(line in _AI_DISCLOSURE_ALIASES for line in lines)


def limit_platform_hashtags(
    platform: str,
    body: str,
    hashtags: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Apply platform topic limits to both body text and hashtag metadata."""
    raw_tags = [str(tag).strip() for tag in (hashtags or []) if str(tag).strip()]
    if (platform or "").strip().lower() != "kuaishou":
        return body, raw_tags

    kept: list[str] = []

    def normalize(tag: str) -> str:
        value = tag.strip()
        return value if value.startswith("#") else f"#{value}"

    def replace(match: re.Match[str]) -> str:
        tag = normalize(match.group(0))
        if tag in kept or len(kept) >= KUAISHOU_HASHTAG_LIMIT:
            return ""
        kept.append(tag)
        return match.group(0)

    limited_body = _HASHTAG_RE.sub(replace, body or "")
    for raw in raw_tags:
        tag = normalize(raw)
        if tag not in kept and len(kept) < KUAISHOU_HASHTAG_LIMIT:
            kept.append(tag)

    # Removing overflow inline topics can leave repeated spaces before punctuation.
    limited_body = re.sub(r"[ \t]{2,}", " ", limited_body)
    limited_body = re.sub(r"[ \t]+\n", "\n", limited_body).strip()
    return limited_body, kept


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


def _sidecar_meta(side: dict[str, Any]) -> dict[str, Any]:
    meta = side.get("meta")
    return meta if isinstance(meta, dict) else {}


def resolve_production_caption_assets(
    output_path: Path,
    side: dict[str, Any],
) -> dict[str, Any]:
    """Locate worker-produced SRT / voiceover already aligned to the ready cut.

    HARD (NARRATION_SUBTITLE_LOCK H9): publish_pack must reuse these when present.
    Regenerating a second narration bed and re-burning causes stacked captions.
    """
    output_path = Path(output_path)
    meta = _sidecar_meta(side)
    already_burned = bool(meta.get("subtitle_burned"))
    srt_candidates: list[Path] = []
    for key in ("subtitle_path", "srt_path"):
        raw = meta.get(key)
        if raw:
            srt_candidates.append(Path(str(raw)))
    stem = output_path.stem
    parent = output_path.parent
    for name in (
        f"{stem}.zh.srt",
        f"{stem}.dual.srt",
        f"{stem}.zh_TW.srt",
        f"{stem}.sub.srt",
    ):
        srt_candidates.append(parent / name)
    srt_path: Path | None = next((p for p in srt_candidates if p.is_file()), None)

    voice_candidates: list[Path] = []
    for key in ("narration_path", "voiceover_path"):
        raw = meta.get(key)
        if raw:
            voice_candidates.append(Path(str(raw)))
    voice_candidates.append(parent / f"{stem}.voice.wav")
    voice_path: Path | None = next((p for p in voice_candidates if p.is_file()), None)

    return {
        "already_burned": already_burned,
        "srt_path": srt_path,
        "voice_path": voice_path,
        "subtitle_burn_method": meta.get("subtitle_burn_method"),
    }


def validate_publish_video(path: str | Path) -> dict[str, Any]:
    """Validate the frozen dual-orientation upload contract."""
    video_path = Path(path)
    if not video_path.is_file():
        raise ValueError(f"成片不存在: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise ValueError(f"发布视频必须为 MP4: {video_path.name}")
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"ffprobe 无法读取发布视频: {(proc.stderr or '').strip()[:240]}")
    try:
        probe = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe 返回了无效 JSON") from exc
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    failures: list[str] = []
    format_name = str((probe.get("format") or {}).get("format_name") or "")
    if "mp4" not in format_name:
        failures.append(f"容器={format_name or 'unknown'}（要求 MP4）")
    if not video:
        failures.append("缺少视频流")
    else:
        if str(video.get("codec_name") or "") != "h264":
            failures.append(f"视频编码={video.get('codec_name') or 'unknown'}（要求 H.264）")
        # yuvj420p = full-range 4:2:0 (common from phone JPEGy sources); treat as
        # upload-compatible with limited-range yuv420p. Reject true non-420 formats.
        pix = str(video.get("pix_fmt") or "")
        if pix not in {"yuv420p", "yuvj420p"}:
            failures.append(f"像素格式={pix or 'unknown'}（要求 yuv420p）")
        dimensions = (int(video.get("width") or 0), int(video.get("height") or 0))
        if dimensions not in {(1080, 1920), (1920, 1080)}:
            failures.append(
                f"画布={dimensions[0]}x{dimensions[1]}"
                "（要求竖屏 1080x1920 或横屏 1920x1080）"
            )
    if not audio:
        failures.append("缺少音频流（要求 AAC）")
    elif str(audio.get("codec_name") or "") != "aac":
        failures.append(f"音频编码={audio.get('codec_name') or 'unknown'}（要求 AAC）")
    if failures:
        raise ValueError("视频兼容门禁失败：" + "；".join(failures))
    return {
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "pix_fmt": "yuv420p",
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "orientation": (
            "landscape"
            if int(video.get("width") or 0) > int(video.get("height") or 0)
            else "portrait"
        ),
    }


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
    safe_description = str(description or "").strip()
    safe_first = re.split(r"[。！？\n]", safe_description, maxsplit=1)[0].strip()
    sub = lines[1] if len(lines) > 1 else (safe_first or "当前现场实拍")
    tags = hashtags[:6] if hashtags else [f"#{theme}", "#短视频", f"#{brand}"]
    tag_line = " ".join(tags)
    kuaishou_body, kuaishou_tags = limit_platform_hashtags(
        "kuaishou",
        f"{hook}\n{sub}\n{tag_line}",
        tags,
    )

    copies = {
        "douyin": {
            "platform": "douyin",
            "title": hook[:30],
            "body": ensure_ai_generated_disclosure(
                f"{hook}\n{sub}\n{tag_line}\n🎵 {music_credit}"
            ),
            "hashtags": tags,
        },
        "channels": {
            "platform": "channels",
            "title": f"{hook} · {brand}"[:64],
            "body": ensure_ai_generated_disclosure(f"{hook}\n{sub}\n{tag_line}"),
            "hashtags": tags,
        },
        "xhs": {
            "platform": "xhs",
            "title": f"「{hook}」{sub}"[:40],
            "body": ensure_ai_generated_disclosure(
                f"{hook} ✨｜{sub}\n"
                f"这组按当前画面记一下 👀\n"
                f"细节都在镜头里 📌\n"
                f"{tag_line}\n"
                f"🎵 {music_credit}"
            ),
            "hashtags": tags,
        },
        "kuaishou": {
            "platform": "kuaishou",
            "title": hook[:30],
            "body": ensure_ai_generated_disclosure(kuaishou_body),
            "hashtags": kuaishou_tags,
        },
    }
    optional_articles = {
        "wechat_mp": {
            "platform": "wechat_mp",
            "title": f"{brand}｜{hook}",
            "body": (
                f"【导语】{hook}。\n\n"
                f"{sub}。本稿内容只描述配套短视频中的当前可见信息。\n\n"
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
        "optional_articles": optional_articles,
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
    video_contract = validate_publish_video(output_path)

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

    copy_doc = build_platform_copy(
        title=title,
        brand=brand_name,
        theme=theme,
        hashtags=hashtags,
        music_credit=music,
        description=str(copy0.get("description") or ""),
    )
    from engine.content.compliance import apply_claim_soft_rewrites, check_output_fields

    side_meta = _sidecar_meta(side)
    pack_policy = (
        pack_data.get("compliance")
        if pack_data and isinstance(pack_data.get("compliance"), dict)
        else None
    )
    evidence_ids = (
        {"semantic.strict_v1"}
        if isinstance(side.get("content_fingerprint"), dict)
        and side["content_fingerprint"].get("strict_semantic_only") is True
        and side["content_fingerprint"].get("evidence_level") == "semantic.v1"
        else set()
    )
    preflight_fields = {
        "title": title,
        "cover_text": title,
        "narration": side_meta.get("narration_script") or "",
        "description": copy0.get("description") or "",
        "hashtags": hashtags,
        "platforms": copy_doc.get("platforms") or {},
    }
    preflight = check_output_fields(
        preflight_fields,
        policy=pack_policy,
        evidence_ids=evidence_ids,
    )
    if not preflight["passed"]:
        rewritten, applied = apply_claim_soft_rewrites(
            preflight_fields,
            preflight,
            policy=pack_policy,
            allow_soft_deny=True,
        )
        if applied:
            preflight2 = check_output_fields(
                rewritten,
                policy=pack_policy,
                evidence_ids=evidence_ids,
            )
            if preflight2.get("passed"):
                preflight = preflight2
                preflight_fields = rewritten
                title = str(rewritten.get("title") or title)
                copy0 = dict(copy0)
                if rewritten.get("description") is not None:
                    copy0["description"] = rewritten.get("description")
                if isinstance(rewritten.get("hashtags"), list):
                    hashtags = list(rewritten.get("hashtags") or [])
                    copy0["hashtags"] = hashtags
                copy_doc = build_platform_copy(
                    title=title,
                    brand=brand_name,
                    theme=theme,
                    hashtags=hashtags,
                    music_credit=music,
                    description=str(copy0.get("description") or ""),
                )
                if isinstance(rewritten.get("platforms"), dict):
                    copy_doc["platforms"] = rewritten["platforms"]
            else:
                preflight = preflight2
        if not preflight["passed"]:
            raise ValueError(
                "发布物料合规门禁失败："
                + "；".join(
                    str(item.get("message") or item.get("rule_id"))
                    for item in preflight["issues"]
                    if item.get("level") == "error"
                )
            )

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

    (dest / "copy.zh.json").write_text(
        json.dumps(copy_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # duration hint from meta if present
    meta = _sidecar_meta(side)
    dur = float(meta.get("duration_sec") or 6.0)
    prod_caps = resolve_production_caption_assets(output_path, side)
    already_burned = bool(prod_caps.get("already_burned"))
    reused_production_srt = False

    # Prefer worker-aligned SRT (same cues already burned into ready mp4).
    if prod_caps.get("srt_path"):
        srt = Path(prod_caps["srt_path"]).read_text(encoding="utf-8")
        reused_production_srt = True
    else:
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
    if reused_production_srt:
        files["subtitle_source"] = "production"

    # Reuse production voiceover when present — do NOT synthesize a second bed.
    if prod_caps.get("voice_path"):
        vlang = prefs["voice_lang"] if prefs["voice_lang"] in ("zh", "zh-TW") else "zh"
        voice_dst = dest / f"voiceover.{vlang}.wav"
        _safe_copy(Path(prod_caps["voice_path"]), voice_dst)
        files["voiceover"] = voice_dst.name
        files["voiceover_source"] = "production"
        narr_script = str(meta.get("narration_script") or "").strip()
        if narr_script:
            narr_dir = dest / f"narration.{vlang}"
            narr_dir.mkdir(parents=True, exist_ok=True)
            (narr_dir / "script.txt").write_text(narr_script, encoding="utf-8")
            files["narration"] = f"narration.{vlang}"

    elif include_narration and prefs["voice_lang"] in ("zh", "zh-TW") and not already_burned:
        # Only synthesize pack-side VO when the ready cut has no burned captions yet.
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
                    srt = timed
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

    # Render: external = ship SRT only; burn_* = soft-burn into pack video.
    # HARD H9: if the ready cut already has burned captions, never burn again.
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
        "already_burned_in_source": already_burned,
        "reused_production_srt": reused_production_srt,
    }
    if burn_intent["burn_in_video"] and burn_source:
        burned_name = "video.burned.mp4"
        burned_path = dest / burned_name
        if already_burned:
            # Copy the already-captioned ready cut — second burn stacks glyphs.
            _safe_copy(dest / "video.mp4", burned_path)
            burn_intent["burned"] = True
            burn_intent["method"] = "reuse_render_burn"
            burn_intent["note"] = (
                "成片已烧录旁白字幕，禁止二次烧录（复用 video.mp4 → video.burned.mp4）"
            )
            burn_intent["reused_existing_burn"] = True
            files["video_burned"] = burned_name
        else:
            from engine.render.subtitles_burn import burn_srt_into_video

            srt_path = dest / str(burn_source)
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
    # Optional article drafts never participate in video-pack completeness.
    video_copy_for_scan = {
        platform: (copy_doc.get("platforms") or {}).get(platform) or {}
        for platform in PLATFORMS
    }
    # 末道：SRT 软洗（防声槽与文案软改不同步导致空合同失败）
    if srt:
        from engine.content.compliance import sanitize_script_claims

        srt_policy = pack_policy if isinstance(pack_policy, dict) else {}
        cleaned_srt = sanitize_script_claims(srt, policy=srt_policy)
        if cleaned_srt != srt:
            srt = cleaned_srt
            (dest / "subtitle.zh.srt").write_text(srt, encoding="utf-8")
            files["subtitle_soft_cleaned"] = True

    scan_texts = [title, json.dumps(video_copy_for_scan, ensure_ascii=False), srt]
    for loc in needed_locales:
        cp = dest / f"copy.{loc}.json"
        if cp.is_file():
            scan_texts.append(cp.read_text(encoding="utf-8"))
    hits = scan_banned_terms(scan_texts, banned)
    platform_compliance: dict[str, Any] = {}
    platform_asset_status: dict[str, Any] = {}
    platforms_root = dest / "platforms"
    for platform in PLATFORMS:
        entry = (copy_doc.get("platforms") or {}).get(platform) or {}
        platform_title = str(entry.get("title") or "").strip()
        platform_body = ensure_ai_generated_disclosure(
            str(entry.get("body") or entry.get("caption") or "").strip()
        )
        entry["body"] = platform_body
        (copy_doc.get("platforms") or {}).setdefault(platform, entry)
        platform_tags = [
            str(tag).strip()
            for tag in (entry.get("hashtags") or [])
            if str(tag).strip()
        ]
        missing = [
            name
            for name, value in (
                ("title", platform_title),
                ("body", platform_body),
                ("hashtags", platform_tags),
            )
            if not value
        ]
        platform_hits = scan_banned_terms(
            [platform_title, platform_body, " ".join(platform_tags)], banned
        )
        passed = not missing and not platform_hits
        platform_compliance[platform] = {
            "passed": passed,
            "hits": platform_hits,
            "missing": missing,
        }
        platform_dir = platforms_root / platform
        platform_dir.mkdir(parents=True, exist_ok=True)
        platform_manifest = {
            "version": PACK_VERSION,
            "platform": platform,
            "video": "../../video.mp4",
            "title": platform_title,
            "body": platform_body,
            "hashtags": platform_tags,
            "compliance": "compliance.json",
        }
        (platform_dir / "manifest.json").write_text(
            json.dumps(platform_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (platform_dir / "compliance.json").write_text(
            json.dumps(platform_compliance[platform], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        platform_asset_status[platform] = {
            "status": "ready" if passed else "blocked",
            "title": bool(platform_title),
            "body": bool(platform_body),
            "hashtags": bool(platform_tags),
            "manifest": f"platforms/{platform}/manifest.json",
            "compliance": f"platforms/{platform}/compliance.json",
            "error": "、".join([*missing, *platform_hits]),
        }

    # Persist bodies with AI disclosure stripped (L14 revoked 2026-08-13).
    (dest / "copy.zh.json").write_text(
        json.dumps(copy_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    compliance = {
        "passed": len(hits) == 0,
        "banned_terms_checked": banned,
        "hits": hits,
        "platforms": platform_compliance,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    compliance["passed"] = bool(
        compliance["passed"]
        and all(item.get("passed") is True for item in platform_compliance.values())
    )
    (dest / "compliance.json").write_text(
        json.dumps(compliance, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "version": PACK_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_output": str(output_path),
        "pack_dir": str(dest),
        "files": files,
        "title": title,
        "theme": theme,
        "brand": brand_name,
        "compliance_passed": compliance["passed"],
        "platforms": list(PLATFORMS),
        "optional_articles": list(OPTIONAL_ARTICLE_PLATFORMS),
        "platform_asset_status": platform_asset_status,
        "video_contract": video_contract,
        "locales": ["zh", *needed_locales],
        "variants": variants,
        "expression": prefs,
        "subtitle_burn_intent": burn_intent,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not compliance["passed"]:
        problems = [
            f"{platform}: {status.get('error') or '合规未通过'}"
            for platform, status in platform_asset_status.items()
            if status.get("status") != "ready"
        ]
        if hits:
            problems.append("全局禁词：" + "、".join(str(h) for h in hits if str(h).strip()))
        if not problems:
            problems.append("合规未通过（平台文案已过，请检查字幕或旁白禁词）")
        raise ValueError("平台发布物料合同未通过：" + "；".join(problems))
    return manifest
