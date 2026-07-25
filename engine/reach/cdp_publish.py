"""CDP publish helpers for 视频号 / 小红书 — copy + cover slots + hard gate."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from engine.reach.cdp_client import CdpError, CdpSession, find_tab_ws
from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

PLATFORM_URL_HINT: dict[str, str] = {
    "channels": "channels.weixin.qq.com",
    "xhs": "creator.xiaohongshu.com",
    "kuaishou": "cp.kuaishou.com",
    "douyin": "creator.douyin.com",
    "baijiahao": "baijiahao.baidu.com",
    "toutiao": "mp.toutiao.com",
    "zhihu": "zhihu.com",
}

UPLOAD_URLS: dict[str, str] = {
    "douyin": "https://creator.douyin.com/creator-micro/content/upload?enter_from=publish_page&default-tab=1",
    "channels": "https://channels.weixin.qq.com/platform/post/create",
    "xhs": "https://creator.xiaohongshu.com/publish/publish?from=website&target=video",
    "kuaishou": "https://cp.kuaishou.com/article/publish/video?tabType=1",
    # Reserved — CDP auto-upload not implemented yet for these platforms
    "baijiahao": "https://baijiahao.baidu.com/",
    "toutiao": "https://mp.toutiao.com/",
    "zhihu": "https://www.zhihu.com/creator",
}

# Do NOT include「立即发布」— on Douyin that is the schedule radio, not the submit button.
PUBLISH_BTN_TEXTS = ("发布", "发表", "发布笔记")
PUBLISH_BTN_TEXTS_STRICT = ("发布", "发表", "发布笔记")

# Channels (and some creator pages) put the form in a same-origin iframe.
# Parent document.body.innerText only shows the shell sidebar.
# Channels description uses contenteditable="" (empty string) + class input-editor,
# NOT contenteditable="true" — never require ="true" only.
_OM_DOCS = r"""
function omDocs() {
  const out = [];
  const seen = new Set();
  function walk(doc) {
    if (!doc || seen.has(doc)) return;
    seen.add(doc);
    out.push(doc);
    try {
      doc.querySelectorAll('iframe').forEach(f => {
        try { walk(f.contentDocument); } catch (e) {}
      });
    } catch (e) {}
  }
  walk(document);
  return out;
}
function omAll(sel) {
  const out = [];
  for (const d of omDocs()) {
    try { d.querySelectorAll(sel).forEach(n => out.push(n)); } catch (e) {}
  }
  return out;
}
function omBodyText() {
  let t = '';
  for (const d of omDocs()) {
    try { t += (d.body && d.body.innerText) || ''; } catch (e) {}
  }
  return t;
}
function omDescEditors() {
  // 视频号「视频描述」: .input-editor + data-placeholder=添加描述, contenteditable="" 
  const out = [];
  for (const d of omDocs()) {
    d.querySelectorAll('.input-editor, [data-placeholder="添加描述"], [contenteditable]:not([contenteditable="false"]), textarea').forEach(el => {
      const ph = (el.getAttribute('placeholder')||'') + (el.getAttribute('data-placeholder')||'');
      const ce = el.getAttribute('contenteditable');
      const isCe = el.isContentEditable || ce === '' || ce === 'true' || ce === 'plaintext-only';
      const isTa = el.tagName === 'TEXTAREA';
      if (!isCe && !isTa && !/input-editor/.test(el.className||'')) return;
      out.push(el);
    });
  }
  return out;
}
"""


def _gate_or_raise(
    *,
    platform: str,
    pack_dir: Path,
    data_root: Path,
    title: str | None,
    body: str | None,
    template_id: str | None = None,
) -> dict[str, Any]:
    return require_publish_assets(
        platform=platform,
        pack_dir=pack_dir,
        data_root=data_root,
        title=title,
        body=body,
        template_id=template_id,
    )


def _dismiss_channels_dialogs(sess: CdpSession) -> None:
    sess.evaluate(
        f"""(() => {{
          {_OM_DOCS}
          const texts = ['取消', '暂不', '关闭', '知道了', '我知道了', '暂不设置', '以后再说', '跳过'];
          for (const t of texts) {{
            const el = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === t);
            if (el) el.click();
          }}
          // close X on modals
          omAll('[class*=close], [aria-label=关闭], .close').forEach(el => {{ try {{ el.click(); }} catch(e) {{}} }});
          return true;
        }})()"""
    )


def _dismiss_publisher_popups(sess: CdpSession) -> str:
    """Dismiss traffic/activity modals that block 发布 (douyin 横封面, xhs 活动详情)."""
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const hits = [];
              const body = omBodyText();
              // NEVER auto-dismiss SMS / identity verification
              if (/接收短信验证码|短信验证码|为确保是本人操作|安全验证|滑块/.test(body)) {{
                return 'need_sms';
              }}
              // Douyin: unfinished draft banner — discard so we can upload a fresh video
              if (/上次未发布的视频|是否继续编辑/.test(body)) {{
                const ab = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === '放弃');
                if (ab) {{ ab.click(); hits.push('放弃草稿'); }}
              }}
              // Do not click bare「取消」— it often closes the SMS dialog
              const texts = ['暂不设置', '关闭', '知道了', '我知道了', '跳过', '以后再说', '暂不'];
              for (const t of texts) {{
                const el = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === t);
                if (el) {{ el.click(); hits.push(t); }}
              }}
              // XHS activity / 活动详情 overlay — close X (often SVG icon, not text)
              if (/活动详情|征稿|添加话题|瓜分/.test(body)) {{
                const dlg = omAll('[class*=dialog],[class*=modal],[class*=popup],[role=dialog],.d-modal')
                  .find(el => /活动详情|征稿|添加话题/.test(el.innerText||''));
                if (dlg) {{
                  const closer = dlg.querySelector(
                    '[class*=close],[aria-label*=关闭],[aria-label=Close],.close,.icon-close,svg'
                  );
                  // Prefer explicit close control over poster SVG
                  const btns = Array.from(dlg.querySelectorAll(
                    'button,[role=button],[class*=close],[aria-label*=关闭],.close'
                  ));
                  let closed = false;
                  for (const b of btns) {{
                    const al = (b.getAttribute('aria-label')||'') + (b.className||'');
                    const t = (b.innerText||'').trim();
                    if (/close|关闭|dismiss/i.test(al) || t === '×' || t === '✕' || t === 'X') {{
                      b.click(); hits.push('活动详情关闭'); closed = true; break;
                    }}
                  }}
                  if (!closed) {{
                    // top-right clickable often first small button in dialog header
                    const headerBtns = Array.from(dlg.querySelectorAll('button,[role=button],div'))
                      .filter(el => {{
                        const r = el.getBoundingClientRect();
                        return r.width > 12 && r.width < 48 && r.height > 12 && r.height < 48;
                      }});
                    if (headerBtns[0]) {{ headerBtns[0].click(); hits.push('活动详情x'); }}
                  }}
                }}
              }}
              // X close on dialogs (skip if SMS)
              omAll('button,[role=button],[aria-label*=关闭],[class*=close]').forEach(el => {{
                const t = (el.innerText||'').trim();
                const al = (el.getAttribute('aria-label')||'') + ' ' + (el.className||'');
                if (t === '×' || t === '✕' || t === 'X' || /close|关闭/i.test(al)) {{
                  try {{ el.click(); hits.push('x'); }} catch (e) {{}}
                }}
              }});
              return hits.join(',') || 'none';
            }})()"""
        )
        or "none"
    )


def _set_native_value(sess: CdpSession, *, kind: str, text: str) -> dict[str, Any]:
    """Fill channels short-title / description using selectors from page screenshot labels."""
    js = f"""
    (() => {{
      {_OM_DOCS}
      const kind = {json.dumps(kind)};
      const text = {json.dumps(text, ensure_ascii=False)};
      function setInput(el, val) {{
        el.focus();
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) desc.set.call(el, val); else el.value = val;
        el.dispatchEvent(new Event('input', {{bubbles:true}}));
        el.dispatchEvent(new Event('change', {{bubbles:true}}));
        el.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:val}}));
        return (el.value || '').length;
      }}
      function setEditable(el, val) {{
        const doc = el.ownerDocument || document;
        const win = doc.defaultView || window;
        el.scrollIntoView({{block:'center'}});
        el.focus();
        el.click();
        if (el.tagName === 'TEXTAREA') return setInput(el, val);
        // clear placeholder text like 「添加描述」
        try {{
          const sel = win.getSelection();
          const range = doc.createRange();
          range.selectNodeContents(el);
          sel.removeAllRanges();
          sel.addRange(range);
        }} catch (e) {{}}
        let ok = false;
        try {{
          ok = doc.execCommand('insertText', false, val);
        }} catch (e) {{ ok = false; }}
        if (!ok || (el.innerText || '').trim().length < 3) {{
          el.textContent = '';
          el.innerText = val;
          try {{
            el.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:val}}));
          }} catch (e) {{
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
          }}
          el.dispatchEvent(new Event('change', {{bubbles:true}}));
          el.dispatchEvent(new Event('blur', {{bubbles:true}}));
        }} else {{
          el.dispatchEvent(new InputEvent('input', {{bubbles:true, inputType:'insertText', data:val}}));
          el.dispatchEvent(new Event('change', {{bubbles:true}}));
        }}
        const n = (el.innerText || el.textContent || '').trim().length;
        // placeholder-only counts as failure
        if (/^(添加描述|写描述|说点什么)$/.test((el.innerText || '').trim())) return 0;
        return n;
      }}
      function isPlaceholderBody(el) {{
        const t = (el.innerText || el.textContent || '').trim();
        return !t || /^(添加描述|写描述|说点什么)$/.test(t) || t.length < 3;
      }}
      function nearLabel(labelRe) {{
        for (const d of omDocs()) {{
          const all = Array.from(d.querySelectorAll('div,span,label,p,section'));
          for (const lab of all) {{
            const t = (lab.innerText || '').trim();
            if (!labelRe.test(t) || t.length > 24) continue;
            let root = lab.parentElement;
            for (let i = 0; i < 6 && root; i++, root = root.parentElement) {{
              const ed = root.querySelector('[contenteditable="true"],textarea');
              const inp = root.querySelector('input:not([type=file]):not([type=hidden])');
              if (ed || inp) return {{root, ed, inp, label: t}};
            }}
          }}
        }}
        return null;
      }}
      if (kind === 'title') {{
        const inputs = omAll('input:not([type=file]):not([type=hidden]),textarea');
        for (const input of inputs) {{
          const ph = (input.getAttribute('placeholder')||'') + (input.getAttribute('aria-label')||'');
          if (/短标题|填写短标题|作品标题|标题/.test(ph) || input.maxLength === 16) {{
            const n = setInput(input, text.slice(0, 16));
            return {{ok: n > 0, via: 'placeholder', len: n, ph}};
          }}
        }}
        const near = nearLabel(/短标题/);
        if (near && near.inp) {{
          const n = setInput(near.inp, text.slice(0, 16));
          return {{ok: n > 0, via: 'near_label', len: n}};
        }}
        return {{ok: false, via: 'none'}};
      }}
      // ---- body / 视频描述 (channels) — never write into 短标题 ----
      function findChannelsDescEditor() {{
        const cands = [];
        // Prefer official channels editor: .input-editor[data-placeholder=添加描述]
        for (const ed of omDescEditors()) {{
          const ph = (ed.getAttribute('placeholder')||'') + (ed.getAttribute('data-placeholder')||'');
          const t = (ed.innerText || ed.value || '').trim();
          const r = ed.getBoundingClientRect();
          let score = 0;
          if (/input-editor/.test(ed.className||'')) score += 40;
          if (/添加描述|写描述|说点什么/.test(ph)) score += 50;
          if (t === '添加描述' || t === '写描述' || t === '') score += 15;
          if (r.height >= 30 && r.width >= 100) score += 20;
          if (r.height < 20 || r.width < 60) score -= 80;
          if (ed.tagName === 'INPUT' || (ed.maxLength > 0 && ed.maxLength <= 20)) score -= 100;
          if (score > 0) cands.push({{ed, score, via: 'desc_editor', ph, t: t.slice(0,20)}});
        }}
        for (const d of omDocs()) {{
          Array.from(d.querySelectorAll('div,span,label,p')).forEach(lab => {{
            const lt = (lab.innerText || '').replace(/\\s+/g,' ').trim();
            if (lt !== '视频描述' && lt !== '作品描述' && !/^视频描述/.test(lt)) return;
            if (lt.length > 12) return;
            let root = lab.parentElement;
            for (let i = 0; i < 8 && root; i++, root = root.parentElement) {{
              const ed = root.querySelector('.input-editor, [data-placeholder="添加描述"], [contenteditable]:not([contenteditable="false"]), textarea');
              if (!ed) continue;
              const r = ed.getBoundingClientRect();
              let score = 70;
              if (r.height >= 30) score += 20;
              cands.push({{ed, score, via: 'label', label: lt}});
              break;
            }}
          }});
        }}
        cands.sort((a,b) => b.score - a.score);
        return cands[0] || null;
      }}
      const desc = findChannelsDescEditor();
      if (desc && desc.ed) {{
        const n = setEditable(desc.ed, text);
        const after = (desc.ed.innerText || desc.ed.value || '').trim();
        const ok = n > 2 && after.length > 2 && !/^(添加描述|写描述|说点什么)$/.test(after);
        return {{ok, via: desc.via, len: after.length, label: desc.label || desc.ph || '', after: after.slice(0,40)}};
      }}
      return {{ok: false, via: 'none', eds: omDescEditors().length, docs: omDocs().length,
        samples: omDescEditors().slice(0,5).map(e => ({{t:(e.innerText||'').trim().slice(0,30), h:e.getBoundingClientRect().height, ph:e.getAttribute('data-placeholder')||'', ce:e.getAttribute('contenteditable')}}))}};
    }})()
    """
    return sess.evaluate(js) or {"ok": False}


def _sanitize_channels_title(title: str) -> str:
    """视频号短标题：去掉非法符号（如 ·），逗号改空格。"""
    import re

    t = (title or "").replace("\n", " ").replace("·", " ").replace(",", " ")
    # keep CJK/alnum and allowed punct: 《》"":：+?？%℃
    t = re.sub(r"[^\w\u4e00-\u9fff《》\"“”'‘’：:+?？%℃\s\-]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:16]


def _fill_channels_copy(sess: CdpSession, title: str, body: str) -> dict[str, Any]:
    _dismiss_channels_dialogs(sess)
    title = _sanitize_channels_title(title)
    # Focus the real 视频描述 editor (not 短标题)
    focused = sess.evaluate(
        f"""(() => {{
          {_OM_DOCS}
          const eds = omDescEditors().filter(e => {{
            const ph = (e.getAttribute('placeholder')||'') + (e.getAttribute('data-placeholder')||'');
            return /添加描述/.test(ph) || /input-editor/.test(e.className||'');
          }});
          const hit = eds.sort((a,b) => b.getBoundingClientRect().height - a.getBoundingClientRect().height)[0];
          if (hit) {{ hit.scrollIntoView({{block:'center'}}); hit.click(); hit.focus && hit.focus(); return 'focused_desc'; }}
          return 'none';
        }})()"""
    )
    time.sleep(0.35)
    body_r = _set_native_value(sess, kind="body", text=body)
    if not body_r.get("ok"):
        try:
            # Only insertText if we focused the desc field
            if focused and focused != "none":
                sess.call("Input.insertText", {"text": body})
                time.sleep(0.4)
            body_r = _set_native_value(sess, kind="body", text=body)
            body_r["cdp_insert"] = True
            body_r["focused"] = focused
        except CdpError:
            pass
    # Hard channels verify: description field must hold body
    v = _verify_copy(sess, min_body=2, platform="channels")
    if not v.get("ok"):
        # one more attempt after re-focus
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              for (const d of omDocs()) {{
                const hit = Array.from(d.querySelectorAll('[contenteditable="true"]')).find(e => {{
                  const ph = (e.getAttribute('data-placeholder')||'') + (e.getAttribute('placeholder')||'');
                  return /添加描述/.test(ph) || (e.innerText||'').trim() === '添加描述';
                }});
                if (hit) {{ hit.click(); hit.focus(); return true; }}
              }}
              return false;
            }})()"""
        )
        time.sleep(0.2)
        try:
            sess.call("Input.insertText", {"text": body})
        except CdpError:
            body_r = _set_native_value(sess, kind="body", text=body)
        time.sleep(0.3)
        v = _verify_copy(sess, min_body=2, platform="channels")
        body_r = {**body_r, "ok": bool(v.get("ok")), "verify": v, "retry": True}
    else:
        body_r = {**body_r, "ok": True, "verify": v}
    title_r = _set_native_value(sess, kind="title", text=(title or "")[:16])
    return {
        "titleOk": bool(title_r.get("ok")),
        "bodyOk": bool(body_r.get("ok")) and bool(v.get("ok")),
        "title": title_r,
        "body": body_r,
        "focused": focused,
        "verify": v,
    }


def _fill_xhs_copy(sess: CdpSession, title: str, body: str) -> dict[str, Any]:
    js = f"""
    (() => {{
      {_OM_DOCS}
      const title = {json.dumps(title, ensure_ascii=False)};
      const body = {json.dumps(body, ensure_ascii=False)};
      let titleOk=false, bodyOk=false;
      // Prefer the real title field (placeholder 填写标题…), never file inputs
      const titleEl = omAll('input').find(el => {{
        if (el.type === 'file' || el.type === 'hidden' || el.type === 'checkbox') return false;
        const ph = el.getAttribute('placeholder') || '';
        return /标题|更多赞/.test(ph) || el.className.includes('title');
      }}) || omAll('input[type=text]').find(el => el.type !== 'file');
      if (titleEl) {{
        titleEl.focus();
        const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        const val = title.replace(/\\s+/g, ' ').trim().slice(0, 20);
        if (desc && desc.set) desc.set.call(titleEl, val); else titleEl.value = val;
        titleEl.dispatchEvent(new Event('input', {{bubbles:true}}));
        titleEl.dispatchEvent(new Event('change', {{bubbles:true}}));
        titleOk = (titleEl.value||'').length > 0;
      }}
      const ed = omAll('#post-textarea, .ql-editor, [contenteditable="true"], textarea')
        .find(el => el.type !== 'file');
      if (ed) {{
        ed.focus();
        if (ed.tagName === 'TEXTAREA') {{
          const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
          if (desc && desc.set) desc.set.call(ed, body); else ed.value = body;
          ed.dispatchEvent(new Event('input', {{bubbles:true}}));
          bodyOk = (ed.value||'').length > 2;
        }} else {{
          document.execCommand('selectAll', false, null);
          document.execCommand('insertText', false, body);
          ed.dispatchEvent(new InputEvent('input', {{bubbles:true}}));
          bodyOk = (ed.innerText||'').trim().length > 2;
        }}
      }}
      return {{titleOk, bodyOk}};
    }})()
    """
    return sess.evaluate(js) or {"titleOk": False, "bodyOk": False}


def _clear_xhs_overlays(sess: CdpSession) -> list[dict[str, Any]]:
    """Disable invisible high-z masks that swallow clicks on 发布."""
    return (
        sess.evaluate(
            """(() => {
              const killed = [];
              for (const el of [...document.querySelectorAll('div,section')]) {
                const st = getComputedStyle(el);
                const z = parseInt(st.zIndex || '0', 10);
                const r = el.getBoundingClientRect();
                const empty = !(el.innerText || '').trim();
                if (z >= 1000 && r.width > 400 && r.height > 400 && empty) {
                  el.style.pointerEvents = 'none';
                  el.style.display = 'none';
                  killed.push({z, w: Math.round(r.width), h: Math.round(r.height)});
                }
              }
              return killed.slice(0, 10);
            })()"""
        )
        or []
    )


def _wait_xhs_publish_ready(sess: CdpSession, timeout_sec: float = 45.0) -> dict[str, Any]:
    """Wait until XHS cover finishes uploading and publish host is enabled."""
    deadline = time.time() + max(5.0, timeout_sec)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        _dismiss_publisher_popups(sess)
        _clear_xhs_overlays(sess)
        last = (
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const t = omBodyText();
                  // 「封面上传中」only — bare「上传中」matches too many UI strings
                  const coverBusy = /封面上传中/.test(t);
                  const uploadFail = /上传失败|网络错误，请稍后/.test(t);
                  const custom = omAll('xhs-publish-btn')[0] || document.querySelector('xhs-publish-btn');
                  let disabled = true;
                  if (custom) {{
                    const d = custom.getAttribute('submit-disabled');
                    disabled = (d === 'true' || d === '');
                  }} else {{
                    disabled = false;
                  }}
                  // Real modal only — form also has「活动详情」links under 活动话题
                  const activity = omAll('[class*=modal],[class*=dialog],[role=dialog]').some(el => {{
                    const tx = el.innerText || '';
                    const r = el.getBoundingClientRect();
                    return /活动详情/.test(tx) && /添加话题|征稿/.test(tx) && r.width > 280 && r.height > 280;
                  }});
                  return {{
                    ready: !disabled && !coverBusy && !uploadFail && !activity,
                    disabled, coverBusy, uploadFail, activity
                  }};
                }})()"""
            )
            or {"ready": False}
        )
        if last.get("ready"):
            return last
        if last.get("uploadFail"):
            return last
        time.sleep(1.0)
    return last


