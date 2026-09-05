"""Chrome G5.V helpers: detect creator login readiness + one-shot upload.

macOS only. Uses Google Chrome AppleScript JavaScript (requires
View → Developer → Allow JavaScript from Apple Events).

Never bypasses captcha/login. Single account / serial profile only.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
VISION = Path("/tmp/suying_chrome_vision")

# Page probe states returned by probe_page()
STATE_READY = "ready"
STATE_NEED_LOGIN = "need_login"
STATE_NEED_HUMAN = "need_human"
STATE_UPLOADING = "uploading"
STATE_FORM = "form"
STATE_OTHER = "other"
STATE_ERROR = "error"


def _osa(*lines: str) -> str:
    script = "\n".join(lines)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"ERR:{err}"
    return (r.stdout or "").strip()


def chrome_js(code: str) -> str:
    """Execute JS in Chrome front window active tab."""
    esc = code.replace("\\", "\\\\").replace('"', '\\"')
    return _osa(
        'tell application "Google Chrome"',
        "activate",
        "if (count of windows) = 0 then return \"ERR:no_window\"",
        f'set r to execute active tab of front window javascript "{esc}"',
        "return r",
        "end tell",
    )


def chrome_url() -> str:
    return _osa(
        'tell application "Google Chrome"',
        "if (count of windows) = 0 then return \"\"",
        "return URL of active tab of front window",
        "end tell",
    )


def chrome_open_url(url: str) -> None:
    """Navigate the front window's active tab; never open a new tab."""
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    _osa(
        'tell application "Google Chrome"',
        "activate",
        "if (count of windows) = 0 then make new window",
        f'set URL of active tab of front window to "{escaped}"',
        "end tell",
    )


def pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)


def probe_page() -> dict[str, Any]:
    """Classify current Chrome creator page (login / captcha / ready / form)."""
    url = chrome_url()
    if url.startswith("ERR:"):
        return {"state": STATE_ERROR, "url": "", "detail": url}
    js = chrome_js(
        "(function(){"
        "var u=location.href||'';"
        "var t=(document.body&&document.body.innerText)||'';"
        "if(/passport|\\/login|sso|sso\\.|accounts\\./i.test(u)||"
        "t.indexOf('扫码登录')>=0||t.indexOf('手机号登录')>=0||"
        "t.indexOf('短信登录')>=0||t.indexOf('登录后免费使用')>=0||"
        "t.indexOf('验证码登录')>=0||t.indexOf('发送验证码')>=0)"
        "return 'need_login';"
        "if(t.indexOf('接收短信验证码')>=0||t.indexOf('请完成安全验证')>=0||"
        "t.indexOf('请拖动滑块')>=0||t.indexOf('向右拖动滑块')>=0||"
        "t.indexOf('人机验证')>=0||t.indexOf('为确保是本人操作')>=0||"
        "/captcha/i.test(u)) return 'need_human';"
        "if(t.indexOf('作品描述')>=0||t.indexOf('填写作品标题')>=0||"
        "t.indexOf('发布设置')>=0) return 'form';"
        "if(u.indexOf('creator.douyin.com')>=0&&"
        "(t.indexOf('上传视频')>=0||t.indexOf('内容管理')>=0||"
        "t.indexOf('发布视频')>=0||t.indexOf('作品管理')>=0||"
        "t.indexOf('高清发布')>=0)) return 'ready';"
        "if(u.indexOf('creator.douyin.com')>=0) return 'maybe';"
        "return 'other';"
        "})();"
    )
    state = js if js and not js.startswith("ERR:") else STATE_ERROR
    if state == "maybe":
        state = STATE_READY  # on creator domain without login wall → treat as ready enough
    return {"state": state, "url": url, "js": js}


