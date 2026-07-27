#!/usr/bin/env python3
"""One tick for the 30s 旁白字幕对齐 loop."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE = Path("/Users/qr/QR-Volume/速影工作区/cache/subtitle_align_loop_state.json")
ENGINE = "http://127.0.0.1:8766"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _get(path: str):
    try:
        with urllib.request.urlopen(f"{ENGINE}{path}", timeout=3) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _ensure_engine() -> bool:
    h = _get("/health")
    if h and h.get("status") in ("ok", "healthy", True) or (h and "ok" in str(h).lower()):
        return True
    if h is not None:
        return True
    script = ROOT / "scripts" / "start-engine.sh"
    if script.is_file():
        subprocess.run(["bash", str(script)], cwd=str(ROOT), check=False)
    return _get("/health") is not None


def _smoke() -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_subtitle_align.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": proc.returncode == 0 and "PASS" in (proc.stdout or ""),
        "code": proc.returncode,
        "tail": ((proc.stdout or "") + (proc.stderr or ""))[-400:],
    }


def _recent_voice_dirs(limit: int = 3) -> list[Path]:
    render = Path("/Users/qr/QR-Volume/速影工作区/render")
    if not render.is_dir():
        return []
    dirs = sorted(
        [p for p in render.glob("job*_voice") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return dirs[:limit]


def _check_dir(d: Path) -> dict:
    from engine.pack.narration_script import tighten_srt_to_voiceover
    from engine.pack.tts import _speech_spans_by_silence, probe_audio_duration

    wav = d / "voiceover.wav"
    srts = list(d.glob("subtitle*.srt"))
    if not wav.is_file() or not srts:
        return {"dir": d.name, "skip": True}
    srt = srts[0].read_text(encoding="utf-8")
    tight = tighten_srt_to_voiceover(srt, wav, tail_trim_seconds=0.12)
    # Compare whether tighten would pull ends back materially
    def ends(body: str) -> list[float]:
        out = []
        for block in body.strip().split("\n\n"):
            lines = block.strip().splitlines()
            if len(lines) >= 2 and "-->" in lines[1]:
                right = lines[1].split("-->")[1].strip()
                hh, mm, rest = right.split(":")
                ss, ms = rest.split(",")
                out.append(int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0)
        return out

    old_e, new_e = ends(srt), ends(tight)
    drift = max((a - b for a, b in zip(old_e, new_e)), default=0.0)
    vo = probe_audio_duration(wav)
    spans = _speech_spans_by_silence(wav)
    return {
        "dir": d.name,
        "vo": round(vo, 2),
        "cues": len(old_e),
        "spans": len(spans),
        "max_tighten_pull": round(drift, 3),
        "needs_regen": drift > 0.15,
    }


def main() -> int:
    engine_ok = _ensure_engine()
    smoke = _smoke()
    samples = [_check_dir(d) for d in _recent_voice_dirs()]
    jobs = _get("/jobs?limit=5")
    active = []
    if isinstance(jobs, list):
        active = [
            {
                "id": j.get("id"),
                "status": j.get("status"),
                "produced": j.get("produced_count"),
                "target": j.get("target_count"),
            }
            for j in jobs
            if isinstance(j, dict) and j.get("status") in ("queued", "running", "circuit_open")
        ]
    state = {
        "at": _now(),
        "engine_ok": engine_ok,
        "smoke_ok": smoke["ok"],
        "smoke_tail": smoke["tail"],
        "active_jobs": active,
        "samples": samples,
        "next": "restart engine if smoke_ok and samples need_regen; then new calendar job for NS.SAMPLE",
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))
    return 0 if smoke["ok"] and engine_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