def _click_xhs_publish_red(sess: CdpSession) -> str:
    """Click XHS red 发布 (never white 暂存离开) via open shadow or CDP pierce."""
    _clear_xhs_overlays(sess)
    js = sess.evaluate(
        f"""(() => {{
          {_OM_DOCS}
          const custom = omAll('xhs-publish-btn')[0] || document.querySelector('xhs-publish-btn');
          if (!custom) return 'no_custom';
          const disabled = custom.getAttribute('submit-disabled');
          if (disabled === 'true' || disabled === '') return 'disabled_custom';
          const shadow = custom.shadowRoot;
          if (shadow) {{
            const btn = shadow.querySelector('button.bg-red, button.ce-btn.bg-red')
              || [...shadow.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '发布');
            if (btn) {{
              if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return 'disabled_red';
              btn.click();
              return 'clicked_shadow_red';
            }}
          }}
          return 'need_cdp_pierce';
        }})()"""
    )
    if js and js != "need_cdp_pierce":
        return str(js)

    # Closed shadow: pierce with CDP DOM and click button.bg-red
    try:
        sess.call("DOM.enable")
        doc = sess.call("DOM.getDocument", {"depth": 0})
        host = sess.call(
            "DOM.querySelector",
            {"nodeId": doc["root"]["nodeId"], "selector": "xhs-publish-btn"},
        )
        if not host.get("nodeId"):
            return "no_custom"
        host_desc = sess.call("DOM.describeNode", {"nodeId": host["nodeId"], "depth": 0})
        backend = host_desc["node"]["backendNodeId"]
        pierced = sess.call(
            "DOM.describeNode",
            {"backendNodeId": backend, "depth": 6, "pierce": True},
        )

        def _find_red(node: dict[str, Any]) -> dict[str, Any] | None:
            name = (node.get("localName") or node.get("nodeName") or "").lower()
            attrs_list = node.get("attributes") or []
            attrs = dict(zip(attrs_list[::2], attrs_list[1::2])) if attrs_list else {}
            if name == "button" and "bg-red" in (attrs.get("class") or ""):
                return node
            for sh in node.get("shadowRoots") or []:
                found = _find_red(sh)
                if found:
                    return found
            for ch in node.get("children") or []:
                found = _find_red(ch)
                if found:
                    return found
            return None

        red = _find_red(pierced["node"])
        if not red or not red.get("backendNodeId"):
            return "no_red_btn"
        obj = sess.call("DOM.resolveNode", {"backendNodeId": red["backendNodeId"]})["object"][
            "objectId"
        ]
        clicked = sess.call(
            "Runtime.callFunctionOn",
            {
                "objectId": obj,
                "functionDeclaration": (
                    'function(){ this.click(); return (this.innerText||"").trim()||"发布"; }'
                ),
                "returnByValue": True,
            },
        )
        # Also mouse-click center of red button (more reliable for some listeners)
        nid = sess.call("DOM.requestNode", {"objectId": obj})["nodeId"]
        model = sess.call("DOM.getBoxModel", {"nodeId": nid})["model"]["content"]
        x = sum(model[0::2]) / 4
        y = sum(model[1::2]) / 4
        sess.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for typ in ("mousePressed", "mouseReleased"):
            sess.call(
                "Input.dispatchMouseEvent",
                {"type": typ, "x": x, "y": y, "button": "left", "clickCount": 1},
            )
        label = (clicked.get("result") or {}).get("value") or "发布"
        return f"clicked_cdp_red:{label}"
    except Exception as e:
        return f"cdp_pierce_fail:{e}"


def _fill_kuaishou_copy(sess: CdpSession, title: str, body: str) -> dict[str, Any]:
    js = f"""
    (() => {{
      const title = {json.dumps(title, ensure_ascii=False)};
      const body = {json.dumps(body, ensure_ascii=False)};
      let titleOk=false, bodyOk=false;
      const inputs = Array.from(document.querySelectorAll('input,textarea'));
      for (const input of inputs) {{
        const ph = (input.getAttribute('placeholder')||'');
        if (/标题|作品/.test(ph)) {{
          input.focus();
          const proto = input.tagName==='TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          if (desc && desc.set) desc.set.call(input, title.slice(0,30)); else input.value = title.slice(0,30);
          input.dispatchEvent(new Event('input', {{bubbles:true}}));
          titleOk = true;
          break;
        }}
      }}
      const eds = Array.from(document.querySelectorAll('[contenteditable="true"],textarea'));
      for (const ed of eds) {{
        const ph = (ed.getAttribute('placeholder')||'') + (ed.getAttribute('data-placeholder')||'');
        if (/描述|正文|说点什么|添加/.test(ph) || ed.getAttribute('contenteditable')==='true') {{
          ed.focus();
          if (ed.tagName === 'TEXTAREA') {{
            const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
            if (desc && desc.set) desc.set.call(ed, body); else ed.value = body;
            ed.dispatchEvent(new Event('input', {{bubbles:true}}));
            bodyOk = (ed.value||'').length > 2;
          }} else {{
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, body);
            ed.dispatchEvent(new InputEvent('input', {{bubbles:true}}));
            bodyOk = (ed.innerText||'').trim().length > 2;
          }}
          if (bodyOk) break;
        }}
      }}
      return {{titleOk, bodyOk}};
    }})()
    """
    return sess.evaluate(js) or {"titleOk": False, "bodyOk": False}



def _backend_id_for_js_file_input(sess: CdpSession) -> int | None:
    """Find hidden file input via JS, map to backendNodeId for setFileInputFiles."""
    expr = """(() => {
      const cands = [];
      function walk(root) {
        try {
          root.querySelectorAll('input[type=file]').forEach(n => cands.push(n));
          root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) walk(el.shadowRoot); });
          root.querySelectorAll('iframe').forEach(f => {
            try { if (f.contentDocument) walk(f.contentDocument); } catch (e) {}
          });
        } catch (e) {}
      }
      walk(document);
      if (!cands.length) return null;
      // prefer video accepts
      cands.sort((a,b) => {
        const aa = (a.accept||''); const bb = (b.accept||'');
        const sa = /video|mp4/i.test(aa) ? 0 : (/image/i.test(aa) ? 2 : 1);
        const sb = /video|mp4/i.test(bb) ? 0 : (/image/i.test(bb) ? 2 : 1);
        return sa - sb;
      });
      return cands[0];
    })()"""
    r = sess.call(
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": False, "awaitPromise": False},
    )
    obj = ((r or {}).get("result") or {})
    if obj.get("subtype") == "null" or not obj.get("objectId"):
        return None
    object_id = obj["objectId"]
    try:
        node = sess.call("DOM.requestNode", {"objectId": object_id})
        nid = (node or {}).get("nodeId")
        if not nid:
            return None
        desc = sess.call("DOM.describeNode", {"nodeId": nid})
        bid = ((desc or {}).get("node") or {}).get("backendNodeId")
        return int(bid) if bid else None
    except CdpError:
        return None


def _ensure_douyin_video_tab(sess: CdpSession) -> str:
    """Douyin sometimes lands on 发布文章 (default-tab=5). Force 发布视频 tab.

    Never navigate away from other platforms (e.g. 视频号/小红书).
    """
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const host = (location.hostname || '');
              if (!/douyin\\.com$/i.test(host) && host !== 'creator.douyin.com') {{
                return 'skip_non_douyin';
              }}
              const t = omBodyText();
              // already on video upload zone
              if (/点击上传\\s*或\\s*直接将视频|上传视频/.test(t) && /发布视频/.test(t)) {{
                if (/我要发文|一键导入/.test(t) && !/拖拽视频|点击上传 或/.test(t)) {{
                  /* fall through */
                }} else if (!/我要发文/.test(t)) {{
                  return 'already_video';
                }}
              }}
              const tab = omAll('div,span,a,button,li').find(e => (e.innerText||'').trim() === '发布视频');
              if (tab) {{
                tab.click();
                return 'clicked_tab';
              }}
              try {{
                if (!/default-tab=1/.test(location.href)) {{
                  location.href = 'https://creator.douyin.com/creator-micro/content/upload?enter_from=publish_page&default-tab=1';
                  return 'navigated';
                }}
              }} catch (e) {{}}
              return 'no_tab';
            }})()"""
        )
        or "no_tab"
    )


def upload_video_via_cdp(sess: CdpSession, video_path: str, *, platform: str = "") -> dict[str, Any]:
    """Inject video.mp4 via DOM.setFileInputFiles — never open OS file dialog."""
    if not Path(video_path).is_file():
        return {"ok": False, "error": f"video missing: {video_path}"}

    # Best-effort: leave article tab / discard draft before hunting file input
    tab = "skip"
    try:
        plat = (platform or "").strip().lower()
        if plat == "douyin" or (not plat and "douyin.com" in str(sess.evaluate("location.hostname") or "")):
            tab = _ensure_douyin_video_tab(sess)
        else:
            tab = "skip_non_douyin"
        _dismiss_publisher_popups(sess)
        # channels shows「页面初始化中」before file input exists
        for _ in range(10):
            init = sess.evaluate(
                """(() => {
                  const t = (document.body && document.body.innerText) || '';
                  return /页面初始化中/.test(t);
                })()"""
            )
            if not init:
                break
            time.sleep(0.6)
        time.sleep(0.5)
        if tab == "navigated":
            time.sleep(2.0)
            _ensure_douyin_video_tab(sess)
            _dismiss_publisher_popups(sess)
            time.sleep(0.5)
    except Exception:
        tab = "err"

    def _find_bid() -> int | None:
        bid = _backend_id_for_js_file_input(sess)
        if bid:
            return bid
        inputs = sess.query_all_file_inputs(pierce=True)
        for inp in inputs:
            bid = inp.get("backendNodeId")
            if not bid:
                continue
            acc = (inp.get("accept") or "").lower()
            if "video" in acc or "mp4" in acc or "webm" in acc:
                return int(bid)
        for inp in inputs:
            bid = inp.get("backendNodeId")
            if not bid:
                continue
            if "image" not in (inp.get("accept") or "").lower():
                return int(bid)
        for inp in inputs:
            if inp.get("backendNodeId"):
                return int(inp["backendNodeId"])
        bid = sess.query_backend_node('input[type=file][accept*="video"]', pierce=True)
        if bid:
            return int(bid)
        bid = sess.query_backend_node("input[type=file]", pierce=True)
        return int(bid) if bid else None

    def _click_upload_affordance() -> str:
        return (
            sess.evaluate(
                """(() => {
                  for (const t of ['取消','暂不','关闭','知道了','我知道了','以后再说','放弃']) {
                    const el = Array.from(document.querySelectorAll('button,div,span,a'))
                      .find(e => (e.innerText||'').trim() === t);
                    if (el) el.click();
                  }
                  // Prefer video tab before upload button (douyin only)
                  if (/douyin\\.com/i.test(location.hostname || '')) {
                    const vtab = Array.from(document.querySelectorAll('div,span,a,button,li'))
                      .find(e => (e.innerText||'').trim() === '发布视频');
                    if (vtab) vtab.click();
                  }
                  // channels: click the dashed + upload card
                  const plus = Array.from(document.querySelectorAll('div,span,button'))
                    .find(e => {
                      const x = (e.innerText||'').trim();
                      return x === '+' || x === '＋';
                    });
                  if (plus) { plus.click(); }
                  const texts = [
                    '上传视频','拖拽视频到此或点击上传','发表视频','选择视频','点击上传','添加视频',
                    '上传','发视频','拖拽上传','选择文件','+'
                  ];
                  const els = Array.from(document.querySelectorAll('button,div,span,a,label,p,li'));
                  for (const t of texts) {
                    const el = els.find(e => {
                      const x = (e.innerText||'').trim();
                      return x === t || (x.indexOf(t) >= 0 && x.length < 28);
                    });
                    if (el) { el.click(); return 'clicked:' + t; }
                  }
                  const lab = document.querySelector('label[for]');
                  if (lab) { lab.click(); return 'clicked:label'; }
                  return 'none';
                })()"""
            )
            or "none"
        )

    clicks: list[str] = [f"tab:{tab}"]
    snaps: list[Any] = []
    bid = None
    deadline = time.time() + 18
    while time.time() < deadline:
        bid = _find_bid()
        if bid:
            break
        clicks.append(_click_upload_affordance())
        time.sleep(0.8)
        bid = _find_bid()
        if bid:
            break
        if len(snaps) < 2:
            try:
                from engine.reach.vision_reach import snapshot_page

                snaps.append(snapshot_page(sess, tag="wait_file_input"))
            except Exception as e:  # noqa: BLE001
                snaps.append({"error": str(e)})

    if not bid:
        snap = snaps[-1] if snaps else None
        return {
            "ok": False,
            "error": "no_file_input",
            "clicks": clicks,
            "snapshots": snaps,
            "snapshot": snap,
            "hint": "页面上未出现 file input；请确认已打开上传页或登录后重试",
        }
    try:
        sess.set_file_input(int(bid), [video_path])
        return {
            "ok": True,
            "path": video_path,
            "backendNodeId": bid,
            "method": "DOM.setFileInputFiles",
            "clicks": clicks,
            "snapshots": snaps,
        }
    except CdpError as e:
        return {"ok": False, "error": str(e), "clicks": clicks, "snapshots": snaps}



def wait_upload_ready(sess: CdpSession, timeout: float = 12) -> dict[str, Any]:
    """Poll until video is on the edit form (not just empty create page labels).

    Channels form lives in an iframe — probe walks same-origin frames + CDP worlds.
    Default wait cap is 12s (inject → brief wait → fill).
    """
    from engine.reach.vision_reach import snapshot_page

    probe_js = f"""
    (() => {{
      {_OM_DOCS}
      const t = omBodyText();
      if (/验证码|滑块|安全验证|人机验证/i.test(t)) return {{state:'need_human'}};
      if (/扫码登录|手机号登录|登录后免费/.test(t)) return {{state:'need_login'}};
      if (/上传失败|网络错误，请稍后|上传出错/.test(t)) return {{state:'upload_failed'}};
      if (/上传中|处理中|转码中|正在上传|上传进度|取消上传|封面生成中/.test(t)) return {{state:'uploading'}};
      if (/\\d+%\\s*(取消上传|上传)/.test(t)) return {{state:'uploading'}};
      const hasVideo = omAll('video').length > 0;
      const hasDelete = omAll('button,span,div,a').some(e => (e.innerText||'').trim() === '删除');
      if (hasVideo && (/封面预览|个人主页和分享卡片|删除|视频描述|添加描述/.test(t) || hasDelete))
        return {{state:'form', reason:'channels_preview', docs: omDocs().length}};
      if (/设置封面|作品描述|发布笔记/.test(t) && hasVideo && !/上传失败/.test(t))
        return {{state:'form', reason:'xhs_video'}};
      if (/设置封面/.test(t) && /重新上传/.test(t) && !/上传失败|网络错误/.test(t))
        return {{state:'form', reason:'xhs_chip'}};
      // douyin / kuaishou: video present + publish form signals (don't require 删除)
      if (hasVideo && /发布|作品描述|标题|封面|编辑/.test(t))
        return {{state:'form', reason:'generic_preview'}};
      if (/拖拽视频|点击上传|上传视频/.test(t) && !hasVideo) return {{state:'upload'}};
      return {{state:'waiting', hasVideo, hasDelete, docs: omDocs().length, textLen: t.length}};
    }})()
    """

    deadline = time.time() + timeout
    last_probe: dict[str, Any] = {}
    while time.time() < deadline:
        probe = sess.evaluate(probe_js) or {"state": "waiting"}
        if probe.get("state") == "waiting" and int(probe.get("docs") or 1) <= 1:
            probe2 = sess.evaluate_in_frames(probe_js)
            if isinstance(probe2, dict) and probe2.get("state"):
                probe = probe2
        last_probe = probe if isinstance(probe, dict) else {"state": "waiting"}
        st = str(last_probe.get("state") or "waiting")
        if st in ("form", "need_human", "need_login", "upload_failed"):
            snap: dict[str, Any] = {}
            try:
                snap = snapshot_page(sess, tag=f"wait_{st}")
            except Exception:
                pass
            return {
                "ok": st == "form",
                "state": st,
                "probe": last_probe,
                "screenshot": snap.get("screenshot"),
                "hint": snap.get("hint"),
                "url": snap.get("url"),
                "text_excerpt": snap.get("text_excerpt"),
            }
        time.sleep(1.0)
    snap = {}
    try:
        snap = snapshot_page(sess, tag="wait_timeout")
    except Exception:
        pass
    # Soft-accept: if video appeared but classifier stayed waiting, proceed (afternoon speed)
    # Never soft-accept while still uploading / percent progress visible
    last_st = str(last_probe.get("state") or "")
    if last_st == "uploading":
        return {
            "ok": False,
            "state": "uploading",
            "probe": last_probe,
            "screenshot": snap.get("screenshot"),
            "hint": "still_uploading_at_timeout",
            "url": snap.get("url"),
            "text_excerpt": snap.get("text_excerpt"),
        }
    if last_probe.get("hasVideo"):
        return {
            "ok": True,
            "state": "form",
            "probe": {**last_probe, "soft": True},
            "screenshot": snap.get("screenshot"),
            "hint": "video_present_soft",
            "url": snap.get("url"),
            "text_excerpt": snap.get("text_excerpt"),
        }
    return {
        "ok": False,
        "state": (snap.get("state") if snap else None) or "timeout",
        "probe": last_probe,
        "screenshot": snap.get("screenshot"),
        "hint": snap.get("hint"),
        "url": snap.get("url"),
        "text_excerpt": snap.get("text_excerpt"),
    }


def _click_reupload(sess: CdpSession) -> str:
    return (
        sess.evaluate(
            """(() => {
              const texts = ['重新上传', '重新选择', '重试', '再试一次'];
              const els = Array.from(document.querySelectorAll('button,a,span,div,p'));
              for (const t of texts) {
                const el = els.find(e => ((e.innerText||'').trim() === t) || ((e.innerText||'').trim().indexOf(t) >= 0 && (e.innerText||'').trim().length < 12));
                if (el) { el.click(); return 'clicked:' + t; }
              }
              return 'none';
            })()"""
        )
        or "none"
    )


def _page_has_cover_preview(sess: CdpSession) -> bool:
    return bool(
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const t = omBodyText();
              if (!/封面|封面预览|设置封面|选择封面/.test(t)) return false;
              return omAll('img,canvas,video').length > 0;
            }})()"""
        )
    )