def wait_until_ready(
    *,
    timeout_sec: float = 300,
    poll_sec: float = 2.0,
    on_tick: Any | None = None,
) -> dict[str, Any]:
    """Poll until ready / need_human / timeout. Stops on captcha."""
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {"state": STATE_OTHER}
    while time.time() < deadline:
        last = probe_page()
        if on_tick:
            on_tick(last)
        st = last.get("state")
        if st == STATE_READY or st == STATE_FORM:
            return {**last, "ok": True, "reason": "ready"}
        if st == STATE_NEED_HUMAN:
            return {**last, "ok": False, "reason": "need_human"}
        # need_login / other / error → keep waiting
        time.sleep(poll_sec)
    return {**last, "ok": False, "reason": "timeout"}


def click_upload_button() -> str:
    return chrome_js(
        "(function(){var btn=Array.from(document.querySelectorAll('button,div,span,a')).find(e=>{"
        "var t=(e.innerText||'').trim(); return t==='上传视频'||t==='+ 上传视频'||"
        "(t.indexOf('上传视频')>=0&&t.length<20);});"
        "if(btn){btn.click();return 'clicked';} return 'fail';})();"
    )


def select_local_file(file_path: Path) -> None:
    """DEPRECATED fallback: macOS file dialog navigation.

    Prefer CDP ``DOM.setFileInputFiles`` (see ``cdp_publish.upload_video_via_cdp``).
    Opening Finder/「前往文件夹」在浏览器里搜路径不稳定且易误操作。
    """
    raise RuntimeError(
        "已禁用系统文件对话框选文件。请用 CDP DOM.setFileInputFiles 注入本机绝对路径"
        f"（目标文件: {file_path}）"
    )


def select_pack_video(pack_dir: Path) -> None:
    video = pack_dir / "video.mp4"
    if not video.is_file():
        if pack_dir.is_file() and pack_dir.suffix.lower() == ".mp4":
            video = pack_dir
        else:
            raise FileNotFoundError(f"缺少 video.mp4: {pack_dir}")
    select_local_file(video)


def set_cover_slots(covers: list[str | Path]) -> dict[str, Any]:
    """Prefer CDP path injection; AppleScript file dialog is disabled."""
    from engine.reach.cdp_client import CdpError, CdpSession, find_tab_ws
    from engine.reach.cdp_publish import _set_cover_files

    try:
        ws_url, _ = find_tab_ws(url_substr="creator.douyin.com")
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"无 CDP，无法注入封面（勿用文件对话框）: {e}",
            "slots": [],
        }
    try:
        with CdpSession(ws_url) as sess:
            try:
                sess.call("DOM.enable")
            except CdpError:
                pass
            return _set_cover_files(sess, [str(c) for c in covers])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "slots": []}


