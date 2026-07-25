#!/usr/bin/env python3
"""Safari batch publish: random ready outputs → publish_pack → Safari upload.

Requires Accessibility for Cursor/Terminal. Location dialog → Allow.
Does not bypass captcha. Single account only.
"""

from __future__ import annotations

import json
import random
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8766"
VISION = Path("/tmp/suying_safari_vision")
HELPER = Path(__file__).resolve().parent / "safari_helpers" / "dismiss_location_allow.applescript"


def api_get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=60) as r:
        return json.loads(r.read().decode(), strict=False)


def api_post(path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode(), strict=False)


def osa(*lines: str) -> str:
    script = "\n".join(lines)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return f"ERR:{r.stderr.strip()}"
    return (r.stdout or "").strip()


def osa_file(path: Path) -> str:
    r = subprocess.run(["osascript", str(path)], capture_output=True, text=True)
    return (r.stdout or r.stderr or "").strip()


def js(code: str) -> str:
    # Escape for AppleScript string
    esc = code.replace("\\", "\\\\").replace('"', '\\"')
    return osa(
        'tell application "Safari"',
        "activate",
        f'set r to do JavaScript "{esc}" in front document',
        "return r",
        "end tell",
    )


def pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def shot(name: str) -> None:
    VISION.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", str(VISION / name)], check=False)


def dismiss_location() -> None:
    if HELPER.exists():
        osa_file(HELPER)
    else:
        osa(
            'tell application "Safari" to activate',
            'tell application "System Events"',
            'tell process "Safari"',
            "set frontmost to true",
            'try',
            'click button "允许" of sheet 1 of window 1',
            "end try",
            "end tell",
            "end tell",
        )


def ensure_upload_page() -> None:
    osa(
        'tell application "Safari"',
        "activate",
        'open location "https://creator.douyin.com/creator-micro/content/upload"',
        "end tell",
    )
    time.sleep(2.5)
    dismiss_location()


def click_upload_button() -> str:
    return js(
        "(function(){var btn=Array.from(document.querySelectorAll('button,div,span,a')).find(e=>{"
        "var t=(e.innerText||'').trim(); return t==='上传视频'||t==='+ 上传视频'||(t.includes('上传视频')&&t.length<20);"
        "}); if(btn){btn.click();return 'clicked';} return 'fail';})();"
    )


def select_pack_video(pack_dir: Path) -> None:
    video = pack_dir / "video.mp4"
    if not video.is_file():
        raise FileNotFoundError(video)
    pbcopy(str(pack_dir))
    osa(
        'tell application "Safari" to activate',
        "delay 0.3",
        'tell application "System Events"',
        'tell process "Safari"',
        "set frontmost to true",
        "delay 0.4",
        'keystroke "g" using {command down, shift down}',
        "delay 0.9",
        'keystroke "a" using {command down}',
        "delay 0.15",
        'keystroke "v" using {command down}',
        "delay 0.4",
        "keystroke return",
        "delay 1.5",
        'keystroke "video.mp4"',
        "delay 0.8",
        "keystroke return",
        "delay 0.5",
        'try',
        'click button "上传" of sheet 1 of window 1',
        "on error",
        "keystroke return",
        "end try",
        "end tell",
        "end tell",
    )


def fill_and_publish(title: str, body: str) -> str:
    title_one = " ".join((title or "").split())
    body = body or ""
    # Fill via JS file to avoid escaping hell
    fill_path = VISION / "fill_once.js"
    fill_path.write_text(
        f"""
(function(){{
  const title = {json.dumps(title_one, ensure_ascii=False)};
  const body = {json.dumps(body, ensure_ascii=False)};
  const input = document.querySelector('input.semi-input, input[placeholder*="标题"], input[placeholder*="作品"]');
  let titleOk=false, bodyOk=false;
  if (input) {{
    input.focus();
    const proto = HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(input, title.slice(0,30)); else input.value = title.slice(0,30);
    input.dispatchEvent(new Event('input', {{bubbles:true}}));
    titleOk = true;
  }}
  const ed = document.querySelector('.editor-kit-container[contenteditable="true"], [contenteditable="true"].zone-container, div[contenteditable="true"]');
  if (ed) {{
    ed.focus();
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, body.slice(0,1000));
    ed.dispatchEvent(new InputEvent('input', {{bubbles:true}}));
    bodyOk = (ed.innerText||'').length > 2;
  }}
  return JSON.stringify({{titleOk, bodyOk}});
}})();
""",
        encoding="utf-8",
    )
    # Read file into Safari JS
    r = subprocess.run(
        [
            "osascript",
            "-e",
            f'set js to read POSIX file "{fill_path}" as «class utf8»\n'
            'tell application "Safari"\nset r to do JavaScript js in front document\nreturn r\nend tell',
        ],
        capture_output=True,
        text=True,
    )
    fill_result = (r.stdout or "").strip()
    dismiss_location()
    time.sleep(0.5)
    pub = js(
        "(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>(x.innerText||'').trim()==='发布');"
        "if(!b)return 'no_btn'; if(b.disabled)return 'disabled'; b.click(); return 'clicked';})();"
    )
    return f"fill={fill_result}; pub={pub}"


