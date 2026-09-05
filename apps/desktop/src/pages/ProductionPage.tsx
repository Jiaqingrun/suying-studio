import { POLL_BUDGET_MS } from "../pollBudget";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { BrandLogoPanel, type BrandLogoState } from "../BrandLogoPanel";
import { outputDisplayLabel } from "../displayId";
import { openMediaTarget } from "../openMediaTarget";
import { uiStatusLabel } from "../reviewLabels";
import { SemanticAnalysisProgress } from "../SemanticAnalysisProgress";
import { briefResult, dryRunSummary } from "../shell/briefResult";
import { PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import {
  SCENE_TOUR_UI,
  ASSET_FOLDER_OPTIONS,
  assetCategoryLabel,
  bucketLabel,
  isKnownProductionCategory,
  pipelinePhaseLabel,
  productionThemeLabel,
  PRODUCTION_CATEGORY_OPTIONS,
  topicModeLabel,
} from "../sceneTourLabels";
import type { ProduceWorkspace, SemanticBackfillStatus, Tab } from "../types";
import type { AskConfirmFn, NotifyFn } from "./pageTypes";

export interface ProductionPageProps {
  workspace: ProduceWorkspace;
  onWorkspaceChange: (w: ProduceWorkspace) => void;
  setTab: (t: Tab) => void;
  customerName: string;
  activeCustomerId?: number | null;
  productionOrientation: "portrait" | "landscape";
  setProductionOrientation: (value: "portrait" | "landscape") => void;
  brandLogo: BrandLogoState;
  brandBusy: boolean;
  saveBrandLogo: (patch: Partial<BrandLogoState>) => Promise<void>;
  notify: NotifyFn;
  theme: string;
  setTheme: (v: string) => void;
  category: string;
  setCategory: (v: string) => void;
  assetCategory: string;
  setAssetCategory: (v: string) => void;
  topicMode: string;
  setTopicMode: (v: string) => void;
  topicClusterIds: string;
  setTopicClusterIds: (v: string) => void;
  topicEvidenceIds: string;
  setTopicEvidenceIds: (v: string) => void;
  targetCount: number;
  setTargetCount: (v: number) => void;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  runDryRun: () => void;
  createJob: () => void;
  createJobQuick: () => void;
  createSceneTourJob?: () => void;
  dryResult: Record<string, unknown> | null;
  jobs: Array<Record<string, unknown>>;
  outputs: Array<Record<string, unknown>>;
  refreshAll: () => Promise<void>;
  calDay: string;
  setCalDay: (v: string) => void;
  calTheme: string;
  setCalTheme: (v: string) => void;
  calQuota: number;
  setCalQuota: (v: number) => void;
  calNote: string;
  setCalNote: (v: string) => void;
  saveCalendarDay: () => void;
  askConfirm: AskConfirmFn;
  calendar: Array<Record<string, unknown>>;
  semanticStatus: SemanticBackfillStatus | null;
  assets: Array<Record<string, unknown>>;
  jobRuleProfileId: number | null;
  setJobRuleProfileId: (id: number | null) => void;
  activeRuleSummary: string;
  ruleRotation: boolean;
  onRuleRotationChange: (enabled: boolean) => void;
  rotationPoolCount: number;
  ruleOptions: Array<{ id: number; label: string; contentCategory: string }>;
  pathHealthOk?: boolean;
  pathHealthErrors?: string[];
  onOpenJobOutput?: (opts: {
    jobId: number;
    outputId?: number;
    needsReview: boolean;
  }) => void;
}

function ProductionCategoryField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
}) {
  const known = isKnownProductionCategory(value);
  const [customMode, setCustomMode] = useState(!known && Boolean(value));

  useEffect(() => {
    if (isKnownProductionCategory(value)) {
      setCustomMode(false);
    }
  }, [value]);

  return (
    <label>
      {label}
      {customMode ? (
        <>
          <input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="行业主题名（中文）"
          />
          <button
            type="button"
            className="ghost"
            style={{ marginTop: 4 }}
            onClick={() => {
              setCustomMode(false);
              onChange("default");
            }}
          >
            改选内容类别
          </button>
        </>
      ) : (
        <select
          value={value || "default"}
          onChange={(e) => {
            if (e.target.value === "__custom__") {
              setCustomMode(true);
              onChange("");
              return;
            }
            onChange(e.target.value);
          }}
        >
          {PRODUCTION_CATEGORY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
          <option value="__custom__">自定义行业主题…</option>
        </select>
      )}
      {!known && value ? (
        <span className="hint" style={{ display: "block", marginTop: 4 }}>
          显示为：{productionThemeLabel(value)}
        </span>
      ) : null}
    </label>
  );
}

export function ProductionPage({
  workspace,
  onWorkspaceChange,
  setTab,
  customerName,
  activeCustomerId = null,
  productionOrientation,
  setProductionOrientation,
  brandLogo,
  brandBusy,
  saveBrandLogo,
  notify,
  theme,
  setTheme,
  category,
  setCategory,
  assetCategory,
  setAssetCategory,
  topicMode,
  setTopicMode,
  topicClusterIds,
  setTopicClusterIds,
  topicEvidenceIds,
  setTopicEvidenceIds,
  targetCount,
  setTargetCount,
  actionBusy,
  setActionBusy,
  runDryRun,
  createJob,
  createJobQuick,
  createSceneTourJob,
  dryResult,
  jobs,
  outputs,
  refreshAll,
  calDay,
  setCalDay,
  calTheme,
  setCalTheme,
  calQuota,
  setCalQuota,
  calNote,
  setCalNote,
  saveCalendarDay,
  askConfirm,
  calendar,
  semanticStatus,
  assets,
  setJobRuleProfileId,
  activeRuleSummary,
  ruleRotation,
  onRuleRotationChange,
  rotationPoolCount,
  pathHealthOk = true,
  pathHealthErrors = [],
  onOpenJobOutput,
}: ProductionPageProps) {
  const pathBlocked = pathHealthOk === false;
  const pathBlockTitle =
    pathBlocked
      ? (pathHealthErrors.length
          ? pathHealthErrors.join("；")
          : "路径异常或磁盘只读，禁止生产")
      : undefined;
  const latestByJob = useMemo(() => {
    const map = new Map<number, Record<string, unknown>>();
    for (const o of outputs) {
      const jid = Number(o.job_id);
      if (!Number.isFinite(jid)) continue;
      const prev = map.get(jid);
      const prevNo = Number(prev?.display_no || prev?.id || 0);
      const curNo = Number(o.display_no || o.id || 0);
      if (!prev || curNo >= prevNo) map.set(jid, o);
    }
    return map;
  }, [outputs]);

  const [pipeline, setPipeline] = useState<{
    running: Record<string, unknown> | null;
    queued_count: number;
    recent_events: Array<Record<string, unknown>>;
    worker_running: boolean;
    resource_gate?: Record<string, unknown> | null;
    clone_runtime?: Record<string, unknown> | null;
    ollama_circuit?: Record<string, unknown> | null;
    chat_probe_ok?: boolean;
  } | null>(null);
  const [sceneCoverage, setSceneCoverage] = useState<{
    ok: boolean;
    reasons: string[];
    buckets: Array<Record<string, unknown>>;
    usable_total: number;
  } | null>(null);
  const [sceneBusy, setSceneBusy] = useState<string | null>(null);

  useEffect(() => {
    setJobRuleProfileId(null);
  }, [category, productionOrientation, setJobRuleProfileId]);

  useEffect(() => {
    let cancelled = false;
    void api
      .sceneTourCoverage(productionOrientation)
      .then((res) => {
        if (cancelled) return;
        setSceneCoverage({
          ok: Boolean(res.ok),
          reasons: Array.isArray(res.reasons) ? res.reasons.map(String) : [],
          buckets: Array.isArray(res.buckets) ? res.buckets : [],
          usable_total: Number(res.usable_total || 0),
        });
      })
      .catch(() => {
        if (!cancelled) setSceneCoverage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [productionOrientation, activeCustomerId, customerName]);

  async function refreshSceneCoverage() {
    try {
      const res = await api.sceneTourCoverage(productionOrientation);
      setSceneCoverage({
        ok: Boolean(res.ok),
        reasons: Array.isArray(res.reasons) ? res.reasons.map(String) : [],
        buckets: Array.isArray(res.buckets) ? res.buckets : [],
        usable_total: Number(res.usable_total || 0),
      });
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function bootstrapSceneBuckets() {
    setSceneBusy("bootstrap");
    try {
      const res = await api.sceneTourBootstrapBuckets(productionOrientation, false);
      notify(String(res.message || "场景分类已更新"), "ok");
      await refreshSceneCoverage();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setSceneBusy(null);
    }
  }

  async function dryRunSceneTour() {
    setSceneBusy("dry");
    try {
      const res = await api.sceneTourDryRun(productionOrientation, 1);
      if (res.blocked || !res.ok) {
        const reasons = (res.reasons || []).map(String).filter(Boolean);
        notify(
          reasons.length
            ? `跟镜精品未通过：${reasons.slice(0, 3).join("；")}`
            : "跟镜精品试规划未通过",
          "warn",
        );
      } else {
        notify(
          `跟镜精品试规划通过：约 ${Number(res.clip_count || 0)} 镜 · ${String(res.title || "")}`,
          "ok",
        );
      }
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setSceneBusy(null);
    }
  }

  const hasActiveRule = activeRuleSummary !== "未启用";
  const canUseRotationPool = ruleRotation && rotationPoolCount > 0;
  const canProduce = hasActiveRule || canUseRotationPool;
  const rotationHint = ruleRotation
    ? rotationPoolCount > 0
      ? `每个任务从轮换池抽一套已保存规则并冻结（池内 ${rotationPoolCount} 条；草稿不参与）`
      : "轮换池为空，将使用当前启用规则"
    : "已关闭轮换，将使用当前启用规则";

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const snap = await api.jobsPipeline();
        if (!cancelled) {
          setPipeline({
            running: snap.running,
            queued_count: snap.queued_count,
            recent_events: snap.recent_events || [],
            worker_running: snap.worker_running,
            resource_gate: snap.resource_gate || null,
            clone_runtime: snap.clone_runtime || null,
            ollama_circuit: snap.ollama_circuit || null,
            chat_probe_ok: Boolean(snap.chat_probe_ok),
          });
        }
      } catch {
        /* ignore */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_BUDGET_MS.semanticFast);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeCustomerId]);

  return (
    <section className="page-stack production-page">
      <PageHeader
        title="生产"
        blurb="主按钮「生成跟镜精品」走画面跟述；日常日更仍可用次要入口。门禁通过后自动过审出包"
      />
      <SegmentNav
        ariaLabel="生产分区"
        value={workspace}
        onChange={onWorkspaceChange}
        items={[
          { id: "tasks", label: "任务" },
          { id: "assets", label: "素材库", badge: assets.length },
        ]}
      />

      {workspace === "tasks" ? (
        <>
          <div className="actions" data-guide="production-orientation" style={{ marginBottom: 12 }}>
            <span className="hint">本次成片</span>
            <button
              type="button"
              className={productionOrientation === "portrait" ? "primary" : undefined}
              onClick={() => setProductionOrientation("portrait")}
            >
              竖屏
            </button>
            <button
              type="button"
              className={productionOrientation === "landscape" ? "primary" : undefined}
              onClick={() => setProductionOrientation("landscape")}
            >
              横屏
            </button>
            <button type="button" onClick={() => setTab("rules")}>
              调整{productionOrientation === "portrait" ? "竖屏" : "横屏"}规则
            </button>
          </div>
          <div className="actions" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className={`primary${actionBusy === "sceneTourJob" ? " is-busy" : ""}`}
              disabled={Boolean(actionBusy) || !canProduce || pathBlocked}
              onClick={() => createSceneTourJob?.()}
              title={
                pathBlockTitle ||
                "按跟镜精品规则生成：旁白跟随画面，不走词池金句；须先完成场景分类"
              }
            >
              {actionBusy === "sceneTourJob" ? "开跑中…" : SCENE_TOUR_UI.generate}
            </button>
            <button
              type="button"
              className={actionBusy === "rushJob" ? "is-busy" : undefined}
              disabled={Boolean(actionBusy) || !canProduce || pathBlocked}
              onClick={() => createJobQuick()}
              title={
                pathBlockTitle ||
                "日常日更：创建 1 条任务并插队，当前片跑完后立刻开跑；READY 后自动过审"
              }
            >
              {actionBusy === "rushJob" ? "开跑中…" : SCENE_TOUR_UI.dailySecondary}
            </button>
            <span className="hint">
              生产服务 {pipeline?.worker_running ? "可用" : "未启动"}
              {pipeline?.running
                ? ` · 生产中${
                    pipeline.running.phase
                      ? ` · ${pipelinePhaseLabel(String(pipeline.running.phase))}`
                      : ""
                  }（${String(pipeline.running.produced_count)}/${String(pipeline.running.target_count)} · ${productionThemeLabel(String(pipeline.running.theme || ""))}）`
                : " · 当前空闲"}
              {pipeline && pipeline.queued_count > 0
                ? ` · 排队 ${pipeline.queued_count} 条`
                : ""}
            </span>
          </div>
          {sceneCoverage ? (
            <div
              className={sceneCoverage.ok ? "banner-ok" : "banner error"}
              style={{ marginBottom: 12 }}
              role="status"
            >
              <strong>{SCENE_TOUR_UI.coverage}</strong>
              {" · "}
              {sceneCoverage.ok
                ? `可用片段约 ${sceneCoverage.usable_total} 条，必选场景已齐`
                : sceneCoverage.reasons[0] || "场景覆盖不足，请先补场景分类"}
              <div className="actions" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="ghost"
                  disabled={Boolean(sceneBusy) || Boolean(actionBusy)}
                  onClick={() => void bootstrapSceneBuckets()}
                >
                  {sceneBusy === "bootstrap" ? "分类中…" : SCENE_TOUR_UI.bootstrap}
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={Boolean(sceneBusy) || Boolean(actionBusy)}
                  onClick={() => void dryRunSceneTour()}
                >
                  {sceneBusy === "dry" ? "试规划中…" : "试规划跟镜精品"}
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={Boolean(sceneBusy)}
                  onClick={() => void refreshSceneCoverage()}
                >
                  刷新覆盖
                </button>
              </div>
              {!sceneCoverage.ok && sceneCoverage.buckets.length > 0 ? (
                <p className="hint" style={{ marginTop: 6 }}>
                  {sceneCoverage.buckets
                    .filter((b) => b.required && !b.ok)
                    .slice(0, 4)
                    .map((b) => `${bucketLabel(String(b.key))} ${Number(b.have || 0)}/${Number(b.need || 1)}`)
                    .join(" · ")}
                </p>
              ) : null}
            </div>
          ) : null}
          {pathBlocked ? (
            <div className="banner error" style={{ marginBottom: 12 }} role="status">
              路径异常，禁止生产：
              {pathHealthErrors.length
                ? pathHealthErrors.join("；")
                : "请检查片库/成片/渲染目录是否可写（只读盘会拦截）"}
            </div>
          ) : null}
          {pipeline?.running ? (
            <div className="banner-ok" style={{ marginBottom: 12 }}>
              生产中
              {pipeline.running.phase
                ? ` · ${pipelinePhaseLabel(String(pipeline.running.phase))}`
                : ""}
              {" · "}
              {productionThemeLabel(String(pipeline.running.theme || ""))} ·{" "}
              {String(pipeline.running.produced_count)}/{String(pipeline.running.target_count)}
              {pipeline.running.content_category
                ? ` · 规则槽 ${String(pipeline.running.content_category)}`
                : ""}
              {pipeline.running.rule_name
                ? ` · ${String(pipeline.running.rule_name)}`
                : ""}
              {Number(pipeline.running.consecutive_ollama_infra || 0) > 0
                ? ` · 旁白基建跳过 ${String(pipeline.running.consecutive_ollama_infra)}`
                : ""}
              {pipeline.ollama_circuit &&
              String((pipeline.ollama_circuit as Record<string, unknown>).state || "") === "open"
                ? " · Ollama 熔断中"
                : ""}
              {pipeline.recent_events[0]
                ? ` · ${String(pipeline.recent_events[0].message || "")}`
                : ""}
            </div>
          ) : null}
          <BrandLogoPanel
            customerName={customerName}
            state={brandLogo}
            busy={brandBusy}
            onChange={(patch) => saveBrandLogo(patch).catch((e: unknown) => notify(String(e), "err"))}
          />
          <h3 className="section-title">手动任务</h3>
          <p className="muted">当前默认规则：{activeRuleSummary || "未启用"}</p>
          <p className="hint">{rotationHint}</p>
          <div className="grid3">
            <label>
              专题模式
              <select value={topicMode} onChange={(e) => setTopicMode(e.target.value)}>
                <option value="">{topicModeLabel("")}</option>
                <option value="single_product">{topicModeLabel("single_product")}</option>
                <option value="same_category_products">
                  {topicModeLabel("same_category_products")}
                </option>
              </select>
            </label>
            {topicMode && (
              <>
                <label>
                  聚类 ID（逗号分隔）
                  <input value={topicClusterIds} onChange={(e) => setTopicClusterIds(e.target.value)} />
                </label>
                <label>
                  官方证据 ID（逗号分隔）
                  <input value={topicEvidenceIds} onChange={(e) => setTopicEvidenceIds(e.target.value)} />
                </label>
              </>
            )}
            <ProductionCategoryField label="主题" value={theme} onChange={setTheme} />
            <label>
              内容类别（规则槽）
              <select
                value={category || "default"}
                onChange={(e) => setCategory(e.target.value)}
              >
                {PRODUCTION_CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <span className="hint" style={{ display: "block", marginTop: 4 }}>
                选「精品样式」只换规则槽，不会按文件夹名 premium 滤片库
              </span>
            </label>
            <label>
              素材目录（可选）
              <select
                value={assetCategory}
                onChange={(e) => setAssetCategory(e.target.value)}
              >
                {ASSET_FOLDER_OPTIONS.map((opt) => (
                  <option key={opt.value || "__all__"} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
                {Array.from(
                  new Set(
                    assets
                      .map((a) => String(a.category || "").trim())
                      .filter(
                        (c) =>
                          c &&
                          !ASSET_FOLDER_OPTIONS.some((o) => o.value === c) &&
                          !isKnownProductionCategory(c),
                      ),
                  ),
                ).map((c) => (
                  <option key={c} value={c}>
                    {assetCategoryLabel(c)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              目标条数
              <input
                type="number"
                value={targetCount}
                onChange={(e) => setTargetCount(Number(e.target.value))}
              />
            </label>
            <label>
              启用规则（规则实验室）
              <span className="hint" style={{ display: "block", marginTop: 4 }}>
                {activeRuleSummary}
                {!canProduce ? " · 请先在规则实验室保存并启用，或打开至少一条已保存规则的轮换开关" : ""}
              </span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={ruleRotation}
                disabled={Boolean(actionBusy)}
                onChange={(e) => onRuleRotationChange(e.target.checked)}
              />
              规则轮换（按任务抽一套）
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              onClick={() => runDryRun()}
              disabled={actionBusy === "dryRun" || !canProduce}
              className={actionBusy === "dryRun" ? "is-busy" : undefined}
            >
              {actionBusy === "dryRun" ? "预览中…" : "预览选片"}
            </button>
            <button
              type="button"
              className={`primary${actionBusy === "createJob" ? " is-busy" : ""}`}
              onClick={() => createJob()}
              disabled={actionBusy === "createJob" || !canProduce || pathBlocked}
              title={pathBlockTitle}
            >
              {actionBusy === "createJob" ? "创建中…" : "创建任务"}
            </button>
            <button type="button" className="ghost" onClick={() => setTab("rules")}>
              打开规则实验室
            </button>
          </div>
          {dryResult &&
            (() => {
              const s = dryRunSummary(dryResult);
              const clipletIds = (Array.isArray(dryResult.clips) ? dryResult.clips : [])
                .map((row) =>
                  row && typeof row === "object"
                    ? Number((row as Record<string, unknown>).cliplet_id)
                    : 0,
                )
                .filter((id) => Number.isInteger(id) && id > 0)
                .slice(0, 10);
              return (
                <div className="dry-card">
                  <div className="dry-card-head">
                    <strong>选片预览</strong>
                    <span className="dry-count">{s.count} 条候选</span>
                  </div>
                  <p className="hint">
                    主题 {s.theme} · 分类 {s.category}
                  </p>
                  {s.samples.length > 0 ? (
                    <ul className="dry-samples">
                      {s.samples.map((t, i) => (
                        <li key={`${i}-${t}`}>{t}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="hint">无候选样本</p>
                  )}
                  <div className="actions">
                    <button
                      type="button"
                      disabled={!clipletIds.length || actionBusy === "verifyCandidates"}
                      onClick={() => {
                        setActionBusy("verifyCandidates");
                        api
                          .verifyCliplets(clipletIds)
                          .then((r) => {
                            const verified = Array.isArray(r.verified) ? r.verified.length : 0;
                            const failed = Array.isArray(r.not_verified) ? r.not_verified.length : 0;
                            notify(`9B 按需验证完成：通过 ${verified}，未通过 ${failed}`, verified ? "ok" : "info");
                            return refreshAll();
                          })
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setActionBusy(null));
                      }}
                    >
                      {actionBusy === "verifyCandidates"
                        ? "9B 验证中…"
                        : `9B 验证本次候选（${clipletIds.length}）`}
                    </button>
                    <span className="hint">只检查这次准备使用的候选片段，不会扫描全部素材。</span>
                  </div>
                  <details>
                    <summary>原始 JSON</summary>
                    <pre className="dry-raw">{JSON.stringify(dryResult, null, 2)}</pre>
                  </details>
                </div>
              );
            })()}
            <table data-guide="production-jobs">
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>进度</th>
                <th>最近成片</th>
                <th>主题 / 规则槽</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {jobs.slice(0, 50).map((j) => {
                const latest = latestByJob.get(Number(j.id));
                const jobId = Number(j.id);
                const label = latest ? outputDisplayLabel(latest) : "";
                const path = String(latest?.output_path || "").trim();
                const outputId = latest != null ? Number(latest.id) : NaN;
                const taskLabel =
                  label && Number.isFinite(jobId)
                    ? `任务 #${jobId} · ${label}`
                    : Number.isFinite(jobId)
                      ? `任务 #${jobId}`
                      : "—";
                const needsReview =
                  latest?.review_status === "uncertain" ||
                  latest?.review_status === "pending" ||
                  latest?.state === "review";
                const slot = String(j.content_category || j.category || "default");
                const asset = String(j.asset_category || "").trim();
                const reasons = Array.isArray(j.ready_reasons)
                  ? (j.ready_reasons as string[]).slice(0, 2)
                  : [];
                const infraN = Number(j.consecutive_ollama_infra || 0);
                return (
                  <tr key={String(j.id)} data-guide={`job-row-${String(j.id)}`}>
                    <td>
                      {Number.isFinite(outputId) && outputId > 0 && onOpenJobOutput ? (
                        <button
                          type="button"
                          className="linkish"
                          title={needsReview ? "跳转到审片" : "跳转到发布"}
                          onClick={() =>
                            onOpenJobOutput({
                              jobId,
                              outputId,
                              needsReview,
                            })
                          }
                        >
                          {taskLabel}
                        </button>
                      ) : (
                        <span>{taskLabel}</span>
                      )}
                    </td>
                    <td>
                      {uiStatusLabel(j.status)}
                      {infraN > 0 ? (
                        <span className="hint" style={{ display: "block" }}>
                          基建跳过 {infraN}
                        </span>
                      ) : null}
                      {reasons.length ? (
                        <span className="hint" style={{ display: "block" }} title={reasons.join("；")}>
                          {reasons[0]}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      {String(j.produced_count)}/{String(j.target_count ?? "—")}
                    </td>
                    <td>
                      {path && label ? (
                        <button
                          type="button"
                          className="linkish"
                          onClick={() =>
                            void openMediaTarget(path)
                              .then(() => notify("已打开文件位置", "ok"))
                              .catch((e: unknown) => notify(String(e), "err"))
                          }
                        >
                          {label}
                        </button>
                      ) : (
                        <span className="hint" title="暂无关联成片路径">
                          {label || "—"}
                        </span>
                      )}
                    </td>
                    <td>
                      {productionThemeLabel(String(j.theme || ""))}
                      <span className="hint" style={{ display: "block" }}>
                        {slot}
                        {asset ? ` · 素材 ${asset}` : ""}
                        {j.rule_name ? ` · ${String(j.rule_name)}` : ""}
                      </span>
                    </td>
                    <td className="actions-inline">
                      <button
                        type="button"
                        disabled={!latest}
                        onClick={() => setTab(needsReview ? "review" : "publish")}
                      >
                        {needsReview ? "去审片" : "去发布"}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          api
                            .pauseJob(Number(j.id))
                            .then(() => {
                              notify("生产任务已暂停", "ok");
                              return refreshAll();
                            })
                            .catch((e: unknown) => notify(String(e), "err"))
                        }
                      >
                        暂停
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          api
                            .resumeJob(Number(j.id))
                            .then(() => {
                              notify("生产任务已恢复", "ok");
                              return refreshAll();
                            })
                            .catch((e: unknown) => notify(String(e), "err"))
                        }
                      >
                        恢复
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <h3 className="section-title">内容日历</h3>
          <div className="grid3">
            <label>
              日期
              <input type="date" value={calDay} onChange={(e) => setCalDay(e.target.value)} />
            </label>
            <label>
              主题
              <select
                value={isKnownProductionCategory(calTheme) ? calTheme : "default"}
                onChange={(e) => setCalTheme(e.target.value)}
              >
                {PRODUCTION_CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              配额
              <input
                type="number"
                value={calQuota}
                onChange={(e) => setCalQuota(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            备注
            <input value={calNote} onChange={(e) => setCalNote(e.target.value)} />
          </label>
          <div className="actions">
            <button
              type="button"
              disabled={actionBusy === "calSave"}
              className={actionBusy === "calSave" ? "is-busy" : undefined}
              onClick={() => saveCalendarDay()}
            >
              {actionBusy === "calSave" ? "保存中…" : "保存该日"}
            </button>
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  const ok = await askConfirm({
                    title: "删除日历日",
                    body: `删除 ${calDay} 的日历计划？此操作不可撤销。`,
                    confirmLabel: "删除",
                    danger: true,
                  });
                  if (!ok) return;
                  try {
                    await api.deleteCalendar(calDay);
                    notify(`已删除日历日：${calDay}`, "ok");
                    await refreshAll();
                  } catch (e) {
                    notify(String(e), "err");
                  }
                })();
              }}
            >
              删除该日
            </button>
          </div>
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>主题</th>
                <th>配额</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {calendar.map((c) => (
                <tr
                  key={String(c.day)}
                  style={{ cursor: "pointer" }}
                  onClick={() => {
                    setCalDay(String(c.day));
                    setCalTheme(String(c.theme || "default"));
                    setCalQuota(Number(c.quota || 5));
                    setCalNote(String(c.note || ""));
                  }}
                >
                  <td>{String(c.day)}</td>
                  <td>{productionThemeLabel(String(c.theme || ""))}</td>
                  <td>{String(c.quota)}</td>
                  <td>{String(c.note || "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {workspace === "assets" ? (
        <>
          <SemanticAnalysisProgress status={semanticStatus} />
          <div className="panel-head">
            <h2>素材库</h2>
            <span className="count">
              竖屏 {assets.filter((asset) => asset.orientation === "portrait").length} · 横屏{" "}
              {assets.filter((asset) => asset.orientation === "landscape").length} · 其他{" "}
              {assets.filter((asset) => !["portrait", "landscape"].includes(String(asset.orientation))).length}
            </span>
          </div>
          <div className="actions">
            <button
              type="button"
              disabled={actionBusy === "scanFull"}
              className={actionBusy === "scanFull" ? "is-busy" : undefined}
              onClick={() => {
                setActionBusy("scanFull");
                api
                  .scanAssets(0)
                  .then((r) => {
                    notify(briefResult("全量扫描已触发", r), "ok");
                    return refreshAll();
                  })
                  .catch((e: unknown) => notify(String(e), "err"))
                  .finally(() => setActionBusy(null));
              }}
            >
              {actionBusy === "scanFull" ? "扫描中…" : "全量扫描"}
            </button>
            <button
              type="button"
              onClick={() =>
                api
                  .scanStatus()
                  .then((r) => notify(briefResult("扫描状态", r), "info"))
                  .catch((e: unknown) => notify(String(e), "err"))
              }
            >
              扫描进度
            </button>
          </div>
          <table>
            <thead>
              <tr>
                <th>分类</th>
                <th>时长</th>
                <th>分辨率</th>
                <th>画幅</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={String(a.id)}>
                  <td>{assetCategoryLabel(String(a.category || ""))}</td>
                  <td>{String(a.duration_sec ?? "-")}s</td>
                  <td>
                    {String(a.width)}x{String(a.height)}
                  </td>
                  <td>
                    {a.orientation === "landscape"
                      ? "横屏"
                      : a.orientation === "portrait"
                        ? "竖屏"
                        : "其他比例"}
                  </td>
                  <td>{uiStatusLabel(a.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      <StepFooter current="produce" onJump={setTab} />
    </section>
  );
}