def wait_upload_form(timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = probe_page()
        if p.get("state") == STATE_FORM:
            return True
        if p.get("state") == STATE_NEED_HUMAN:
            return False
        time.sleep(2)
    return False


def fill_copy_only(title: str, body: str) -> dict[str, Any]:
    """Fill Douyin title/body and return parseable verify result. Does NOT click 发布."""
    VISION.mkdir(parents=True, exist_ok=True)
    title_one = " ".join((title or "").split())
    fill_path = VISION / "fill_once.js"
    fill_path.write_text(
        f"""
(function(){{
  const title = {json.dumps(title_one, ensure_ascii=False)};
  const body = {json.dumps(body or "", ensure_ascii=False)};
  const input = document.querySelector('input.semi-input, input[placeholder*="标题"], input[placeholder*="作品"]');
  let titleOk=false, bodyOk=false, bodyLen=0;
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
    bodyLen = (ed.innerText||'').trim().length;
    bodyOk = bodyLen > 2;
  }}
  return JSON.stringify({{titleOk, bodyOk, bodyLen}});
}})();
""",
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            "osascript",
            "-e",
            f'set js to read POSIX file "{fill_path}" as «class utf8»\n'
            'tell application "Google Chrome"\n'
            "activate\n"
            "set r to execute active tab of front window javascript js\n"
            "return r\n"
            "end tell",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (r.stdout or r.stderr or "").strip()
    try:
        # AppleScript may wrap JSON in quotes
        parsed = json.loads(raw.strip('"').replace('\\"', '"') if raw.startswith('"') else raw)
    except Exception:
        parsed = {"titleOk": False, "bodyOk": False, "raw": raw}
    return parsed if isinstance(parsed, dict) else {"titleOk": False, "bodyOk": False, "raw": raw}


def click_publish_if_allowed() -> str:
    return chrome_js(
        "(function(){var b=Array.from(document.querySelectorAll('button')).find("
        "x=>(x.innerText||'').trim()==='发布');"
        "if(!b)return 'no_btn'; if(b.disabled)return 'disabled'; b.click(); return 'clicked';})();"
    )


def fill_and_publish(title: str, body: str) -> str:
    """Legacy helper: fill then publish (prefer publish_one which gates covers)."""
    fill = fill_copy_only(title, body)
    time.sleep(0.5)
    pub = click_publish_if_allowed()
    return f"fill={json.dumps(fill, ensure_ascii=False)}; pub={pub}"


def publish_one(
    *,
    pack_dir: Path,
    title: str,
    body: str,
    open_upload: bool = True,
    data_root: Path | None = None,
    template_id: str | None = None,
    covers: list[str] | None = None,
) -> dict[str, Any]:
    """Publish via CDP file injection + vision snapshots (no OS file dialog)."""
    from engine.reach.cdp_publish import publish_via_cdp
    from engine.reach.cover_templates import resolve_cover_store_for_settings
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    root = Path(data_root) if data_root is not None else resolve_cover_store_for_settings()
    try:
        assets = require_publish_assets(
            platform="douyin",
            pack_dir=pack_dir,
            data_root=root,
            title=title,
            body=body,
            template_id=template_id,
        )
    except PublishAssetsError as e:
        return {
            "ok": False,
            "need_human": True,
            "pub_clicked": False,
            "error": str(e),
            "phase": "gate_failed",
        }

    if open_upload:
        chrome_open_url(UPLOAD_URL)
        time.sleep(2.0)

    # Prefer full CDP path (video+copy+cover+gate). Falls back to need_human if CDP down.
    pub = publish_via_cdp(
        platform="douyin",
        pack_dir=Path(pack_dir),
        data_root=root,
        title=assets["title"],
        body=assets["body"],
        template_id=template_id,
        click_publish=True,
        upload_video=True,
    )
    if pub.get("phase") == "cdp_unavailable":
        return {
            "ok": False,
            "need_human": True,
            "pub_clicked": False,
            "error": (
                "请用 --remote-debugging-port=9222 打开 Chrome，"
                "以便本机路径注入上传（不再使用系统文件对话框）"
            ),
            "phase": "cdp_unavailable",
            "result": pub,
            "assets": assets,
        }
    return {
        "ok": bool(pub.get("ok") and pub.get("pub_clicked")),
        "need_human": bool(pub.get("need_human")),
        "pub_clicked": bool(pub.get("pub_clicked")),
        "form": pub.get("phase") == "done" or pub.get("verify", {}).get("ok"),
        "fill": pub.get("fill"),
        "covers": pub.get("covers"),
        "assets": assets,
        "result": pub,
        "error": pub.get("error"),
        "phase": pub.get("phase"),
        "url": pub.get("page_url") or chrome_url(),
    }


def resolve_pack_dir(pack_dir: str | None, video_path: str | None) -> Path:
    if pack_dir:
        p = Path(pack_dir)
        if (p / "video.mp4").is_file():
            return p
        if p.is_file() and p.suffix.lower() == ".mp4":
            return p.parent
    if video_path:
        vp = Path(video_path)
        if vp.is_file():
            # prefer sibling publish_pack
            parent = vp.parent
            if (parent / "video.mp4").is_file():
                return parent
            return parent
    raise FileNotFoundError("无法解析 publish_pack / video.mp4 路径")
