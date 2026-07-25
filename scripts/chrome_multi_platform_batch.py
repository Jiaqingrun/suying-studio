#!/usr/bin/env python3
"""Serial Chrome multi-platform upload: switch profile → 2 videos each.

Douyin: G5.V auto click-upload.
Channels/XHS: open official page + wait login + paste card; stop if captcha.
Does not run accounts in parallel. Accept risk implied by operator.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Unbuffered progress for long runs
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass

API = "http://127.0.0.1:8766"
PLAN = Path.home() / "QR-Volume" / "速影工作区" / "db" / "chrome_multi_upload_plan.json"
RESULT = Path.home() / "QR-Volume" / "速影工作区" / "db" / "chrome_multi_upload_result.json"
PROFILES_ROOT = Path.home() / "QR-Volume" / "速影工作区" / "chrome-profiles"


def ensure_apple_events_js(profile_name: str) -> None:
    """Seed browser.allow_javascript_apple_events before open (Chrome reads on start)."""
    pref = PROFILES_ROOT / profile_name / "Default" / "Preferences"
    pref.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if pref.exists():
        try:
            data = json.loads(pref.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("browser", {})["allow_javascript_apple_events"] = True
    pref.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def quit_all_chrome() -> None:
    """Serial only: close Chrome so next profile is the AppleScript target."""
    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True,
        check=False,
    )
    time.sleep(1.5)
    subprocess.run(["killall", "-9", "Google Chrome"], capture_output=True, check=False)
    time.sleep(1.0)


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.loads(r.read().decode(), strict=False)


def post(path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode(), strict=False)


def wait_auto_upload(timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = get("/reach/auto-upload/status")
        phase = last.get("phase")
        print(f"  auto phase={phase} msg={last.get('message') or ''}")
        if phase in ("done", "failed", "cancelled", "need_human", "awaiting_confirm", "idle"):
            return last
        time.sleep(2)
    return last


def enqueue(pack_dir: str, platform: str) -> int | None:
    try:
        fr = post(
            "/reach/queue/from-pack",
            {"pack_dir": pack_dir, "platforms": [platform]},
        )
    except urllib.error.HTTPError as e:
        print("  enqueue HTTP", e.code, e.read().decode()[:300])
        return None
    items = fr.get("items") or []
    if not items:
        return None
    return int(items[0]["id"])


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    profiles = plan["profiles"]
    videos = plan["videos"]
    results = []

    for prof in profiles:
        name = prof["name"]
        platform = prof["platform"]
        print(f"\n===== PROFILE {name} ({platform}) =====")
        # Cancel leftover auto-upload before switching
        try:
            post("/reach/auto-upload/cancel")
        except Exception:
            pass
        quit_all_chrome()
        ensure_apple_events_js(name)
        post("/reach/chrome-profiles/select", {"name": name, "platform": platform})
        opened = post(
            "/reach/chrome-profiles/open",
            {"name": name, "platform": platform, "dry_run": False},
        )
        print("  opened", opened.get("platform"), opened.get("open", {}).get("opened"))
        time.sleep(5)

        for vid in videos:
            pack_dir = vid["pack_dir"]
            oid = vid["output_id"]
            print(f"\n--- {name} ← output#{oid} ---")
            qid = enqueue(pack_dir, platform)
            print("  queue_id", qid)
            if not qid:
                results.append(
                    {
                        "profile": name,
                        "platform": platform,
                        "output_id": oid,
                        "ok": False,
                        "error": "enqueue_failed",
                    }
                )
                continue

            try:
                start = post(
                    "/reach/auto-upload/start",
                    {
                        "chrome_profile": name,
                        "queue_id": qid,
                        "platform": platform,
                        "accept_risk": True,
                        "timeout_sec": 180,
                        "dry_run": False,
                    },
                )
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:400]
                print("  auto-upload start fail", e.code, body)
                results.append(
                    {
                        "profile": name,
                        "platform": platform,
                        "output_id": oid,
                        "queue_id": qid,
                        "ok": False,
                        "error": body,
                    }
                )
                continue

            print("  started", start.get("phase"), start.get("message"))
            st = wait_auto_upload(240)
            row = {
                "profile": name,
                "platform": platform,
                "output_id": oid,
                "queue_id": qid,
                "phase": st.get("phase"),
                "message": st.get("message"),
                "need_human": st.get("need_human"),
                "ok": st.get("phase") in ("done", "awaiting_confirm"),
            }
            results.append(row)
            print("  result", row["phase"], row.get("message"))
            # brief pause between videos / accounts
            time.sleep(4)

        # cancel leftover job before next profile
        try:
            post("/reach/auto-upload/cancel")
        except Exception:
            pass
        time.sleep(2)

    out = {"plan": plan, "results": results}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nDONE", RESULT)
    # summary
    for r in results:
        print(
            f"{r.get('profile')} #{r.get('output_id')} -> {r.get('phase')} ok={r.get('ok')} {r.get('message') or r.get('error') or ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
