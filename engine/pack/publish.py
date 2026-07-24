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
            "body": f"{sub}。{brand}仓配/门店实拍，欢迎咨询。\n{tag_line}",
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
) -> dict[str, Any]:
    """
    Create `<stem>.publish_pack/` beside the ready mp4.
    Returns manifest dict + paths.

    G4: by default also emits English subtitle + copy + optional narration bed
    (``foreign_locales`` default ``["en"]``).
    """
    output_path = Path(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(f"成片不存在: {output_path}")

    locales = list(foreign_locales) if foreign_locales is not None else ["en"]

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
    variants: dict[str, Any] = {}

    if "en" in locales:
        from engine.pack.locale import (
            build_platform_copy_en,
            glossary_from_pack,
            narration_script_en,
            translate_phrase,
        )

        gloss = glossary_from_pack(pack_data)
        copy_en = build_platform_copy_en(
            title_zh=title,
            brand=brand_name,
            theme=theme,
            hashtags=hashtags,
            music_credit=music,
            glossary=gloss,
            description=str(copy0.get("description") or ""),
        )
        (dest / "copy.en.json").write_text(
            json.dumps(copy_en, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        title_en = str(copy_en.get("title_en") or translate_phrase(title, gloss))
        srt_en = build_subtitle_srt(title_en.replace(" — ", "｜"), duration_sec=min(8.0, max(4.0, dur * 0.25)))
        (dest / "subtitle.en.srt").write_text(srt_en, encoding="utf-8")
        files["copy_en"] = "copy.en.json"
        files["subtitle_en"] = "subtitle.en.srt"
        variant_meta: dict[str, Any] = {
            "locale": "en",
            "copy": "copy.en.json",
            "subtitle": "subtitle.en.srt",
        }
        if include_narration:
            from engine.pack.tts import narration_bed_from_result, synthesize_script

            script_en = narration_script_en(title, brand=brand_name, glossary=gloss)
            narr_dir = dest / "narration.en"
            narr = synthesize_script(
                script_en,
                narr_dir,
                lang="en",
                provider=tts_provider if tts_provider in ("mock", "say") else "mock",  # type: ignore[arg-type]
            )
            bed = narration_bed_from_result(narr, dest / "voiceover.en.wav")
            variant_meta["narration_dir"] = "narration.en"
            variant_meta["voiceover"] = "voiceover.en.wav"
            variant_meta["narration_manifest"] = "narration.en/narration_manifest.json"
            variant_meta["script"] = script_en
            variant_meta["duration_sec"] = narr.total_duration_sec
            files["voiceover_en"] = bed.name
            files["narration_en"] = "narration.en"
        variants["en"] = variant_meta

    banned = collect_banned_terms(profile=profile, pack_data=pack_data)
    scan_texts = [title, json.dumps(copy_doc, ensure_ascii=False), srt]
    if "copy.en.json" in (files.get("copy_en") or ""):
        scan_texts.append((dest / "copy.en.json").read_text(encoding="utf-8"))
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
        "version": "1.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_output": str(output_path),
        "pack_dir": str(dest),
        "files": files,
        "title": title,
        "theme": theme,
        "brand": brand_name,
        "compliance_passed": compliance["passed"],
        "platforms": list(PLATFORMS),
        "locales": ["zh", *locales],
        "variants": variants,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
