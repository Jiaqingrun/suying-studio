#!/usr/bin/env python3
"""GQual Q1: sample recent ready outputs into quality_sample.json."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.catalog.db import Customer, RenderOutput, get_session, init_db  # noqa: E402
from engine.config.settings import load_settings  # noqa: E402
from engine.ops.publish_trail import read_sidecar, voice_flags_from_meta  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _ffprobe(path: Path) -> dict:
    if not path.is_file():
        return {"exists": False}
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        dur = float((data.get("format") or {}).get("duration") or 0)
        return {"exists": True, "duration_sec": dur, "has_audio": has_audio}
    except Exception as e:  # noqa: BLE001
        return {"exists": True, "error": str(e)}


def main() -> None:
    settings = load_settings()
    init_db(settings)
    session = get_session()
    try:
        name = settings.active_customer or ""
        customer = session.scalar(select(Customer).where(Customer.name == name))
        if not customer:
            customer = session.scalars(select(Customer).limit(1)).first()
        if not customer:
            print(json.dumps({"ok": False, "error": "no customer"}))
            return
        rows = list(
            session.scalars(
                select(RenderOutput)
                .where(RenderOutput.state.in_(("ready", "published")))
                .order_by(RenderOutput.id.desc())
                .limit(40)
            ).all()
        )
        samples = []
        silent = 0
        no_voice = 0
        no_sub = 0
        missing_file = 0
        for o in rows:
            side = read_sidecar(o.sidecar_path)
            meta = side.get("meta") if isinstance(side.get("meta"), dict) else {}
            hv, hs = voice_flags_from_meta(meta)
            probe = _ffprobe(Path(o.output_path or ""))
            if not probe.get("exists"):
                missing_file += 1
            if probe.get("has_audio") is False:
                silent += 1
            if not hv:
                no_voice += 1
            if not hs:
                no_sub += 1
            samples.append(
                {
                    "id": o.id,
                    "state": o.state,
                    "path": o.output_path,
                    "has_voice_meta": hv,
                    "has_subtitle_meta": hs,
                    "probe": probe,
                    "archived": "published" in Path(o.output_path or "").parts,
                }
            )
        n = len(samples) or 1
        report = {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "customer": customer.name,
            "sample_size": len(samples),
            "rates": {
                "missing_file": round(missing_file / n, 4),
                "silent_probe": round(silent / n, 4),
                "no_voice_meta": round(no_voice / n, 4),
                "no_subtitle_meta": round(no_sub / n, 4),
            },
            "thresholds": {
                "ready_rate_target": 0.7,
                "silent_max": 0.0,
            },
            "samples": samples,
        }
        out = Path(settings.paths.data_root) / "quality_sample.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "path": str(out), "rates": report["rates"]}, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
