#!/usr/bin/env python3
"""Publish ONE video to Douyin only — CDP path inject + real 发布 click verify."""

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
RESULT = Path.home() / "QR-Volume" / "速影工作区" / "db" / "douyin_one_publish.json"

PACK = Path(
    "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片/ready/"
    "2026-07-24/montage_30_1116988907.publish_pack"
)

PROFILE = "抖音-01"
PLATFORM = "douyin"
QUEUE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 0


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
    cmd = [
        str(CHROME),
        f"--user-data-dir={user_data}",
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_cdp(timeout: float = 50) -> bool:
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


def main() -> int:
    from engine.config.settings import load_settings
    from engine.reach.cdp_publish import UPLOAD_URLS, publish_via_cdp

    data_root = Path(load_settings().paths.data_root)
    url = UPLOAD_URLS[PLATFORM]
    print("抖音单发 · CDP 注入 + 真点击发布校验")
    print("PACK", PACK)

    title = body = None
    qid = QUEUE_ID
    try:
        q = json.loads(urllib.request.urlopen(f"{API}/reach/queue").read().decode())
        for it in q.get("items") or []:
            if PLATFORM not in str(it.get("platform") or "").lower() and "douyin" not in str(
                it.get("platform") or ""
            ):
                continue
            if qid and int(it.get("id") or 0) != qid:
                continue
            if it.get("status") == "published":
                continue
            title = it.get("title") or title
            body = it.get("body") or body
            qid = int(it.get("id") or 0)
            break
        if not title:
            # fall back to any douyin row copy / pack
            for it in q.get("items") or []:
                if it.get("platform") == "douyin" and (it.get("body") or it.get("title")):
                    title = title or it.get("title")
                    body = body or it.get("body")
                    if not qid:
                        qid = int(it.get("id") or 0)
                    break
    except Exception as e:
        print("queue lookup", e)

    if not (body or "").strip():
        cp = json.loads((PACK / "copy.zh.json").read_text(encoding="utf-8"))
        dy = (cp.get("platforms") or {}).get("douyin") or {}
        title = title or dy.get("title")
        body = dy.get("body")

    template_id = "tpl_869138b1"
    try:
        ct = json.loads(urllib.request.urlopen(f"{API}/reach/cover-templates").read().decode())
        template_id = ct.get("selected_id") or template_id
    except Exception:
        pass
    print(f"queue=#{qid} title={(title or '')[:40]!r} template={template_id}")

    quit_chrome()
    print("launch", url)
    launch_chrome_cdp(PROFILE, url)
    if not wait_cdp(50):
        print("cdp_timeout")
        RESULT.write_text(json.dumps({"ok": False, "error": "cdp_timeout"}, ensure_ascii=False, indent=2))
        return 2
    time.sleep(5)

    pub = publish_via_cdp(
        platform=PLATFORM,
        pack_dir=PACK,
        data_root=data_root,
        title=title,
        body=body,
        template_id=template_id,
        click_publish=True,
        upload_video=True,
        cdp_http=CDP,
    )
    print(
        "phase=",
        pub.get("phase"),
        "err=",
        pub.get("error") or "-",
        "clicked=",
        pub.get("pub_clicked"),
        "result=",
        pub.get("result"),
    )
    for s in pub.get("snapshots") or []:
        if isinstance(s, dict) and s.get("screenshot"):
            print("shot", s.get("state"), s.get("screenshot"), s.get("hint"))
        if isinstance(s, dict) and s.get("publish_verify"):
            print("publish_verify", s.get("publish_verify"))
        if isinstance(s, dict) and s.get("publish_trail"):
            print("publish_trail", json.dumps(s.get("publish_trail"), ensure_ascii=False)[:800])

    ok = bool(pub.get("ok") and pub.get("pub_clicked"))
    if ok and qid:
        try:
            post(f"/reach/queue/{qid}/status", {"status": "published", "note": "douyin_one_real_click"})
        except Exception as e:
            print("mark fail", e)

    out = {"ok": ok, "queue_id": qid, "result": pub}
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("result →", RESULT)
    # keep chrome open briefly so user can see; don't quit if success verify failed
    if ok:
        time.sleep(2)
        quit_chrome()
    else:
        print("Chrome 保持打开，便于核对页面是否还停在「发布」")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