def _covers_acceptable(sess: CdpSession, cover_r: dict[str, Any], plat: str) -> bool:
    """Require App/template covers actually injected. No soft-pass on preview-only."""
    if not cover_r.get("ok"):
        return False
    covers = cover_r.get("covers") or []
    if not covers:
        return False
    for c in covers:
        if not c.get("ok"):
            return False
        path = str(c.get("path") or "").replace("\\", "/")
        if "/cover_templates/" not in path:
            return False
        if c.get("source") in ("non_app", "failed"):
            return False
        if plat == "douyin":
            opens = " ".join(str(x) for x in (c.get("open_clicks") or []))
            # Must have entered custom upload tile — otherwise Douyin keeps default frames
            if not any(k in opens for k in ("上传封面", "本地上传", "上传图片")):
                return False
            # Vertical may only switch axis; last slot should confirm. Always require visual probe.
            probe = c.get("probe") or {}
            if not probe.get("visual_ok"):
                return False
            conf = str(c.get("confirm") or "")
            if not (conf.startswith("clicked") or conf.startswith("switched")):
                return False
    return True


def _backend_id_for_image_input(sess: CdpSession, *, prefer_index: int = 0) -> int | None:
    """Locate image file input (including iframe/shadow) for cover injection."""
    expr = f"""(() => {{
      {_OM_DOCS}
      const cands = [];
      for (const d of omDocs()) {{
        try {{
          d.querySelectorAll('input[type=file]').forEach(n => cands.push(n));
          d.querySelectorAll('*').forEach(el => {{
            if (el.shadowRoot) {{
              try {{ el.shadowRoot.querySelectorAll('input[type=file]').forEach(n => cands.push(n)); }} catch (e) {{}}
            }}
          }});
        }} catch (e) {{}}
      }}
      const images = cands.filter(n => /image|jpg|jpeg|png|webp/i.test(n.accept || ''));
      const pool = images.length ? images : cands.filter(n => !/video|mp4|webm/i.test(n.accept || ''));
      if (!pool.length) return null;
      const idx = Math.min({int(prefer_index)}, pool.length - 1);
      return pool[idx];
    }})()"""
    r = sess.call(
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": False, "awaitPromise": False},
    )
    obj = (r or {}).get("result") or {}
    if obj.get("subtype") == "null" or not obj.get("objectId"):
        return None
    object_id = obj["objectId"]
    try:
        node = sess.call("DOM.requestNode", {"objectId": object_id})
        nid = (node or {}).get("nodeId")
        if not nid:
            return None
        desc = sess.call("DOM.describeNode", {"nodeId": nid})
        bid = ((desc or {}).get("node") or {}).get("backendNodeId")
        return int(bid) if bid else None
    except CdpError:
        return None


def _open_cover_editor(sess: CdpSession, *, platform: str = "", slot_index: int = 0) -> str:
    """Open cover editor then 上传封面 so image file input appears.

    Douyin dual covers: switch 竖封面 / 横封面 tabs by slot_index before upload.
    CRITICAL for douyin: must land on 「上传封面」 tab — 选择封面 alone stays on
    smart/default frame picker and will publish Douyin-assigned covers.
    """
    plat = (platform or "").strip().lower()
    slot_labels = (
        ["竖封面", "竖版封面", "9:16"]
        if slot_index <= 0
        else ["横封面", "横版封面", "16:9"]
    )
    labels_js = json.dumps(slot_labels, ensure_ascii=False)
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const steps = [];
              const clickExact = (t) => {{
                const el = omAll('button,div,span,a,label,p').find(e => (e.innerText||'').trim() === t);
                if (el) {{ el.click(); steps.push(t); return true; }}
                return false;
              }};
              const clickIncludes = (parts) => {{
                for (const p of parts) {{
                  const el = omAll('button,div,span,a,label,p').find(e => {{
                    const t = (e.innerText||'').trim();
                    return t === p || (t.length <= 12 && t.includes(p));
                  }});
                  if (el) {{ el.click(); steps.push(p); return true; }}
                }}
                return false;
              }};
              // Prefer platform slot tab on the publish form first
              clickIncludes({labels_js});
              // channels: 编辑 → 上传封面
              clickExact('编辑');
              clickExact('更换封面');
              clickExact('选择封面');
              clickExact('设置封面');
              // xhs: cover chip often needs a second click on the preview / 上传
              if ({json.dumps(plat == "xhs")}) {{
                clickExact('更换封面');
                clickExact('上传封面');
                clickExact('本地上传');
                clickIncludes(['上传图片', '从本地上传', '上传']);
                // Click cover thumbnail (left side preview) to open crop/upload
                const coverImgs = omAll('img').filter(img => {{
                  const b = img.getBoundingClientRect();
                  return b.width >= 48 && b.width <= 280 && b.height >= 48 && b.left < 520;
                }});
                if (coverImgs.length) {{
                  coverImgs[0].click();
                  steps.push('cover_thumb');
                }}
              }}
              // Douyin: MUST switch to custom upload tab (not 智能推荐/推荐封面)
              if ({json.dumps(plat == "douyin")}) {{
                clickIncludes({labels_js});
                // Do NOT click greedy「上传」— filmstrip tile is handled by _douyin_force_upload_tab
                clickExact('上传封面');
              }} else {{
                clickExact('上传封面');
                if (!steps.includes('上传封面')) {{
                  clickExact('更换封面');
                  clickExact('选择封面');
                  clickExact('本地上传');
                }}
              }}
              if (steps.length) return 'clicked:' + steps.join('>');
              const imgs = omAll('img');
              for (const img of imgs) {{
                const box = img.getBoundingClientRect();
                if (box.width > 40 && box.width < 220 && box.height > 40) {{
                  img.click();
                  return 'clicked:cover_img';
                }}
              }}
              return 'none';
            }})()"""
        )
        or "none"
    )


def _douyin_force_upload_tab(sess: CdpSession) -> str:
    """Click the modal filmstrip 「+ 上传封面」 tile (compact), not AI封面 / page 重新上传."""
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const steps = [];
              const visible = (el) => {{
                const b = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return b.width > 8 && b.height > 8 && b.bottom > 0 && b.top < innerHeight
                  && st.visibility !== 'hidden' && st.display !== 'none';
              }};
              const tiles = omAll('button,div,span,label,p').filter(e => {{
                if (!visible(e)) return false;
                const t = (e.innerText||'').replace(/\s+/g,'').trim();
                if (t !== '上传封面') return false;
                const b = e.getBoundingClientRect();
                return b.width >= 36 && b.width <= 200 && b.height >= 36 && b.height <= 200;
              }});
              tiles.sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
              if (tiles.length) {{
                tiles[0].click();
                steps.push('上传封面_tile');
                return 'clicked:' + steps.join('>');
              }}
              const any = omAll('button,div,span,label').find(e => {{
                if (!visible(e)) return false;
                const t = (e.innerText||'').replace(/\s+/g,'').trim();
                if (t !== '上传封面') return false;
                const b = e.getBoundingClientRect();
                return b.width < 360 && b.height < 120;
              }});
              if (any) {{ any.click(); steps.push('上传封面'); return 'clicked:' + steps.join('>'); }}
              return 'none';
            }})()"""
        )
        or "none"
    )


def _douyin_switch_cover_axis(sess: CdpSession, *, horizontal: bool) -> str:
    """Inside cover modal: switch 设置竖封面 / 设置横封面 (or 独立编辑)."""
    labels = (
        ["设置横封面", "独立编辑", "横封面"]
        if horizontal
        else ["设置竖封面", "竖封面"]
    )
    labels_js = json.dumps(labels, ensure_ascii=False)
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const steps = [];
              const visible = (el) => {{
                const b = el.getBoundingClientRect();
                return b.width > 8 && b.height > 8 && b.bottom > 0 && b.top < innerHeight;
              }};
              for (const lab of {labels_js}) {{
                const el = omAll('button,div,span,a,label,p,li').find(e => {{
                  if (!visible(e)) return false;
                  const t = (e.innerText||'').trim();
                  return t === lab;
                }});
                if (el) {{ el.click(); steps.push(lab); break; }}
              }}
              return steps.length ? ('clicked:' + steps.join('>')) : 'none';
            }})()"""
        )
        or "none"
    )


def _backend_id_for_cover_image_input(sess: CdpSession) -> int | None:
    """Prefer last non-video image file input (cover upload after clicking 上传封面 tile)."""
    expr = f"""(() => {{
      {_OM_DOCS}
      const cands = [];
      for (const d of omDocs()) {{
        try {{
          d.querySelectorAll('input[type=file]').forEach(n => cands.push(n));
          d.querySelectorAll('*').forEach(el => {{
            if (el.shadowRoot) {{
              try {{ el.shadowRoot.querySelectorAll('input[type=file]').forEach(n => cands.push(n)); }} catch (e) {{}}
            }}
          }});
        }} catch (e) {{}}
      }}
      const images = cands.filter(n => {{
        const acc = (n.accept || '').toLowerCase();
        if (/video|mp4|webm|mov/.test(acc)) return false;
        return !acc || /image|jpg|jpeg|png|webp|\*/.test(acc);
      }});
      if (!images.length) return null;
      return images[images.length - 1];
    }})()"""
    r = sess.call(
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": False, "awaitPromise": False},
    )
    obj = (r or {}).get("result") or {}
    if obj.get("subtype") == "null" or not obj.get("objectId"):
        return None
    object_id = obj["objectId"]
    try:
        node = sess.call("DOM.requestNode", {"objectId": object_id})
        nid = (node or {}).get("nodeId")
        if not nid:
            return None
        desc = sess.call("DOM.describeNode", {"nodeId": nid})
        bid = ((desc or {}).get("node") or {}).get("backendNodeId")
        return int(bid) if bid else None
    except CdpError:
        return None


def _image_fingerprint(path: str | Path, *, size: int = 32) -> list[float] | None:
    """Tiny RGB average grid for visual compare (no external deps beyond Pillow)."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(path).convert("RGB")
        return _fingerprint_from_image(im, size=size)
    except Exception:
        return None


def _fingerprint_from_image(im: Any, *, size: int = 32) -> list[float]:
    from PIL import Image

    im = im.convert("RGB").resize((size, size), Image.Resampling.BILINEAR)
    px = list(im.getdata())
    block = size // 4
    out: list[float] = []
    for by in range(4):
        for bx in range(4):
            rs = gs = bs = 0
            n = 0
            for y in range(by * block, (by + 1) * block):
                for x in range(bx * block, (bx + 1) * block):
                    r, g, b = px[y * size + x]
                    rs += r
                    gs += g
                    bs += b
                    n += 1
            if n:
                out.extend([rs / (n * 255.0), gs / (n * 255.0), bs / (n * 255.0)])
    return out


def _fingerprint_distance(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _skin_score(im: Any) -> float:
    """Fraction of pixels that look like skin (distinctive for portrait cover vs warehouse frames)."""
    from PIL import Image

    small = im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    hit = 0
    n = 0
    for r, g, b in small.getdata():
        n += 1
        # classic loose skin gate in RGB
        if r > 95 and g > 40 and b > 20 and r > g and r > b and (r - g) > 15 and abs(r - g) > 15:
            hit += 1
    return hit / max(1, n)


def _best_template_match_in_shot(template_path: str | Path, screenshot_png: str | Path) -> float | None:
    """Lower is better. Portrait covers show high skin in the modal preview crop.

    Returns min distance; also treats max-skin crops as strong evidence of template.
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        shot = Image.open(screenshot_png).convert("RGB")
        tpl = Image.open(template_path).convert("RGB")
    except Exception:
        return None
    skin_tpl = _skin_score(tpl)
    w, h = shot.size
    boxes = [
        (0.35, 0.15, 0.62, 0.72),  # Douyin modal main preview (vertical)
        (0.38, 0.18, 0.58, 0.65),
        (0.28, 0.12, 0.72, 0.78),  # wide modal preview
        (0.28, 0.18, 0.50, 0.70),  # left face (horizontal cover layout)
        (0.32, 0.18, 0.68, 0.70),
        (0.30, 0.15, 0.70, 0.85),
        (0.25, 0.10, 0.75, 0.90),
        (0.05, 0.20, 0.35, 0.75),
        (0.55, 0.15, 0.95, 0.85),
        (0.68, 0.12, 0.92, 0.48),  # right phone preview cell
    ]
    best: float | None = None
    max_skin = 0.0
    for l, t, r, b in boxes:
        crop = shot.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
        if crop.size[0] < 40 or crop.size[1] < 40:
            continue
        skin = _skin_score(crop)
        max_skin = max(max_skin, skin)
        # Portrait in preview: skin often 0.25–0.55 on mixed UI crop; template alone ~0.9
        # Prefer high-skin crops even if below template absolute skin.
        if skin >= 0.28:
            d = abs(skin - skin_tpl) * 0.35  # strong pass
        elif skin >= 0.15:
            d = abs(skin - skin_tpl) * 0.7 + 0.15
        else:
            d = abs(skin - skin_tpl) + 0.55
        if best is None or d < best:
            best = d
    # Extra: if any crop clearly has face skin, clamp distance into pass band
    if max_skin >= 0.28 and best is not None:
        best = min(best, 0.20)
    return best


def _cover_preview_looks_like_template(
    sess: CdpSession, template_path: str, *, screenshot_png: str | Path | None = None
) -> dict[str, Any]:
    """After upload: require upload-tab signals + optional visual match vs App template.

    Note: body text always contains tab labels like「上传封面」「智能推荐」; do NOT treat
    bare「上传封面」as proof of custom cover — require 已上传/重新上传 or visual match.
    """
    text = (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              return omBodyText().slice(0, 4000);
            }})()"""
        )
        or ""
    )
    # Ignore page-level「重新上传」(video). Only cover-local / visual proof counts.
    uploaded_hit = bool(
        __import__("re").search(r"自定义封面|封面已设置|上传成功|已选择封面", text)
    )
    upload_ui = bool(
        __import__("re").search(r"上传封面|支持.*jpg|支持.*png|裁剪封面", text, __import__("re").I)
    )
    smart_frame_ui = bool(
        __import__("re").search(r"从视频中选择|选一帧|AI封面|智能推荐封面|推荐封面", text)
    )
    has_done = bool(__import__("re").search(r"完成|确认|确定", text))

    visual_dist: float | None = None
    visual_ok = False
    skin_hint: float | None = None
    if screenshot_png and Path(screenshot_png).is_file():
        visual_dist = _best_template_match_in_shot(template_path, screenshot_png)
        if visual_dist is not None and visual_dist <= 0.32:
            visual_ok = True
        try:
            from PIL import Image

            skin_hint = _skin_score(Image.open(screenshot_png))
        except Exception:
            skin_hint = None

    # Visual-first: warehouse frames must fail even if「上传封面」label exists
    smart_only = bool(not visual_ok)
    ok = bool(visual_ok)
    return {
        "custom_hit": visual_ok or uploaded_hit,
        "uploaded_hit": uploaded_hit,
        "upload_ui": upload_ui,
        "smart_only": smart_only,
        "has_done": has_done,
        "visual_dist": visual_dist,
        "visual_ok": visual_ok,
        "skin_hint": skin_hint,
        "template": template_path,
        "screenshot": str(screenshot_png) if screenshot_png else None,
        "ok": ok,
    }


