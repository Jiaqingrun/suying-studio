"""G5.V auto-upload job: wait for Chrome login ready → upload one queue item.

Douyin: full click-upload path with copy+cover hard gate.
Channels/XHS: CDP fill copy+covers+gate when debugging port up; else paste card.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.reach.browser import entry_for_platform, open_chrome_profile, write_paste_card
from engine.reach.chrome_publish import (
    STATE_NEED_HUMAN,
    STATE_NEED_LOGIN,
    UPLOAD_URL,
    chrome_url,
    publish_one,
    resolve_pack_dir,
    wait_until_ready,
)

_lock = threading.Lock()
_job: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_root() -> Path:
    """Cover template store for active customer (05-品牌/封面模板)."""
    from engine.reach.cover_templates import resolve_cover_store_for_settings

    return resolve_cover_store_for_settings()


def _app_selected_cover_template_id() -> str | None:
    """Read App-selected cover template id from per-customer cover store."""
    try:
        from engine.reach.cover_templates import load_index

        idx = load_index(_data_root())
        tid = (idx.get("selected_id") or "").strip()
        return tid or None
    except Exception:
        return None


def get_status() -> dict[str, Any]:
    with _lock:
        if not _job:
            return {
                "ok": True,
                "active": False,
                "phase": "idle",
                "auto_publish": False,
                "human_in_loop": True,
            }
        return {
            "ok": True,
            "active": True,
            "auto_publish": False,
            "human_in_loop": True,
            **{k: v for k, v in _job.items() if k != "_cancel"},
        }


def cancel_job() -> dict[str, Any]:
    with _lock:
        if not _job:
            return {"ok": True, "cancelled": False, "reason": "no_job"}
        _job["_cancel"] = True
        _job["phase"] = "cancelled"
        _job["message"] = "用户取消"
        _job["updated_at"] = _now()
        return {"ok": True, "cancelled": True, "job_id": _job.get("job_id")}


def _set(**kwargs: Any) -> None:
    with _lock:
        if _job is None:
            return
        _job.update(kwargs)
        _job["updated_at"] = _now()


def _cancelled() -> bool:
    with _lock:
        return bool(_job and _job.get("_cancel"))


def start_job(
    *,
    chrome_profile: str,
    queue_id: int,
    customer_id: int,
    platform: str,
    title: str,
    body: str,
    video_path: str,
    pack_dir: str | None,
    accept_risk: bool,
    timeout_sec: float = 300,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not accept_risk:
        raise ValueError("须 accept_risk=true（G5.V 风控自负）")
    plat = (platform or "douyin").strip().lower()
    entry = entry_for_platform(plat)
    with _lock:
        global _job
        if _job and _job.get("phase") in ("waiting_login", "uploading", "starting"):
            raise ValueError(f"已有自动上传任务进行中（phase={_job.get('phase')}）")
        job_id = uuid.uuid4().hex[:12]
        _job = {
            "job_id": job_id,
            "phase": "starting",
            "chrome_profile": chrome_profile,
            "queue_id": queue_id,
            "customer_id": customer_id,
            "platform": plat,
            "title": title,
            "message": f"正在打开 {entry['label']}…",
            "probe": None,
            "result": None,
            "error": None,
            "need_human": False,
            "dry_run": dry_run,
            "timeout_sec": timeout_sec,
            "created_at": _now(),
            "updated_at": _now(),
            "_cancel": False,
            "disclaimer": (
                "等待登录就绪后处理当前队列一项；验证码/登录须人过；"
                "文案+封面槽位齐套才允许点发；不做矩阵连发。"
            ),
        }

    t = threading.Thread(
        target=_run_job,
        kwargs={
            "chrome_profile": chrome_profile,
            "queue_id": queue_id,
            "platform": plat,
            "title": title,
            "body": body,
            "video_path": video_path,
            "pack_dir": pack_dir,
            "timeout_sec": timeout_sec,
            "dry_run": dry_run,
        },
        daemon=True,
        name=f"reach-auto-upload-{job_id}",
    )
    t.start()
    return get_status()


def _wait_generic_login(*, host_hint: str, timeout_sec: float) -> dict[str, Any]:
    """Poll Chrome URL until not on an obvious login wall (non-Douyin)."""
    import time

    deadline = time.time() + timeout_sec
    last_url = ""
    while time.time() < deadline:
        if _cancelled():
            return {"ok": False, "reason": "cancelled", "url": last_url}
        last_url = chrome_url()
        low = (last_url or "").lower()
        if last_url.startswith("ERR:"):
            _set(message="页面探测失败（检查 Chrome Apple 事件 JavaScript）", probe={"url": last_url})
            time.sleep(2)
            continue
        if any(x in low for x in ("passport", "login", "sso", "accounts.")):
            _set(message="等待你在 Chrome 中登录…", probe={"url": last_url, "state": STATE_NEED_LOGIN})
            time.sleep(2)
            continue
        if host_hint and host_hint in low:
            return {"ok": True, "reason": "ready", "url": last_url, "state": "ready"}
        if last_url.startswith("http"):
            return {"ok": True, "reason": "ready", "url": last_url, "state": "ready"}
        time.sleep(2)
    return {"ok": False, "reason": "timeout", "url": last_url}


def _mark_awaiting(queue_id: int, note: str, paste_card: str | None = None) -> None:
    from engine.catalog.db import get_session
    from engine.reach.queue import get_item, set_status

    session = get_session()
    try:
        item = get_item(session, queue_id)
        if not item:
            return
        if item.status == "queued":
            set_status(session, item, "awaiting_human", note=note)
        elif item.status == "awaiting_human" and note:
            item.note = note
            session.commit()
        _set(paste_card=paste_card)
    finally:
        session.close()


def _preflight_assets(
    *,
    platform: str,
    pack_dir: str | None,
    video_path: str,
    title: str,
    body: str,
    template_id: str | None = None,
) -> dict[str, Any]:
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    try:
        pdir = resolve_pack_dir(pack_dir, video_path)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    try:
        assets = require_publish_assets(
            platform=platform,
            pack_dir=pdir,
            data_root=_data_root(),
            title=title,
            body=body,
            template_id=template_id or _app_selected_cover_template_id(),
        )
        return {"ok": True, "assets": assets, "pack_dir": pdir}
    except PublishAssetsError as e:
        return {"ok": False, "error": str(e), "pack_dir": pdir}


def _finish_queue(queue_id: int, pub: dict[str, Any], note: str) -> None:
    from engine.catalog.db import get_session
    from engine.reach.queue import get_item, set_status

    session = get_session()
    try:
        item = get_item(session, queue_id)
        if not item:
            _set(phase="done", result=pub, message="上传流程结束（队列项未找到）")
            return
        if item.status == "queued":
            set_status(session, item, "awaiting_human", note=note)
            item = get_item(session, queue_id) or item
        if pub.get("ok") or pub.get("pub_clicked"):
            if item.status != "published":
                set_status(session, item, "published", note=note)
            _set(
                phase="done",
                result=pub,
                message="已自动点击发布（请在 Chrome 确认是否成功）",
                marked_published=True,
            )
        else:
            _set(
                phase="awaiting_confirm",
                result=pub,
                message=pub.get("error") or "已尝试上传/填表；请人工确认并点「我已发布」",
                marked_published=False,
                need_human=bool(pub.get("need_human")),
            )
    finally:
        session.close()


def _wait_cdp_ready(timeout: float = 45) -> bool:
    import time
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cancelled():
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2) as r:
                if r.status == 200:
                    with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=2) as r2:
                        tabs = json.loads(r2.read().decode())
                        if any(t.get("type") == "page" for t in tabs):
                            return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _cdp_navigate_upload(platform: str, url: str) -> str:
    """Force the CDP tab onto the platform upload URL (session restore often lands on home)."""
    try:
        from engine.reach.cdp_client import CdpSession, find_tab_ws
        from engine.reach.cdp_publish import PLATFORM_URL_HINT

        hint = PLATFORM_URL_HINT.get(platform) or platform
        ws, cur = find_tab_ws(url_substr=hint, cdp_http="http://127.0.0.1:9222")
        with CdpSession(ws) as sess:
            sess.call("Page.enable")
            if url and url not in (cur or ""):
                sess.call("Page.navigate", {"url": url})
                import time

                time.sleep(3.0)
            return cur or url
    except Exception as e:  # noqa: BLE001
        return f"nav_err:{e}"


def _run_cdp_publish(
    *,
    platform: str,
    pack_dir: Path,
    title: str,
    body: str,
    template_id: str | None,
) -> dict[str, Any]:
    from engine.reach.cdp_publish import publish_via_cdp

    last: dict[str, Any] = {}
    for attempt in range(2):
        try:
            last = publish_via_cdp(
                platform=platform,
                pack_dir=pack_dir,
                data_root=_data_root(),
                title=title,
                body=body,
                template_id=template_id,
                click_publish=True,
                upload_video=True,
                cdp_http="http://127.0.0.1:9222",
            )
            if last.get("phase") != "cdp_unavailable":
                return last
        except Exception as e:  # noqa: BLE001
            last = {
                "ok": False,
                "need_human": True,
                "error": str(e),
                "pub_clicked": False,
                "phase": "cdp_error",
            }
            if "Connection reset" in str(e) or "断开" in str(e) or "CDP" in str(e):
                import time

                _set(message=f"CDP 断开，重试 {attempt + 1}/2…")
                time.sleep(2.5)
                continue
            return last
    return last


def _quit_chrome_for_profile_switch() -> None:
    """Serial account switch: only one Chrome user-data-dir can own CDP :9222."""
    import subprocess
    import time

    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True,
        check=False,
    )
    time.sleep(1.0)
    subprocess.run(["killall", "-9", "Google Chrome"], capture_output=True, check=False)
    time.sleep(0.8)


def _run_job(
    *,
    chrome_profile: str,
    queue_id: int,
    platform: str,
    title: str,
    body: str,
    video_path: str,
    pack_dir: str | None,
    timeout_sec: float,
    dry_run: bool,
) -> None:
    try:
        import time

        from engine.reach.cdp_publish import UPLOAD_URLS

        entry = entry_for_platform(platform)
        # Prefer official upload URL from App reach map; fall back to platform home
        open_url = UPLOAD_URLS.get(platform) or (
            UPLOAD_URL if platform == "douyin" else entry["url"]
        )
        template_id = _app_selected_cover_template_id()
        _set(
            message=f"使用 App 封面模板 {template_id or '(未选)'}; Chrome={chrome_profile}",
            cover_template_id=template_id,
        )

        # Ensure CDP port free for this profile
        if not dry_run:
            _quit_chrome_for_profile_switch()
        open_info = open_chrome_profile(
            chrome_profile,
            url=open_url,
            dry_run=dry_run,
            cdp_port=None if dry_run else 9222,
        )
        _set(
            phase="waiting_login",
            message=f"已打开 {entry['label']}（客户配置 {chrome_profile} + CDP）；请完成登录（如需）",
            open=open_info,
            platform=platform,
        )
        if dry_run:
            _set(
                phase="done",
                message="dry_run：已模拟打开，未真正等待/上传",
                result={"dry_run": True, "open": open_info, "platform": platform},
            )
            return

        # Give Chrome time to bind :9222 before CDP attach
        time.sleep(3.0)
        if not _wait_cdp_ready(45):
            _set(
                phase="failed",
                need_human=True,
                error="cdp_timeout",
                message="Chrome CDP :9222 未就绪（请确认已用客户配置打开并允许远程调试）",
            )
            return
        nav = _cdp_navigate_upload(platform, open_url)
        _set(message=f"CDP 已就绪，已跳转上传页（{nav[:80]}）")
        time.sleep(2.0)

        pre = _preflight_assets(
            platform=platform,
            pack_dir=pack_dir,
            video_path=video_path,
            title=title,
            body=body,
            template_id=template_id,
        )
        if not pre.get("ok"):
            _set(
                phase="failed",
                need_human=True,
                error=pre.get("error"),
                message=f"发布门禁未通过：{pre.get('error')}",
            )
            return

        assets = pre["assets"]
        pdir = pre["pack_dir"]
        title = assets["title"]
        body = assets["body"]

        cdp_platforms = ("channels", "xhs", "kuaishou", "douyin")
        if platform in cdp_platforms:
            from urllib.parse import urlparse

            host = urlparse(open_url).netloc.replace("www.", "")
            wait = _wait_generic_login(host_hint=host.split(":")[0], timeout_sec=min(timeout_sec, 90))
            if _cancelled():
                _set(phase="cancelled", message="用户取消")
                return
            if not wait.get("ok"):
                _set(
                    phase="failed" if wait.get("reason") == "timeout" else "cancelled",
                    error=str(wait.get("reason")),
                    probe=wait,
                    message="等待登录就绪超时或已取消",
                )
                return

            _set(
                phase="uploading",
                message=f"{entry['label']} 就绪，按 App 客户配置 CDP 注入视频/文案/封面…",
                probe=wait,
            )
            pub = _run_cdp_publish(
                platform=platform,
                pack_dir=Path(pdir),
                title=title,
                body=body,
                template_id=template_id,
            )

            if pub.get("phase") == "cdp_unavailable":
                from engine.catalog.db import get_session
                from engine.reach.queue import get_item

                session = get_session()
                try:
                    item = get_item(session, queue_id)
                    card = write_paste_card(item, covers=assets.get("covers")) if item else None
                finally:
                    session.close()
                _mark_awaiting(
                    queue_id,
                    note="chrome_open_awaiting_human_cdp_down",
                    paste_card=str(card) if card else None,
                )
                _set(
                    phase="awaiting_confirm",
                    result=pub,
                    message=(
                        "CDP 不可用：已写粘贴卡（含封面路径），请手动上传发布。"
                        + (f" 粘贴卡：{card}" if card else "")
                    ),
                    marked_published=False,
                )
                return

            if pub.get("phase") == "need_sms_verify":
                _set(
                    phase="need_human",
                    need_human=True,
                    result=pub,
                    error=pub.get("error"),
                    message=pub.get("error") or "已点发布，等待短信/安全验证",
                )
                return

            if pub.get("need_human") and not pub.get("pub_clicked"):
                _set(
                    phase="need_human",
                    need_human=True,
                    result=pub,
                    error=pub.get("error"),
                    message=pub.get("error") or "CDP 发布未完成，已禁止点发或需人工确认",
                )
                return

            finish_note = "app_customer_cdp_auto_upload"
            if pub.get("cover_soft_fail"):
                finish_note = "xhs_cover_soft_fail_封面待补"
            _finish_queue(queue_id, pub, note=finish_note)
            if pub.get("cover_soft_fail"):
                _set(
                    message=(
                        pub.get("cover_note")
                        or "小红书封面未设成功，已放行发布（封面待补）"
                    )
                    + "；请在 Chrome 确认是否发布成功"
                )
            return

        if platform != "douyin":
            from urllib.parse import urlparse

            host = urlparse(entry["url"]).netloc.replace("www.", "")
            wait = _wait_generic_login(host_hint=host.split(":")[0], timeout_sec=timeout_sec)
            if _cancelled():
                _set(phase="cancelled", message="用户取消")
                return
            if not wait.get("ok"):
                _set(
                    phase="failed" if wait.get("reason") == "timeout" else "cancelled",
                    error=str(wait.get("reason")),
                    probe=wait,
                    message="等待登录就绪超时或已取消",
                )
                return

            from engine.catalog.db import get_session
            from engine.reach.queue import get_item

            session = get_session()
            try:
                item = get_item(session, queue_id)
                card = write_paste_card(item, covers=assets.get("covers")) if item else None
            finally:
                session.close()
            _mark_awaiting(
                queue_id,
                note="chrome_open_awaiting_human",
                paste_card=str(card) if card else None,
            )
            _set(
                phase="awaiting_confirm",
                result={"platform": platform, "wait": wait, "assets": assets},
                message=(
                    f"{entry['label']} 已就绪。请按粘贴卡手动上传并发布（文案+封面已列齐），"
                    f"完成后点「我已发布」。"
                    + (f" 粘贴卡：{card}" if card else "")
                ),
                marked_published=False,
            )
            return

        def on_tick(probe: dict[str, Any]) -> None:
            if _cancelled():
                return
            st = probe.get("state")
            msg = {
                STATE_NEED_LOGIN: "等待你在 Chrome 中登录…",
                STATE_NEED_HUMAN: "检测到验证/风控，已停下",
                "ready": "已检测到创作者页就绪",
                "form": "检测到上传表单",
                "other": "等待页面就绪…",
                "error": "页面探测失败（检查 Chrome「允许 Apple 事件执行 JavaScript」）",
            }.get(str(st), f"状态 {st}")
            _set(probe=probe, message=msg, need_human=(st == STATE_NEED_HUMAN))

        wait = wait_until_ready(
            timeout_sec=timeout_sec,
            poll_sec=2.0,
            on_tick=on_tick,
        )
        if _cancelled():
            _set(phase="cancelled", message="用户取消")
            return
        if not wait.get("ok"):
            reason = wait.get("reason")
            if reason == "need_human" or wait.get("state") == STATE_NEED_HUMAN:
                _set(
                    phase="need_human",
                    need_human=True,
                    probe=wait,
                    message="⚠️ 需要你在 Chrome 完成验证/登录。完成后可再点「等待登录后自动上传」。",
                )
                return
            _set(
                phase="failed",
                error=f"等待登录超时或失败: {reason}",
                probe=wait,
                message="等待登录就绪超时",
            )
            return

        if _cancelled():
            _set(phase="cancelled", message="用户取消")
            return

        _set(phase="uploading", message="登录就绪，开始自动上传（文案+封面门禁）…", probe=wait)
        pub = publish_one(
            pack_dir=Path(pdir),
            title=title,
            body=body,
            open_upload=False,
            data_root=_data_root(),
            covers=assets.get("covers"),
            template_id=template_id,
        )
        if pub.get("need_human") and not pub.get("pub_clicked"):
            _set(
                phase="need_human" if pub.get("phase") != "gate_failed" else "failed",
                need_human=True,
                result=pub,
                error=pub.get("error"),
                message=pub.get("error") or "上传过程遇到验证或门禁，已停下",
            )
            return

        _finish_queue(queue_id, pub, note="app_customer_chrome_auto_upload")
    except Exception as e:  # noqa: BLE001
        _set(phase="failed", error=str(e), message=f"自动上传失败: {e}")


def pick_queue_item(session, customer_id: int, queue_id: int | None, platform: str = "douyin"):  # noqa: ANN001
    """Pick an explicit queue id or first open item for platform."""
    from engine.reach.queue import get_item, list_items

    if queue_id is not None:
        item = get_item(session, queue_id, customer_id=customer_id)
        if not item:
            raise ValueError(f"队列项不存在: {queue_id}")
        return item
    items = list_items(session, customer_id=customer_id)
    for it in items:
        if it.platform == platform and it.status in ("queued", "awaiting_human"):
            return it
    raise ValueError(f"没有可处理的 {platform} 队列项（请先从物料包入队）")
