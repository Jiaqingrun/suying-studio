import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { AutomationTasksPanel } from "./AutomationTasksPanel";
import { PageSection } from "./shell/PageChrome";
import type {
  ChromeProfile,
  HumanAlert,
  PublishRun,
  PublishRunItem,
} from "./types";

type Props = {
  chromeProfiles: ChromeProfile[];
  activeCustomerId: number | null;
  notify: (text: string, kind?: "ok" | "err" | "info" | "warn") => void;
  onRefresh: () => void;
};

const RUN_STORAGE_KEY = "suying_publish_run_id";
export function PublishBatchPanel({
  chromeProfiles,
  activeCustomerId,
  notify,
  onRefresh,
}: Props) {
  const storageKey = `${RUN_STORAGE_KEY}:${activeCustomerId ?? "none"}`;
  const [runId, setRunId] = useState(() => localStorage.getItem(storageKey) || "");
  const [runStatus, setRunStatus] = useState<PublishRun | null>(null);
  const [deferredItems, setDeferredItems] = useState<PublishRunItem[]>([]);
  const refreshInFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [totalCount, setTotalCount] = useState(1);
  const [alerts, setAlerts] = useState<HumanAlert[]>([]);
  const accountRows = useMemo(
    () =>
      chromeProfiles
        .filter((profile) => profile.platform)
        .map((profile) => ({
          key: `${profile.platform}:${profile.name}`,
          platform: String(profile.platform),
          chrome_profile: profile.name,
          selectable: true,
          login_status: profile.login_status || "stale_unknown",
        })),
    [chromeProfiles],
  );

  const refreshRun = useCallback(
    async (id?: string) => {
      const rid = id || runId;
      if (!rid) {
        const active = await api.reachPublishActiveStatus();
        if (active.run_id) {
          const found = String(active.run_id);
          setRunId(found);
          localStorage.setItem(storageKey, found);
          setRunStatus(active as PublishRun);
        }
        return;
      }
      const status = await api.reachPublishRunStatus(rid);
      setRunStatus(status);
      if (["completed", "failed", "cancelled", "interrupted_system"].includes(String(status.status))) {
        localStorage.removeItem(storageKey);
      }
    },
    [runId, storageKey],
  );

  const refreshPanels = useCallback(async () => {
    const [alertResult, deferredResult] = await Promise.all([
      api.humanAlerts(),
      api.reachPublishDeferred(),
    ]);
    setAlerts(alertResult.alerts || []);
    setDeferredItems(deferredResult.items || []);
  }, []);

  useEffect(() => {
    const poll = async () => {
      if (refreshInFlight.current) return;
      refreshInFlight.current = true;
      try {
        await Promise.all([refreshRun(), refreshPanels()]);
      } finally {
        refreshInFlight.current = false;
      }
    };
    void poll().catch(() => null);
    const activePolling = Boolean(
      runStatus &&
        !["completed", "failed", "cancelled", "interrupted_system"].includes(
          runStatus.status,
        ),
    );
    const timer = setInterval(() => void poll().catch(() => null), activePolling ? 1000 : 5000);
    return () => clearInterval(timer);
  }, [refreshPanels, refreshRun, runStatus?.status]);

  useEffect(() => {
    const restored = localStorage.getItem(storageKey) || "";
    setRunId(restored);
    setRunStatus(null);
  }, [storageKey]);

  useEffect(() => {
    setSelectedAccounts((previous) =>
      previous.length
        ? previous.filter((key) =>
            accountRows.some((row) => row.key === key && row.selectable),
          )
        : accountRows.filter((row) => row.selectable).map((row) => row.key),
    );
  }, [accountRows]);

  function immediateBody() {
    return {
      accounts: accountRows
        .filter((account) => account.selectable && selectedAccounts.includes(account.key))
        .map(({ platform, chrome_profile }) => ({ platform, chrome_profile })),
      total_count: totalCount,
      allocation_mode: "auto_even" as const,
      manual_counts: {},
      content_mode: "random_unique" as const,
      output_ids: [],
    };
  }

  async function startBatch() {
    if (!selectedAccounts.length) {
      notify("请至少选择一个发布账号", "err");
      return;
    }
    setBusy(true);
    try {
      const active = await api.reachPublishActiveStatus();
      if (active.active && active.run_id) {
        const activeRunId = String(active.run_id);
        setRunId(activeRunId);
        setRunStatus(active as PublishRun);
        localStorage.setItem(storageKey, activeRunId);
        notify(`已有进行中批次 ${activeRunId}，已切换到该批次，请先处理或取消`, "warn");
        return;
      }
      const body = immediateBody();
      const preview = await api.reachPublishImmediatePreview(body);
      const accountCounts = new Map<string, number>();
      preview.assignments.forEach((assignment) => {
        const profile = String(assignment.chrome_profile || "");
        accountCounts.set(profile, (accountCounts.get(profile) || 0) + 1);
      });
      const queueSummary = Array.from(accountCounts.entries())
        .map(([profile, count]) => `${profile} ${count} 条`)
        .join("、");
      const skippedSummary = (preview.skipped_accounts || [])
        .map((account) => account.chrome_profile)
        .join("、");
      const warning = skippedSummary
        ? `\n\n以下账号缺少对应平台的完整发布资产，本次只跳过这些账号且不会把次数转给别人：${skippedSummary}`
        : "";
      if (
        !window.confirm(
          `实际将创建 ${preview.actual_count} 条队列：${queueSummary}。${warning}\n\n系统会在每个账号轮到时，用同一个独立 Chrome 原进程检查登录并直接上传；不会“检查后关闭再重开”。未登录时会停在该窗口等待你完成登录。是否继续？`,
        )
      ) {
        return;
      }
      const run = await api.reachPublishImmediateStart({
        ...body,
        seed: preview.seed,
        accept_risk: true,
      });
      const nextRunId = String(run.run_id || "");
      setRunId(nextRunId);
      localStorage.setItem(storageKey, nextRunId);
      setRunStatus(run as unknown as PublishRun);
      notify(`登录检查已通过，已按预览开始发布 ${preview.actual_count} 条视频`, "ok");
      onRefresh();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(alertId: number) {
    try {
      await api.acknowledgeHumanAlert(alertId);
      await refreshPanels();
      notify("已停止该提醒的后续升级", "ok");
    } catch (error) {
      notify(String(error), "err");
    }
  }

  async function skipCurrent(retryMode: "manual" | "auto" | "none") {
    if (!runId || !current) return;
    const copy =
      retryMode === "auto"
        ? "跳过当前条，系统稍后自动补发，并立即继续后面的任务？"
        : retryMode === "manual"
          ? "跳过当前条，留到待补发列表，并立即继续后面的任务？"
          : "跳过当前条且不再补发，并立即继续后面的任务？";
    if (!window.confirm(copy)) return;
    setBusy(true);
    try {
      await api.reachPublishSkipCurrent(runId, {
        item_id: current.id,
        retry_mode: retryMode,
      });
      notify("已请求跳过当前条，后续任务将继续", "ok");
      await Promise.all([refreshRun(runId), refreshPanels()]);
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function stopBatch() {
    if (!runId || !window.confirm("确定结束本批次？未执行的条目会取消；当前条若已提交，仍需确认结果。")) return;
    setBusy(true);
    try {
      await api.reachPublishCancelRun(runId);
      notify("已请求结束批次，系统正在安全释放发布窗口", "warn");
      await refreshRun(runId);
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function confirmPublished(item: PublishRunItem) {
    if (!runId) return;
    setBusy(true);
    try {
      await api.reachPublishConfirmOutcome(runId, {
        item_id: item.id,
        outcome: "published",
        note: "用户确认发布成功",
      });
      notify("已确认发布成功", "ok");
      await Promise.all([refreshRun(runId), refreshPanels()]);
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function confirmNotPublished(
    item: PublishRunItem,
    retryMode: "manual" | "auto",
  ) {
    if (!runId) return;
    if (!window.confirm("请先确认平台作品列表中没有这条视频。确认未发布并进入补发吗？")) return;
    setBusy(true);
    try {
      await api.reachPublishConfirmOutcome(runId, {
        item_id: item.id,
        outcome: "not_published",
        retry_mode: retryMode,
        note: "用户确认未发布",
      });
      notify(retryMode === "auto" ? "已进入自动补发队列" : "已留待手动补发", "ok");
      await Promise.all([refreshRun(runId), refreshPanels()]);
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function retryDeferred() {
    if (!deferredItems.length) return;
    if (!window.confirm(`确定补发 ${deferredItems.length} 条待处理视频？`)) return;
    setBusy(true);
    try {
      const run = await api.reachPublishRetryDeferred(deferredItems.map((item) => item.id));
      setRunId(run.run_id);
      setRunStatus(run);
      localStorage.setItem(storageKey, run.run_id);
      notify("待处理视频已开始补发", "ok");
      await refreshPanels();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  const status = String(runStatus?.status || "idle");
  const phase = String(runStatus?.phase || "");
  const current = runStatus?.current_item || null;
  const needHuman = ["paused_human", "outcome_unknown", "waiting_login"].includes(status);
  const statusLabel: Record<string, string> = {
    idle: "还没有开始",
    queued: "准备中",
    running: "正在发布",
    paused_human: "需要你操作",
    waiting_login: "待扫码（有限宽限）",
    outcome_unknown: "等待确认结果",
    completed: "发布完成",
    failed: "发布已停止",
    cancelled: "已取消",
    interrupted_system: "系统暂停",
    stopping: "正在安全结束",
  };
  const phaseLabel: Record<string, string> = {
    queued: "等待执行",
    switching_profile: "切换账号",
    waiting_login: "待扫码（有限宽限）",
    soft_skipped: "已跳过后补",
    uploading: "上传视频",
    filling_copy: "填写文案",
    setting_cover: "设置封面",
    submitting: "提交发布",
    verifying: "核验结果",
    paused_human: "等待人工处理",
    outcome_unknown: "等待确认结果",
    awaiting_confirmation: "待确认",
    published: "已发布",
    deferred: "已跳过待补发",
    failed: "失败",
    skipped: "已跳过",
    cancelled: "已取消",
  };
  const counts = runStatus?.counts || {
    total: runStatus?.items?.length || 0,
    published: 0,
    failed: 0,
    deferred: 0,
    cancelled: 0,
    remaining: 0,
  };
  const finishedCount = Math.max(0, counts.total - counts.remaining);
  const progress = counts.total ? Math.round((finishedCount / counts.total) * 100) : 0;

  return (
    <div
      className="page-stack publish-batch-panel"
      data-guide={runId ? `publish-run-${runId}` : "publish-run-current"}
    >
      {alerts.length || needHuman ? (
        <PageSection
          id="human-return"
          title="现在需要你操作"
          description={
            alerts.some((alert) => alert.kind === "profile_busy")
              ? "账号 Chrome 窗口正占用登录目录。请关闭多余窗口，或直接点继续让系统接管已打开的页面。"
              : alerts.some((alert) => alert.kind === "login_required")
                ? "请尽快在已打开的 Chrome 完成扫码登录；宽限到期会跳过该号并继续其它账号，已跳过的可在「自动任务」补发。"
                : alerts.some((alert) => alert.kind === "publish_window_missed")
                  ? "有发布窗口已错过，不会自动重开；请人工补发安全待补条目。"
                  : alerts.some((alert) => alert.kind === "verification_required")
                  ? "请在已打开的 Chrome 完成真实短信/滑块验证；帮助文案里的「验证」可忽略。"
                  : "请按下面提示处理已打开的 Chrome，或确认发布结果。"
          }
          status={`${Math.max(alerts.length, 1)} 项`}
          tone="attention"
        >
          <div className="review-list">
            {alerts.map((alert) => (
              <article key={alert.id} className="human-alert-card">
                <strong>{alert.summary}</strong>
                <div className="actions-inline">
                  <button type="button" className="primary" onClick={() => void acknowledge(alert.id)}>
                    我现在去处理
                  </button>
                </div>
              </article>
            ))}
            {needHuman && !alerts.length ? (
              <article className="human-alert-card">
                <strong>
                  {phase === "outcome_unknown"
                    ? "请先到平台作品列表确认这条视频是否已经发布"
                    : String(runStatus?.error || current?.error || "").includes("占用")
                      ? String(runStatus?.error || current?.error)
                      : String(runStatus?.error || current?.error || "请按提示完成 Chrome 中的操作后继续")}
                </strong>
              </article>
            ) : null}
          </div>
        </PageSection>
      ) : null}

      <PageSection
        id="batch-execution"
        title="立即发布"
        description="系统自动选择合格待发视频，按账号平均分配并依次发布。"
        status={statusLabel[status] || "处理中"}
        tone={status === "failed" ? "attention" : status === "completed" ? "success" : "default"}
      >
        <p className="hint">
          配置库共 {accountRows.length} 个账号 · 可进入发布 {accountRows.filter((account) => account.selectable).length} 个
          · 实时确认已登录 {accountRows.filter((account) => account.login_status === "verified_logged_in").length} 个
        </p>
        {accountRows.length > 1 ? (
          <>
            <h3>选择要使用的账号</h3>
            <div className="publish-account-grid">
              {accountRows.map((account) => (
                <label key={account.key} className="publish-account-choice">
                  <input
                    type="checkbox"
                    checked={account.selectable && selectedAccounts.includes(account.key)}
                    disabled={!account.selectable}
                    onChange={() =>
                      setSelectedAccounts((previous) =>
                        previous.includes(account.key)
                          ? previous.filter((key) => key !== account.key)
                          : [...previous, account.key],
                      )
                    }
                  />
                  <span>
                    {account.chrome_profile}
                    <small>
                      {account.login_status === "verified_logged_in"
                        ? "实时确认已登录"
                        : account.login_status === "logged_out"
                          ? "需扫码登录"
                          : account.login_status === "checking"
                            ? "检查中"
                            : "未开窗 · 状态未知"}
                    </small>
                  </span>
                </label>
              ))}
            </div>
          </>
        ) : (
          <p className="publish-account-summary">发布账号：{accountRows[0]?.chrome_profile || "尚未添加"}</p>
        )}
        <div className="publish-quick-action">
          <label>
            这次发几条
            <input
              type="number"
              min={1}
              max={200}
              value={totalCount}
              onChange={(event) => setTotalCount(Math.max(1, Number(event.target.value) || 1))}
            />
          </label>
          <button
            type="button"
            className="primary publish-start-button"
            disabled={busy || !selectedAccounts.length || status === "running"}
            onClick={() => void startBatch()}
          >
            {busy || status === "running" ? "正在发布…" : "开始发布"}
          </button>
        </div>
        {runStatus && counts.total > 0 ? (
          <div className="publish-run-card" aria-live="polite">
            <div className="publish-run-head">
              <div>
                <strong>
                  {statusLabel[status] || "处理中"} · {finishedCount}/{counts.total}
                </strong>
                <p className="hint">
                  成功 {counts.published} · 待补 {counts.deferred} · 失败 {counts.failed} · 剩余{" "}
                  {counts.remaining}
                </p>
              </div>
              <span>{progress}%</span>
            </div>
            <div className="publish-run-progress" aria-label={`发布进度 ${progress}%`}>
              <span style={{ width: `${progress}%` }} />
            </div>
            {current ? (
              <div className="publish-current-item">
                <strong>
                  当前第 {current.ordinal + 1} 条 · {current.platform} · {current.chrome_profile}
                </strong>
                <span>{phaseLabel[current.phase] || current.phase}</span>
                {current.title ? <p>{current.title}</p> : null}
                {current.error ? <p className="error">{current.error}</p> : null}
                {current.updated_at ? (
                  <small>最近更新：{new Date(current.updated_at).toLocaleTimeString()}</small>
                ) : null}
              </div>
            ) : null}
            <div className="publish-stage-track">
              {[
                "switching_profile",
                "waiting_login",
                "uploading",
                "filling_copy",
                "setting_cover",
                "submitting",
                "verifying",
              ].map((stage) => (
                <span key={stage} className={phase === stage ? "is-current" : ""}>
                  {phaseLabel[stage]}
                </span>
              ))}
            </div>
            <div className="actions-inline publish-control-actions">
              {current?.can_skip ? (
                <>
                  <button type="button" disabled={busy} onClick={() => void skipCurrent("manual")}>
                    跳过，稍后手动补发
                  </button>
                  <button type="button" disabled={busy} onClick={() => void skipCurrent("auto")}>
                    跳过，稍后自动补发
                  </button>
                </>
              ) : null}
              {current?.requires_outcome_confirmation ? (
                <>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => void confirmPublished(current)}
                  >
                    确认已发布
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void confirmNotPublished(current, "manual")}
                  >
                    确认未发布，稍后补发
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void confirmNotPublished(current, "auto")}
                  >
                    确认未发布，自动补发
                  </button>
                </>
              ) : null}
              {runStatus.can_stop ? (
                <button type="button" className="danger" disabled={busy} onClick={() => void stopBatch()}>
                  结束本批次
                </button>
              ) : null}
            </div>
            <details className="publish-item-list">
              <summary>查看每条发布状态</summary>
              <ol>
                {runStatus.items.map((item) => (
                  <li key={item.id}>
                    <span>
                      {item.ordinal + 1}. {item.platform} · {item.chrome_profile}
                    </span>
                    <strong>{phaseLabel[item.phase] || item.phase}</strong>
                    {item.error ? <small>{item.error}</small> : null}
                    {item.requires_outcome_confirmation ? (
                      <div className="actions-inline publish-item-confirm">
                        <button type="button" disabled={busy} onClick={() => void confirmPublished(item)}>
                          已发布
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void confirmNotPublished(item, "manual")}
                        >
                          未发布，待补发
                        </button>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ol>
            </details>
          </div>
        ) : null}
        {deferredItems.length ? (
          <div className="publish-deferred-card">
            <div>
              <strong>待补发 {deferredItems.length} 条</strong>
              <p className="hint">均为已确认未发布或在提交前安全跳过的条目。</p>
            </div>
            <button
              type="button"
              disabled={
                busy ||
                Boolean(
                  runStatus &&
                    !["completed", "failed", "cancelled", "interrupted_system"].includes(
                      runStatus.status,
                    ),
                )
              }
              onClick={() => void retryDeferred()}
            >
              手动补发待处理项
            </button>
          </div>
        ) : null}
        {needHuman && phase !== "outcome_unknown" ? (
          <p className="human-alert-card">
            {alerts.some((alert) => alert.kind === "profile_busy") ||
            String(runStatus?.error || "").includes("占用")
              ? "关闭多余 Chrome 窗口后点继续；若官方页已打开，系统会自动接管同一登录目录。"
              : alerts.some((alert) => alert.kind === "verification_required")
                ? "完成真实短信/滑块后，系统会自动继续。"
                : "在 Chrome 完成必要操作后，系统会自动继续。"}
          </p>
        ) : null}
      </PageSection>

      <AutomationTasksPanel chromeProfiles={chromeProfiles} notify={notify} />
    </div>
  );
}
