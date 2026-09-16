"""Paid voice artifact reuse (V-02 G1C / PL-13).

After Edge/clone TTS succeeds, persist a stamp + WAV/SRT under a stable
per-job cache. On a later attempt of the same job, when the spoken script and
voice params are unchanged, skip re-synthesis and copy the paid files into the
new work_dir.

Does **not** relax READY_GATE / PL-21. Scene-tour (跟镜) is excluded by callers.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

VOICE_ARTIFACT_VERSION = 1


def compute_voice_stamp(
    *,
    script: str,
    provider: str,
    voice: str,
    rate: str | None = None,
    pitch: str | None = None,
    volume: str | None = None,
) -> str:
    payload = {
        "v": VOICE_ARTIFACT_VERSION,
        "script": str(script or "").strip(),
        "provider": str(provider or "").strip().lower(),
        "voice": str(voice or "").strip(),
        "rate": str(rate or "").strip(),
        "pitch": str(pitch or "").strip(),
        "volume": str(volume or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def paid_voice_cache_dir(rendering_dir: Path, job_id: int) -> Path:
    return Path(rendering_dir) / f"job{int(job_id)}_paid_voice"


def save_voice_artifact(
    cache_dir: Path,
    *,
    stamp: str,
    narr_path: Path,
    srt_path: Path | None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Copy paid WAV/SRT into cache_dir and write stamp.json."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    narr_src = Path(narr_path)
    if not narr_src.is_file() or narr_src.stat().st_size < 64:
        raise ValueError("narration missing or too small to cache")
    narr_dest = cache_dir / "voiceover.wav"
    shutil.copy2(narr_src, narr_dest)
    srt_dest: Path | None = None
    if srt_path and Path(srt_path).is_file():
        srt_dest = cache_dir / Path(srt_path).name
        shutil.copy2(srt_path, srt_dest)
    manifest = {
        "version": VOICE_ARTIFACT_VERSION,
        "stamp": stamp,
        "narration": narr_dest.name,
        "srt": srt_dest.name if srt_dest else None,
        "meta": dict(meta or {}),
    }
    (cache_dir / "stamp.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cache_dir


def try_reuse_voice_artifact(
    cache_dir: Path,
    *,
    stamp: str,
    dest_work_dir: Path,
) -> dict[str, Any] | None:
    """If cache stamp matches, copy artifacts into dest_work_dir and return paths."""
    cache_dir = Path(cache_dir)
    man_path = cache_dir / "stamp.json"
    if not man_path.is_file():
        return None
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if str(man.get("stamp") or "") != stamp:
        return None
    narr_name = str(man.get("narration") or "voiceover.wav")
    narr_src = cache_dir / narr_name
    if not narr_src.is_file() or narr_src.stat().st_size < 64:
        return None
    dest_work_dir = Path(dest_work_dir)
    dest_work_dir.mkdir(parents=True, exist_ok=True)
    narr_dest = dest_work_dir / "voiceover.wav"
    shutil.copy2(narr_src, narr_dest)
    srt_dest: Path | None = None
    srt_name = man.get("srt")
    if srt_name:
        srt_src = cache_dir / str(srt_name)
        if srt_src.is_file():
            srt_dest = dest_work_dir / srt_src.name
            shutil.copy2(srt_src, srt_dest)
    return {
        "narration_path": str(narr_dest.resolve()),
        "srt_path": str(srt_dest.resolve()) if srt_dest else None,
        "stamp": stamp,
        "reused": True,
        "meta": dict(man.get("meta") or {}),
    }


def clear_voice_artifact(cache_dir: Path) -> None:
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return
    shutil.rmtree(cache_dir, ignore_errors=True)
