#!/usr/bin/env python3
"""30s tick: emoji sticker QA (visible + never spoken)."""

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

STATE = Path("/Users/qr/QR-Volume/速影工作区/cache/emoji_sticker_loop_state.json")
ENGINE = "http://127.0.0.1:8766"
READY = Path("/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片/ready")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _get(path: str):
    try:
        with urllib.request.urlopen(f"{ENGINE}{path}", timeout=4) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _post(path: str, body: dict):
    req = urllib.request.Request(
        f"{ENGINE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _smoke() -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_emoji_stickers.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": proc.returncode == 0 and "PASS" in (proc.stdout or ""),
        "tail": ((proc.stdout or "") + (proc.stderr or ""))[-500:],
    }


def _qa_ready(limit: int = 3) -> list[dict]:
    from engine.pack.emoji_stickers import strip_emoji_for_speech

    today = datetime.now().strftime("%Y-%m-%d")
    day = READY / today
    if not day.is_dir():
        return []
    outs = sorted(day.glob("montage_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    results = []
    for mp4 in outs:
        side = mp4.with_suffix(".json")
        info: dict = {"file": mp4.name, "sidecar": side.is_file()}
        if side.is_file():
            meta = json.loads(side.read_text(encoding="utf-8"))
            m = meta.get("meta") if isinstance(meta.get("meta"), dict) else meta
            script = str((m or {}).get("narration_script") or "")
            cues = (m or {}).get("emoji_cues") or []
            info["script_has_emoji"] = script != strip_emoji_for_speech(script)
            info["emoji_cues"] = len(cues) if isinstance(cues, list) else 0
            info["emoji_burned"] = bool((m or {}).get("emoji_burned"))
            info["emoji_burn_count"] = (m or {}).get("emoji_burn_count")
        # Sample upper-right at 1.0s — file size heuristic after crop
        crop = Path("/tmp") / f"emoji_qa_{mp4.stem}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "1.0",
                "-i",
                str(mp4),
                "-vf",
                "crop=240:240:820:100",
                "-frames:v",
                "1",
                str(crop),
            ],
            capture_output=True,
            check=False,
        )
        info["corner_jpg_bytes"] = crop.stat().st_size if crop.is_file() else 0
        # Pure green/dark corner crops are tiny; stickers push size up
        info["corner_looks_empty"] = info["corner_jpg_bytes"] < 3500
        info["ok"] = (
            not info.get("script_has_emoji")
            and int(info.get("emoji_cues") or 0) >= 1
            and not info.get("corner_looks_empty")
        )
        results.append(info)
    return results


def main() -> int:
    health = _get("/health")
    engine_ok = bool(health)
    smoke = _smoke()
    jobs = _get("/jobs?limit=5")
    active = []
    if isinstance(jobs, list):
        active = [
            {"id": j.get("id"), "status": j.get("status"), "produced": j.get("produced_count"), "target": j.get("target_count")}
            for j in jobs
            if isinstance(j, dict) and j.get("status") in ("queued", "running", "circuit_open")
        ]
    samples = _qa_ready()
    all_ok = smoke["ok"] and engine_ok and (not samples or all(s.get("ok") for s in samples[:1]))
    state = {
        "at": _now(),
        "engine_ok": engine_ok,
        "smoke_ok": smoke["ok"],
        "smoke_tail": smoke.get("tail"),
        "active_jobs": active,
        "samples": samples,
        "all_ok": all_ok,
        "next": "if not all_ok fix code; if no recent sample queue 1-clip job; after stable write EMOJI_STICKER_LOCK + final clip",
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))
    return 0 if smoke["ok"] and engine_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
