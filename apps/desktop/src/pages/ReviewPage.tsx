import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { isTauri } from "../engineControl";
import {
  bindOutputFileDrag,
  bindOutputFilePointerDown,
  outputDragHtmlEnabled,
} from "../mediaDrag";
import { previewCoverSrc, previewVideoSrc } from "../mediaPreview";
import { outputDisplayLabel } from "../displayId";
import { openMediaTarget } from "../openMediaTarget";
import {
  isAutoApproveNote,
  outputStateLabel,
  reviewNoteLabel,
  reviewSourceLabel,
  reviewStatusBadgeClass,
  reviewStatusLabel,
} from "../reviewLabels";
import { bucketLabel, roleLabel, SCENE_TOUR_UI } from "../sceneTourLabels";
import { EmptyState, PageHeader, StepFooter } from "../shell/PageChrome";
import type { Tab } from "../types";
import type { NotifyFn } from "./pageTypes";

const SKIP_KNOWN_KEY = "suying.review.skipKnownIssues.v1";
const REVIEW_COVER_SLOTS = 3;

type CoverLightbox = {
  oid: number;
  index: number;
  path: string | null;
  label: string;
};

export interface ReviewPageProps {
  active?: boolean;
  setTab: (t: Tab) => void;
  readyOutputs: Array<Record<string, unknown>>;
  cinemaMode: boolean;
  setCinemaMode: Dispatch<SetStateAction<boolean>>;
  reviewFilter: "all" | "missing_voice" | "missing_sub" | "tts_bad";
  setReviewFilter: (v: "all" | "missing_voice" | "missing_sub" | "tts_bad") => void;
  missingVoiceCount: number;
  ttsBadCount: number;
  batchBusy: boolean;
  setBatchBusy: (v: boolean) => void;
  notify: NotifyFn;
  refreshAll: () => Promise<void>;
  setMediaEpoch: Dispatch<SetStateAction<number>>;
  reviewReason: string;
  setReviewReason: (v: string) => void;
  reviewReasons: Array<{ code: string; label: string }>;
  reviewNote: string;
  setReviewNote: (v: string) => void;
  reviewFocusId: number | null;
  setReviewFocusId: (v: number | null) => void;
  mediaEpoch: number;
  activeCustomerId: number | null;
  reviewBusyId: number | null;
  decideReview: (oid: number, decision: "approved" | "rejected", rerender?: boolean) => void;
  rerenderOnly: (oid: number) => void;
}