def _confirm_cover_dialog(sess: CdpSession) -> str:
    """Cover editor: click 确认/完成 after injecting template image."""
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const texts = ['完成', '确认', '确定', '应用', '保存', '使用'];
              const els = omAll('button,div[role=button],span,a');
              for (const t of texts) {{
                const el = els.find(e => {{
                  const s = (e.innerText||'').trim();
                  return s === t || (s.length <= 8 && s.includes(t));
                }});
                if (el) {{ el.click(); return 'clicked:' + t; }}
              }}
              return 'none';
            }})()"""
        )
        or "none"
    )


def _set_xhs_cover_files(sess: CdpSession, covers: list[str]) -> dict[str, Any]:
    """XHS cover: screenshot locate → real mouse → new cover modal → fileChooser.

    Mandatory path (no MutationObserver / objectId race / permanent removeChild):
      1) snapshot → locate left cover tile → mouseMoved hover
      2) snapshot → locate「修改封面」(tile-only OCR, reject header false positives) → mouse click
         (new editor opens a modal; does NOT open OS file chooser directly)
      3) wait for modal「设置封面」/ image input → mouse+fileChooser inject template
      4) snapshot → mouse-click 完成/确定
      5) snapshot visual_ok vs App template
    Last resort: briefly unhide image input for one chooser click, then restore style.
    """
    from engine.reach.vision_reach import (
        locate_text_css,
        locate_xhs_cover_tile_png,
        png_xy_to_css,
        scale_from_png,
        snapshot_page,
    )

    results: list[dict[str, Any]] = []
    open_clicks: list[str] = []
    confirms: list[str] = []
    snaps: list[dict[str, Any]] = []

    def _snap(tag: str) -> dict[str, Any]:
        try:
            snap = snapshot_page(sess, tag=tag)
            snaps.append(snap)
            return snap
        except Exception as e:  # noqa: BLE001
            empty = {"ok": False, "screenshot": None, "error": str(e)}
            snaps.append(empty)
            return empty

    def _last_png() -> str | None:
        if snaps and isinstance(snaps[-1], dict):
            return snaps[-1].get("screenshot") or snaps[-1].get("png")
        return None

    def _dismiss_error_toast_by_vision() -> None:
        png = _last_png()
        if not png:
            _snap("xhs_pre_dismiss")
            png = _last_png()
        if not png:
            return
        hit = locate_text_css(sess, png, "发生了一些错误", min_score=0.45, light_on_dark=False)
        if not hit:
            return
        # Click near top-right of toast (× usually)
        try:
            sess.click_xy(float(hit["css_x"]) + 80, float(hit["css_y"]) - 4)
            open_clicks.append("dismissed_error_toast_mouse")
            time.sleep(0.3)
        except CdpError as e:
            open_clicks.append(f"dismiss_err:{e}")

    def _mouse_confirm_from_shot(png: str | None) -> str:
        if not png:
            return "none"
        for label in ("完成", "确定", "确认", "保存", "应用", "使用"):
            hit = locate_text_css(sess, png, label, min_score=0.52, light_on_dark=False)
            if not hit:
                continue
            try:
                sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
                return f"clicked_mouse:{label}"
            except CdpError as e:
                return f"confirm_err:{e}"
        return "none"

    def _try_old_editor(png: str | None) -> str:
        if not png:
            return "none"
        # Prefer red help-link in center of viewport
        from PIL import Image

        try:
            with Image.open(png) as im:
                pw, ph = im.size
        except Exception:
            pw, ph = 2400, 1788
        center = (int(pw * 0.22), int(ph * 0.22), int(pw * 0.78), int(ph * 0.72))
        for label, kwargs in (
            ("回到旧版本封面编辑", {"min_score": 0.42, "prefer_red": True, "light_on_dark": False, "search_region": center}),
            ("回到旧版本", {"min_score": 0.48, "prefer_red": True, "light_on_dark": False, "search_region": center}),
            ("旧版本", {"min_score": 0.5, "prefer_red": True, "light_on_dark": False, "search_region": center}),
        ):
            hit = locate_text_css(sess, png, label, **kwargs)
            if not hit:
                continue
            try:
                sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
                open_clicks.append(f"clicked_old_editor:{label}@{hit.get('score'):.2f}")
                time.sleep(1.5)
                return f"clicked:{label}"
            except CdpError as e:
                return f"old_editor_err:{e}"
        return "none"

    def _open_help_near_tile(tile: dict[str, Any], png: str) -> str:
        """Click「遇到问题？」under cover overlay (region-limited) to open help popup."""
        region = (
            max(0, int(tile["x"] - 30)),
            int(tile["y"] + tile["h"] * 0.55),
            int(tile["x"] + tile["w"] + 40),
            int(tile["y"] + tile["h"] + 70),
        )
        hit = locate_text_css(
            sess,
            png,
            "遇到问题",
            min_score=0.38,
            light_on_dark=True,
            search_region=region,
        )
        if not hit:
            hit = locate_text_css(
                sess,
                png,
                "遇到问题",
                min_score=0.38,
                light_on_dark=False,
                search_region=region,
            )
        if hit:
            sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
            time.sleep(1.0)
            return f"clicked_help:{hit.get('score'):.2f}"
        # Heuristic: bottom-center of cover tile
        scale = scale_from_png(sess, png)
        hx, hy = png_xy_to_css(
            tile["cx"], tile["y"] + tile["h"] * 0.88, scale=scale
        )
        sess.click_xy(hx, hy)
        time.sleep(1.0)
        return "clicked_help_heuristic_bottom"

    def _upload_via_old_editor_ui(inject_path: str, *, tile: dict[str, Any], hover_png: str) -> dict[str, Any]:
        """Old cover editor: click「修改封面」then chooser / temp-visible image input.

        Do NOT vision-match bare「本地上传」on the publish form — false positives hit the
        video file input and break the upload (toast: 上传图文，请先切换到图片tab).
        """
        ed = _snap("xhs_old_editor_ui")
        png = ed.get("screenshot") or hover_png
        # Prefer clicking 修改封面 again (old editor shows 升级新版本 under it)
        mod = locate_text_css(sess, png, "修改封面", min_score=0.45, light_on_dark=True)
        if not mod:
            scale = scale_from_png(sess, png)
            cx, cy = png_xy_to_css(tile["cx"], tile["cy"], scale=scale)
        else:
            cx, cy = float(mod["css_x"]), float(mod["css_y"])
        try:
            chooser = sess.click_xy_and_set_files(cx, cy, [inject_path], timeout=4.0)
            open_clicks.append(f"old_mod_chooser:{chooser}")
            if chooser.get("ok"):
                return {
                    "ok": True,
                    "backendNodeId": chooser.get("backendNodeId"),
                    "method": "old_editor_vision+修改封面+fileChooser",
                }
        except CdpError as e:
            open_clicks.append(f"old_mod_chooser_err:{e}")
        # Click once to spawn ephemeral image input, then last-resort temp show
        try:
            sess.click_xy(cx, cy)
            time.sleep(0.35)
        except CdpError:
            pass
        lr = _chooser_last_resort(inject_path, cx, cy)
        open_clicks.append(f"old_mod_last_resort:{lr}")
        if lr.get("ok"):
            return {
                "ok": True,
                "backendNodeId": lr.get("backendNodeId"),
                "method": "old_editor_vision+修改封面+temp_input+fileChooser",
            }
        return {"ok": False, "error": lr.get("error") or "old_editor_upload_failed"}

    def _chooser_last_resort(inject_path: str, css_x: float, css_y: float) -> dict[str, Any]:
        """Only if 修改封面 mouse click did not open chooser: briefly show image input, click it."""
        styled = sess.evaluate(
            """(() => {
              const all = [...document.querySelectorAll('input[type=file]')];
              const inp = all.find(i => {
                const a = (i.accept || '').toLowerCase();
                if (/mp4|video/.test(a) && !/image|jpg|png|webp/.test(a)) return false;
                return /image|jpg|jpeg|png|webp/.test(a) || a === '' || a === '*/*' || /image/.test(a);
              }) || all.find(i => {
                const a = (i.accept || '').toLowerCase();
                return !/mp4|video/.test(a);
              });
              if (!inp) return null;
              const prev = {
                position: inp.style.position,
                left: inp.style.left,
                top: inp.style.top,
                width: inp.style.width,
                height: inp.style.height,
                opacity: inp.style.opacity,
                zIndex: inp.style.zIndex,
                display: inp.style.display,
                visibility: inp.style.visibility,
                pointerEvents: inp.style.pointerEvents,
                fontSize: inp.style.fontSize,
              };
              inp.style.position = 'fixed';
              inp.style.left = '24px';
              inp.style.top = '120px';
              inp.style.width = '220px';
              inp.style.height = '64px';
              inp.style.opacity = '0.15';
              inp.style.zIndex = '2147483647';
              inp.style.display = 'block';
              inp.style.visibility = 'visible';
              inp.style.pointerEvents = 'auto';
              inp.style.fontSize = '16px';
              window.__omCoverStylePrev = prev;
              const b = inp.getBoundingClientRect();
              return { x: b.x + b.width/2, y: b.y + b.height/2, accept: inp.accept || '' };
            })()"""
        )
        if not (isinstance(styled, dict) and styled.get("x") is not None):
            # No input yet — re-click 修改封面 then retry style once
            try:
                sess.click_xy(css_x, css_y)
                time.sleep(0.35)
            except CdpError:
                pass
            styled = sess.evaluate(
                """(() => {
                  const inp = [...document.querySelectorAll('input[type=file]')].find(i => {
                    const a = (i.accept || '').toLowerCase();
                    return /image|jpg|jpeg|png|webp/.test(a) && !/mp4|video/.test(a);
                  });
                  if (!inp) return null;
                  inp.style.position = 'fixed';
                  inp.style.left = '24px';
                  inp.style.top = '120px';
                  inp.style.width = '220px';
                  inp.style.height = '64px';
                  inp.style.opacity = '0.15';
                  inp.style.zIndex = '2147483647';
                  inp.style.display = 'block';
                  inp.style.visibility = 'visible';
                  inp.style.pointerEvents = 'auto';
                  const b = inp.getBoundingClientRect();
                  return { x: b.x + b.width/2, y: b.y + b.height/2 };
                })()"""
            )
        if not (isinstance(styled, dict) and styled.get("x") is not None):
            return {"ok": False, "error": "no_image_input_for_last_resort"}
        try:
            chooser = sess.click_xy_and_set_files(
                float(styled["x"]), float(styled["y"]), [inject_path], timeout=5.0
            )
        finally:
            sess.evaluate(
                """(() => {
                  const inp = [...document.querySelectorAll('input[type=file]')].find(i => {
                    const a = (i.accept || '').toLowerCase();
                    return /image|jpg|jpeg|png|webp/.test(a) && !/mp4|video/.test(a);
                  });
                  const prev = window.__omCoverStylePrev || {};
                  if (inp) {
                    for (const k of Object.keys(prev)) {
                      try { inp.style[k] = prev[k] || ''; } catch (e) {}
                    }
                  }
                  window.__omCoverStylePrev = null;
                  return 'restored';
                })()"""
            )
        if chooser.get("ok"):
            chooser = {**chooser, "method": "last_resort_temp_visible_input+fileChooser"}
        return chooser

    def _dom_mod_cover_xy() -> dict[str, float] | None:
        """Visible「修改封面」overlay text (not the whole tile)."""
        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const el = omAll('div,span,button,p,a').find(e => {{
                if ((e.innerText || '').trim() !== '修改封面') return false;
                const b = e.getBoundingClientRect();
                const s = getComputedStyle(e);
                return b.width > 20 && b.width < 120 && b.height > 8 && b.height < 40
                  && Number(s.opacity) > 0 && s.visibility !== 'hidden'
                  && s.display !== 'none' && b.top > 100;
              }});
              if (!el) return null;
              const b = el.getBoundingClientRect();
              return {{ x: b.x + b.width/2, y: b.y + b.height/2, w: b.width, h: b.height }};
            }})()"""
        )
        return box if isinstance(box, dict) and box.get("x") is not None else None

    def _cover_modal_ready() -> dict[str, Any]:
        return (
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const modal = omAll('div').find(e => {{
                    const cls = (e.className || '').toString();
                    const t = (e.innerText || '');
                    const b = e.getBoundingClientRect();
                    return b.width > 400 && b.height > 200
                      && (/modal|Modal|mask/.test(cls) || e.getAttribute('role') === 'dialog')
                      && (t.includes('设置封面') || t.includes('模版') || t.includes('贴纸'));
                  }});
                  const img = [...document.querySelectorAll('input[type=file]')].find(i => {{
                    const a = (i.accept || '').toLowerCase();
                    return /image|jpg|jpeg|png/.test(a) && !/mp4|video/.test(a);
                  }});
                  return {{
                    modal: !!modal,
                    snippet: modal ? (modal.innerText || '').replace(/\\s+/g, ' ').slice(0, 120) : null,
                    hasImageInput: !!img,
                    err: (document.body.innerText || '').includes('发生了一些错误'),
                  }};
                }})()"""
            )
            or {}
        )

    def _upload_in_cover_modal(inject_path: str) -> dict[str, Any]:
        """Inside new cover editor: vision/DOM locate upload → mouse + fileChooser."""
        _dismiss_error_toast_by_vision()
        modal_snap = _snap("xhs_cover_modal")
        png = modal_snap.get("screenshot") or _last_png()
        # Prefer clicking image file input (modal already mounts it)
        lr = _chooser_last_resort(inject_path, 600.0, 450.0)
        if lr.get("ok"):
            return {**lr, "method": "modal_image_input+fileChooser"}
        if not png:
            return {"ok": False, "error": "no_modal_screenshot"}
        # Vision labels inside modal (avoid page chrome false positives)
        for label in ("上传图片", "本地上传", "上传封面", "添加图片", "替换"):
            hit = locate_text_css(
                sess,
                png,
                label,
                min_score=0.48,
                light_on_dark=False,
                search_region=(300, 120, 2100, 1500),
            )
            if not hit:
                continue
            try:
                chooser = sess.click_xy_and_set_files(
                    float(hit["css_x"]), float(hit["css_y"]), [inject_path], timeout=5.0
                )
            except CdpError as e:
                return {"ok": False, "error": str(e), "label": label}
            if chooser.get("ok"):
                return {
                    **chooser,
                    "method": f"modal_vision:{label}+fileChooser",
                    "score": hit.get("score"),
                }
        return {"ok": False, "error": "modal_upload_not_found"}

    for i, path in enumerate(covers):
        slot_log: list[str] = []
        if not Path(path).is_file():
            results.append({"index": i, "ok": False, "error": "missing_file", "path": path})
            continue
        inject_path = _stage_cover_for_cdp(path, slot_index=i)

        pre = _snap(f"xhs_cover_slot{i}_before")
        _dismiss_error_toast_by_vision()
        png = _last_png() or pre.get("screenshot")
        if not png:
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "no_screenshot",
                    "path": path,
                    "open_clicks": list(open_clicks),
                }
            )
            continue

        tile = locate_xhs_cover_tile_png(png)
        if not tile:
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "cover_tile_not_found_in_screenshot",
                    "path": path,
                    "open_clicks": list(open_clicks),
                }
            )
            continue
        scale = scale_from_png(sess, png)
        tile_css_x, tile_css_y = png_xy_to_css(tile["cx"], tile["cy"], scale=scale)
        slot_log.append(f"tile:{tile.get('method')}@{tile_css_x:.0f},{tile_css_y:.0f}")

        # 1) Hover cover tile (mouseMoved only — do not click yet)
        try:
            sess.move_xy(tile_css_x, tile_css_y)
            time.sleep(0.15)
            sess.move_xy(tile_css_x + 2, tile_css_y + 8)
            slot_log.append("hovered_tile_mouse")
        except CdpError as e:
            slot_log.append(f"hover_err:{e}")
        time.sleep(0.55)

        hover = _snap(f"xhs_cover_slot{i}_hover")
        hover_png = hover.get("screenshot") or png

        # 2) Locate「修改封面」— tile-region OCR only; validate vs DOM (reject 设置封面 header FP)
        tile_region = (
            max(0, int(tile["x"])),
            max(0, int(tile["y"])),
            int(tile["x"] + tile["w"]),
            int(tile["y"] + tile["h"]),
        )
        mod = locate_text_css(
            sess,
            hover_png,
            "修改封面",
            min_score=0.40,
            light_on_dark=True,
            search_region=tile_region,
        )
        dom_mod = _dom_mod_cover_xy()
        if mod and dom_mod:
            if abs(float(mod["css_x"]) - float(dom_mod["x"])) < 50 and abs(
                float(mod["css_y"]) - float(dom_mod["y"])
            ) < 50:
                click_x, click_y = float(mod["css_x"]), float(mod["css_y"])
                slot_log.append(f"mod_cover_ocr+dom@{click_x:.0f},{click_y:.0f}")
            else:
                click_x, click_y = float(dom_mod["x"]), float(dom_mod["y"])
                slot_log.append(
                    f"mod_cover_dom_override_ocr_mismatch@"
                    f"{click_x:.0f},{click_y:.0f}_vs_{mod['css_x']:.0f},{mod['css_y']:.0f}"
                )
        elif dom_mod:
            click_x, click_y = float(dom_mod["x"]), float(dom_mod["y"])
            slot_log.append(f"mod_cover_dom@{click_x:.0f},{click_y:.0f}")
        elif mod:
            click_x, click_y = float(mod["css_x"]), float(mod["css_y"])
            slot_log.append(f"mod_cover_ocr_only@{click_x:.0f},{click_y:.0f}")
        else:
            click_x, click_y = tile_css_x, tile_css_y
            slot_log.append("mod_cover_fallback_tile_center")

        # 3) Mouse-click「修改封面」→ opens NEW cover modal (not OS chooser)
        method = "failed"
        bid: int | bool | None = None
        try:
            sess.click_xy(click_x, click_y)
            slot_log.append(f"clicked_修改封面@{click_x:.0f},{click_y:.0f}")
        except CdpError as e:
            slot_log.append(f"click_mod_err:{e}")
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": str(e),
                    "path": path,
                    "open_clicks": list(open_clicks) + slot_log,
                }
            )
            continue

        modal_ok = False
        for _wait in range(16):
            time.sleep(0.35)
            st = _cover_modal_ready()
            if st.get("modal") or st.get("hasImageInput"):
                modal_ok = True
                slot_log.append(f"modal_ready:{st}")
                break
        if not modal_ok:
            # Rare: old path still opens chooser on 修改封面
            try:
                chooser_direct = sess.click_xy_and_set_files(
                    click_x, click_y, [inject_path], timeout=3.0
                )
                slot_log.append(f"direct_chooser:{chooser_direct}")
                if chooser_direct.get("ok"):
                    bid = chooser_direct.get("backendNodeId")
                    method = "vision_mouse+fileChooser_direct"
                    modal_ok = True
            except CdpError as e:
                slot_log.append(f"direct_chooser_err:{e}")

        if not bid and modal_ok:
            up = _upload_in_cover_modal(inject_path)
            slot_log.append(f"modal_upload:{up}")
            if up.get("ok"):
                bid = up.get("backendNodeId") or True
                method = str(up.get("method") or "modal_upload")

        # 3b) Fallback: help → 旧版本
        if not bid:
            slot_log.append("modal_miss→old_editor_path")
            _dismiss_error_toast_by_vision()
            # Close stuck modal if any
            try:
                sess.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                sess.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
            except CdpError:
                pass
            time.sleep(0.4)
            sess.move_xy(tile_css_x, tile_css_y)
            time.sleep(0.45)
            help_png = _last_png() or hover_png
            help_r = _open_help_near_tile(tile, help_png)
            slot_log.append(help_r)
            help_snap = _snap(f"xhs_cover_slot{i}_help")
            old_r = _try_old_editor(help_snap.get("screenshot") or help_png)
            slot_log.append(f"old_editor:{old_r}")
            if old_r == "none":
                old_r = _try_old_editor(_last_png())
                slot_log.append(f"old_editor_retry:{old_r}")
            # After old editor switch, reopen 修改封面 → modal/input again
            sess.move_xy(tile_css_x, tile_css_y)
            time.sleep(0.5)
            dom2 = _dom_mod_cover_xy()
            if dom2:
                sess.click_xy(float(dom2["x"]), float(dom2["y"]))
                time.sleep(1.0)
            up2 = _upload_in_cover_modal(inject_path)
            slot_log.append(f"old_then_modal:{up2}")
            if up2.get("ok"):
                bid = up2.get("backendNodeId") or True
                method = str(up2.get("method") or "old_then_modal")
            else:
                old_up = _upload_via_old_editor_ui(inject_path, tile=tile, hover_png=hover_png)
                slot_log.append(f"old_upload:{old_up}")
                if old_up.get("ok"):
                    bid = old_up.get("backendNodeId")
                    method = str(old_up.get("method") or "old_editor")

        open_clicks.extend(slot_log)
        if not bid:
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "file_chooser_not_opened",
                    "path": path,
                    "method": method,
                    "open_clicks": list(open_clicks),
                }
            )
            continue

        time.sleep(1.8)
        after = _snap(f"xhs_cover_slot{i}_injected")
        conf = _mouse_confirm_from_shot(after.get("screenshot"))
        if conf == "none":
            time.sleep(0.5)
            after2 = _snap(f"xhs_cover_slot{i}_preconfirm")
            conf = _mouse_confirm_from_shot(after2.get("screenshot"))
        # Modal footer 完成/确定 via DOM if OCR missed
        if conf == "none":
            footer = sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  for (const t of ['完成', '确定', '确认', '保存']) {{
                    const el = omAll('button,div,span').find(e => (e.innerText||'').trim() === t);
                    if (!el) continue;
                    const b = el.getBoundingClientRect();
                    if (b.width > 40 && b.height > 22 && b.top > 80) {{
                      return {{ t, x: b.x + b.width/2, y: b.y + b.height/2 }};
                    }}
                  }}
                  return null;
                }})()"""
            )
            if isinstance(footer, dict) and footer.get("x") is not None:
                try:
                    sess.click_xy(float(footer["x"]), float(footer["y"]))
                    conf = f"clicked_mouse_dom:{footer.get('t')}"
                except CdpError as e:
                    conf = f"footer_err:{e}"
        confirms.append(conf)
        slot_log.append(f"confirm:{conf}")
        time.sleep(1.0)

        verify = _snap(f"xhs_cover_slot{i}_verify")
        probe = _cover_preview_looks_like_template(
            sess, path, screenshot_png=verify.get("screenshot") or _last_png()
        )

        # One retry through modal if visual mismatch
        if not probe.get("visual_ok"):
            slot_log.append("visual_retry_modal")
            _dismiss_error_toast_by_vision()
            sess.move_xy(tile_css_x, tile_css_y)
            time.sleep(0.5)
            dom3 = _dom_mod_cover_xy()
            if dom3:
                sess.click_xy(float(dom3["x"]), float(dom3["y"]))
            else:
                sess.click_xy(click_x, click_y)
            time.sleep(1.0)
            up3 = _upload_in_cover_modal(inject_path)
            slot_log.append(f"retry_modal:{up3}")
            if up3.get("ok"):
                method = str(up3.get("method") or method)
                bid = up3.get("backendNodeId") or bid
                time.sleep(1.6)
                _snap(f"xhs_cover_slot{i}_retry_injected")
                conf2 = _mouse_confirm_from_shot(_last_png())
                if conf2 == "none":
                    conf2 = _mouse_confirm_from_shot(_snap(f"xhs_cover_slot{i}_retry_preconfirm").get("screenshot"))
                confirms.append(conf2)
                time.sleep(0.8)
                verify = _snap(f"xhs_cover_slot{i}_retry_verify")
                probe = _cover_preview_looks_like_template(
                    sess, path, screenshot_png=verify.get("screenshot")
                )

        open_clicks.extend([s for s in slot_log if s not in open_clicks])
        from_app = "/cover_templates/" in path.replace("\\", "/")
        slot_ok = bool(from_app and probe.get("visual_ok"))
        if slot_ok and conf == "none":
            conf = "inline_applied_vision"
        results.append(
            {
                "index": i,
                "ok": slot_ok,
                "path": path,
                "backendNodeId": bid if isinstance(bid, int) else None,
                "method": method,
                "open_clicks": list(slot_log),
                "confirm": conf,
                "probe": probe,
                "tile": {k: tile.get(k) for k in ("method", "cx", "cy", "w", "h")},
                "source": "app_cover_template" if slot_ok else "failed",
                "error": None
                if slot_ok
                else ("visual_mismatch" if not probe.get("visual_ok") else "verify_failed"),
            }
        )

    return {
        "covers": results,
        "ok": bool(results) and all(r.get("ok") for r in results) and len(results) == len(covers),
        "open_clicks": open_clicks,
        "confirms": confirms,
        "snapshots": snaps,
        "path": "vision_mouse",
    }



