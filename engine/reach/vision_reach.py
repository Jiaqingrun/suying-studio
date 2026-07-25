"""Screenshot + page-text recognition for G5.V publish steps.

Prefer CDP DOM + Page.captureScreenshot. Never open OS file dialogs to
"search" for local paths — inject via DOM.setFileInputFiles instead.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.reach.cdp_client import CdpSession

VISION_DIR = Path("/tmp/suying_chrome_vision")

# Heuristic page states for creator publish flows
STATE_NEED_LOGIN = "need_login"
STATE_NEED_HUMAN = "need_human"
STATE_UPLOAD = "upload"  # empty upload dropzone
STATE_UPLOADING = "uploading"
STATE_UPLOAD_FAILED = "upload_failed"
STATE_FORM = "form"  # title/body/cover editable
STATE_DONE = "done"
STATE_OTHER = "other"


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def snapshot_page(sess: CdpSession, *, tag: str = "step") -> dict[str, Any]:
    """Capture screenshot + page text, classify publish step."""
    VISION_DIR.mkdir(parents=True, exist_ok=True)
    png = VISION_DIR / f"{tag}_{_now_tag()}.png"
    try:
        sess.capture_screenshot_png(png)
    except Exception as e:  # noqa: BLE001
        png_err = str(e)
        png = None
    else:
        png_err = None

    info = (
        sess.evaluate(
            """(() => {
              function omDocs() {
                const out = [];
                const seen = new Set();
                function walk(doc) {
                  if (!doc || seen.has(doc)) return;
                  seen.add(doc);
                  out.push(doc);
                  try {
                    doc.querySelectorAll('iframe').forEach(f => {
                      try { if (f.contentDocument) walk(f.contentDocument); } catch (e) {}
                    });
                  } catch (e) {}
                }
                walk(document);
                return out;
              }
              let t = '';
              for (const d of omDocs()) {
                try { t += ((d.body && d.body.innerText) || '') + '\\n'; } catch (e) {}
              }
              t = t.slice(0, 4000);
              let hasFile = false;
              for (const d of omDocs()) {
                try { if (d.querySelector('input[type=file]')) hasFile = true; } catch (e) {}
              }
              return {
                url: location.href || '',
                title: document.title || '',
                text: t,
                docs: omDocs().length,
                hasFileInput: hasFile,
              };
            })()"""
        )
        or {}
    )
    text = str(info.get("text") or "")
    url = str(info.get("url") or "")
    state = classify_publish_state(url=url, text=text)
    return {
        "ok": True,
        "state": state,
        "url": url,
        "title": info.get("title"),
        "text_excerpt": text[:500],
        "screenshot": str(png) if png else None,
        "screenshot_error": png_err,
        "hint": action_hint(state),
    }


def classify_publish_state(*, url: str, text: str) -> str:
    low_u = (url or "").lower()
    t = text or ""
    if re.search(r"验证码|滑块|安全验证|人机验证|captcha", t, re.I) or "captcha" in low_u:
        return STATE_NEED_HUMAN
    if re.search(r"扫码登录|手机号登录|登录后免费|验证码登录|passport|sso", t) or any(
        x in low_u for x in ("passport", "login", "sso", "accounts.")
    ):
        return STATE_NEED_LOGIN
    if re.search(r"发布成功|已发布|作品已发布|发布完成", t):
        return STATE_DONE
    if re.search(r"上传失败|网络错误，请稍后|上传出错", t):
        return STATE_UPLOAD_FAILED
    if re.search(r"上传中|处理中|转码|上传进度|正在上传", t):
        return STATE_UPLOADING
    if re.search(
        r"作品描述|添加描述|填写作品|发布设置|选择封面|封面|标题|说点什么|立即发布|发布笔记|作品标题|视频描述|短标题",
        t,
    ):
        return STATE_FORM
    if re.search(r"上传视频|拖拽|点击上传|选择视频|上传文件|\+ 上传", t):
        return STATE_UPLOAD
    return STATE_OTHER


def action_hint(state: str) -> str:
    return {
        STATE_NEED_LOGIN: "请在 Chrome 完成登录后继续",
        STATE_NEED_HUMAN: "检测到验证/风控，请人工处理",
        STATE_UPLOAD: "向隐藏 file input 注入本机 video.mp4（勿开系统文件对话框浏览）",
        STATE_UPLOADING: "等待上传/转码完成",
        STATE_UPLOAD_FAILED: "页面显示上传失败 — 点「重新上传」后再次 DOM.setFileInputFiles",
        STATE_FORM: "注入封面路径 + 填写文案并回读，通过后门禁再点发布",
        STATE_DONE: "已发布成功",
        STATE_OTHER: "根据截图与文案继续探测",
    }.get(state, "继续探测")


def wait_state(
    sess: CdpSession,
    *,
    want: set[str] | str,
    timeout: float = 120,
    tag: str = "wait",
    poll: float = 2.0,
) -> dict[str, Any]:
    wanted = {want} if isinstance(want, str) else set(want)
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = snapshot_page(sess, tag=tag)
        st = last.get("state")
        if st in wanted:
            return {**last, "matched": True}
        if st in (STATE_NEED_HUMAN, STATE_NEED_LOGIN):
            return {**last, "matched": False, "stop": True}
        time.sleep(poll)
    return {**last, "matched": False, "stop": False}


_CN_FONTS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _load_cn_font(size: int):
    from PIL import ImageFont

    for path in _CN_FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except OSError:
                continue
    return ImageFont.load_default()


def render_text_patch(
    text: str,
    *,
    size: int = 28,
    fill: tuple[int, int, int] = (255, 255, 255),
    bg: tuple[int, int, int] | None = (40, 40, 40),
) -> Any:
    """Render a small RGB patch of Chinese UI text for template matching."""
    from PIL import Image, ImageDraw

    font = _load_cn_font(size)
    dummy = Image.new("RGB", (8, 8), bg or (0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = max(8, bbox[2] - bbox[0] + 10)
    h = max(8, bbox[3] - bbox[1] + 8)
    if bg is None:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((5, 2), text, fill=fill + (255,), font=font)
        return img.convert("RGB")
    img = Image.new("RGB", (w, h), bg)
    ImageDraw.Draw(img).text((5, 2), text, fill=fill, font=font)
    return img


def locate_text_in_png(
    png_path: str | Path,
    text: str,
    *,
    min_score: float = 0.55,
    scales: tuple[float, ...] | None = None,
    search_region: tuple[int, int, int, int] | None = None,
    light_on_dark: bool = True,
    prefer_red: bool = False,
) -> dict[str, Any] | None:
    """Find UI label via rendered-text template match. Returns PNG-pixel box + center."""
    import cv2
    import numpy as np
    from PIL import Image

    path = Path(png_path)
    if not path.is_file():
        return None
    shot = Image.open(path).convert("RGB")
    arr = np.array(shot)
    if search_region:
        x0, y0, x1, y1 = search_region
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(arr.shape[1], x1), min(arr.shape[0], y1)
        hay = arr[y0:y1, x0:x1]
        ox, oy = x0, y0
    else:
        hay = arr
        ox = oy = 0
    if hay.size == 0:
        return None
    hay_bgr = cv2.cvtColor(hay, cv2.COLOR_RGB2BGR)
    variants: list[Any] = []
    if prefer_red:
        variants.append(render_text_patch(text, size=26, fill=(255, 36, 66), bg=(255, 255, 255)))
        variants.append(render_text_patch(text, size=28, fill=(255, 36, 66), bg=(250, 250, 250)))
    if light_on_dark:
        variants.append(render_text_patch(text, size=28, fill=(255, 255, 255), bg=(30, 30, 30)))
        variants.append(render_text_patch(text, size=32, fill=(255, 255, 255), bg=(50, 50, 50)))
    variants.append(render_text_patch(text, size=28, fill=(51, 51, 51), bg=(255, 255, 255)))
    variants.append(render_text_patch(text, size=24, fill=(102, 102, 102), bg=(255, 255, 255)))
    if not prefer_red:
        variants.append(render_text_patch(text, size=26, fill=(255, 36, 66), bg=(255, 255, 255)))
    scale_list = scales or (0.7, 0.85, 1.0, 1.15, 1.35, 1.55)
    best: tuple[float, int, int, int, int] | None = None
    for patch in variants:
        templ = cv2.cvtColor(np.array(patch.convert("RGB")), cv2.COLOR_RGB2BGR)
        th0, tw0 = templ.shape[:2]
        for scale in scale_list:
            tw = max(8, int(tw0 * scale))
            th = max(8, int(th0 * scale))
            if tw >= hay_bgr.shape[1] or th >= hay_bgr.shape[0]:
                continue
            resized = cv2.resize(templ, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(hay_bgr, resized, cv2.TM_CCOEFF_NORMED)
            _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
            if best is None or max_v > best[0]:
                best = (float(max_v), int(max_l[0] + ox), int(max_l[1] + oy), tw, th)
    if not best or best[0] < min_score:
        return None
    score, x, y, w, h = best
    return {
        "ok": True,
        "text": text,
        "score": score,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "cx": x + w / 2.0,
        "cy": y + h / 2.0,
    }


def locate_xhs_cover_tile_png(png_path: str | Path) -> dict[str, Any] | None:
    """Locate leftmost cover thumbnail under「设置封面」via label + portrait rect heuristic."""
    import cv2
    import numpy as np
    from PIL import Image

    path = Path(png_path)
    if not path.is_file():
        return None
    im = Image.open(path).convert("RGB")
    w, h = im.size
    label = locate_text_in_png(
        path,
        "设置封面",
        min_score=0.45,
        light_on_dark=False,
        search_region=(int(w * 0.12), int(h * 0.08), int(w * 0.70), int(h * 0.55)),
    )
    # Search band for portrait cover tiles (3:4-ish)
    if label:
        band_x0 = max(0, int(label["x"] - 20))
        band_y0 = int(label["y"] + label["h"] + 8)
        band_x1 = min(w, int(label["x"] + w * 0.42))
        band_y1 = min(h, int(band_y0 + h * 0.42))
    else:
        band_x0, band_y0 = int(w * 0.14), int(h * 0.22)
        band_x1, band_y1 = int(w * 0.48), int(h * 0.62)

    arr = np.array(im.crop((band_x0, band_y0, band_x1, band_y1)))
    if arr.size == 0:
        return None
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cands: list[tuple[float, int, int, int, int]] = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < 60 or bh < 90:
            continue
        aspect = bh / max(bw, 1)
        if aspect < 1.15 or aspect > 1.85:
            continue
        area = bw * bh
        # Prefer left-most sizable portrait tile
        score = area / 1000.0 - x * 0.02
        cands.append((score, x + band_x0, y + band_y0, bw, bh))
    if not cands:
        # Fallback: fixed left portrait slot under 设置封面
        fw, fh = int(w * 0.075), int(h * 0.22)
        fx = band_x0 + int((band_x1 - band_x0) * 0.02)
        fy = band_y0 + 12
        return {
            "ok": True,
            "method": "layout_fallback",
            "x": fx,
            "y": fy,
            "w": fw,
            "h": fh,
            "cx": fx + fw / 2.0,
            "cy": fy + fh / 2.0,
            "label": label,
        }
    cands.sort(key=lambda t: (-t[0], t[1]))
    _s, x, y, bw, bh = cands[0]
    return {
        "ok": True,
        "method": "contour",
        "x": x,
        "y": y,
        "w": bw,
        "h": bh,
        "cx": x + bw / 2.0,
        "cy": y + bh / 2.0,
        "label": label,
    }


def scale_from_png(sess: CdpSession, png_path: str | Path) -> dict[str, float]:
    """Prefer actual PNG size / CSS viewport (reliable on retina)."""
    from PIL import Image

    css = sess.screenshot_scale()
    css_w = float(css.get("css_w") or 0) or 1.0
    css_h = float(css.get("css_h") or 0) or 1.0
    try:
        with Image.open(png_path) as im:
            pw, ph = im.size
    except Exception:
        return css
    sx = (pw / css_w) if css_w else float(css.get("sx") or 2.0)
    sy = (ph / css_h) if css_h else float(css.get("sy") or 2.0)
    return {**css, "sx": sx, "sy": sy, "png_w": float(pw), "png_h": float(ph)}


def png_xy_to_css(
    png_x: float,
    png_y: float,
    *,
    scale: dict[str, float] | None = None,
    sess: CdpSession | None = None,
    png_path: str | Path | None = None,
) -> tuple[float, float]:
    """Convert screenshot pixel coords → CSS coords for Input.dispatchMouseEvent."""
    if scale is None:
        if sess is not None and png_path is not None:
            scale = scale_from_png(sess, png_path)
        elif sess is not None:
            scale = sess.screenshot_scale()
        else:
            scale = {"sx": 2.0, "sy": 2.0}
    sx = float(scale.get("sx") or 2.0) or 2.0
    sy = float(scale.get("sy") or 2.0) or 2.0
    return png_x / sx, png_y / sy


def locate_text_css(
    sess: CdpSession,
    png_path: str | Path,
    text: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    hit = locate_text_in_png(png_path, text, **kwargs)
    if not hit:
        return None
    scale = scale_from_png(sess, png_path)
    cx, cy = png_xy_to_css(hit["cx"], hit["cy"], scale=scale)
    return {**hit, "css_x": cx, "css_y": cy, "scale": scale}
