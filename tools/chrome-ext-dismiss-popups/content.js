(() => {
  try {
    window.__SUYING_POPUP_BLOCKER__ = "1.0.0";
  } catch (_) {}
  const CLICK_TEXTS = [
    "暂不设置",
    "暂不",
    "以后再说",
    "我知道了",
    "知道了",
    "跳过",
    "关闭",
    "取消",
  ];

  // Prefer these exact buttons when present (douyin horizontal cover promo)
  const PREFER = ["暂不设置", "暂不", "以后再说"];

  const TITLE_HINTS = /活动详情|设置横封面|获更多流量|征稿|活动|新手引导|温馨提示/;

  function visible(el) {
    if (!el || !(el instanceof Element)) return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 8 && r.height > 8;
  }

  function clickEl(el) {
    try {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      el.click();
      return true;
    } catch (_) {
      return false;
    }
  }

  function findByText(texts) {
    const nodes = Array.from(
      document.querySelectorAll("button, a, div[role=button], span, p, li")
    );
    for (const want of texts) {
      const hit = nodes.find((n) => {
        if (!visible(n)) return false;
        const t = (n.innerText || n.textContent || "").trim();
        return t === want || (t.length < 16 && t.includes(want));
      });
      if (hit) return hit;
    }
    return null;
  }

  function closeXButtons() {
    const nodes = Array.from(
      document.querySelectorAll(
        "button, [aria-label=关闭], [aria-label=close], .close, [class*=close], [class*=Close]"
      )
    );
    for (const n of nodes) {
      if (!visible(n)) continue;
      const t = (n.innerText || "").trim();
      const al = (n.getAttribute("aria-label") || "").toLowerCase();
      if (t === "×" || t === "✕" || t === "X" || t === "x" || al.includes("关闭") || al === "close") {
        clickEl(n);
        return true;
      }
    }
    return false;
  }

  function modalLooksLikePromo() {
    const text = (document.body && document.body.innerText) || "";
    return TITLE_HINTS.test(text.slice(0, 2500));
  }

  function sweep() {
    // Prefer explicit dismiss buttons
    let el = findByText(PREFER);
    if (el && clickEl(el)) return "prefer";

    if (modalLooksLikePromo()) {
      el = findByText(CLICK_TEXTS);
      if (el && clickEl(el)) return "promo_text";
      if (closeXButtons()) return "promo_x";
    }

    // Soft: only click 暂不设置 / 我知道了 even without title hint
    el = findByText(["暂不设置", "我知道了", "知道了", "以后再说"]);
    if (el && clickEl(el)) return "soft";
    return null;
  }

  let last = 0;
  const run = () => {
    const now = Date.now();
    if (now - last < 400) return;
    const r = sweep();
    if (r) last = now;
  };

  run();
  setInterval(run, 800);

  const mo = new MutationObserver(() => run());
  if (document.documentElement) {
    mo.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