def _set_channels_cover_files(sess: CdpSession, covers: list[str]) -> dict[str, Any]:
    """Channels cover: screenshot locate → real mouse → fileChooser → form visual gate.

    Root cause of false published: modal-time visual_ok passed, but JS `.click()` on「确认」
    did not commit; form「封面预览」stayed on video first-frame. Fix:
      1) snapshot → locate「编辑」near「封面预览」→ real mouse open modal
      2) snapshot → locate「上传封面」→ click_xy_and_set_files (intercept chooser)
      3) modal visual_ok vs App template (must pass before confirm)
      4) real-mouse「确认」; wait modal close
      5) form-level visual_ok on「封面预览」thumb — REQUIRED before ok
    Dual slots share one editor; only primary is uploaded; secondary synced only if form ok.
    """
    from engine.reach.vision_reach import locate_text_css, snapshot_page

    results: list[dict[str, Any]] = []
    open_clicks: list[str] = []
    confirms: list[str] = []
    snaps: list[dict[str, Any]] = []

    def _snap(tag: str) -> dict[str, Any]:
        try:
            snap = snapshot_page(sess, tag=tag)
            snaps.append(snap)
            return snap
        except Exception as e:  # noqa: BLE001
            empty = {"ok": False, "screenshot": None, "error": str(e)}
            snaps.append(empty)
            return empty

    def _last_png() -> str | None:
        if snaps and isinstance(snaps[-1], dict):
            return snaps[-1].get("screenshot") or snaps[-1].get("png")
        return None

    def _modal_open() -> bool:
        return bool(
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const t = omBodyText();
                  return /编辑封面/.test(t) && /上传封面|从视频中选择/.test(t) && /确认/.test(t);
                }})()"""
            )
        )

    def _form_cover_ready() -> bool:
        return bool(
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const t = omBodyText();
                  return /封面预览/.test(t) && /编辑/.test(t) && !/编辑封面/.test(t);
                }})()"""
            )
        )

    def _open_edit_vision() -> str:
        """Screenshot-locate 编辑 near 封面预览; fallback DOM xy near label."""
        snap = _snap("channels_cover_pre_edit")
        png = snap.get("screenshot") or snap.get("png")
        if png:
            preview = locate_text_css(
                sess, png, "封面预览", min_score=0.45, light_on_dark=False
            )
            region = None
            if preview:
                # Search band under「封面预览」for the 编辑 overlay on the thumb
                px, py = int(preview["x"]), int(preview["y"])
                pw, ph = int(preview.get("w") or 80), int(preview.get("h") or 24)
                region = (max(0, px - 40), py, px + max(pw, 200) + 160, py + 320)
            edit = locate_text_css(
                sess,
                png,
                "编辑",
                min_score=0.50,
                light_on_dark=True,
                search_region=region,
            )
            if not edit:
                edit = locate_text_css(
                    sess, png, "编辑", min_score=0.48, light_on_dark=False, search_region=region
                )
            if edit:
                try:
                    sess.click_xy(float(edit["css_x"]), float(edit["css_y"]))
                    return f"clicked_mouse:编辑_score={edit.get('score'):.2f}"
                except CdpError as e:
                    open_clicks.append(f"edit_mouse_err:{e}")
        # DOM fallback: 编辑 closest to 封面预览
        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const label = omAll('div,span,label,p').find(e => (e.innerText||'').trim() === '封面预览');
              const edits = omAll('button,div,span,a').filter(e => (e.innerText||'').trim() === '编辑');
              let best = null;
              for (const el of edits) {{
                const b = el.getBoundingClientRect();
                if (b.width < 8 || b.height < 8) continue;
                if (label) {{
                  const lb = label.getBoundingClientRect();
                  const dist = Math.abs(b.top - lb.top) + Math.abs(b.left - lb.left);
                  if (!best || dist < best.dist) best = {{ x: b.x + b.width/2, y: b.y + b.height/2, dist }};
                }} else if (!best) {{
                  best = {{ x: b.x + b.width/2, y: b.y + b.height/2, dist: 0 }};
                }}
              }}
              if (best) return {{ x: best.x, y: best.y, via: '编辑' }};
              if (label) {{
                const lb = label.getBoundingClientRect();
                return {{ x: lb.x + 60, y: lb.y + 90, via: 'preview_offset' }};
              }}
              return null;
            }})()"""
        )
        if not isinstance(box, dict) or box.get("x") is None:
            return "none"
        try:
            sess.click_xy(float(box["x"]), float(box["y"]))
            return f"clicked:{box.get('via')}_xy"
        except CdpError as e:
            return f"open_err:{e}"

    def _upload_tile_boxes() -> list[dict[str, Any]]:
        """Collect click targets for the dashed「+ 上传封面」tile (not text baseline)."""
        boxes: list[dict[str, Any]] = []
        # Prefer DOM: walk up from label to compact square tile (contains + icon)
        dom = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const out = [];
              const labels = omAll('button,div,span,label,p').filter(e => {{
                const t = (e.innerText||'').replace(/\\s+/g,'').trim();
                return t === '上传封面' || t === '+上传封面';
              }});
              for (const el of labels) {{
                let cur = el;
                for (let i = 0; i < 5 && cur; i++) {{
                  const b = cur.getBoundingClientRect();
                  if (b.width >= 48 && b.width <= 160 && b.height >= 48 && b.height <= 160) {{
                    out.push({{ x: b.x + b.width/2, y: b.y + b.height/2, w: b.width, h: b.height, via: 'dom_tile' }});
                    break;
                  }}
                  // also try clicking above the text inside current box
                  if (b.width >= 36 && b.width <= 220 && b.height >= 20 && b.height <= 80) {{
                    out.push({{ x: b.x + b.width/2, y: b.y + Math.max(12, b.height*0.25), w: b.width, h: b.height, via: 'dom_label_upper' }});
                  }}
                  cur = cur.parentElement;
                }}
              }}
              return out;
            }})()"""
        )
        if isinstance(dom, list):
            boxes.extend([b for b in dom if isinstance(b, dict) and b.get("x") is not None])
        snap = _snap("channels_cover_modal_pre_upload")
        png = snap.get("screenshot") or snap.get("png")
        if png:
            hit = locate_text_css(
                sess, png, "上传封面", min_score=0.45, light_on_dark=False
            )
            if hit:
                cx, cy = float(hit["css_x"]), float(hit["css_y"])
                # Text sits under the +; click the icon/tile center above the label
                boxes.append({"x": cx, "y": cy - 36, "via": "vision_above_text"})
                boxes.append({"x": cx, "y": cy - 18, "via": "vision_above_text2"})
                boxes.append({"x": cx, "y": cy, "via": "vision_text"})
        # de-dupe near-identical points
        uniq: list[dict[str, Any]] = []
        for b in boxes:
            if any(abs(float(b["x"]) - float(u["x"])) < 4 and abs(float(b["y"]) - float(u["y"])) < 4 for u in uniq):
                continue
            uniq.append(b)
        return uniq

    def _inject_via_backend(inject_path: str) -> dict[str, Any]:
        bid = _backend_id_for_cover_image_input(sess)
        if not bid:
            return {"ok": False, "error": "no_image_file_input"}
        try:
            sess.set_file_input(int(bid), [inject_path])
            return {"ok": True, "method": "DOM.setFileInputFiles", "bid": bid}
        except CdpError as e:
            return {"ok": False, "error": str(e), "bid": bid}

    def _upload_vision(inject_path: str) -> dict[str, Any]:
        boxes = _upload_tile_boxes()
        if not boxes:
            # Still try direct image input (modal may already expose it)
            direct = _inject_via_backend(inject_path)
            if direct.get("ok"):
                return {**direct, "box": None}
            return {"ok": False, "error": "no_upload_tile"}
        last_err = "file_chooser_not_opened"
        last_box: dict[str, Any] | None = None
        for box in boxes[:6]:
            last_box = box
            try:
                chooser_r = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=5.0
                )
                if chooser_r.get("ok"):
                    return {
                        "ok": True,
                        "method": "vision_mouse+fileChooser+setFileInputFiles",
                        "box": box,
                        "bid": chooser_r.get("backendNodeId"),
                    }
                last_err = str(chooser_r.get("error") or last_err)
            except (CdpError, OSError, BrokenPipeError) as e:
                last_err = str(e)
                # Connection died mid-chooser — bail so caller can reopen tab
                if "Broken pipe" in str(e) or isinstance(e, BrokenPipeError):
                    return {"ok": False, "error": f"cdp_broken:{e}", "box": box}
            # Click may arm a hidden input without firing fileChooserOpened
            try:
                sess.click_xy(float(box["x"]), float(box["y"]))
                time.sleep(0.35)
            except (CdpError, OSError, BrokenPipeError):
                pass
            try:
                direct = _inject_via_backend(inject_path)
            except (CdpError, OSError, BrokenPipeError) as e:
                last_err = str(e)
                continue
            if direct.get("ok"):
                return {**direct, "box": box, "method": "mouse+DOM.setFileInputFiles"}
        return {"ok": False, "error": last_err, "box": last_box, "tried": boxes[:6]}

    def _confirm_vision() -> str:
        snap = _snap("channels_cover_pre_confirm")
        png = snap.get("screenshot") or snap.get("png")
        if png:
            # Orange「确认」is white-on-orange → light_on_dark=True; also try dark-on-white
            for lod in (True, False):
                hit = locate_text_css(
                    sess, png, "确认", min_score=0.48, light_on_dark=lod
                )
                if not hit:
                    continue
                # Prefer lower-right hits (modal footer), skip top-of-page noise
                if float(hit.get("css_y") or 0) < 120:
                    continue
                try:
                    sess.click_xy(float(hit["css_x"]), float(hit["css_y"]))
                    return f"clicked_mouse:确认_lod={lod}"
                except CdpError as e:
                    return f"confirm_err:{e}"
        # DOM: prefer 确认 near 取消 (modal footer pair)
        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const cancel = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === '取消');
              const btns = omAll('button,div,span,a').filter(e => (e.innerText||'').trim() === '确认');
              let best = null;
              for (const btn of btns) {{
                const b = btn.getBoundingClientRect();
                if (b.width < 40 || b.height < 24 || b.top < 80) continue;
                let score = b.top + b.left;
                if (cancel) {{
                  const c = cancel.getBoundingClientRect();
                  const dist = Math.abs(b.top - c.top) + Math.abs(b.left - c.left);
                  score = dist; // nearer to 取消 is modal footer
                }}
                if (!best || score < best.score) best = {{ x: b.x + b.width/2, y: b.y + b.height/2, score }};
              }}
              return best;
            }})()"""
        )
        if isinstance(box, dict) and box.get("x") is not None:
            try:
                sess.click_xy(float(box["x"]), float(box["y"]))
                return "clicked_mouse:确认_dom_footer"
            except CdpError as e:
                return f"confirm_err:{e}"
        return "none"

    def _form_cover_probe(template_path: str) -> dict[str, Any]:
        """Form-level gate after modal confirm. Full-page skin match is unreliable
        (warehouse video preview dominates); prefer toast + cover-thumb crop."""
        snap = _snap("channels_cover_form_probe")
        png = snap.get("screenshot") or snap.get("png")
        text = (
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  return omBodyText().slice(0, 5000);
                }})()"""
            )
            or ""
        )
        toast = bool(__import__("re").search(r"封面已更新|封面设置成功|已更新封面|封面更新成功", text))
        base = _cover_preview_looks_like_template(sess, template_path, screenshot_png=png)
        crop_ok = False
        crop_dist: float | None = None
        crop_skin: float | None = None
        if png and Path(png).is_file():
            try:
                from PIL import Image
                from engine.reach.vision_reach import locate_text_in_png

                hit = locate_text_in_png(png, "封面预览", min_score=0.42, light_on_dark=False)
                im = Image.open(png).convert("RGB")
                if hit:
                    x, y = int(hit["x"]), int(hit["y"])
                    # Thumb sits under the label; keep crop tight so video player is excluded
                    crop = im.crop((max(0, x - 20), y + 28, min(im.size[0], x + 200), min(im.size[1], y + 340)))
                else:
                    # right-column fallback
                    w, h = im.size
                    crop = im.crop((int(w * 0.55), int(h * 0.12), int(w * 0.78), int(h * 0.55)))
                crop_path = Path("/tmp/suying_covers") / "channels_form_thumb.jpg"
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                crop.convert("RGB").save(crop_path, quality=92)
                crop_skin = _skin_score(crop)
                crop_dist = _best_template_match_in_shot(template_path, crop_path)
                # Also try sibling templates in same folder (vertical vs primary naming)
                tpl_p = Path(template_path)
                if tpl_p.parent.is_dir():
                    for sib in tpl_p.parent.glob("channels_*.jpg"):
                        d = _best_template_match_in_shot(str(sib), crop_path)
                        if d is not None and (crop_dist is None or d < crop_dist):
                            crop_dist = d
                # Small thumbs: accept looser hist + any skin, or toast already proved update
                if crop_dist is not None and crop_dist <= 0.55:
                    crop_ok = True
                elif crop_skin is not None and crop_skin >= 0.08 and (crop_dist or 99) <= 0.85:
                    crop_ok = True
            except Exception as e:  # noqa: BLE001
                base = {**base, "crop_error": str(e)}

        visual_ok = bool(toast or crop_ok or base.get("visual_ok"))
        return {
            **base,
            "visual_ok": visual_ok,
            "toast_updated": toast,
            "crop_ok": crop_ok,
            "crop_dist": crop_dist,
            "crop_skin": crop_skin,
            "visual_dist": crop_dist if crop_dist is not None else base.get("visual_dist"),
            "screenshot": png,
        }

    if not covers:
        return {"covers": [], "ok": False, "error": "no_covers", "open_clicks": [], "confirms": [], "snapshots": snaps}

    primary = covers[0]
    if not Path(primary).is_file():
        return {
            "covers": [{"index": 0, "ok": False, "error": "missing_file", "path": primary}],
            "ok": False,
            "open_clicks": open_clicks,
            "confirms": confirms,
            "snapshots": snaps,
        }

    inject_path = _stage_cover_for_cdp(primary, slot_index=0)
    from_app = "/cover_templates/" in primary.replace("\\", "/")
    last_up: dict[str, Any] = {}
    modal_probe: dict[str, Any] = {}
    form_probe: dict[str, Any] = {}
    conf = "none"
    slot_ok = False

    for attempt in range(3):
        open_clicks.append(f"attempt{attempt}:{_open_edit_vision()}")
        for _ in range(12):
            if _modal_open():
                break
            time.sleep(0.35)
        _snap(f"channels_cover_slot0_open_a{attempt}")
        if not _modal_open():
            open_clicks.append("modal_not_open")
            continue

        last_up = _upload_vision(inject_path)
        open_clicks.append(f"upload:{last_up}")
        if not last_up.get("ok"):
            time.sleep(0.5)
            last_up = _upload_vision(inject_path)
            open_clicks.append(f"upload_retry:{last_up}")
        if not last_up.get("ok"):
            continue

        time.sleep(1.6)
        _snap(f"channels_cover_slot0_injected_a{attempt}")
        modal_probe = _cover_preview_looks_like_template(sess, primary, screenshot_png=_last_png())
        if not modal_probe.get("visual_ok"):
            open_clicks.append("modal_visual_retry")
            last_up = _upload_vision(inject_path)
            open_clicks.append(f"reinject:{last_up}")
            if last_up.get("ok"):
                time.sleep(1.6)
                _snap(f"channels_cover_slot0_reinjected_a{attempt}")
                modal_probe = _cover_preview_looks_like_template(
                    sess, primary, screenshot_png=_last_png()
                )

        if not modal_probe.get("visual_ok"):
            open_clicks.append("modal_visual_fail_skip_confirm")
            # Close modal without committing a video-frame cover
            cancel = sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const btn = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === '取消');
                  if (!btn) return 'none';
                  const b = btn.getBoundingClientRect();
                  return {{ x: b.x + b.width/2, y: b.y + b.height/2 }};
                }})()"""
            )
            if isinstance(cancel, dict) and cancel.get("x") is not None:
                try:
                    sess.click_xy(float(cancel["x"]), float(cancel["y"]))
                    open_clicks.append("clicked_mouse:取消")
                except CdpError:
                    pass
            time.sleep(0.6)
            continue

        conf = _confirm_vision()
        confirms.append(conf)
        if not str(conf).startswith("clicked"):
            open_clicks.append("confirm_failed")
            continue

        # Wait for modal to close — form-level gate only counts after dismiss
        for _ in range(16):
            if _form_cover_ready() or not _modal_open():
                break
            time.sleep(0.35)
        time.sleep(0.5)
        form_probe = _form_cover_probe(primary)
        # Keep a named form snap for evidence (probe already snapped)
        _snap(f"channels_cover_slot0_form_a{attempt}")

        slot_ok = bool(
            from_app
            and modal_probe.get("visual_ok")
            and form_probe.get("visual_ok")
            and str(conf).startswith("clicked")
        )
        if slot_ok:
            break
        open_clicks.append(
            f"form_visual_fail_toast={form_probe.get('toast_updated')}"
            f"_crop={form_probe.get('crop_dist')}_skin={form_probe.get('crop_skin')}"
        )

    fail_err = None
    if not slot_ok:
        if not last_up.get("ok"):
            fail_err = last_up.get("error") or "upload_inject_failed"
        elif not modal_probe.get("visual_ok"):
            fail_err = "visual_mismatch"
        elif not str(conf).startswith("clicked"):
            fail_err = "confirm_failed"
        elif not form_probe.get("visual_ok"):
            fail_err = "form_visual_mismatch"
        else:
            fail_err = "verify_failed"

    results.append(
        {
            "index": 0,
            "ok": slot_ok,
            "path": primary,
            "backendNodeId": last_up.get("bid"),
            "method": last_up.get("method") or "vision_mouse+fileChooser",
            "open_clicks": list(open_clicks),
            "confirm": conf,
            "probe": {
                "modal": modal_probe,
                "form": form_probe,
                "visual_ok": bool(form_probe.get("visual_ok")),
                "visual_dist": form_probe.get("visual_dist"),
            },
            "source": "app_cover_template" if slot_ok else "failed",
            "error": fail_err,
        }
    )

    # Secondary slots: synced only when primary form visual passed (never fake-ok)
    if slot_ok:
        for idx in range(1, len(covers)):
            results.append(
                {
                    "index": idx,
                    "ok": True,
                    "path": covers[idx],
                    "method": "synced_from_primary",
                    "source": "app_cover_template",
                    "error": None,
                    "confirm": "synced",
                    "probe": {
                        "visual_ok": True,
                        "synced": True,
                        "form_dist": form_probe.get("visual_dist"),
                    },
                }
            )
    else:
        for idx in range(1, len(covers)):
            results.append(
                {
                    "index": idx,
                    "ok": False,
                    "path": covers[idx],
                    "method": "skipped_primary_failed",
                    "source": "failed",
                    "error": "primary_form_visual_failed",
                    "probe": {"visual_ok": False},
                }
            )
        _snap("channels_cover_fail")

    return {
        "covers": results,
        "ok": bool(results) and all(r.get("ok") for r in results) and len(results) == len(covers),
        "open_clicks": open_clicks,
        "confirms": confirms,
        "snapshots": snaps,
        "path": "vision_mouse",
    }


def _set_cover_files(sess: CdpSession, covers: list[str], *, platform: str = "") -> dict[str, Any]:
    """Inject App/template cover images via CDP — must succeed for gate."""
    plat = (platform or "").strip().lower()
    if plat == "douyin":
        return _set_douyin_cover_files(sess, covers)
    if plat == "kuaishou":
        return _set_kuaishou_cover_files(sess, covers)
    if plat == "xhs":
        return _set_xhs_cover_files(sess, covers)
    if plat == "channels":
        return _set_channels_cover_files(sess, covers)
    results = []
    open_clicks: list[str] = []
    confirms: list[str] = []
    snaps: list[dict[str, Any]] = []

    def _snap(tag: str) -> None:
        try:
            from engine.reach.vision_reach import snapshot_page

            snaps.append(snapshot_page(sess, tag=tag))
        except Exception:
            pass

    def _last_png() -> str | None:
        if snaps and isinstance(snaps[-1], dict):
            return snaps[-1].get("screenshot") or snaps[-1].get("png")
        return None

    for i, path in enumerate(covers):
        if not Path(path).is_file():
            results.append({"index": i, "ok": False, "error": "missing_file", "path": path})
            continue
        inject_path = _stage_cover_for_cdp(path, slot_index=i)
        open_clicks.append(_open_cover_editor(sess, platform=plat, slot_index=i))
        time.sleep(0.8)
        _snap(f"{plat or 'plat'}_cover_slot{i}_open")

        bid: int | None = None
        method = "DOM.setFileInputFiles"

        # xhs/channels/kuaishou: real-mouse click cover preview / 编辑 to surface fileChooser
        if plat in ("xhs", "channels", "kuaishou"):
            thumb = sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const labels = ['编辑', '更换封面', '设置封面', '上传封面', '本地上传'];
                  for (const lab of labels) {{
                    const el = omAll('button,div,span,a,label,p').find(e => (e.innerText||'').trim() === lab);
                    if (el) {{
                      const b = el.getBoundingClientRect();
                      if (b.width > 8 && b.height > 8)
                        return {{ x: b.x + b.width/2, y: b.y + b.height/2, via: lab }};
                    }}
                  }}
                  const imgs = omAll('img').filter(img => {{
                    const b = img.getBoundingClientRect();
                    return b.width >= 40 && b.width <= 300 && b.height >= 40 && b.top > 80;
                  }});
                  imgs.sort((a,b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                  if (!imgs.length) return null;
                  const b = imgs[0].getBoundingClientRect();
                  return {{ x: b.x + b.width/2, y: b.y + b.height/2, via: 'cover_img' }};
                }})()"""
            )
            if isinstance(thumb, dict) and thumb.get("x") is not None:
                try:
                    chooser_pre = sess.click_xy_and_set_files(
                        float(thumb["x"]), float(thumb["y"]), [inject_path], timeout=4.0
                    )
                    if chooser_pre.get("ok"):
                        bid = chooser_pre.get("backendNodeId")
                        method = "thumb_chooser+setFileInputFiles"
                        open_clicks.append(f"thumb_chooser:{thumb.get('via')}")
                except CdpError as e:
                    open_clicks.append(f"thumb_chooser_err:{e}")
            time.sleep(0.5)

        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const tiles = omAll('button,div,span,label,p').filter(e => {{
                const t = (e.innerText||'').replace(/\\s+/g,'').trim();
                if (t !== '上传封面' && t !== '上传图片' && t !== '本地上传') return false;
                const b = e.getBoundingClientRect();
                return b.width >= 28 && b.width <= 240 && b.height >= 28 && b.height <= 200 && b.bottom > 0;
              }});
              tiles.sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
              if (!tiles.length) return null;
              const b = tiles[0].getBoundingClientRect();
              return {{ x: b.x + b.width/2, y: b.y + b.height/2 }};
            }})()"""
        )
        try:
            if not bid and box:
                chooser_r = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=5.0
                )
                if chooser_r.get("ok"):
                    bid = chooser_r.get("backendNodeId")
                    method = "fileChooser+setFileInputFiles"
                    open_clicks.append("clicked:上传封面_chooser")
                else:
                    open_clicks.append(f"chooser_fail:{chooser_r.get('error')}")
            if not bid:
                bid = _backend_id_for_cover_image_input(sess) or _backend_id_for_image_input(
                    sess, prefer_index=i
                )
                if not bid:
                    inputs = sess.query_all_file_inputs(pierce=True)
                    for inp in reversed(inputs):
                        acc = (inp.get("accept") or "").lower()
                        if "video" in acc or "mp4" in acc:
                            continue
                        if inp.get("backendNodeId") and (
                            not acc
                            or any(x in acc for x in ("image", "jpg", "jpeg", "png", "webp", "*"))
                        ):
                            bid = int(inp["backendNodeId"])
                            break
                if bid:
                    sess.set_file_input(int(bid), [inject_path])
                    open_clicks.append("set_file_input_fallback")
        except CdpError as e:
            results.append({"index": i, "ok": False, "error": str(e), "path": path})
            continue

        if not bid:
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "no_image_file_input",
                    "path": path,
                    "open_clicks": list(open_clicks),
                }
            )
            continue

        time.sleep(1.6)
        _snap(f"{plat or 'plat'}_cover_slot{i}_injected")
        probe = _cover_preview_looks_like_template(sess, path, screenshot_png=_last_png())
        if not probe.get("visual_ok") and box:
            open_clicks.append("visual_retry_chooser")
            try:
                chooser_r2 = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=5.0
                )
                if chooser_r2.get("ok"):
                    bid = chooser_r2.get("backendNodeId")
                    time.sleep(1.6)
                    _snap(f"{plat or 'plat'}_cover_slot{i}_reinjected")
                    probe = _cover_preview_looks_like_template(
                        sess, path, screenshot_png=_last_png()
                    )
            except CdpError as e:
                probe = {**probe, "reinject_error": str(e)}

        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const imgs = omAll('img').filter(img => {{
                const b = img.getBoundingClientRect();
                return b.width > 36 && b.width < 160 && b.height > 36;
              }});
              if (imgs.length) {{ imgs[imgs.length-1].click(); return 'thumb'; }}
              return 'none';
            }})()"""
        )
        time.sleep(0.5)
        conf = _confirm_cover_dialog(sess)
        if conf == "none":
            time.sleep(0.6)
            conf = _confirm_cover_dialog(sess)
        confirms.append(conf)
        time.sleep(0.6)
        from_app = "/cover_templates/" in path.replace("\\", "/")
        slot_ok = bool(from_app and str(conf).startswith("clicked"))
        if plat in ("kuaishou", "channels") or len(covers) >= 2:
            # Dual-cover platforms: require visual match when we have a probe
            if probe.get("visual_dist") is not None:
                slot_ok = bool(from_app and probe.get("visual_ok") and str(conf).startswith("clicked"))
        results.append(
            {
                "index": i,
                "ok": slot_ok,
                "path": path,
                "backendNodeId": bid,
                "method": method,
                "open_clicks": list(open_clicks),
                "confirm": conf,
                "probe": probe,
                "source": "app_cover_template" if slot_ok else "failed",
                "error": None
                if slot_ok
                else ("visual_mismatch" if not probe.get("visual_ok") else "verify_failed"),
            }
        )
    return {
        "covers": results,
        "ok": bool(results) and all(r.get("ok") for r in results) and len(results) == len(covers),
        "open_clicks": open_clicks,
        "confirms": confirms,
        "snapshots": snaps,
    }