export function ReviewPage({
  active = true,
  setTab,
  readyOutputs,
  cinemaMode,
  setCinemaMode,
  reviewFilter,
  setReviewFilter,
  missingVoiceCount,
  ttsBadCount,
  batchBusy,
  setBatchBusy,
  notify,
  refreshAll,
  setMediaEpoch,
  reviewReason,
  setReviewReason,
  reviewReasons,
  reviewNote,
  setReviewNote,
  reviewFocusId,
  setReviewFocusId,
  mediaEpoch,
  activeCustomerId,
  reviewBusyId,
  decideReview,
  rerenderOnly,
}: ReviewPageProps) {
  const pendingCount = readyOutputs.length;
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [coverLightbox, setCoverLightbox] = useState<CoverLightbox | null>(null);
  const [skipKnownIssues, setSkipKnownIssues] = useState(() => {
    try {
      const v = localStorage.getItem(SKIP_KNOWN_KEY);
      if (v === "false") return false;
      if (v === "true") return true;
    } catch {
      /* ignore */
    }
    return true;
  });

  useEffect(() => {
    try {
      localStorage.setItem(SKIP_KNOWN_KEY, skipKnownIssues ? "true" : "false");
    } catch {
      /* ignore */
    }
  }, [skipKnownIssues]);

  useEffect(() => {
    if (!coverLightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCoverLightbox(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [coverLightbox]);

  async function loadHistory() {
    if (historyBusy) return;
    setHistoryBusy(true);
    try {
      setHistory(await api.listReviews("history", 100));
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setHistoryBusy(false);
    }
  }

  async function runBatchApprove() {
    if (batchBusy) return;
    setBatchBusy(true);
    try {
      const r = await api.reviewBatchApprove({
        skipKnownIssues,
        limit: 50,
      });
      const errN = r.errors?.length ?? 0;
      const parts = [
        `通过 ${r.approved}`,
        skipKnownIssues ? `跳过已知问题 ${r.skipped_known}` : null,
        r.skipped_gate ? `门禁未过 ${r.skipped_gate}` : null,
        r.skipped_media ? `文件缺失 ${r.skipped_media}` : null,
        errN ? `失败 ${errN}` : null,
      ].filter(Boolean);
      notify(
        `一键审核：${parts.join(" · ")}`,
        r.approved > 0 ? (errN ? "warn" : "ok") : errN ? "err" : "warn",
      );
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBatchBusy(false);
    }
  }

  async function runReconcileGate() {
    if (batchBusy) return;
    setBatchBusy(true);
    try {
      const r = await api.reviewReconcileGate({
        limit: 50,
        archiveMissing: true,
        archiveGateFail: false,
      });
      const errN = r.errors?.length ?? 0;
      const parts = [
        `通过 ${r.approved ?? 0}`,
        r.archived_missing ? `缺文件归档 ${r.archived_missing}` : null,
        r.gate_failed ? `门禁未过 ${r.gate_failed}` : null,
        r.archived_gate_fail ? `门禁失败归档 ${r.archived_gate_fail}` : null,
        errN ? `失败 ${errN}` : null,
      ].filter(Boolean);
      notify(
        `重验门禁：${parts.join(" · ")}`,
        (r.approved ?? 0) > 0 || (r.archived_missing ?? 0) > 0
          ? errN
            ? "warn"
            : "ok"
          : errN
            ? "err"
            : "warn",
      );
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBatchBusy(false);
    }
  }

  async function runAutoBackfill() {
    if (batchBusy) return;
    setBatchBusy(true);
    try {
      const r = await api.reviewAutoApproveBackfill(50);
      const errN = r.errors?.length ?? 0;
      notify(
        `自动审片补录：通过 ${r.approved ?? 0} · 待人工 ${r.uncertain ?? 0} · 拒绝 ${r.rejected ?? 0} · 跳过 ${r.skipped ?? 0}${errN ? ` · 失败 ${errN}` : ""}`,
        (r.approved ?? 0) > 0 ? (errN ? "warn" : "ok") : errN ? "err" : "warn",
      );
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBatchBusy(false);
    }
  }

  async function reconcileOne(oid: number) {
    if (reviewBusyId === oid) return;
    // parent decideReview sets busy; for reconcile we need local hook — re-use decide via notify only after refresh
    try {
      const r = await api.reviewReconcileGateOne(oid);
      if (r.action === "approved" || r.ready_gate_ok) {
        notify("门禁重验通过，已自动过审", "ok");
      } else if (r.action === "gate_failed") {
        const fails = (r.ready_gate_fails || []).slice(0, 3).join("；");
        notify(
          fails ? `门禁未过：${fails}` : "门禁重验未过，请未通过删除或归档出队",
          "warn",
        );
      } else if (r.action === "media_missing" || r.action === "archived_missing") {
        notify("成片文件缺失，已无法核验门禁", "warn");
      } else {
        notify(`重验结果：${r.action || "完成"}`, "info");
      }
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function archiveOne(oid: number, reason: "manual" | "missing" | "gate_fail" = "manual") {
    try {
      const r = await api.reviewArchiveUnusable(oid, reason);
      notify(r.note || "已归档出队（未标记为可发通过）", "ok");
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    }
  }

  return (
    <section className="page-stack review-page">
      <PageHeader
        title="成片抽检"
        blurb="只处理证据冲突或无法自动判定的成片"
        actions={<span className="count">{pendingCount} 条待人工</span>}
      />
      <p className="hint">
        出片门禁明确通过的成片会强制自动通过，不进本页待人工列表；可在下方「决策历史」或「日志 → 质检门禁」查看「审片自动通过」。
        无门禁证据的旧片请先「重验门禁」；文件丢失或确认弃用请「归档出队」（不会绕过出片门禁标为通过）。
        一键审核通过仍不能绕过出片门禁；可勾选跳过无旁白/无烧录字幕/音色违规成片。
        快捷键：J/K 上下、A 通过（仅门禁已过）、R 重渲、F 影院。
      </p>
      <div className="actions-inline" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <button
          type="button"
          className={`primary${batchBusy ? " is-busy" : ""}`}
          disabled={batchBusy}
          onClick={() => void runReconcileGate()}
          title="对最多 50 条待人工成片重新跑出片门禁；通过则自动过审；缺文件默认归档出队"
        >
          {batchBusy ? "处理中…" : "重验门禁并收口"}
        </button>
        <button
          type="button"
          className={batchBusy ? "is-busy" : undefined}
          disabled={batchBusy}
          onClick={() => void runBatchApprove()}
          title={
            skipKnownIssues
              ? "批量通过门禁已过且无已知问题标识的成片（≤50）"
              : "批量通过门禁已过的待人工成片（含有已知问题标识者，≤50）"
          }
        >
          {batchBusy ? "处理中…" : "一键审核通过"}
        </button>
        <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={skipKnownIssues}
            disabled={batchBusy}
            onChange={(e) => setSkipKnownIssues(e.target.checked)}
          />
          跳过有已知问题标识
        </label>
        <button
          type="button"
          className={batchBusy ? "is-busy" : undefined}
          disabled={batchBusy}
          onClick={() => void runAutoBackfill()}
          title="把本该强制自动过审却漏掉的 ready 成片补齐（≤50）"
        >
          {batchBusy ? "处理中…" : "同步自动审片补录"}
        </button>
      </div>
      <details
        onToggle={(event) => {
          if (event.currentTarget.open) void loadHistory();
        }}
      >
        <summary>决策历史（自动与人工）</summary>
        <div className="review-list" style={{ marginTop: 8 }}>
          {history.map((item) => (
            <article key={String(item.id)} className="review-card">
              <div className="review-meta">
                <strong>
                  {outputDisplayLabel({
                    display_label: item.display_label,
                    display_no: item.display_no,
                  }) || "成片"}
                </strong>
                <div className="review-meta-badges">
                  <em className={reviewStatusBadgeClass(item.status)}>
                    {reviewStatusLabel(item.status)}
                  </em>
                  <em className="badge-mute">{reviewSourceLabel(item.decision_source)}</em>
                  {item.is_current ? (
                    <em className="badge-ok">当前</em>
                  ) : (
                    <em className="badge-mute">历史</em>
                  )}
                </div>
              </div>
              <p className="hint">{reviewNoteLabel(item.note)}</p>
            </article>
          ))}
          {!history.length && <p className="hint">{historyBusy ? "读取中…" : "暂无决策历史"}</p>}
        </div>
      </details>
      <div className="cinema-bar">
        <button
          type="button"
          className={cinemaMode ? "primary" : undefined}
          onClick={() => setCinemaMode((v) => !v)}
        >
          {cinemaMode ? "退出影院" : "影院模式"}
        </button>
        <button type="button" onClick={() => setTab("publish")}>
          去发布台
        </button>
      </div>
      <div className="actions-inline" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <label>
          筛选
          <select
            value={reviewFilter}
            onChange={(e) =>
              setReviewFilter(e.target.value as "all" | "missing_voice" | "missing_sub" | "tts_bad")
            }
          >
            <option value="all">全部待人工</option>
            <option value="missing_voice">仅无旁白（{missingVoiceCount}）</option>
            <option value="missing_sub">仅无烧录字幕</option>
            <option value="tts_bad">音色违规 say（{ttsBadCount}）</option>
          </select>
        </label>
        <button
          type="button"
          className={`primary${batchBusy ? " is-busy" : ""}`}
          disabled={batchBusy || missingVoiceCount === 0}
          onClick={async () => {
            setBatchBusy(true);
            try {
              const r = await api.batchRerender({
                missing_voice: true,
                missing_subtitle: true,
                limit: 20,
                reason: "ops_voice_subtitle",
              });
              notify(
                `已排队重渲 ${r.count} 条${r.errors.length ? `，失败 ${r.errors.length}` : ""}`,
                r.count ? "ok" : "warn",
              );
              await refreshAll();
              setMediaEpoch((n) => n + 1);
            } catch (e) {
              notify(String(e), "err");
            } finally {
              setBatchBusy(false);
            }
          }}
        >
          {batchBusy ? "排队中…" : `批量补旁白/字幕（≤20）`}
        </button>
        <button
          type="button"
          className={`${batchBusy ? "is-busy" : ""}`}
          disabled={batchBusy || ttsBadCount === 0}
          onClick={async () => {
            setBatchBusy(true);
            try {
              const r = await api.batchRerender({
                noncompliant_tts: true,
                missing_voice: false,
                missing_subtitle: false,
                limit: 20,
                reason: "ops_tts_noncompliant",
              });
              notify(
                `音色违规已排队 Edge 重渲 ${r.count} 条${r.errors.length ? `，失败 ${r.errors.length}` : ""}`,
                r.count ? "ok" : "warn",
              );
              await refreshAll();
              setMediaEpoch((n) => n + 1);
            } catch (e) {
              notify(String(e), "err");
            } finally {
              setBatchBusy(false);
            }
          }}
        >
          {batchBusy ? "排队中…" : `批量重渲音色违规→Edge（≤20）`}
        </button>
      </div>
      <p className="review-note-hint">
        以下未通过原因与批注应用于下一次通过/未通过删除/重渲操作（全页共享，非每卡独立）。
      </p>
      <label>
        未通过原因
        <select value={reviewReason} onChange={(e) => setReviewReason(e.target.value)}>
          {reviewReasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        批注
        <input value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
      </label>
      <div className={cinemaMode ? "review-list review-layout-cinema" : "review-list"}>
        {readyOutputs.map((o) => {
          const covers = (o.covers as string[]) || [];
          const oid = Number(o.id);
          const label = outputDisplayLabel(o) || "成片";
          const videoPath = String(o.output_path || "");
          const mediaOk = o.media_ok !== false && Boolean(videoPath);
          const approved = o.review_status === "approved";
          const gateOk = o.ready_gate_ok === true;
          const gateFails = Array.isArray(o.ready_gate_fails)
            ? (o.ready_gate_fails as string[]).slice(0, 3)
            : [];
          const focused =
            reviewFocusId === oid ||
            (reviewFocusId == null &&
              readyOutputs[0] != null &&
              Number(readyOutputs[0].id) === oid);
          return (
            <article
              key={String(o.id)}
              className={`review-card${focused ? " is-focus" : ""}`}
              data-guide={`review-output-${oid}`}
              onClick={() => setReviewFocusId(oid)}
            >
              <div className="review-meta">
                <strong>{label}</strong>
                <span>{String(o.title || "(无标题)")}</span>
                <div className="review-meta-badges">
                  <em className="badge-mute">{outputStateLabel(o.state)}</em>
                  {o.has_voice ? <em className="badge-ok">旁白</em> : <em className="badge-mute">无旁白</em>}
                  {o.subtitle_burned ? (
                    <em className="badge-ok">字幕</em>
                  ) : (
                    <em className="badge-mute">无烧录字幕</em>
                  )}
                  {approved ? <em className="badge-ok">已通过</em> : null}
                  {approved && isAutoApproveNote(o.review_note) ? (
                    <em className="badge-ok">自动</em>
                  ) : null}
                  {gateOk ? (
                    <em className="badge-ok">门禁通过</em>
                  ) : (
                    <em className="badge-warn">门禁未证</em>
                  )}
                  {o.published || (Array.isArray(o.publish_trail) && o.publish_trail.length > 0) ? (
                    <em className="badge-ok">
                      已发
                      {Array.isArray(o.publish_trail) && o.publish_trail[0]
                        ? ` ${(o.publish_trail[0] as { platform?: string }).platform || ""}`
                        : ""}
                    </em>
                  ) : null}
                  {(o.tts_noncompliant || o.tts_compliant === false) && (
                    <em className="badge-warn">
                      音色违规 {String(o.tts_provider || o.tts_noncompliant || "系统朗读")}
                    </em>
                  )}
                  {(o.production_mode === "scene_tour" ||
                    o.category === "scene_tour" ||
                    o.theme === "scene_tour") && (
                    <em className="badge-ok">{SCENE_TOUR_UI.badge}</em>
                  )}
                  {!mediaOk && <em className="badge-warn">文件缺失</em>}
                </div>
              </div>
              {!gateOk && gateFails.length > 0 ? (
                <p className="hint" style={{ marginTop: 4 }}>
                  门禁：{gateFails.join("；")}
                </p>
              ) : null}
              {Array.isArray(o.shot_script) && (o.shot_script as unknown[]).length > 0 ? (
                <div className="hint" style={{ marginTop: 8 }}>
                  <strong>{SCENE_TOUR_UI.shotAlign}</strong>
                  <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {(o.shot_script as Array<Record<string, unknown>>)
                      .slice(0, 12)
                      .map((shot, idx) => (
                        <li key={`${oid}-shot-${idx}`}>
                          {roleLabel(String(shot.role))} · {bucketLabel(String(shot.bucket))}
                          {" — "}
                          {String(shot.text || "").trim() || "（无旁白）"}
                        </li>
                      ))}
                  </ol>
                  {o.validation_report &&
                  typeof o.validation_report === "object" &&
                  Array.isArray((o.validation_report as { reasons?: unknown }).reasons) &&
                  ((o.validation_report as { reasons: unknown[] }).reasons || []).length > 0 ? (
                    <p style={{ marginTop: 6 }}>
                      {SCENE_TOUR_UI.blockReasons}：
                      {(
                        (o.validation_report as { reasons: unknown[] }).reasons || []
                      )
                        .map(String)
                        .slice(0, 3)
                        .join("；")}
                    </p>
                  ) : null}
                </div>
              ) : null}
              <div className="review-preview">
                {mediaOk && active && focused ? (
                  <div className="review-media-col">
                    <video
                      key={`v-${mediaEpoch}-${oid}`}
                      className="review-video"
                      controls
                      preload="metadata"
                      playsInline
                      src={previewVideoSrc(
                        oid,
                        videoPath,
                        `${activeCustomerId ?? 0}-${mediaEpoch}`,
                      )}
                      draggable={outputDragHtmlEnabled()}
                      onDragStart={
                        outputDragHtmlEnabled()
                          ? (e) =>
                              bindOutputFileDrag(e, {
                                id: oid,
                                path: videoPath,
                                mediaOk,
                                onError: (m) => notify(m, "err"),
                              })
                          : undefined
                      }
                      onError={() =>
                        notify(
                          `${label} 预览失败：可点「打开文件位置」直接打开文件；若持续失败请重启 App`,
                          "warn",
                        )
                      }
                    />
                  </div>
                ) : mediaOk ? (
                  <div className="review-video review-video-placeholder" role="status">
                    <strong>点击预览</strong>
                    <span>{active ? "仅加载当前选中的成片" : "切回审片页后可预览"}</span>
                  </div>
                ) : (
                  <div className="review-video missing">
                    <p>成片文件不在磁盘</p>
                    <p className="hint">路径失效或未同步，无法预览；可重渲生成</p>
                  </div>
                )}
                <div className="review-side-dock" data-guide={`review-dock-${oid}`}>
                  <div
                    className="publish-drag-zone"
                    draggable={outputDragHtmlEnabled() && mediaOk}
                    onDragStart={
                      outputDragHtmlEnabled()
                        ? (e) => {
                            if (!mediaOk) return;
                            bindOutputFileDrag(e, {
                              id: oid,
                              path: videoPath,
                              mediaOk,
                              onError: (m) => notify(m, "err"),
                            });
                          }
                        : undefined
                    }
                    onPointerDown={(e) => {
                      if (!mediaOk) return;
                      bindOutputFilePointerDown(e, {
                        id: oid,
                        path: videoPath,
                        mediaOk,
                        onError: (m) => notify(m, "err"),
                      });
                    }}
                    title="按住拖向浏览器上传框；App 投递真实 .mp4 文件"
                  >
                    <strong>拖拽成片</strong>
                    <span>按住拖向网页上传框（系统真实成片文件）</span>
                  </div>
                  <p className="path">{videoPath || "（无本地路径）"}</p>
                  <div className="review-side-actions">
                    <button
                      type="button"
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!videoPath) {
                          notify("无本地路径", "err");
                          return;
                        }
                        try {
                          if (isTauri()) {
                            await openMediaTarget(videoPath);
                            notify("已打开文件位置，可拖到网页上传", "ok");
                          } else {
                            notify(`本地路径：${videoPath}`, "info");
                          }
                        } catch (err) {
                          notify(String(err), "err");
                        }
                      }}
                      disabled={!mediaOk}
                    >
                      打开文件位置
                    </button>
                    {!gateOk ? (
                      <button
                        type="button"
                        className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          void reconcileOne(oid);
                        }}
                        disabled={reviewBusyId === oid}
                        title="对磁盘成片重新跑出片门禁；通过则自动过审"
                      >
                        {reviewBusyId === oid ? "处理中…" : "重验门禁"}
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={`${gateOk ? "primary" : ""}${reviewBusyId === oid ? " is-busy" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        decideReview(oid, "approved");
                      }}
                      disabled={!mediaOk || !gateOk || reviewBusyId === oid || approved}
                      title={
                        !mediaOk
                          ? "成片文件缺失，无法通过"
                          : !gateOk
                            ? "须先重验门禁且通过，不能人工绕过"
                            : approved
                              ? "该成片已经通过审核"
                              : undefined
                      }
                    >
                      {reviewBusyId === oid ? "处理中…" : approved ? "已通过" : "通过"}
                    </button>
                    <button
                      type="button"
                      className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        decideReview(oid, "rejected", false);
                      }}
                      disabled={reviewBusyId === oid}
                      title="未通过：删除成片文件并释放词池/纸片，不再自动重渲"
                    >
                      {reviewBusyId === oid ? "处理中…" : "未通过删除"}
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        rerenderOnly(oid);
                      }}
                      disabled={reviewBusyId === oid}
                      className={reviewBusyId === oid ? "is-busy" : undefined}
                      title="运维手动重渲（审片未通过默认不启用）"
                    >
                      仅重渲
                    </button>
                    {!gateOk || !mediaOk ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          void archiveOne(oid, mediaOk ? "manual" : "missing");
                        }}
                        disabled={reviewBusyId === oid}
                        title="退出待审队列，不标记为可发通过；适合旧片已发或文件丢失"
                      >
                        归档出队
                      </button>
                    ) : null}
                  </div>
                  {covers.length > 0 ? (
                    <div className="review-side-covers" aria-label="封面预览">
                      {covers.slice(0, REVIEW_COVER_SLOTS).map((coverPath, i) => {
                        const path =
                          typeof coverPath === "string" && coverPath.trim()
                            ? coverPath
                            : null;
                        if (!path) return null;
                        return (
                          <button
                            key={`${mediaEpoch}-${oid}-cover-${i}`}
                            type="button"
                            className="review-cover-slot"
                            title={`预览封面 ${i + 1}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setCoverLightbox({
                                oid,
                                index: i,
                                path,
                                label: `${label} · 封面 ${i + 1}`,
                              });
                            }}
                          >
                            <img
                              src={previewCoverSrc(
                                oid,
                                i,
                                path,
                                `${activeCustomerId ?? 0}-${mediaEpoch}`,
                              )}
                              alt={`封面 ${i + 1}`}
                              className="cover-thumb"
                              draggable={false}
                            />
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              </div>
            </article>
          );
        })}
        {readyOutputs.length === 0 && (
          <EmptyState
            title="暂无待人工条目"
            actionLabel="去生产"
            onAction={() => setTab("produce")}
          />
        )}
      </div>
      <StepFooter current="review" onJump={setTab} />
      {coverLightbox
        ? createPortal(
            <div
              className="review-cover-lightbox"
              role="dialog"
              aria-modal="true"
              aria-label={coverLightbox.label}
              onClick={() => setCoverLightbox(null)}
            >
              <div
                className="review-cover-lightbox-panel"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="review-cover-lightbox-head">
                  <strong>{coverLightbox.label}</strong>
                  <button type="button" onClick={() => setCoverLightbox(null)}>
                    关闭
                  </button>
                </div>
                <img
                  src={previewCoverSrc(
                    coverLightbox.oid,
                    coverLightbox.index,
                    coverLightbox.path,
                    `${activeCustomerId ?? 0}-${mediaEpoch}`,
                  )}
                  alt={coverLightbox.label}
                  className="review-cover-lightbox-img"
                />
              </div>
            </div>,
            document.body,
          )
        : null}
    </section>
  );
}