def wait_upload_form(timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        dismiss_location()
        t = js(
            "(function(){var s=(document.body&&document.body.innerText)||'';"
            "return (s.includes('作品描述')||s.includes('填写作品标题')||s.includes('发布设置'))?'form':"
            "(s.includes('上传')?'uploading':'other');})();"
        )
        if t == "form":
            return True
        time.sleep(2)
    return False


def publish_one(pack_dir: Path, title: str, body: str, tag: str) -> dict:
    ensure_upload_page()
    time.sleep(1)
    cr = click_upload_button()
    time.sleep(1.2)
    select_pack_video(pack_dir)
    shot(f"{tag}_after_select.png")
    ok_form = wait_upload_form()
    dismiss_location()
    # wait a bit more for 100%
    time.sleep(3)
    dismiss_location()
    result = fill_and_publish(title, body)
    time.sleep(4)
    dismiss_location()
    shot(f"{tag}_after_pub.png")
    url = osa('tell application "Safari" to get URL of front document')
    return {"click_upload": cr, "form": ok_form, "result": result, "url": url}


def load_excluded_ids() -> set[int]:
    used: set[int] = {39}
    db = Path.home() / "QR-Volume" / "速影工作区" / "db"
    for name in ("batch_publish_5.json", "batch_publish_5_round2.json", "ship_trial_s1.json"):
        p = db / name
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in raw.get("plan") or []:
            if item.get("output_id") is not None:
                used.add(int(item["output_id"]))
        if raw.get("output_id") is not None:
            used.add(int(raw["output_id"]))
    return used


def main() -> int:
    VISION.mkdir(parents=True, exist_ok=True)
    excluded = load_excluded_ids()
    print("exclude_ids", sorted(excluded))
    outputs = api_get("/outputs")
    items = outputs if isinstance(outputs, list) else outputs.get("outputs") or []
    ready = [
        it
        for it in items
        if (it.get("state") == "ready" or str(it.get("output_path") or "").find("/ready/") >= 0)
        and it.get("output_path")
        and Path(it["output_path"]).is_file()
        and int(it.get("id") or 0) not in excluded
        and "_branded" not in str(it.get("output_path") or "")
    ]
    if len(ready) < 5:
        raise SystemExit(f"未发布 ready 不足 5 条: {len(ready)} (excluded={sorted(excluded)})")
    picked = random.sample(ready, 5)
    plan = []
    for it in picked:
        oid = int(it["id"])
        pack = api_post(f"/outputs/{oid}/publish-pack")
        manifest = pack.get("manifest") or {}
        pack_dir = Path(manifest.get("pack_dir") or "")
        if not pack_dir.is_dir():
            raise SystemExit(f"pack missing for {oid}")
        try:
            fr = api_post(
                "/reach/queue/from-pack",
                {"pack_dir": str(pack_dir), "platforms": ["douyin"]},
            )
            qid = ((fr.get("items") or [{}])[0] or {}).get("id")
        except urllib.error.HTTPError:
            qid = None
        title = manifest.get("title") or it.get("title") or ""
        body = ""
        cz = pack_dir / "copy.zh.json"
        if cz.is_file():
            copy = json.loads(cz.read_text(encoding="utf-8"))
            body = ((copy.get("platforms") or {}).get("douyin") or {}).get("body") or ""
        plan.append(
            {
                "output_id": oid,
                "title": title,
                "body": body,
                "pack_dir": str(pack_dir),
                "queue_id": qid,
                "path": it.get("output_path"),
            }
        )

    plan_path = Path.home() / "QR-Volume" / "速影工作区" / "db" / "batch_publish_5_round2.json"
    plan_path.write_text(
        json.dumps({"plan": plan, "excluded": sorted(excluded)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"picked": [{"id": p["output_id"], "title": p["title"][:40]} for p in plan]}, ensure_ascii=False, indent=2))

    results = []
    for i, p in enumerate(plan, 1):
        print(f"\n=== [{i}/5] output#{p['output_id']} {p['title'][:40]} ===")
        try:
            r = publish_one(Path(p["pack_dir"]), p["title"], p["body"], f"r2_{i}")
            if p.get("queue_id"):
                try:
                    api_post(
                        f"/reach/queue/{p['queue_id']}/status",
                        {"status": "published", "note": f"safari_batch_r2_{i}"},
                    )
                except Exception as e:
                    r["mark_err"] = str(e)
            r["ok"] = True
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        r["output_id"] = p["output_id"]
        r["queue_id"] = p.get("queue_id")
        results.append(r)
        print(json.dumps(r, ensure_ascii=False))
        time.sleep(3)

    out = {"plan": plan, "excluded": sorted(excluded), "results": results}
    plan_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nDONE", plan_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