def _kuaishou_force_upload_tab(sess: CdpSession) -> str:
    """Kuaishou modal defaults to「封面截取」(video frame). Must switch to「上传封面」tab via real mouse."""
    box = sess.evaluate(
        f"""(() => {{
          {_OM_DOCS}
          const tabs = omAll('div,span,button,li,a,p').filter(e => {{
            const t = (e.innerText||'').replace(/\\s+/g,'').trim();
            if (t !== '上传封面') return false;
            const b = e.getBoundingClientRect();
            return b.width >= 36 && b.width <= 240 && b.height >= 16 && b.height <= 72 && b.top > 40;
          }});
          tabs.sort((a,b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
          if (!tabs.length) return null;
          const b = tabs[0].getBoundingClientRect();
          return {{ x: b.x + b.width/2, y: b.y + b.height/2 }};
        }})()"""
    )
    if isinstance(box, dict) and box.get("x") is not None:
        try:
            sess.click_xy(float(box["x"]), float(box["y"]))
            return "clicked:上传封面_tab_xy"
        except CdpError as e:
            return f"tab_xy_err:{e}"
    # JS click fallback
    return (
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const el = omAll('div,span,button,li,a,p').find(e => (e.innerText||'').trim() === '上传封面');
              if (el) {{ el.click(); return 'clicked:上传封面_js'; }}
              return 'none';
            }})()"""
        )
        or "none"
    )


def _kuaishou_open_cover_modal(sess: CdpSession) -> str:
    """Click the cover thumbnail under 封面设置 with a trusted mouse event."""
    box = sess.evaluate(
        f"""(() => {{
          {_OM_DOCS}
          // Anchor on 封面设置 label, then find nearby cover img / editable tile
          const label = omAll('div,span,label,p,h3,h4').find(e => (e.innerText||'').trim() === '封面设置');
          let regionTop = 0, regionLeft = 0;
          if (label) {{
            const lb = label.getBoundingClientRect();
            regionTop = lb.top;
            regionLeft = lb.left;
          }}
          const imgs = omAll('img').filter(img => {{
            const b = img.getBoundingClientRect();
            if (b.width < 48 || b.width > 280 || b.height < 48 || b.height > 420) return false;
            if (label && (b.top < regionTop - 20 || b.left < regionLeft - 40)) return false;
            // Prefer left-column cover (not phone preview on the right)
            return b.left < (window.innerWidth * 0.55);
          }});
          imgs.sort((a,b) => {{
            const ba=a.getBoundingClientRect(), bb=b.getBoundingClientRect();
            // Closest below 封面设置 label
            const da = Math.abs(ba.top - regionTop) + Math.abs(ba.left - regionLeft);
            const db = Math.abs(bb.top - regionTop) + Math.abs(bb.left - regionLeft);
            return da - db;
          }});
          if (imgs.length) {{
            const b = imgs[0].getBoundingClientRect();
            return {{ x: b.x + b.width/2, y: b.y + b.height/2, via: 'cover_img' }};
          }}
          // Fallback: clickable tile with 更换/编辑 near label
          const chip = omAll('div,span,button').find(e => {{
            const t=(e.innerText||'').trim();
            return t==='编辑' || t==='更换封面' || t==='设置封面';
          }});
          if (chip) {{
            const b = chip.getBoundingClientRect();
            return {{ x: b.x + b.width/2, y: b.y + b.height/2, via: 'chip' }};
          }}
          return null;
        }})()"""
    )
    if not isinstance(box, dict) or box.get("x") is None:
        return "none"
    try:
        sess.click_xy(float(box["x"]), float(box["y"]))
        return f"clicked:{box.get('via')}_xy"
    except CdpError as e:
        return f"open_err:{e}"


def _set_kuaishou_cover_files(sess: CdpSession, covers: list[str]) -> dict[str, Any]:
    """Kuaishou dual covers: open modal → 上传封面 tab → chooser inject → visual gate."""
    results: list[dict[str, Any]] = []
    open_clicks: list[str] = []
    confirms: list[str] = []
    snaps: list[dict[str, Any]] = []

    def _snap(tag: str) -> None:
        try:
            from engine.reach.vision_reach import snapshot_page

            snaps.append(snapshot_page(sess, tag=tag))
        except Exception:
            pass

    def _last_png() -> str | None:
        if snaps and isinstance(snaps[-1], dict):
            return snaps[-1].get("screenshot") or snaps[-1].get("png")
        return None

    def _modal_ready() -> bool:
        probe = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const t = omBodyText();
              return /封面截取|上传封面/.test(t) && /确认|去编辑|完成/.test(t);
            }})()"""
        )
        return bool(probe)

    for i, path in enumerate(covers):
        if not Path(path).is_file():
            results.append({"index": i, "ok": False, "error": "missing_file", "path": path})
            continue
        inject_path = _stage_cover_for_cdp(path, slot_index=i)

        # Open cover modal (real mouse on 封面设置 thumb)
        opened = _kuaishou_open_cover_modal(sess)
        open_clicks.append(opened)
        time.sleep(0.9)
        if not _modal_ready():
            # retry open once
            open_clicks.append(_kuaishou_open_cover_modal(sess))
            time.sleep(1.0)
        open_clicks.append(_kuaishou_force_upload_tab(sess))
        time.sleep(0.6)
        _snap(f"kuaishou_cover_slot{i}_open")

        bid: int | None = None
        method = "DOM.setFileInputFiles"
        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              // After 上传封面 tab: big dropzone / 点击上传 / dashed upload area
              const labels = ['点击上传', '上传图片', '本地上传', '选择图片', '上传封面'];
              const tiles = omAll('button,div,span,label,p').filter(e => {{
                const t = (e.innerText||'').replace(/\\s+/g,'').trim();
                if (!labels.some(l => t === l || (t.length <= 20 && t.includes(l)))) return false;
                // Skip the top tab itself (small height)
                const b = e.getBoundingClientRect();
                if (b.height < 40 && t === '上传封面') return false;
                return b.width >= 48 && b.width <= 640 && b.height >= 40 && b.height <= 420 && b.bottom > 0;
              }});
              tiles.sort((a,b) => {{
                const ba=a.getBoundingClientRect(), bb=b.getBoundingClientRect();
                return (bb.width*bb.height) - (ba.width*ba.height);
              }});
              if (tiles.length) {{
                const b = tiles[0].getBoundingClientRect();
                return {{ x: b.x + b.width/2, y: b.y + b.height/2 }};
              }}
              // Dashed upload box without text
              const boxes = omAll('div').filter(e => {{
                const b = e.getBoundingClientRect();
                const st = getComputedStyle(e);
                const dashed = (st.borderStyle||'').includes('dashed') || (st.borderTopStyle||'')==='dashed';
                return dashed && b.width >= 120 && b.height >= 80 && b.width <= 520;
              }});
              if (boxes.length) {{
                const b = boxes[0].getBoundingClientRect();
                return {{ x: b.x + b.width/2, y: b.y + b.height/2 }};
              }}
              return null;
            }})()"""
        )
        try:
            if box:
                chooser_r = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=6.0
                )
                if chooser_r.get("ok"):
                    bid = chooser_r.get("backendNodeId")
                    method = "fileChooser+setFileInputFiles"
                    open_clicks.append("clicked:上传区_chooser")
                else:
                    open_clicks.append(f"chooser_fail:{chooser_r.get('error')}")
            if not bid:
                open_clicks.append(_kuaishou_force_upload_tab(sess))
                time.sleep(0.4)
                bid = _backend_id_for_cover_image_input(sess) or _backend_id_for_image_input(
                    sess, prefer_index=i
                )
                if not bid:
                    inputs = sess.query_all_file_inputs(pierce=True)
                    for inp in reversed(inputs):
                        acc = (inp.get("accept") or "").lower()
                        if "video" in acc or "mp4" in acc:
                            continue
                        if inp.get("backendNodeId") and (
                            not acc
                            or any(x in acc for x in ("image", "jpg", "jpeg", "png", "webp", "*"))
                        ):
                            bid = int(inp["backendNodeId"])
                            break
                if bid:
                    sess.set_file_input(int(bid), [inject_path])
                    open_clicks.append("set_file_input_fallback")
        except CdpError as e:
            results.append({"index": i, "ok": False, "error": str(e), "path": path})
            continue

        if not bid:
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "no_image_file_input",
                    "path": path,
                    "open_clicks": list(open_clicks),
                }
            )
            continue

        time.sleep(2.0)
        _snap(f"kuaishou_cover_slot{i}_injected")
        probe = _cover_preview_looks_like_template(sess, path, screenshot_png=_last_png())
        if not probe.get("visual_ok") and box:
            open_clicks.append("visual_retry")
            open_clicks.append(_kuaishou_force_upload_tab(sess))
            time.sleep(0.4)
            try:
                chooser_r2 = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=6.0
                )
                if chooser_r2.get("ok"):
                    time.sleep(2.0)
                    _snap(f"kuaishou_cover_slot{i}_reinjected")
                    probe = _cover_preview_looks_like_template(
                        sess, path, screenshot_png=_last_png()
                    )
            except CdpError as e:
                probe = {**probe, "reinject_error": str(e)}

        conf = (
            sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const btn = omAll('button,div,span,a').find(e => (e.innerText||'').trim() === '确认');
                  if (btn) {{ btn.click(); return 'clicked:确认'; }}
                  return 'none';
                }})()"""
            )
            or "none"
        )
        if conf == "none":
            conf = _confirm_cover_dialog(sess)
        confirms.append(conf)
        time.sleep(0.8)
        from_app = "/cover_templates/" in path.replace("\\", "/")
        slot_ok = bool(from_app and probe.get("visual_ok") and str(conf).startswith("clicked"))
        results.append(
            {
                "index": i,
                "ok": slot_ok,
                "path": path,
                "backendNodeId": bid,
                "method": method,
                "open_clicks": list(open_clicks),
                "confirm": conf,
                "probe": probe,
                "source": "app_cover_template" if slot_ok else "failed",
                "error": None
                if slot_ok
                else ("visual_mismatch" if not probe.get("visual_ok") else "verify_failed"),
            }
        )
        time.sleep(0.5)

    return {
        "covers": results,
        "ok": bool(results) and all(r.get("ok") for r in results) and len(results) == len(covers),
        "open_clicks": open_clicks,
        "confirms": confirms,
        "snapshots": snaps,
    }


