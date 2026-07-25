#!/usr/bin/env python3
"""G6 Ops smoke: reports/ops fields + missing_voice filter + batch-rerender API shape."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("SUYING_ENGINE", "http://127.0.0.1:8766")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)


def post(path: str, body: dict | None = None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    errors: list[str] = []
    try:
        h = get("/health")
        if h.get("status") != "ok":
            errors.append(f"health not ok: {h}")
    except Exception as e:
        print("FAIL: engine unreachable", e)
        return 1

    try:
        ops = get("/reports/ops")
        for key in (
            "ready_rate",
            "failure_rate",
            "voice_coverage",
            "health_line",
            "missing_voice",
            "tts_noncompliant",
            "tts_lock_hard_fail",
            "quota",
        ):
            if key not in ops:
                errors.append(f"reports/ops missing {key}")
        print(
            "ops",
            ops.get("customer_name"),
            "voice_cov",
            ops.get("voice_coverage"),
            "tts_bad",
            ops.get("tts_noncompliant"),
            "health",
            ops.get("health_line"),
        )
    except Exception as e:
        errors.append(f"reports/ops: {e}")

    try:
        missing = get("/outputs?missing_voice=true&state=ready")
        if not isinstance(missing, list):
            errors.append("missing_voice filter did not return list")
        else:
            bad = [o for o in missing if o.get("has_voice")]
            if bad:
                errors.append(f"missing_voice leaked has_voice ids={[o.get('id') for o in bad[:3]]}")
            print("missing_voice_ready", len(missing))
    except Exception as e:
        errors.append(f"outputs filter: {e}")

    try:
        # Shape check without enqueueing real work: unknown id → errors[], count 0
        try:
            br = post(
                "/outputs/batch-rerender",
                {
                    "output_ids": [0],
                    "missing_voice": True,
                    "missing_subtitle": False,
                    "limit": 1,
                },
            )
            if "queued" not in br or "count" not in br or "errors" not in br:
                errors.append(f"batch-rerender shape: {br}")
            else:
                print(
                    "batch_rerender",
                    "count",
                    br.get("count"),
                    "errors",
                    len(br.get("errors") or []),
                    "limit",
                    br.get("limit"),
                )
        except urllib.error.HTTPError as he:
            body = he.read().decode("utf-8", errors="replace")
            if he.code == 409:
                print("batch_rerender blocked (production not ready) — acceptable:", body[:120])
            else:
                errors.append(f"batch-rerender HTTP {he.code}: {body[:200]}")
    except Exception as e:
        errors.append(f"batch-rerender: {e}")

    # Unit: publish trail + TTS lock helpers offline
    from engine.ops.publish_trail import tts_lock_violation, voice_flags_from_meta

    assert voice_flags_from_meta({"narration_path": "/x", "subtitle_burned": True}) == (True, True)
    assert voice_flags_from_meta({}) == (False, False)
    lock = {"locked": True, "voice": {"provider": "edge"}}
    assert tts_lock_violation({"narration_path": "/x", "tts_provider": "say"}, lock) == "tts_fell_back_to_macos_say"
    assert tts_lock_violation({"narration_path": "/x", "tts_provider": "edge"}, lock) is None
    print("publish_trail helpers ok")

    if errors:
        print("SMOKE_OPS FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("SMOKE_OPS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
