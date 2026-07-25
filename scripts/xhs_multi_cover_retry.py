#!/usr/bin/env python3
"""Multi-strategy Xiaohongshu cover upload on live CDP, then publish if visual_ok."""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

from engine.config.settings import load_settings
from engine.reach.cdp_client import CdpSession, find_tab_ws
from engine.reach.cdp_publish import (
    _cover_preview_looks_like_template,
    _stage_cover_for_cdp,
    publish_via_cdp,
)
from engine.reach.vision_reach import (
    locate_text_css,
    locate_xhs_cover_tile_png,
    png_xy_to_css,
    scale_from_png,
    snapshot_page,
)

API = "http://127.0.0.1:8766"
CDP = "http://127.0.0.1:9222"
COVER_SRC = Path.home() / "QR-Volume/速影工作区/db/cover_templates/tpl_869138b1/xhs_feed.jpg"
RESULT = Path.home() / "QR-Volume/速影工作区/db/xhs_multi_cover_retry.json"

log: list[str] = []


def L(msg: object) -> None:
    print(msg, flush=True)
    log.append(str(msg))


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as r:
        return json.loads(r.read().decode(), strict=False)


def post(path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode(), strict=False)


def main() -> None:
    try:
        post("/reach/auto-upload/cancel", {})
    except Exception as e:  # noqa: BLE001
        L(f"cancel: {e}")

    ws, url = find_tab_ws(url_substr="creator.xiaohongshu.com", cdp_http=CDP)
    L(f"attach {url[:90]}")
    sess = CdpSession(ws)
    for m in ("Page.enable", "Runtime.enable", "DOM.enable"):
        try:
            sess.call(m)
        except Exception:
            pass

    for _ in range(20):
        if sess.evaluate("!!document.body && document.body.innerText.length>50"):
            break
        time.sleep(0.5)
    time.sleep(1.2)

    inject = _stage_cover_for_cdp(str(COVER_SRC), slot_index=0)
    L(f"inject {inject}")

    def snap(tag: str):
        s = snapshot_page(sess, tag=tag)
        L(f"  snap {tag} -> {s.get('screenshot')}")
        return s

    def page_info():
        return sess.evaluate(
            """(()=>({
          hasForm: /设置封面/.test(document.body.innerText) && /发布/.test(document.body.innerText),
          hasVideo: /video\\.mp4|高清视频|检测为高清/.test(document.body.innerText),
          emptyUpload: /拖拽视频到此/.test(document.body.innerText) && !/设置封面/.test(document.body.innerText),
          err: /发生了一些错误/.test(document.body.innerText),
          modal: !![...document.querySelectorAll('div')].find(e=>{
            const t=e.innerText||'', b=e.getBoundingClientRect();
            return b.width>400 && (t.includes('模版')||t.includes('贴纸')) && t.includes('设置封面');
          }),
          hasImgInput: !![...document.querySelectorAll('input[type=file]')].find(i=>/image/.test((i.accept||'').toLowerCase())&&!/mp4/.test((i.accept||'').toLowerCase())),
          oldHint: /旧版本封面|升级新版本/.test(document.body.innerText),
        }))()"""
        )

    info = page_info()
    L(f"page {info}")

    q = get("/reach/queue")
    item = next(i for i in q["items"] if int(i["id"]) == 66)
    pack = Path(item["pack_dir"])
    title = item.get("title") or ""
    body = item.get("body") or ""
    cover_ok = False
    method_used = None

    if info.get("emptyUpload") or not info.get("hasForm"):
        L("form empty → publish_via_cdp upload+fill (no publish)")
        sess.close()
        data_root = Path(load_settings().paths.data_root)
        pub = publish_via_cdp(
            platform="xhs",
            pack_dir=pack,
            data_root=data_root,
            title=title,
            body=body,
            template_id="tpl_869138b1",
            click_publish=False,
            upload_video=True,
            cdp_http=CDP,
        )
        L(
            f"prefill phase={pub.get('phase')} covers_ok={(pub.get('covers') or {}).get('ok')} err={pub.get('error')}"
        )
        cov = pub.get("covers") or {}
        for c in (cov.get("covers") or [])[:1]:
            L(
                f"  slot method={c.get('method')} err={c.get('error')} clicks={(c.get('open_clicks') or [])[-8:]}"
            )
        if cov.get("ok"):
            cover_ok = True
            method_used = "builtin"
        ws, url = find_tab_ws(url_substr="creator.xiaohongshu.com", cdp_http=CDP)
        sess = CdpSession(ws)
        for m in ("Page.enable", "Runtime.enable", "DOM.enable"):
            try:
                sess.call(m)
            except Exception:
                pass
        info = page_info()
        L(f"page after prefill {info}")

    def dismiss_err() -> None:
        sess.evaluate(
            """(()=>{
          [...document.querySelectorAll('.d-new-toast button, .d-new-toast i, [class*=toast] button,[class*=close]')].forEach(b=>{try{b.click()}catch(e){}});
          const x=[...document.querySelectorAll('button,span,i')].find(e=>{
            const t=(e.innerText||e.getAttribute('aria-label')||'').trim();
            return t==='×'||t==='关闭';
          });
          if(x) x.click();
        })()"""
        )
        time.sleep(0.25)

    def hover_tile():
        png = snap("xhs_ms_before")["screenshot"]
        tile = locate_xhs_cover_tile_png(png)
        if not tile:
            L("no tile")
            return None, None, None
        scale = scale_from_png(sess, png)
        tx, ty = png_xy_to_css(tile["cx"], tile["cy"], scale=scale)
        sess.move_xy(tx, ty)
        time.sleep(0.12)
        sess.move_xy(tx + 2, ty + 8)
        time.sleep(0.55)
        return tile, tx, ty

    def dom_mod():
        return sess.evaluate(
            """(()=>{
          const el=[...document.querySelectorAll('div,span,button')].find(e=>{
            if((e.innerText||'').trim()!=='修改封面') return false;
            const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
            return b.width>20&&b.width<120&&b.height>8&&b.height<40&&Number(s.opacity)>0&&b.top>100;
          });
          if(!el) return null; const b=el.getBoundingClientRect();
          return {x:b.x+b.width/2,y:b.y+b.height/2};
        })()"""
        )

    def click_xy_files(x, y, path, timeout=5.0):
        try:
            return sess.click_xy_and_set_files(float(x), float(y), [path], timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def temp_show_image_input_and_click(path):
        box = sess.evaluate(
            """(()=>{
          const inp=[...document.querySelectorAll('input[type=file]')].find(i=>{
            const a=(i.accept||'').toLowerCase();
            return /image|jpg|png/.test(a) && !/mp4|video/.test(a);
          });
          if(!inp) return null;
          window.__omPrev={opacity:inp.style.opacity,position:inp.style.position,left:inp.style.left,top:inp.style.top,width:inp.style.width,height:inp.style.height,zIndex:inp.style.zIndex,pointerEvents:inp.style.pointerEvents};
          inp.style.position='fixed'; inp.style.left='42%'; inp.style.top='48%';
          inp.style.width='220px'; inp.style.height='64px'; inp.style.opacity='0.05';
          inp.style.zIndex='2147483647'; inp.style.pointerEvents='auto'; inp.style.display='block';
          try{document.body.appendChild(inp)}catch(e){}
          const b=inp.getBoundingClientRect();
          return {x:b.x+b.width/2,y:b.y+b.height/2};
        })()"""
        )
        if not box:
            return {"ok": False, "error": "no_image_input"}
        r = click_xy_files(box["x"], box["y"], path, timeout=6)
        sess.evaluate(
            """(()=>{
          const inp=[...document.querySelectorAll('input[type=file]')].find(i=>/image/.test((i.accept||'').toLowerCase()));
          const p=window.__omPrev||{};
          if(inp){ for(const k of Object.keys(p)) try{inp.style[k]=p[k]||''}catch(e){} }
        })()"""
        )
        return r

    def mouse_confirm():
        png = snap("xhs_ms_confirm")["screenshot"]
        for label in ("完成", "确定", "确认", "保存", "使用", "应用"):
            hit = locate_text_css(sess, png, label, min_score=0.5, prefer_red=True)
            if hit:
                sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
                L(f"confirm vision {label}")
                time.sleep(1.0)
                return label
        foot = sess.evaluate(
            """(()=>{
          for (const t of ['完成','确定','确认','保存']) {
            const el=[...document.querySelectorAll('button,div,span')].find(e=>(e.innerText||'').trim()===t);
            if(!el) continue; const b=el.getBoundingClientRect();
            if(b.width>40&&b.height>22&&b.top>80) return {t,x:b.x+b.width/2,y:b.y+b.height/2};
          }
          return null;
        })()"""
        )
        if foot:
            sess.click_xy(float(foot["x"]), float(foot["y"]))
            L(f"confirm dom {foot['t']}")
            time.sleep(1.0)
            return foot["t"]
        return None

    def verify():
        s = snap("xhs_ms_verify")
        probe = _cover_preview_looks_like_template(
            sess, str(COVER_SRC), screenshot_png=s.get("screenshot")
        )
        L(
            f"verify visual_ok={probe.get('visual_ok')} dist={probe.get('visual_dist')} skin={probe.get('skin_score')}"
        )
        return probe

    def esc() -> None:
        try:
            sess.call(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
            )
            sess.call(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
            )
        except Exception:
            pass

    def strat_A() -> bool:
        L("=== A: 修改封面 → 新弹窗 → 临时显形 input + fileChooser ===")
        dismiss_err()
        tile, tx, ty = hover_tile()
        if not tile:
            return False
        mod = dom_mod()
        if not mod:
            L("A no mod")
            return False
        sess.click_xy(float(mod["x"]), float(mod["y"]))
        time.sleep(1.3)
        L(f"A after {page_info()}")
        snap("xhs_ms_A_modal")
        dismiss_err()
        r = temp_show_image_input_and_click(inject)
        L(f"A {r}")
        if not r.get("ok"):
            return False
        time.sleep(2)
        mouse_confirm()
        return bool(verify().get("visual_ok"))

    def strat_B() -> bool:
        L("=== B: 遇到问题 → 旧版本 → 修改封面 ===")
        dismiss_err()
        esc()
        time.sleep(0.4)
        tile, tx, ty = hover_tile()
        if not tile:
            return False
        hov = snap("xhs_ms_B_hover")["screenshot"]
        region = (
            max(0, int(tile["x"] - 30)),
            int(tile["y"] + tile["h"] * 0.5),
            int(tile["x"] + tile["w"] + 40),
            int(tile["y"] + tile["h"] + 80),
        )
        help_hit = locate_text_css(
            sess, hov, "遇到问题", min_score=0.35, light_on_dark=True, search_region=region
        )
        if help_hit:
            sess.click_xy(float(help_hit["css_x"]), float(help_hit["css_y"]))
            L("B 遇到问题")
        else:
            scale = scale_from_png(sess, hov)
            sess.click_xy(tx, ty + tile["h"] / max(scale["sy"], 1) * 0.35)
            L("B heur help")
        time.sleep(1.0)
        help_png = snap("xhs_ms_B_help")["screenshot"]
        old = locate_text_css(
            sess, help_png, "回到旧版本封面编辑", min_score=0.35, prefer_red=True
        ) or locate_text_css(sess, help_png, "旧版本", min_score=0.45, prefer_red=True)
        if old:
            sess.click_xy(float(old["css_x"]), float(old["css_y"]))
            L("B old editor")
            time.sleep(1.5)
        else:
            L("B no old link")
        tile, tx, ty = hover_tile()
        mod = dom_mod()
        if not mod:
            return False
        r = click_xy_files(mod["x"], mod["y"], inject, 5)
        L(f"B chooser {r}")
        if not r.get("ok"):
            sess.click_xy(float(mod["x"]), float(mod["y"]))
            time.sleep(0.4)
            r = temp_show_image_input_and_click(inject)
            L(f"B temp {r}")
        if not r.get("ok"):
            return False
        time.sleep(2)
        mouse_confirm()
        return bool(verify().get("visual_ok"))

    def strat_C() -> bool:
        L("=== C: 弹窗 OCR 上传图片/本地上传 ===")
        dismiss_err()
        tile, tx, ty = hover_tile()
        mod = dom_mod()
        if not mod:
            return False
        sess.click_xy(float(mod["x"]), float(mod["y"]))
        time.sleep(1.5)
        png = snap("xhs_ms_C_modal")["screenshot"]
        for label in ("上传图片", "本地上传", "上传封面", "添加图片", "替换"):
            hit = locate_text_css(
                sess, png, label, min_score=0.45, search_region=(350, 150, 2100, 1500)
            )
            if not hit:
                continue
            L(f"C {label} score={hit.get('score')}")
            r = click_xy_files(hit["css_x"], hit["css_y"], inject, 5)
            L(f"C {r}")
            if r.get("ok"):
                time.sleep(2)
                mouse_confirm()
                return bool(verify().get("visual_ok"))
        return False

    def strat_D() -> bool:
        L("=== D: backendNodeId 直注 ===")
        dismiss_err()
        tile, tx, ty = hover_tile()
        mod = dom_mod()
        if not mod:
            return False
        sess.click_xy(float(mod["x"]), float(mod["y"]))
        time.sleep(1.2)
        if not sess.evaluate(
            """(()=>{
          const inp=[...document.querySelectorAll('input[type=file]')].find(i=>/image/.test((i.accept||'').toLowerCase())&&!/mp4/.test((i.accept||'').toLowerCase()));
          if(!inp) return false; inp.setAttribute('data-om-xhs','1'); return true;
        })()"""
        ):
            L("D no input")
            return False
        doc = sess.call("DOM.getDocument", {"depth": -1, "pierce": True})
        q = sess.call(
            "DOM.querySelector",
            {"nodeId": doc["root"]["nodeId"], "selector": 'input[data-om-xhs="1"]'},
        )
        if not q.get("nodeId"):
            L("D query fail")
            return False
        bid = sess.call("DOM.describeNode", {"nodeId": q["nodeId"]})["node"]["backendNodeId"]
        sess.call("DOM.setFileInputFiles", {"files": [inject], "backendNodeId": int(bid)})
        sess.evaluate(
            """(()=>{ const inp=document.querySelector('input[data-om-xhs="1"]');
          if(inp){ inp.dispatchEvent(new Event('input',{bubbles:true})); inp.dispatchEvent(new Event('change',{bubbles:true})); }})()"""
        )
        L(f"D bid={bid}")
        time.sleep(2.5)
        L(f"D {page_info()}")
        mouse_confirm()
        return bool(verify().get("visual_ok"))

    def strat_E() -> bool:
        L("=== E: File+DataTransfer 注入 image input ===")
        dismiss_err()
        tile, tx, ty = hover_tile()
        mod = dom_mod()
        if mod:
            sess.click_xy(float(mod["x"]), float(mod["y"]))
            time.sleep(1.0)
        raw = Path(inject).read_bytes()
        if len(raw) > 900_000:
            L("E too big")
            return False
        b64s = base64.b64encode(raw).decode()
        ok = sess.evaluate(
            f"""(() => {{
          try {{
            const b64="{b64s}";
            const bin=atob(b64); const arr=new Uint8Array(bin.length);
            for(let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
            const file=new File([arr],"xhs_feed.jpg",{{type:"image/jpeg"}});
            const dt=new DataTransfer(); dt.items.add(file);
            let inp=[...document.querySelectorAll('input[type=file]')].find(i=>/image/.test((i.accept||'').toLowerCase())&&!/mp4/.test((i.accept||'').toLowerCase()));
            if(!inp) return {{ok:false, reason:'no_inp'}};
            inp.files=dt.files;
            inp.dispatchEvent(new Event('input',{{bubbles:true}}));
            inp.dispatchEvent(new Event('change',{{bubbles:true}}));
            return {{ok:true, n:inp.files.length, name:inp.files[0]&&inp.files[0].name}};
          }} catch(e) {{ return {{ok:false, reason:String(e)}}; }}
        }})()"""
        )
        L(f"E {ok}")
        if not (isinstance(ok, dict) and ok.get("ok")):
            return False
        time.sleep(2)
        mouse_confirm()
        return bool(verify().get("visual_ok"))

    def strat_F() -> bool:
        L("=== F: 升级新版本 / 旧版链接 / 上传 ===")
        dismiss_err()
        esc()
        time.sleep(0.3)
        tile, tx, ty = hover_tile()
        upg = sess.evaluate(
            """(()=>{
          const el=[...document.querySelectorAll('div,span,a,p')].find(e=>(e.innerText||'').trim()==='升级新版本');
          if(!el) return null; const b=el.getBoundingClientRect();
          return {x:b.x+b.width/2,y:b.y+b.height/2,w:b.width,h:b.height};
        })()"""
        )
        L(f"F upgrade {upg}")
        if upg and upg.get("w", 0) > 5:
            sess.click_xy(float(upg["x"]), float(upg["y"]))
            time.sleep(1.5)
        else:
            mod = dom_mod()
            if not mod:
                return False
            sess.click_xy(float(mod["x"]), float(mod["y"]))
            time.sleep(1.5)
        png = snap("xhs_ms_F")["screenshot"]
        for label in ("回到旧版本封面编辑", "回到旧版本", "上传图片", "本地上传"):
            hit = locate_text_css(
                sess, png, label, min_score=0.4, prefer_red=("旧版" in label)
            )
            if not hit:
                continue
            L(f"F hit {label}")
            if "旧版" in label:
                sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
                time.sleep(1.5)
                tile, tx, ty = hover_tile()
                mod = dom_mod()
                if mod:
                    r = click_xy_files(mod["x"], mod["y"], inject, 5)
                    if not r.get("ok"):
                        sess.click_xy(float(mod["x"]), float(mod["y"]))
                        time.sleep(0.35)
                        r = temp_show_image_input_and_click(inject)
                    L(f"F after old {r}")
                    if r.get("ok"):
                        time.sleep(2)
                        mouse_confirm()
                        return bool(verify().get("visual_ok"))
            else:
                r = click_xy_files(hit["css_x"], hit["css_y"], inject, 5)
                L(f"F upload {r}")
                if r.get("ok"):
                    time.sleep(2)
                    mouse_confirm()
                    return bool(verify().get("visual_ok"))
        r = temp_show_image_input_and_click(inject)
        L(f"F temp {r}")
        if r.get("ok"):
            time.sleep(2)
            mouse_confirm()
            return bool(verify().get("visual_ok"))
        return False

    if not cover_ok:
        for name, fn in [
            ("A", strat_A),
            ("B", strat_B),
            ("C", strat_C),
            ("D", strat_D),
            ("E", strat_E),
            ("F", strat_F),
        ]:
            try:
                if fn():
                    cover_ok = True
                    method_used = name
                    L(f"*** COVER OK via {name} ***")
                    break
                L(f"--- {name} failed ---")
            except Exception as e:  # noqa: BLE001
                L(f"--- {name} exception: {e} ---")
            esc()
            dismiss_err()
            time.sleep(0.4)

    pub_clicked = False
    if cover_ok:
        L("=== Publish ===")
        png = snap("xhs_ms_pre_pub")["screenshot"]
        hit = locate_text_css(sess, png, "发布", min_score=0.45, prefer_red=True) or locate_text_css(
            sess, png, "发布", min_score=0.5
        )
        if hit:
            sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
            L("clicked 发布")
            time.sleep(2.5)
            mouse_confirm()
            time.sleep(2)
            text = sess.evaluate('(document.body.innerText||"").slice(0,250)') or ""
            L(f"after {text[:250]}")
            pub_clicked = True
            try:
                post(
                    "/reach/queue/66/status",
                    {"status": "published", "note": f"xhs_multi_cover_ok:{method_used}"},
                )
            except Exception as e:  # noqa: BLE001
                L(f"mark {e}")
        else:
            L("no 发布 btn")
    else:
        L("ALL strategies failed — no publish")

    out = {
        "ok": bool(cover_ok and pub_clicked),
        "cover_ok": cover_ok,
        "method": method_used,
        "pub_clicked": pub_clicked,
        "log": log,
    }
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    L(f"wrote {RESULT}")
    sess.close()
    print("DONE", {k: out[k] for k in ("ok", "cover_ok", "method", "pub_clicked")})


if __name__ == "__main__":
    main()