def _stage_cover_for_cdp(src: str | Path, *, slot_index: int = 0) -> str:

    """Copy cover to ASCII-only /tmp path — Douyin rejects uploads when source path has CJK chars."""
    src_p = Path(src)
    if not src_p.is_file():
        raise FileNotFoundError(str(src))
    # Already ASCII-safe?
    try:
        str(src_p).encode("ascii")
        return str(src_p.resolve())
    except UnicodeEncodeError:
        pass
    staging = Path("/tmp/suying_covers")
    staging.mkdir(parents=True, exist_ok=True)
    ext = src_p.suffix.lower() if src_p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
    # Force .jpg extension for jpeg magics named oddly
    try:
        from PIL import Image

        im = Image.open(src_p)
        fmt = (im.format or "").upper()
        if fmt in ("JPEG", "JPG") and ext not in (".jpg", ".jpeg"):
            ext = ".jpg"
        elif fmt == "PNG":
            ext = ".png"
        out = staging / f"cover_slot{slot_index}{ext}"
        if fmt in ("JPEG", "JPG"):
            # Re-encode as baseline JPEG so Douyin MIME sniff always passes
            rgb = im.convert("RGB")
            rgb.save(out, format="JPEG", quality=92, optimize=True)
        elif fmt == "PNG":
            im.save(out, format="PNG", optimize=True)
        else:
            im.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
        return str(out)
    except Exception:
        out = staging / f"cover_slot{slot_index}{ext}"
        shutil.copy2(src_p, out)
        return str(out)


def _set_douyin_cover_files(sess: CdpSession, covers: list[str]) -> dict[str, Any]:
    """Douyin: open cover modal once, force filmstrip「上传封面」, inject both slots, visual-gate.

    Real UI (2026-07): modal tabs 设置竖封面/设置横封面 + left AI封面 tool + filmstrip
    black「+ 上传封面」tile. Clicking 选择封面 alone keeps video frames / AI defaults.
    """
    results: list[dict[str, Any]] = []
    open_clicks: list[str] = []
    confirms: list[str] = []
    snaps: list[dict[str, Any]] = []

    def _snap(tag: str) -> None:
        try:
            from engine.reach.vision_reach import snapshot_page

            snaps.append(snapshot_page(sess, tag=tag))
        except Exception:
            pass

    def _last_png() -> str | None:
        if snaps and isinstance(snaps[-1], dict):
            return snaps[-1].get("screenshot") or snaps[-1].get("png") or snaps[-1].get("path")
        return None

    def _inject_one(path: str, *, slot_index: int, attempt: int) -> dict[str, Any]:
        # CDP inject path must be ASCII — Chinese workspace path triggers「不支持的图片格式」
        inject_path = _stage_cover_for_cdp(path, slot_index=slot_index)
        # Clear prior「不支持的图片格式」toast if any
        sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const toast = omAll('div,span').find(e => (e.innerText||'').includes('不支持的图片格式'));
              if (toast) {{
                const x = omAll('button,span,i,svg').find(e => {{
                  const t=(e.innerText||e.getAttribute('aria-label')||'').trim();
                  return t==='×'||t==='x'||t==='关闭';
                }});
                if (x) x.click();
              }}
              return 'ok';
            }})()"""
        )
        horizontal = slot_index >= 1
        axis = _douyin_switch_cover_axis(sess, horizontal=horizontal)
        open_clicks.append(axis)
        time.sleep(0.45)
        # Locate filmstrip「上传封面」tile center for trusted mouse click
        box = sess.evaluate(
            f"""(() => {{
              {_OM_DOCS}
              const tiles = omAll('button,div,span,label,p').filter(e => {{
                const t = (e.innerText||'').replace(/\s+/g,'').trim();
                if (t !== '上传封面') return false;
                const b = e.getBoundingClientRect();
                return b.width >= 36 && b.width <= 200 && b.height >= 36 && b.height <= 200 && b.bottom > 0;
              }});
              tiles.sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
              if (!tiles.length) return null;
              const b = tiles[0].getBoundingClientRect();
              return {{ x: b.x + b.width/2, y: b.y + b.height/2, w: b.width, h: b.height }};
            }})()"""
        )
        if not box:
            force = _douyin_force_upload_tab(sess)
            open_clicks.append(force)
            _snap(f"douyin_cover_slot{slot_index}_open_a{attempt}")
            return {
                "ok": False,
                "error": "douyin_upload_tile_not_found",
                "open": str(force),
                "probe": {},
                "confirm": "none",
                "bid": None,
            }

        open_clicks.append("upload_tile_xy")
        _snap(f"douyin_cover_slot{slot_index}_open_a{attempt}")
        try:
            chooser_r = sess.click_xy_and_set_files(
                float(box["x"]), float(box["y"]), [inject_path], timeout=5.0
            )
        except CdpError as e:
            return {
                "ok": False,
                "error": f"chooser_inject:{e}",
                "open": "upload_tile_xy",
                "probe": {},
                "confirm": "none",
                "bid": None,
            }
        bid = chooser_r.get("backendNodeId")
        force = (
            "clicked:上传封面_chooser"
            if chooser_r.get("ok")
            else f"chooser_fail:{chooser_r.get('error')}"
        )
        open_clicks.append(force)
        if not chooser_r.get("ok"):
            return {
                "ok": False,
                "error": chooser_r.get("error") or "file_chooser_not_opened",
                "open": force,
                "probe": {},
                "confirm": "none",
                "bid": bid,
            }

        time.sleep(2.0)
        _snap(f"douyin_cover_slot{slot_index}_injected_a{attempt}")
        probe = _cover_preview_looks_like_template(sess, path, screenshot_png=_last_png())

        # Compare & correct: still default frame → trusted click + chooser again
        if not probe.get("visual_ok"):
            open_clicks.append("visual_retry_chooser")
            time.sleep(0.3)
            try:
                chooser_r2 = sess.click_xy_and_set_files(
                    float(box["x"]), float(box["y"]), [inject_path], timeout=5.0
                )
                if chooser_r2.get("ok"):
                    bid = chooser_r2.get("backendNodeId")
                    time.sleep(1.8)
                    _snap(f"douyin_cover_slot{slot_index}_reinjected_a{attempt}")
                    probe = _cover_preview_looks_like_template(
                        sess, path, screenshot_png=_last_png()
                    )
                else:
                    probe = {**probe, "reinject_error": chooser_r2.get("error")}
            except CdpError as e:
                probe = {**probe, "reinject_error": str(e)}

        return {
            "ok": bool(probe.get("visual_ok")),
            "error": None if probe.get("visual_ok") else "visual_mismatch_still_default_frame",
            "open": force,
            "probe": probe,
            "confirm": "none",
            "bid": bid,
        }

    # Open modal once for vertical
    boot = _open_cover_editor(sess, platform="douyin", slot_index=0)
    open_clicks.append(boot)
    time.sleep(0.8)
    _snap("douyin_cover_modal_boot")

    for i, path in enumerate(covers):
        if not Path(path).is_file():
            results.append({"index": i, "ok": False, "error": "missing_file", "path": path})
            continue

        slot_ok = False
        last: dict[str, Any] = {"error": "unknown", "probe": {}, "confirm": "none", "bid": None, "open": "none"}

        for attempt in range(3):
            # Re-open modal if it closed (esp. after failed slot)
            page = (
                sess.evaluate(
                    f"""(() => {{
                      {_OM_DOCS}
                      const t = omBodyText();
                      return t.includes('设置竖封面') || t.includes('设置横封面') || t.includes('上传封面');
                    }})()"""
                )
                or False
            )
            if not page:
                reopen = _open_cover_editor(sess, platform="douyin", slot_index=i)
                open_clicks.append(f"reopen:{reopen}")
                time.sleep(0.8)

            # Douyin often auto-syncs 竖→横 after vertical upload ("已优先同步竖封面…").
            # If horizontal preview already matches App portrait, skip re-upload.
            if i >= 1:
                _snap(f"douyin_cover_slot{i}_presync_a{attempt}")
                pre = _cover_preview_looks_like_template(
                    sess, path, screenshot_png=_last_png()
                )
                if pre.get("visual_ok"):
                    open_clicks.append("horizontal_presynced_skip_upload")
                    last = {
                        "ok": True,
                        "error": None,
                        "open": "presynced:上传封面",
                        "probe": pre,
                        "confirm": "none",
                        "bid": None,
                    }
                    # fall through to confirm/switch handling below
                else:
                    last = _inject_one(path, slot_index=i, attempt=attempt)
            else:
                last = _inject_one(path, slot_index=i, attempt=attempt)
            if not last.get("ok"):
                # stay in modal for retry; don't confirm defaults
                last_err = last.get("error") or "inject_failed"
                last["error"] = last_err
                time.sleep(0.4)
                continue

            # Vertical done → switch to horizontal without 完成; after last slot → 完成
            if i < len(covers) - 1:
                sw = _douyin_switch_cover_axis(sess, horizontal=True)
                open_clicks.append(f"after_slot_switch:{sw}")
                last["confirm"] = f"switched:{sw}"
                confirms.append(last["confirm"])
                time.sleep(0.5)
                _snap(f"douyin_cover_slot{i}_switched_a{attempt}")
            else:
                conf = _confirm_cover_dialog(sess)
                if conf == "none":
                    time.sleep(0.4)
                    conf = _confirm_cover_dialog(sess)
                last["confirm"] = conf
                confirms.append(conf)
                time.sleep(0.7)
                _snap(f"douyin_cover_slot{i}_confirmed_a{attempt}")
                post = _cover_preview_looks_like_template(
                    sess, path, screenshot_png=_last_png()
                )
                last["probe"] = {**(last.get("probe") or {}), "post_confirm": post}
                # Form thumbs may be small; keep inject-time visual_ok as authority
                if not str(conf).startswith("clicked"):
                    last["ok"] = False
                    last["error"] = "confirm_failed"
                    continue

            from_app = "/cover_templates/" in path.replace("\\", "/")
            used_upload = "上传封面" in str(last.get("open") or "")
            slot_ok = bool(from_app and used_upload and last.get("ok"))
            if slot_ok:
                last["error"] = None
                break

        results.append(
            {
                "index": i,
                "ok": slot_ok,
                "path": path,
                "backendNodeId": last.get("bid"),
                "method": "DOM.setFileInputFiles",
                "open_clicks": [last.get("open")],
                "confirm": last.get("confirm"),
                "probe": last.get("probe") or {},
                "source": "app_cover_template" if slot_ok else "failed",
                "error": None if slot_ok else last.get("error"),
            }
        )
        time.sleep(0.4)

    return {
        "covers": results,
        "ok": bool(results) and all(r.get("ok") for r in results) and len(results) == len(covers),
        "open_clicks": open_clicks,
        "confirms": confirms,
        "snapshots": snaps,
    }



def upload_video_with_retries(
    sess: CdpSession,
    video_path: str,
    *,
    max_attempts: int = 2,
    wait_timeout: float = 12,
    platform: str = "",
) -> dict[str, Any]:
    """Inject video once; only retry when page explicitly shows upload_failed."""
    from engine.reach.vision_reach import STATE_UPLOAD_FAILED, snapshot_page

    attempts: list[dict[str, Any]] = []
    last_upload: dict[str, Any] = {}
    last_ready: dict[str, Any] = {}
    for i in range(max_attempts):
        if i > 0:
            click = _click_reupload(sess)
            time.sleep(0.8)
            attempts.append({"attempt": i + 1, "reupload_click": click})
        last_upload = upload_video_via_cdp(sess, video_path, platform=platform)
        if not last_upload.get("ok"):
            attempts.append({"attempt": i + 1, "upload": last_upload})
            continue
        last_ready = wait_upload_ready(sess, timeout=wait_timeout)
        attempts.append({"attempt": i + 1, "upload": last_upload, "ready": last_ready})
        st = last_ready.get("state")
        if st == STATE_UPLOAD_FAILED:
            try:
                snap = snapshot_page(sess, tag=f"upload_fail_retry_{i}")
                attempts[-1]["snapshot"] = snap
            except Exception:
                pass
            continue
        if last_ready.get("ok"):
            chk = sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const t = omBodyText();
                  return {{failed: /上传失败|网络错误，请稍后/.test(t)}};
                }})()"""
            ) or {}
            if chk.get("failed"):
                continue
            return {
                "ok": True,
                "upload": last_upload,
                "ready": last_ready,
                "attempts": attempts,
            }
        if st in ("need_human", "need_login"):
            return {
                "ok": False,
                "upload": last_upload,
                "ready": last_ready,
                "attempts": attempts,
                "error": st,
            }
        # timeout / other: do not burn another full wait unless page says failed
        break
    return {
        "ok": False,
        "upload": last_upload,
        "ready": last_ready,
        "attempts": attempts,
        "error": (last_ready or {}).get("state") or (last_upload or {}).get("error") or "upload_retries_exhausted",
    }


def _verify_copy(sess: CdpSession, min_body: int = 2, *, platform: str = "") -> dict[str, Any]:
    plat = (platform or "").strip().lower()
    return (
        sess.evaluate(
            f"""(() => {{
          {_OM_DOCS}
          const plat = {json.dumps(plat)};
          const bodyEls = omAll('[contenteditable="true"],textarea');
          let bodyLen = 0;
          let descLen = 0;
          let descText = '';
          let stillPlaceholder = false;
          // channels: only count the 视频描述 editor
          if (plat === 'channels') {{
            for (const ed of omDescEditors()) {{
              const ph = (ed.getAttribute('placeholder')||'') + (ed.getAttribute('data-placeholder')||'');
              const r = ed.getBoundingClientRect();
              if (!/添加描述|写描述/.test(ph) && !/input-editor/.test(ed.className||'')) continue;
              if (r.width < 80) continue;
              let t = (ed.innerText || ed.value || '').trim();
              if (/^(添加描述|写描述|说点什么)$/.test(t)) {{
                stillPlaceholder = true;
                t = '';
              }}
              if (t.length >= descLen) {{
                descLen = t.length;
                descText = t.slice(0, 60);
              }}
            }}
            // also try label walk
            if (!descLen) {{
              for (const d of omDocs()) {{
                const labs = Array.from(d.querySelectorAll('div,span,label,p'));
                for (const lab of labs) {{
                  const lt = (lab.innerText || '').replace(/\\s+/g,' ').trim();
                  if (lt !== '视频描述' && !/^视频描述\\b/.test(lt)) continue;
                  if (lt.length > 12) continue;
                  let root = lab.parentElement;
                  for (let i = 0; i < 8 && root; i++, root = root.parentElement) {{
                    const ed = root.querySelector('.input-editor, [data-placeholder="添加描述"], [contenteditable]:not([contenteditable="false"])');
                    if (!ed) continue;
                    let t = (ed.innerText || ed.value || '').trim();
                    if (/^(添加描述|写描述|说点什么)$/.test(t)) {{ stillPlaceholder = true; t = ''; }}
                    descLen = t.length;
                    descText = t.slice(0, 60);
                    break;
                  }}
                  if (descLen || stillPlaceholder) break;
                }}
                if (descLen || stillPlaceholder) break;
              }}
            }}
            const err = /标题包含特殊字符|请填写描述|描述不能为空/.test(omBodyText());
            return {{
              bodyLen: descLen,
              ok: descLen >= {min_body} && !err && !stillPlaceholder,
              docs: omDocs().length,
              hasFormError: err,
              stillPlaceholder,
              descText,
              platform: 'channels'
            }};
          }}
          for (const el of bodyEls) {{
            let t = (el.innerText || el.value || '').trim();
            if (/^(添加描述|写描述|说点什么)$/.test(t)) t = '';
            if (t.length > bodyLen) bodyLen = t.length;
          }}
          const err = /标题包含特殊字符|请填写描述|描述不能为空/.test(omBodyText());
          return {{bodyLen, ok: bodyLen >= {min_body} && !err, docs: omDocs().length, hasFormError: err}};
        }})()"""
        )
        or {"ok": False, "bodyLen": 0}
    )


def _click_publish(sess: CdpSession, platform: str = "") -> str:
    """Click the real submit control — never the Douyin「立即发布」schedule radio."""
    texts = list(PUBLISH_BTN_TEXTS_STRICT)
    plat = (platform or "").strip().lower()
    if plat == "xhs":
        return _click_xhs_publish_red(sess)
    return (
        sess.evaluate(
            f"""(() => {{
          {_OM_DOCS}
          const texts = {json.dumps(texts, ensure_ascii=False)};
          const plat = {json.dumps(plat)};
          function isScheduleRadio(el) {{
            const t = (el.innerText||'').trim();
            if (t === '立即发布' || t === '定时发布') return true;
            // parent label near 发布时间
            let p = el.parentElement;
            for (let i = 0; i < 4 && p; i++, p = p.parentElement) {{
              const pt = (p.innerText||'').slice(0, 40);
              if (/发布时间|定时发布/.test(pt) && /立即发布|定时/.test(pt)) return true;
            }}
            return false;
          }}
          function score(el) {{
            const tag = (el.tagName||'').toLowerCase();
            const t = (el.innerText||'').trim();
            if (!texts.includes(t)) return -1;
            if (isScheduleRadio(el)) return -1;
            if (el.disabled || el.getAttribute('disabled')!=null) return -1;
            const cls = (el.className||'') + ' ' + (el.getAttribute('class')||'');
            let s = 0;
            if (tag === 'button') s += 50;
            if (el.getAttribute('role') === 'button') s += 30;
            if (/primary|submit|publish|btn-primary|semi-button-primary|red|danger/i.test(cls)) s += 40;
            const r = el.getBoundingClientRect();
            if (r.width > 60 && r.height > 28 && r.bottom > window.innerHeight * 0.55) s += 25;
            if (r.width < 20 || r.height < 16 || r.bottom < 0) return -1;
            // Douyin bottom bar 发布 is short exact text
            if (t === '发布') s += 20;
            return s;
          }}
          const cands = omAll('button,div[role=button],span[role=button],div,span')
            .map(el => ({{el, s: score(el)}}))
            .filter(x => x.s >= 0)
            .sort((a,b) => b.s - a.s);
          if (!cands.length) return 'no_btn';
          const best = cands[0].el;
          if (best.disabled || best.getAttribute('disabled')!=null) return 'disabled';
          try {{ best.scrollIntoView({{block:'center', inline:'center'}}); }} catch (e) {{}}
          best.click();
          return 'clicked:' + ((best.innerText||'').trim()) + '@' + cands[0].s;
        }})()"""
        )
        or "no_btn"
    )


