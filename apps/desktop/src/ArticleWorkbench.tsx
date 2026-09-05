import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { NotifyFn } from "./pages/pageTypes";
import { reviewStatusBadgeClass, uiStatusLabel } from "./reviewLabels";
import type { ChromeProfile } from "./types";

type Props = {
  notify: NotifyFn;
  activeCustomerId?: number | null;
  onGoBrand?: () => void;
  onGoAccounts?: () => void;
};

type ContentChromeSnapshot = {
  profiles?: ChromeProfile[];
  legacy_profiles?: ChromeProfile[];
  selected?: string | null;
  root?: string;
};

const CONTENT_CHROME_SYNC_EVENT = "suying:content-chrome-profiles";

export function ArticleWorkbench({
  notify,
  activeCustomerId = null,
  onGoBrand,
  onGoAccounts,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [completeness, setCompleteness] = useState<{
    score: number;
    can_publish: boolean;
    missing: string[];
    verified_fact_count: number;
    site_count: number;
  } | null>(null);
  const [sources, setSources] = useState<Array<Record<string, unknown>>>([]);
  const [sites, setSites] = useState<Array<Record<string, unknown>>>([]);
  const [articles, setArticles] = useState<Array<Record<string, unknown>>>([]);
  const [variants, setVariants] = useState<Array<Record<string, unknown>>>([]);
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const [selectedArticleId, setSelectedArticleId] = useState<number | null>(null);
  const [articleDetail, setArticleDetail] = useState<Record<string, unknown> | null>(null);
  const customerGeneration = useRef(0);
  const refreshSequence = useRef(0);

  const [brandName, setBrandName] = useState("");
  const [factTitle, setFactTitle] = useState("");
  const [factBody, setFactBody] = useState("");
  const [factUrl, setFactUrl] = useState("");
  const [domain, setDomain] = useState("");
  const [sitemapUrl, setSitemapUrl] = useState("");
  const [topic, setTopic] = useState("");

  const [chromeProfiles, setChromeProfiles] = useState<ChromeProfile[]>([]);
  const [legacyChromeProfiles, setLegacyChromeProfiles] = useState<ChromeProfile[]>([]);
  const [chromeSelected, setChromeSelected] = useState("");
  const [chromeRoot, setChromeRoot] = useState("");
  const [chromeCreatePlatform] = useState("baijiahao");
  const [chromeBusy, setChromeBusy] = useState(false);

  const applyChromeSnapshot = useCallback((res: ContentChromeSnapshot) => {
    setChromeProfiles(res.profiles || []);
    setLegacyChromeProfiles(res.legacy_profiles || []);
    setChromeSelected(res.selected || "");
    setChromeRoot(res.root || "");
  }, []);

  const refreshChrome = useCallback(async () => {
    const generation = customerGeneration.current;
    const res = await api.contentChromeProfiles();
    if (generation !== customerGeneration.current) return;
    applyChromeSnapshot(res);
  }, [applyChromeSnapshot]);

  const refresh = useCallback(async () => {
    const generation = customerGeneration.current;
    const sequence = ++refreshSequence.current;
    const [c, s, si, a, j] = await Promise.all([
      api.contentCompleteness(),
      api.contentSources(),
      api.contentSites(),
      api.contentArticles(),
      api.contentJobs(),
    ]);
    if (generation !== customerGeneration.current || sequence !== refreshSequence.current) return;
    setCompleteness({
      score: c.score,
      can_publish: c.can_publish,
      missing: c.missing || [],
      verified_fact_count: c.verified_fact_count,
      site_count: c.site_count,
    });
    setSources(s.sources || []);
    setSites(si.sites || []);
    setArticles(a.articles || []);
    setJobs(j.jobs || []);
    await refreshChrome();
  }, [refreshChrome]);

  useEffect(() => {
    // Switching customer must reset soft-article chrome selection to that customer's scope.
    customerGeneration.current += 1;
    refreshSequence.current += 1;
    setChromeSelected("");
    setChromeProfiles([]);
    setCompleteness(null);
    setSources([]);
    setSites([]);
    setArticles([]);
    setVariants([]);
    setJobs([]);
    setSelectedArticleId(null);
    setArticleDetail(null);
    void refresh().catch((e) => notify(String(e), "err"));
  }, [refresh, notify, activeCustomerId]);

  useEffect(() => {
    const consumeRootSync = (event: Event) => {
      const snapshot = (event as CustomEvent<ContentChromeSnapshot>).detail;
      if (snapshot) applyChromeSnapshot(snapshot);
    };
    window.addEventListener(CONTENT_CHROME_SYNC_EVENT, consumeRootSync);
    return () => window.removeEventListener(CONTENT_CHROME_SYNC_EVENT, consumeRootSync);
  }, [applyChromeSnapshot]);

  useEffect(() => {
    const syncLogin = () => {
      if (document.visibilityState !== "visible") return;
      void refreshChrome().catch((e) => notify(`软文登录态同步失败：${String(e)}`, "warn"));
    };
    document.addEventListener("visibilitychange", syncLogin);
    window.addEventListener("focus", syncLogin);
    return () => {
      document.removeEventListener("visibilitychange", syncLogin);
      window.removeEventListener("focus", syncLogin);
    };
  }, [notify, refreshChrome]);

  async function selectContentChrome(name: string) {
    setChromeBusy(true);
    try {
      const hit = chromeProfiles.find((p) => p.name === name);
      await api.contentChromeSelect(name, hit?.platform || chromeCreatePlatform);
      setChromeSelected(name);
      notify(`已选择软文发布账号 ${name}`, "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function confirmLegacyContentChrome(name: string, platform: string) {
    setChromeBusy(true);
    try {
      await api.contentChromeConfirmLegacy(name, platform);
      await refreshChrome();
      notify(`已明确将「${name}」确认为软文账号`, "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function openContentChrome() {
    if (!chromeSelected) {
      notify("请先选择软文发布账号", "warn");
      return;
    }
    setChromeBusy(true);
    try {
      const hit = chromeProfiles.find((p) => p.name === chromeSelected);
      const plat = hit?.platform || chromeCreatePlatform;
      await api.contentChromeSelect(chromeSelected, plat);
      await api.contentChromeOpen(chromeSelected, plat, false);
      notify("已打开软文官方入口，请本人登录", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function bindContentMessageAccount() {
    if (!chromeSelected) {
      notify("请先选择软文发布账号", "warn");
      return;
    }
    const hit = chromeProfiles.find((p) => p.name === chromeSelected);
    const platform = hit?.platform || chromeCreatePlatform;
    setChromeBusy(true);
    try {
      await api.contentMessageAccountCreate({
        platform,
        profile_name: chromeSelected,
        display_name: chromeSelected,
        enabled: true,
      });
      notify(`已将「${chromeSelected}」加入软文消息巡检`, "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  useEffect(() => {
    if (!activeCustomerId) {
      setBrandName("");
      return;
    }
    const generation = customerGeneration.current;
    void api
      .listCustomers()
      .then((list) => {
        if (generation !== customerGeneration.current) return;
        const hit = list.find((c) => c.id === activeCustomerId);
        const brand = (hit?.profile?.brand as Record<string, unknown> | undefined) || {};
        setBrandName(String(brand.display_name || ""));
      })
      .catch(() => {
        /* ignore */
      });
  }, [activeCustomerId]);

  async function saveBrandName() {
    if (!activeCustomerId) {
      notify("请先选择客户", "err");
      return;
    }
    const name = brandName.trim();
    if (!name) {
      notify("请填写品牌显示名", "warn");
      return;
    }
    setBusy(true);
    try {
      await api.updateCustomer(activeCustomerId, { brand: { display_name: name } });
      notify("品牌显示名已保存", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function addFact() {
    if (!factTitle.trim() || !factBody.trim()) {
      notify("请填写事实标题与正文", "warn");
      return;
    }
    setBusy(true);
    try {
      const created = await api.contentCreateSource({
        title: factTitle.trim(),
        body: factBody.trim(),
        source_url: factUrl.trim(),
        verified: true,
        verified_by: "operator",
      });
      if (created.source?.id) {
        await api.contentVerifySource(Number(created.source.id));
      }
      setFactTitle("");
      setFactBody("");
      setFactUrl("");
      notify("已添加并确认事实", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function addSite() {
    if (!domain.trim()) {
      notify("请填写域名", "warn");
      return;
    }
    setBusy(true);
    try {
      const raw = domain.trim().replace(/\/+$/, "");
      const host = raw.replace(/^https?:\/\//i, "").split("/")[0] || raw;
      await api.contentCreateSite({
        domain: host,
        role: "primary",
        sitemap_url: sitemapUrl.trim(),
        publish_url: raw.startsWith("http") ? raw : `https://${host}`,
      });
      setDomain("");
      setSitemapUrl("");
      notify("已添加托管域名", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!topic.trim()) {
      notify("请填写选题主题", "warn");
      return;
    }
    setBusy(true);
    try {
      const res = await api.contentGenerateArticle({
        topic: topic.trim(),
        platforms: ["website", "baijiahao", "toutiao", "wechat_mp"],
      });
      setSelectedArticleId(Number(res.article.id));
      setArticleDetail(res.article);
      setVariants(res.variants || []);
      notify("已生成主事实稿与平台变体（待审核）", "ok");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function loadArticle(id: number) {
    setBusy(true);
    try {
      const res = await api.contentGetArticle(id);
      setSelectedArticleId(id);
      setArticleDetail(res.article);
      setVariants(res.variants || []);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function approve(id: number) {
    if (articleDetail == null || Number(articleDetail.id) !== id) {
      notify("请先展开并核对主稿正文、事实引用与合规结果", "warn");
      return;
    }
    setBusy(true);
    try {
      await api.contentApproveArticle(id);
      notify("已审核通过", "ok");
      await refresh();
      await loadArticle(id);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function enqueueAndDryRun(variantId: number) {
    if (!chromeSelected) {
      notify("请先创建并选择软文发布账号", "warn");
      return;
    }
    setBusy(true);
    try {
      const enq = await api.contentEnqueueJob(variantId, chromeSelected);
      const run = await api.contentRunJob(Number(enq.job.id), true, false);
      if (run.need_human) {
        notify("需要人工验证：请打开官方页完成后点继续", "warn");
      } else if (run.ok) {
        notify("dry-run 发布校验通过", "ok");
      } else {
        notify("发布任务未成功，请查看队列状态", "warn");
      }
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function resumeJob(jobId: number) {
    setBusy(true);
    try {
      const run = await api.contentRunJob(jobId, true, true);
      notify(run.ok ? "已继续任务（dry-run）" : "继续后仍需人工或失败", run.ok ? "ok" : "warn");
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function prepareLive(jobId: number, platform: string) {
    if (!chromeSelected) {
      notify("请先选择软文发布账号", "warn");
      return;
    }
    setBusy(true);
    try {
      const hit = chromeProfiles.find((p) => p.name === chromeSelected);
      await api.contentChromeOpen(chromeSelected, hit?.platform || platform, false);
      const run = await api.contentRunJob(jobId, false, false);
      notify(
        run.need_human
          ? "已进入 Live 人工发布：系统不会自动点发布；完成后须回填 URL 或审核号"
          : "Live 准备完成",
        "info",
      );
      await refresh();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  const selected = articleDetail && Number(articleDetail.id) === selectedArticleId ? articleDetail : null;
  const needBrand = Boolean(completeness && !completeness.can_publish && (completeness.missing || []).some((m) => m.includes("品牌")));

  return (
    <div className="article-workbench">
      <div className="panel-head">
        <h2>SEO / GEO 软文</h2>
        <span className="count">完整度 {completeness?.score ?? 0}%</span>
      </div>
      <p className="hint">
        先过门禁（品牌名 + ≥3 条事实 + ≥1 域名）→ 生成 → 审核 → 用本区软文 Chrome 入队。仅自有站点与本人账号；验证停人；不做站群。
      </p>

      <div className="panel-block">
        <h3>0. 品牌显示名（门禁）</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          对外署名，写入客户档案。也可在「设置→品牌」修改。与生产页 Logo 角标无关。
        </p>
        <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8 }}>
          <input
            value={brandName}
            onChange={(e) => setBrandName(e.target.value)}
            placeholder="例如：客户品牌名"
            style={{ minWidth: 200 }}
            disabled={!activeCustomerId}
          />
          <button type="button" className="primary" disabled={busy || !activeCustomerId} onClick={() => void saveBrandName()}>
            保存品牌名
          </button>
          {onGoBrand ? (
            <button type="button" disabled={busy} onClick={onGoBrand}>
              去设置·品牌
            </button>
          ) : null}
        </div>
      </div>

      {completeness && !completeness.can_publish ? (
        <p className="hint" style={{ color: "var(--danger)" }}>
          发布门禁未过：{(completeness.missing || []).join("；") || "资料不完整"}
          {needBrand && onGoBrand ? (
            <>
              {" · "}
              <button type="button" onClick={onGoBrand}>
                去填品牌名
              </button>
            </>
          ) : null}
        </p>
      ) : (
        <p className="path">
          已确认事实 {completeness?.verified_fact_count ?? 0} · 托管域名 {completeness?.site_count ?? 0} ·
          可进入审核发布
        </p>
      )}

      <div className="panel-block">
        <h3>软文发布账号</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          与视频账号物理隔离。统一入口在「设置 · 账号管理」。当前 {chromeProfiles.length} 个
          {chromeSelected ? ` · 当前 ${chromeSelected}` : ""}。
        </p>
        <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
          {onGoAccounts ? (
            <button type="button" className="primary" onClick={onGoAccounts}>
              打开账号管理
            </button>
          ) : null}
          <select
            value={chromeSelected}
            onChange={(e) => void selectContentChrome(e.target.value)}
            disabled={chromeBusy || chromeProfiles.length === 0}
          >
            {chromeProfiles.length === 0 && <option value="">暂无软文配置</option>}
            {chromeProfiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label ? `${p.name}（${p.label}）` : p.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="primary"
            disabled={chromeBusy || !chromeSelected}
            onClick={() => void openContentChrome()}
          >
            打开登录
          </button>
          <button
            type="button"
            disabled={chromeBusy || !chromeSelected}
            onClick={() => void bindContentMessageAccount()}
          >
            加入软文消息巡检
          </button>
          <button type="button" disabled={chromeBusy} onClick={() => void refreshChrome()}>
            刷新登录状态
          </button>
        </div>
        <p className="path" style={{ marginTop: 8 }}>
          {chromeSelected ? `当前软文账号：${chromeSelected}` : "未选择软文发布账号"}
          {chromeRoot ? ` · ${chromeRoot}` : ""}
        </p>
        {chromeSelected ? (
          <p className="hint">
            登录状态：
            {chromeProfiles.find((profile) => profile.name === chromeSelected)?.login_status ===
            "verified_logged_in"
              ? "实时确认已登录"
              : chromeProfiles.find((profile) => profile.name === chromeSelected)?.login_status ===
                  "logged_out"
                ? "实时确认未登录"
                : "状态未知或已过期（Cookie 仅作诊断，不算已登录）"}
          </p>
        ) : null}
        {legacyChromeProfiles.length ? (
          <details style={{ marginTop: 10 }}>
            <summary>发现 {legacyChromeProfiles.length} 个历史未确认配置</summary>
            <p className="hint">
              历史配置默认停用且不会出现在软文消息中。只有你明确确认用途后才会启用。
            </p>
            <div className="review-list">
              {legacyChromeProfiles.map((profile) => (
                <article key={profile.name} className="review-card">
                  <strong>{profile.name}</strong>
                  <span className="hint">{profile.platform || "未绑定平台"}</span>
                  <button
                    type="button"
                    disabled={chromeBusy || !profile.platform}
                    onClick={() =>
                      void confirmLegacyContentChrome(
                        profile.name,
                        profile.platform || chromeCreatePlatform,
                      )
                    }
                  >
                    明确确认为软文账号
                  </button>
                </article>
              ))}
            </div>
          </details>
        ) : null}
      </div>

      <div className="panel-block">
        <h3>1. 事实库</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          录入可核验事实（营业时间、配送范围、资质等）。至少 3 条已确认后才能过门禁。禁止未证实宣传。
        </p>
        <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8 }}>
          <input
            value={factTitle}
            onChange={(e) => setFactTitle(e.target.value)}
            placeholder="事实标题"
            style={{ minWidth: 160 }}
          />
          <input
            value={factUrl}
            onChange={(e) => setFactUrl(e.target.value)}
            placeholder="来源 URL（可选）"
            style={{ minWidth: 200, flex: 1 }}
          />
        </div>
        <textarea
          value={factBody}
          onChange={(e) => setFactBody(e.target.value)}
          placeholder="可核验事实正文（禁止未证实宣传）"
          rows={3}
          style={{ width: "100%", marginTop: 8 }}
        />
        <div className="actions-inline" style={{ marginTop: 8 }}>
          <button type="button" className="primary" disabled={busy} onClick={() => void addFact()}>
            添加并确认事实
          </button>
        </div>
        <div className="review-list" style={{ marginTop: 8 }}>
          {sources.slice(0, 6).map((s) => (
            <article key={String(s.id)} className="review-card">
              <div className="review-meta">
                <strong>#{String(s.id)}</strong>
                <span>{String(s.title || "")}</span>
                <em className={s.verified ? "badge-ok" : "badge-mute"}>
                  {s.verified ? "已确认" : "未确认"}
                </em>
              </div>
              <p className="path">{String(s.body || "").slice(0, 160)}</p>
            </article>
          ))}
          {sources.length === 0 ? <p className="empty">暂无事实</p> : null}
        </div>
      </div>

      <div className="panel-block">
        <h3>2. 官网 / 多域名</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          只登记<strong>客户自有</strong>站点。域名填主机名（如 <code>www.example.com</code>，可不带
          https）；sitemap 可选，有则填完整 URL，便于后续收录提交。多域名默认同一 canonical
          组，避免站群铺量。
        </p>
        <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8 }}>
          <input
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="www.example.com"
            style={{ minWidth: 180 }}
            title="主机名或完整 URL；保存时会规范化为域名"
          />
          <input
            value={sitemapUrl}
            onChange={(e) => setSitemapUrl(e.target.value)}
            placeholder="sitemap（可选）https://www.example.com/sitemap.xml"
            style={{ minWidth: 240, flex: 1 }}
          />
          <button type="button" className="primary" disabled={busy} onClick={() => void addSite()}>
            添加域名
          </button>
        </div>
        <div className="review-list" style={{ marginTop: 8 }}>
          {sites.map((s) => (
            <article key={String(s.id)} className="review-card">
              <div className="review-meta">
                <strong>{String(s.domain)}</strong>
                <span>{String(s.role)}</span>
                <span>canonical:{String(s.canonical_group)}</span>
              </div>
              <p className="path">{String(s.sitemap_url || s.publish_url || "")}</p>
            </article>
          ))}
          {sites.length === 0 ? <p className="empty">暂无托管域名 — 至少添加一个自有官网</p> : null}
        </div>
      </div>

      <div className="panel-block">
        <h3>3. 生成主事实稿</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          基于已确认事实生成主稿，并出官网 / 百家号 / 头条 / 公众号变体；生成后须人工审核。
        </p>
        <div className="actions-inline" style={{ gap: 8, flexWrap: "wrap" }}>
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="选题主题，例如：本地配送时效说明"
            style={{ minWidth: 280, flex: 1 }}
          />
          <button type="button" className="primary" disabled={busy} onClick={() => void generate()}>
            {busy ? "生成中…" : "生成（官网+百家号+头条+公众号）"}
          </button>
          <button type="button" disabled={busy} onClick={() => void refresh()}>
            刷新
          </button>
        </div>
      </div>

      <div className="panel-block">
        <h3>4. 文章与变体</h3>
        <div className="review-list">
          {articles.slice(0, 12).map((a) => (
            <article key={String(a.id)} className="review-card">
              <div className="review-meta">
                <strong>#{String(a.id)}</strong>
                <span>{String(a.title || "")}</span>
                <div className="review-meta-badges">
                  <em className={reviewStatusBadgeClass(a.status)}>{uiStatusLabel(a.status)}</em>
                </div>
              </div>
              <p className="path">{String(a.summary || "").slice(0, 120)}</p>
              <div className="actions-inline">
                <button type="button" onClick={() => void loadArticle(Number(a.id))}>
                  展开正文与审核依据
                </button>
                {a.status === "approved" ? (
                  <em className="badge-ok">已通过</em>
                ) : null}
              </div>
            </article>
          ))}
          {articles.length === 0 ? <p className="empty">暂无文章</p> : null}
        </div>

        {selected ? (
          <>
            <h4 style={{ marginTop: 12 }}>主稿核对 · #{String(selected.id)}</h4>
            <article className="review-card">
              <h3>{String(selected.title || "（无标题）")}</h3>
              <p className="hint">{String(selected.summary || "")}</p>
              <pre className="dry-raw" style={{ whiteSpace: "pre-wrap", maxHeight: 420 }}>
                {String(selected.body_md || "")}
              </pre>
              <div className="review-meta" style={{ marginTop: 8 }}>
                <span>事实 ID：{(Array.isArray(selected.fact_ids) ? selected.fact_ids : []).join("、") || "无"}</span>
                <span>引用：{Array.isArray(selected.citations) ? selected.citations.length : 0}</span>
                <em
                  className={
                    (selected.compliance as Record<string, unknown> | undefined)?.passed
                      ? "badge-ok"
                      : "badge-mute"
                  }
                >
                  {(selected.compliance as Record<string, unknown> | undefined)?.passed
                    ? "合规通过"
                    : "合规未通过"}
                </em>
              </div>
              {Array.isArray(
                (selected.compliance as Record<string, unknown> | undefined)?.issues,
              ) &&
              (
                (selected.compliance as Record<string, unknown>).issues as Array<
                  Record<string, unknown>
                >
              ).length > 0 ? (
                <ul className="hint">
                  {(
                    (selected.compliance as Record<string, unknown>).issues as Array<
                      Record<string, unknown>
                    >
                  ).map((issue, i) => (
                    <li key={`${String(issue.code)}-${i}`}>{String(issue.message || issue.code)}</li>
                  ))}
                </ul>
              ) : null}
              {selected.status !== "approved" ? (
                <button
                  type="button"
                  className="primary"
                  disabled={
                    busy ||
                    !(selected.compliance as Record<string, unknown> | undefined)?.passed ||
                    !Array.isArray(selected.fact_ids) ||
                    selected.fact_ids.length === 0 ||
                    !String(selected.body_md || "").trim()
                  }
                  onClick={() => void approve(Number(selected.id))}
                >
                  已核对正文、事实与合规，审核通过
                </button>
              ) : (
                <em className="badge-ok">主稿已人工审核</em>
              )}
            </article>
            <h4 style={{ marginTop: 12 }}>平台变体</h4>
            <div className="review-list">
              {variants.map((v) => (
                <article key={String(v.id)} className="review-card">
                  <div className="review-meta">
                    <strong>{String(v.platform)}</strong>
                    <span>相似 {Math.round(Number(v.similarity || 0) * 100)}%</span>
                    <div className="review-meta-badges">
                      <em className={reviewStatusBadgeClass(v.status)}>{uiStatusLabel(v.status)}</em>
                    </div>
                  </div>
                  <p>{String(v.title || "")}</p>
                  <details>
                    <summary>查看变体正文与合规</summary>
                    <pre className="dry-raw" style={{ whiteSpace: "pre-wrap", maxHeight: 300 }}>
                      {String(v.body_md || "")}
                    </pre>
                    <p className="hint">
                      合规：
                      {String(
                        ((v.payload as Record<string, unknown> | undefined)?.compliance as
                          | Record<string, unknown>
                          | undefined)?.passed
                          ? "通过"
                          : "未通过",
                      )}
                    </p>
                  </details>
                  <div className="actions-inline">
                    <button
                      type="button"
                      className="primary"
                      disabled={busy || selected.status !== "approved"}
                      onClick={() => void enqueueAndDryRun(Number(v.id))}
                    >
                      入队并 dry-run
                    </button>
                  </div>
                </article>
              ))}
              {variants.length === 0 ? <p className="empty">先点「查看变体」</p> : null}
            </div>
          </>
        ) : null}
      </div>

      <div className="panel-block">
        <h3>5. 发布队列</h3>
        <p className="hint" style={{ marginBottom: 8 }}>
          Dry-run 只会进入「已校验」，绝不会记为已发布。实发只打开官方入口并等待本人发布，
          不自动点发布；发布后必须有可核验链接或平台审核号才能记为已发布。
        </p>
        <div className="review-list">
          {jobs.slice(0, 20).map((j) => (
            <article key={String(j.id)} className="review-card">
              <div className="review-meta">
                <strong>{String(j.platform)} · {String(j.profile_name || "软文发布任务")}</strong>
                <div className="review-meta-badges">
                  <em className={reviewStatusBadgeClass(j.status)}>
                    {j.status === "validated"
                      ? "Dry-run 已校验"
                      : j.status === "need_human"
                        ? "等待人工发布"
                        : uiStatusLabel(j.status)}
                  </em>
                </div>
              </div>
              <p className="path">
                {String(j.published_url || j.human_resume_url || j.error || j.idempotency_key || "")}
              </p>
              <div className="actions-inline">
                {j.status === "need_human" ? (
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => void resumeJob(Number(j.id))}
                  >
                    重新执行 Dry-run
                  </button>
                ) : null}
                {j.status === "validated" ? (
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => void prepareLive(Number(j.id), String(j.platform || ""))}
                  >
                    进入 Live（仅打开，不自动发布）
                  </button>
                ) : null}
              </div>
            </article>
          ))}
          {jobs.length === 0 ? <p className="empty">暂无发布任务</p> : null}
        </div>
      </div>
    </div>
  );
}
