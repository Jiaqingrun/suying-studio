#!/usr/bin/env python3
"""Publish ONE video to 4 platforms via CDP path injection + screenshot vision.

No OS file-dialog browsing. Video/covers use DOM.setFileInputFiles with absolute
local paths. Page steps classified from CDP screenshots + DOM text.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8766"
CDP = "http://127.0.0.1:9222"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILES = Path.home() / "QR-Volume" / "速影工作区" / "chrome-profiles"
RESULT = Path.home() / "QR-Volume" / "速影工作区" / "db" / "four_platform_one_publish.json"

PACK = Path(
    "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片/ready/"
    "2026-07-24/montage_30_1116988907.publish_pack"
)

JOBS = [
    {"profile": "抖音-01", "platform": "douyin", "queue_id": 17},
    {"profile": "视频号-01", "platform": "channels", "queue_id": 18},
    {"profile": "小红书-01", "platform": "xhs", "queue_id": 19},
    {"profile": "快手-01", "platform": "kuaishou", "queue_id": 20},
]


def post(path: str, body: dict | None = None, timeout: float = 60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode(), strict=False)


def quit_chrome() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True,
        check=False,
    )
    time.sleep(1.2)
    subprocess.run(["killall", "-9", "Google Chrome"], capture_output=True, check=False)
    time.sleep(1.0)


def launch_chrome_cdp(profile: str, url: str) -> None:
    user_data = PROFILES / profile
    user_data.mkdir(parents=True, exist_ok=True)
    # Seed apple events (harmless) but we rely on CDP, not AppleScript JS
    pref = user_data / "Default" / "Preferences"
    pref.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if pref.exists():
        try:
            data = json.loads(pref.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("browser", {})["allow_javascript_apple_events"] = True
    pref.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    cmd = [
        str(CHROME),
        f"--user-data-dir={user_data}",
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        from engine.reach.browser import dismiss_popups_extension_dir

        ext = dismiss_popups_extension_dir()
        if ext is not None:
            cmd.append(f"--load-extension={ext}")
    except Exception:
        pass
    cmd.append(url)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_cdp(timeout: float = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{CDP}/json/list", timeout=2) as r:
                if json.loads(r.read().decode()):
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def mark_published(queue_id: int, note: str) -> None:
    try:
        post(f"/reach/queue/{queue_id}/status", {"status": "published", "note": note})
    except Exception as e:
        print("    mark fail", e)


def run_one(job: dict) -> dict:
    from engine.config.settings import load_settings
    from engine.reach.cdp_publish import UPLOAD_URLS, publish_via_cdp

    data_root = Path(load_settings().paths.data_root)
    url = UPLOAD_URLS[job["platform"]]
    print(f"  launch CDP Chrome → {url}")
    launch_chrome_cdp(job["profile"], url)
    if not wait_cdp(50):
        return {"ok": False, "error": "cdp_timeout"}
    # Fast settle; retry once if CDP drops during first attach
    time.sleep(5)
    # Prefer queue title/body (covers kuaishou when pack copy lacks platform)
    title = body = None
    try:
        import urllib.request
        q = json.loads(urllib.request.urlopen(f"{API}/reach/queue").read().decode())
        for it in q.get("items") or []:
            if int(it.get("id") or 0) == int(job["queue_id"]):
                title = it.get("title") or None
                body = it.get("body") or None
                break
    except Exception:
        pass
    if job["platform"] == "kuaishou" and not (body or "").strip():
        # last resort: reuse douyin copy from pack
        import json as _json
        cp = _json.loads((PACK / "copy.zh.json").read_text(encoding="utf-8"))
        dy = (cp.get("platforms") or {}).get("douyin") or {}
        title = title or dy.get("title")
        body = dy.get("body")
    # Use App-selected cover template (never silently fall back without it)
    template_id = "tpl_869138b1"
    try:
        ct = json.loads(urllib.request.urlopen(f"{API}/reach/cover-templates").read().decode())
        template_id = ct.get("selected_id") or template_id
        print(f"  cover template={template_id}")
    except Exception as e:
        print(f"  cover template lookup fail: {e}, using {template_id}")
    pub: dict = {}
    last_err = None
    for attempt in range(2):
        try:
            pub = publish_via_cdp(
                platform=job["platform"],
                pack_dir=PACK,
                data_root=data_root,
                title=title,
                body=body,
                template_id=template_id,
                click_publish=True,
                upload_video=True,
                cdp_http=CDP,
            )
            last_err = None
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            print(f"  attempt {attempt+1} EXC {msg}")
            if "断开" in msg or "CDP" in msg:
                time.sleep(3)
                # re-check CDP still up
                if not wait_cdp(20):
                    return {"ok": False, "error": "cdp_timeout_retry"}
                time.sleep(4)
                continue
            raise
    if last_err is not None and not pub:
        raise last_err
    print(
        "  phase=",
        pub.get("phase"),
        "err=",
        pub.get("error") or "-",
        "clicked=",
        pub.get("pub_clicked"),
        "upload=",
        (pub.get("upload") or {}).get("method") or (pub.get("upload") or {}).get("error"),
        "covers=",
        (pub.get("covers") or {}).get("ok"),
    )
    snaps = pub.get("snapshots") or []
    for s in snaps:
        if isinstance(s, dict) and s.get("screenshot"):
            print("  shot", s.get("state"), s.get("screenshot"), s.get("hint"))
    ok = bool(pub.get("ok") and pub.get("pub_clicked"))
    if ok:
        mark_published(job["queue_id"], "four_platform_cdp_inject")
    return {"ok": ok, "via": "cdp_inject", "result": pub}


def main() -> int:
    import os

    force_all = os.environ.get("FORCE_ALL", "") == "1"
    print("四平台各发 1 条 · CDP 本机路径注入 · 截图识别步骤 · 禁止文件对话框浏览")
    print("PACK", PACK)
    results = []
    for job in JOBS:
        print(f"\n===== {job['profile']} ({job['platform']}) #{job['queue_id']} =====")
        if job.get("skip_if_done") and not force_all:
            try:
                q = json.loads(urllib.request.urlopen(f"{API}/reach/queue").read().decode())
                st = next((it for it in (q.get("items") or []) if int(it.get("id") or 0) == job["queue_id"]), None)
                if st and st.get("status") == "published":
                    print("  skip already published")
                    results.append({**job, "ok": True, "skipped": True})
                    continue
            except Exception:
                pass
        quit_chrome()
        try:
            row = run_one(job)
        except Exception as e:
            row = {"ok": False, "error": str(e)}
            print("  EXC", e)
        row.update(job)
        results.append(row)
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        time.sleep(2)
    quit_chrome()
    print("\n===== SUMMARY =====")
    for r in results:
        print(r["platform"], "OK" if r.get("ok") else "FAIL", r.get("error") or ("skipped" if r.get("skipped") else ""))
    print("result →", RESULT)
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