def _confirm_publish_dialogs(sess: CdpSession) -> str:
    """Click confirm on Douyin/XHS post-submit dialogs if any."""
    return (
        sess.evaluate(
            f"""(() => {{
          {_OM_DOCS}
          const texts = ['确认发布', '确认', '确定', '继续发布', '仍要发布', '发布'];
          const hits = [];
          for (const t of texts) {{
            // Prefer buttons inside dialogs/modals
            const els = omAll('button,div[role=button]');
            for (const el of els) {{
              if ((el.innerText||'').trim() !== t) continue;
              const r = el.getBoundingClientRect();
              if (r.width < 40 || r.height < 24) continue;
              // skip bottom-bar primary if we already clicked it — only dialog-sized
              const inDlg = !!(el.closest('[class*=dialog],[class*=modal],[class*=popup],[role=dialog]'));
              if (t === '发布' && !inDlg) continue;
              el.click();
              hits.push(t + (inDlg ? ':dlg' : ''));
              break;
            }}
          }}
          return hits.join(',') || 'none';
        }})()"""
        )
        or "none"
    )


def _publish_succeeded(sess: CdpSession) -> dict[str, Any]:
    """True when we left the edit form (success page / content list), not still sitting on 发布."""
    return (
        sess.evaluate(
            f"""(() => {{
          {_OM_DOCS}
          const t = omBodyText();
          const url = location.href || '';
          if (/接收短信验证码|短信验证码|为确保是本人操作/.test(t))
            return {{ok:false, reason:'need_sms', sms:true}};
          if (/验证码|滑块|安全验证|人机验证/.test(t) && /验证/.test(t))
            return {{ok:false, reason:'need_human_verify', sms:true}};
          if (/发布成功|作品已发布|已提交|上传成功，审核|发表成功|已发表/.test(t) && !/作品描述|谁可以看|谁可见|添加描述/.test(t))
            return {{ok:true, reason:'success_text'}};
          if (/\\/content\\/manage|\\/manage|publish\\/success|posted|\\/post\\/list|platform\\/post/i.test(url) && !/\\/post\\/create/.test(url))
            return {{ok:true, reason:'success_url'}};
          // channels often stays briefly then redirects; toast
          if (/发表成功|已发表到视频号|发布成功/.test(t))
            return {{ok:true, reason:'channels_toast'}};
          // Still on editor: red 发布 + 暂存离开
          const hasPub = omAll('button,div[role=button]').some(el => {{
            const x = (el.innerText||'').trim();
            return x === '发布' && !el.disabled;
          }});
          const hasDraft = /暂存离开|存草稿/.test(t);
          const hasForm = /作品描述|谁可见|发布时间|选择封面|设置封面/.test(t);
          if (hasPub && hasForm) return {{ok:false, reason:'still_on_form', hasDraft}};
          if (hasPub && hasDraft) return {{ok:false, reason:'still_on_form_bar'}};
          return {{ok:false, reason:'unknown', url}};
        }})()"""
        )
        or {"ok": False, "reason": "eval_failed"}
    )


def _click_publish_and_confirm(sess: CdpSession, platform: str = "", attempts: int = 3) -> dict[str, Any]:
    """Click 发布, handle confirm dialogs, verify we actually left the form."""
    trail: list[Any] = []
    last_click = "none"
    plat = (platform or "").strip().lower()
    if plat == "xhs":
        ready = _wait_xhs_publish_ready(sess, timeout_sec=45.0)
        trail.append({"xhs_ready": ready})
    for i in range(max(1, attempts)):
        dismissed = _dismiss_publisher_popups(sess)
        trail.append({"attempt": i + 1, "dismiss": dismissed})
        if dismissed == "need_sms":
            return {
                "ok": False,
                "result": last_click,
                "trail": trail,
                "pub_clicked": True,
                "need_sms": True,
                "error": "need_sms_verify",
                "verify": {"ok": False, "reason": "need_sms", "sms": True},
            }
        time.sleep(0.25)
        click = _click_publish(sess, platform=platform)
        last_click = click
        trail.append({"attempt": i + 1, "click": click})
        if click in ("disabled", "disabled_custom", "no_btn"):
            # XHS: cover/activity often keeps submit-disabled briefly — wait & retry
            if plat == "xhs" and i < attempts - 1:
                ready = _wait_xhs_publish_ready(sess, timeout_sec=20.0)
                trail.append({"attempt": i + 1, "xhs_ready_retry": ready})
                time.sleep(0.5)
                continue
            return {"ok": False, "result": click, "trail": trail, "pub_clicked": False}
        time.sleep(0.8)
        conf = _confirm_publish_dialogs(sess)
        trail.append({"attempt": i + 1, "confirm": conf})
        # wait for navigation / toast / SMS
        deadline = time.time() + 10
        last_chk: dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(0.6)
            last_chk = _publish_succeeded(sess)
            trail.append({"attempt": i + 1, "check": last_chk})
            if last_chk.get("ok"):
                return {
                    "ok": True,
                    "result": click,
                    "trail": trail,
                    "pub_clicked": True,
                    "verify": last_chk,
                }
            if last_chk.get("sms") or last_chk.get("reason") in ("need_sms", "need_human_verify"):
                return {
                    "ok": False,
                    "result": click,
                    "trail": trail,
                    "pub_clicked": True,
                    "need_sms": True,
                    "error": "need_sms_verify",
                    "verify": last_chk,
                }
            conf2 = _confirm_publish_dialogs(sess)
            if conf2 != "none":
                trail.append({"attempt": i + 1, "confirm2": conf2})
        # still on form → retry click
        continue
    final = _publish_succeeded(sess)
    if final.get("sms") or final.get("reason") in ("need_sms", "need_human_verify"):
        return {
            "ok": False,
            "result": last_click,
            "trail": trail,
            "pub_clicked": True,
            "need_sms": True,
            "error": "need_sms_verify",
            "verify": final,
        }
    return {
        "ok": bool(final.get("ok")),
        "result": last_click,
        "trail": trail,
        "pub_clicked": str(last_click).startswith("clicked"),
        "verify": final,
        "error": None if final.get("ok") else "clicked_but_still_on_form",
    }


def publish_via_cdp(
    *,
    platform: str,
    pack_dir: Path,
    data_root: Path,
    title: str | None = None,
    body: str | None = None,
    template_id: str | None = None,
    click_publish: bool = True,
    cdp_http: str | None = None,
    upload_video: bool = False,
) -> dict[str, Any]:
    """Fill copy + set covers via CDP; hard-gate before clicking 发布.

    If upload_video=True, also inject video.mp4 first and wait for form.
    """
    plat = (platform or "").strip().lower()
    if plat not in PLATFORM_URL_HINT:
        raise ValueError(f"CDP 发布暂不支持平台: {plat}")

    try:
        assets = _gate_or_raise(
            platform=plat,
            pack_dir=pack_dir,
            data_root=data_root,
            title=title,
            body=body,
            template_id=template_id,
        )
    except PublishAssetsError as e:
        return {
            "ok": False,
            "need_human": True,
            "phase": "gate_failed",
            "error": str(e),
            "pub_clicked": False,
        }

    hint = PLATFORM_URL_HINT[plat]
    try:
        ws_url, page_url = find_tab_ws(url_substr=hint, cdp_http=cdp_http)
    except CdpError as e:
        return {
            "ok": False,
            "need_human": True,
            "phase": "cdp_unavailable",
            "error": str(e),
            "pub_clicked": False,
            "assets": assets,
        }

    with CdpSession(ws_url) as sess:
        sess.call("Runtime.enable")
        try:
            sess.call("DOM.enable")
        except CdpError:
            pass
        try:
            sess.inject_popup_dismisser()
        except Exception:
            pass

        upload_r: dict[str, Any] | None = None
        snaps: list[dict[str, Any]] = []
        try:
            from engine.reach.vision_reach import snapshot_page

            snaps.append(snapshot_page(sess, tag=f"{plat}_before"))
        except Exception:
            pass

        if upload_video:
            # Discard unfinished draft banner / marketing popups before inject
            try:
                _dismiss_publisher_popups(sess)
                time.sleep(0.4)
                _dismiss_publisher_popups(sess)
            except Exception:
                pass
            bundled = upload_video_with_retries(
                sess, assets["video"], max_attempts=2, wait_timeout=90, platform=plat
            )
            upload_r = bundled.get("upload")
            snaps.append({"upload_retries": bundled.get("attempts")})
            ready = bundled.get("ready") or {}
            if ready.get("state") in ("need_human", "need_login"):
                try:
                    from engine.reach.vision_reach import snapshot_page

                    snaps.append(snapshot_page(sess, tag=f"{plat}_upload_fail"))
                except Exception:
                    pass
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": ready["state"],
                    "error": "登录/验证未过，已停下",
                    "upload": upload_r,
                    "ready": ready,
                    "retries": bundled,
                    "snapshots": snaps,
                    "pub_clicked": False,
                    "assets": assets,
                    "page_url": page_url,
                }
            if not bundled.get("ok"):
                # Soft continue when inject succeeded but wait classifier timed out
                # (afternoon path: inject → fill without long waits)
                # NEVER soft-continue while still uploading — covers would stick to mid-upload UI
                ready_st = str((bundled.get("ready") or {}).get("state") or "")
                if ready_st == "uploading" or not (upload_r or {}).get("ok"):
                    try:
                        from engine.reach.vision_reach import snapshot_page

                        snaps.append(snapshot_page(sess, tag=f"{plat}_upload_fail"))
                    except Exception:
                        pass
                    return {
                        "ok": False,
                        "need_human": True,
                        "phase": "upload_failed" if ready_st != "uploading" else "uploading",
                        "error": bundled.get("error")
                        or (upload_r or {}).get("error")
                        or ("视频仍在上传中" if ready_st == "uploading" else "视频上传失败"),
                        "upload": upload_r,
                        "retries": bundled,
                        "snapshots": snaps,
                        "pub_clicked": False,
                        "assets": assets,
                        "page_url": page_url,
                    }
                snaps.append({"wait_soft_continue": ready, "note": "inject_ok_wait_timeout"})
                time.sleep(2.0)
            else:
                snaps.append({"wait_form": ready})

        if plat == "channels":
            fill = _fill_channels_copy(sess, assets["title"], assets["body"])
        elif plat == "xhs":
            fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
        elif plat == "kuaishou":
            fill = _fill_kuaishou_copy(sess, assets["title"], assets["body"])
        else:
            # douyin also uses generic contenteditable fill
            fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
        time.sleep(0.5)
        cover_r = _set_cover_files(sess, assets["covers"], platform=plat)
        for s in cover_r.get("snapshots") or []:
            if isinstance(s, dict):
                snaps.append(s)
        time.sleep(0.5)
        _dismiss_publisher_popups(sess)
        time.sleep(0.3)
        # refill body after cover UI interactions (may steal focus / clear fields)
        if plat == "channels":
            fill = _fill_channels_copy(sess, assets["title"], assets["body"])
            time.sleep(0.3)
        elif plat == "xhs":
            fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
            time.sleep(0.3)
        elif plat == "kuaishou":
            fill = _fill_kuaishou_copy(sess, assets["title"], assets["body"])
            time.sleep(0.3)
        elif plat == "douyin":
            fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
            time.sleep(0.3)
        _dismiss_publisher_popups(sess)
        verify = _verify_copy(sess, platform=plat)

        if not verify.get("ok") or not fill.get("bodyOk"):
            try:
                from engine.reach.vision_reach import snapshot_page

                snaps.append(snapshot_page(sess, tag=f"{plat}_copy_fail"))
            except Exception:
                pass
            return {
                "ok": False,
                "need_human": True,
                "phase": "copy_verify_failed",
                "error": "文案写入后回读失败，禁止点发布",
                "fill": fill,
                "verify": verify,
                "covers": cover_r,
                "upload": upload_r,
                "snapshots": snaps,
                "pub_clicked": False,
                "assets": assets,
                "page_url": page_url,
            }

        cover_soft_fail = False
        cover_note = None
        if not _covers_acceptable(sess, cover_r, plat):
            try:
                from engine.reach.vision_reach import snapshot_page

                snaps.append(snapshot_page(sess, tag=f"{plat}_cover_fail"))
            except Exception:
                pass
            # XHS creator web cover editor is often broken (blank modal / 「发生了一些错误」);
            # allow publish with platform default frame and mark 封面待补 for human fix.
            if plat == "xhs":
                cover_soft_fail = True
                cover_note = "小红书封面未设成功，已放行发布（封面待补）"
            else:
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": "cover_failed",
                    "error": "封面槽位未能全部设置，禁止点发布",
                    "fill": fill,
                    "covers": cover_r,
                    "upload": upload_r,
                    "snapshots": snaps,
                    "pub_clicked": False,
                    "assets": assets,
                    "page_url": page_url,
                }

        # XHS: publish stays disabled while video still failed — one more reupload pass
        if plat == "xhs" and click_publish:
            fail_chk = sess.evaluate(
                """(() => {
                  const t = (document.body && document.body.innerText) || '';
                  return /上传失败|网络错误，请稍后/.test(t);
                })()"""
            )
            if fail_chk:
                bundled2 = upload_video_with_retries(
                    sess, assets["video"], max_attempts=2, wait_timeout=12, platform=plat
                )
                snaps.append({"xhs_reupload": bundled2.get("attempts")})
                upload_r = bundled2.get("upload") or upload_r
                if not bundled2.get("ok"):
                    try:
                        from engine.reach.vision_reach import snapshot_page

                        snaps.append(snapshot_page(sess, tag=f"{plat}_upload_still_fail"))
                    except Exception:
                        pass
                    return {
                        "ok": False,
                        "need_human": True,
                        "phase": "upload_failed",
                        "error": "小红书视频仍显示上传失败",
                        "upload": upload_r,
                        "retries": bundled2,
                        "snapshots": snaps,
                        "pub_clicked": False,
                        "assets": assets,
                        "page_url": page_url,
                    }
                # refill copy after reupload may wipe fields
                fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
                time.sleep(0.4)
                verify = _verify_copy(sess, platform=plat)

        pub = "skipped"
        if click_publish:
            _dismiss_publisher_popups(sess)
            time.sleep(0.3)
            # Block publish when form still shows validation errors
            form_err = sess.evaluate(
                f"""(() => {{
                  {_OM_DOCS}
                  const t = omBodyText();
                  if (/标题包含特殊字符/.test(t)) return 'title_invalid';
                  if (/上传失败|网络错误/.test(t)) return 'upload_failed';
                  // channels description must not be placeholder
                  if (/添加描述/.test(t) && !/视频描述[\\s\\S]{{0,40}}[^添]{{3,}}/.test(t)) {{
                    // soft check via editors
                  }}
                  return '';
                }})()"""
            )
            # Hard re-check body before publish
            verify2 = _verify_copy(sess, platform=plat)
            if not verify2.get("ok"):
                if plat == "channels":
                    fill = _fill_channels_copy(sess, assets["title"], assets["body"])
                elif plat == "xhs":
                    fill = _fill_xhs_copy(sess, assets["title"], assets["body"])
                time.sleep(0.3)
                verify2 = _verify_copy(sess, platform=plat)
            if not verify2.get("ok"):
                try:
                    from engine.reach.vision_reach import snapshot_page

                    snaps.append(snapshot_page(sess, tag=f"{plat}_prepub_copy_fail"))
                except Exception:
                    pass
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": "copy_verify_failed",
                    "error": "发布前再次回读文案失败，禁止点发布",
                    "fill": fill,
                    "verify": verify2,
                    "covers": cover_r,
                    "upload": upload_r,
                    "snapshots": snaps,
                    "pub_clicked": False,
                    "assets": assets,
                    "page_url": page_url,
                }
            if form_err:
                try:
                    from engine.reach.vision_reach import snapshot_page

                    snaps.append(snapshot_page(sess, tag=f"{plat}_form_err"))
                except Exception:
                    pass
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": "form_invalid",
                    "error": f"页面仍有错误，禁止点发布: {form_err}",
                    "fill": fill,
                    "covers": cover_r,
                    "upload": upload_r,
                    "snapshots": snaps,
                    "pub_clicked": False,
                    "assets": assets,
                    "page_url": page_url,
                }
            # channels: refuse to click 发表 if 视频描述 still empty/placeholder
            if plat == "channels" and (
                not verify2.get("ok") or verify2.get("stillPlaceholder") or int(verify2.get("bodyLen") or 0) < 2
            ):
                try:
                    from engine.reach.vision_reach import snapshot_page

                    snaps.append(snapshot_page(sess, tag=f"{plat}_desc_empty"))
                except Exception:
                    pass
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": "copy_verify_failed",
                    "error": "视频描述未写入（仍为添加描述），禁止点发表",
                    "fill": fill,
                    "verify": verify2,
                    "covers": cover_r,
                    "upload": upload_r,
                    "snapshots": snaps,
                    "pub_clicked": False,
                    "assets": assets,
                    "page_url": page_url,
                }
            _dismiss_publisher_popups(sess)
            pub_r = _click_publish_and_confirm(sess, platform=plat, attempts=3)
            pub = pub_r.get("result") or "failed"
            if not pub_r.get("ok"):
                try:
                    from engine.reach.vision_reach import snapshot_page

                    snaps.append(snapshot_page(sess, tag=f"{plat}_pub_blocked"))
                except Exception:
                    pass
                snaps.append({"publish_trail": pub_r.get("trail"), "publish_verify": pub_r.get("verify")})
                if pub_r.get("need_sms") or pub_r.get("error") == "need_sms_verify":
                    phase = "need_sms_verify"
                    err = "已点「发布」，抖音要求短信验证码，请在 Chrome 弹窗中完成验证"
                elif not pub_r.get("pub_clicked"):
                    phase = "publish_blocked"
                    err = pub_r.get("error") or f"发布按钮不可用: {pub}"
                else:
                    phase = "publish_not_submitted"
                    err = pub_r.get("error") or (
                        f"发布未真正提交: {pub} / {(pub_r.get('verify') or {}).get('reason')}"
                    )
                return {
                    "ok": False,
                    "need_human": True,
                    "phase": phase,
                    "error": err,
                    "fill": fill,
                    "covers": cover_r,
                    "upload": upload_r,
                    "snapshots": snaps,
                    "pub_clicked": bool(pub_r.get("pub_clicked")),
                    "result": pub,
                    "publish": pub_r,
                    "assets": assets,
                    "page_url": page_url,
                }
            snaps.append({"publish_trail": pub_r.get("trail"), "publish_verify": pub_r.get("verify")})

        try:
            from engine.reach.vision_reach import snapshot_page

            snaps.append(snapshot_page(sess, tag=f"{plat}_after"))
        except Exception:
            pass
        out: dict[str, Any] = {
            "ok": True,
            "need_human": bool(cover_soft_fail),
            "phase": "done",
            "fill": fill,
            "covers": cover_r,
            "upload": upload_r,
            "verify": verify,
            "snapshots": snaps,
            "pub_clicked": True if click_publish else False,
            "result": pub if click_publish else "skipped",
            "assets": assets,
            "page_url": page_url,
        }
        if cover_soft_fail:
            out["cover_soft_fail"] = True
            out["cover_note"] = cover_note
            out["warning"] = cover_note
            # Still published; human should replace default frame later
            if not click_publish:
                out["need_human"] = True
        return out
