"""跟镜精品 · TTS 后按真实旁白重算镜长（防片尾冻帧旁白仍在说）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.template.engine import MontagePlan


def _probe_media_duration(path: Path) -> float:
    try:
        from engine.render.ffmpeg import probe_output

        info = probe_output(path)
        return float((info.get("format") or {}).get("duration") or 0.0)
    except Exception:
        try:
            from engine.pack.tts import probe_audio_duration

            return float(probe_audio_duration(path))
        except Exception:
            return 0.0


def _clip_available_sec(clip: Any) -> float:
    src = Path(str(getattr(clip, "source_path", "") or ""))
    if not src.is_file():
        return max(0.5, float(getattr(clip, "duration_sec", 0) or 0.5))
    src_dur = _probe_media_duration(src)
    start = max(0.0, float(getattr(clip, "start_sec", 0) or 0.0))
    # 留 0.05s 尾，避免踩到片尾黑帧
    return max(0.5, src_dur - start - 0.05)


def expand_plan_clips_to_cover_narration(
    plan: MontagePlan,
    *,
    narration_path: str | Path | None,
    min_exceed_sec: float = 0.15,
) -> dict[str, Any]:
    """按真实旁白时长拉长各镜（受片源可用时长上限），减少片尾冻帧。

    合同：SCENE_TOUR「镜长 = 该句旁白配音时长 + 句间停顿」；L15 成片>旁白仍满足。
    若片源总可用时长仍不够盖住旁白，返回 need_fit=True，由调用方再 tempo-fit 旁白。
    """
    out: dict[str, Any] = {
        "applied": False,
        "need_fit": False,
        "before_picture_sec": 0.0,
        "after_picture_sec": 0.0,
        "narration_sec": 0.0,
        "target_picture_sec": 0.0,
        "reason": "",
    }
    clips = list(getattr(plan, "clips", None) or [])
    if not clips:
        out["reason"] = "no_clips"
        return out
    bed = Path(str(narration_path or ""))
    if not bed.is_file():
        out["reason"] = "no_narration"
        return out
    try:
        from engine.pack.tts import probe_audio_duration

        ndur = float(probe_audio_duration(bed))
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"probe_narration_failed:{exc}"
        return out
    out["narration_sec"] = round(ndur, 3)
    before = sum(max(0.1, float(c.duration_sec or 0)) for c in clips)
    out["before_picture_sec"] = round(before, 3)
    target = ndur + max(0.05, float(min_exceed_sec))
    out["target_picture_sec"] = round(target, 3)

    avail = [_clip_available_sec(c) for c in clips]
    total_avail = sum(avail)
    # 权重：按镜文字长度，避免平均拉长导致短句镜过长
    weights = [max(8.0, float(len(str(c.description or "")))) for c in clips]
    wsum = sum(weights) or float(len(clips))

    if total_avail + 0.02 < target:
        # 片源不够：用尽可用时长，交回 tempo-fit
        for c, a in zip(clips, avail):
            c.duration_sec = round(float(a), 3)
        out["after_picture_sec"] = round(sum(float(c.duration_sec or 0) for c in clips), 3)
        out["applied"] = True
        out["need_fit"] = True
        out["reason"] = "source_short_need_fit"
        return out

    # 比例分配，再按可用上限裁切，不够的部分二次均摊到有余量的镜
    desired = [target * (w / wsum) for w in weights]
    assigned = [min(d, a) for d, a in zip(desired, avail)]
    deficit = target - sum(assigned)
    if deficit > 0.02:
        for _ in range(len(clips) * 2):
            if deficit <= 0.02:
                break
            room = [max(0.0, a - x) for a, x in zip(avail, assigned)]
            room_sum = sum(room)
            if room_sum <= 0.001:
                break
            for i in range(len(clips)):
                if room[i] <= 0.001:
                    continue
                take = min(room[i], deficit * (room[i] / room_sum))
                assigned[i] += take
                deficit -= take
                if deficit <= 0.02:
                    break

    for c, x in zip(clips, assigned):
        c.duration_sec = round(max(0.5, float(x)), 3)
    after = sum(float(c.duration_sec or 0) for c in clips)
    out["after_picture_sec"] = round(after, 3)
    out["applied"] = True
    out["need_fit"] = after + 0.02 < target
    out["reason"] = "expanded_to_cover" if not out["need_fit"] else "partial_expand_need_fit"
    return out


def apply_slot_speech_durations(
    plan: MontagePlan,
    slot_speech_sec: list[float],
    *,
    gap_sec: float = 0.12,
) -> dict[str, Any]:
    """跟镜精品：每镜时长 = 该镜旁白实测 + 句间停顿（受片源可用上限）。"""
    clips = list(getattr(plan, "clips", None) or [])
    out: dict[str, Any] = {
        "applied": False,
        "need_fit": False,
        "slots": [],
        "reason": "",
    }
    if not clips or not slot_speech_sec:
        out["reason"] = "no_slots"
        return out
    n = min(len(clips), len(slot_speech_sec))
    need_fit = False
    for i in range(n):
        speech = max(0.4, float(slot_speech_sec[i] or 0.0))
        want = speech + max(0.05, float(gap_sec))
        avail = _clip_available_sec(clips[i])
        if want > avail + 0.02:
            need_fit = True
        dur = min(avail, want)
        clips[i].duration_sec = round(max(0.5, dur), 3)
        out["slots"].append(
            {
                "index": i,
                "speech_sec": round(speech, 3),
                "duration_sec": clips[i].duration_sec,
                "available_sec": round(avail, 3),
            }
        )
    out["applied"] = True
    out["need_fit"] = need_fit
    out["reason"] = "slot_aligned" if not need_fit else "slot_aligned_need_fit"
    return out


def clip_available_sec_for_plan_clip(clip: Any) -> float:
    return _clip_available_sec(clip)
