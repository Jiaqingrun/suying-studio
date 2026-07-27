import type { Dispatch, SetStateAction } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { api } from "../api";
import { isTauri } from "../engineControl";
import { bindOutputFileDrag } from "../mediaDrag";
import { previewCoverSrc, previewVideoSrc } from "../mediaPreview";
import { EmptyState, PageHeader, StepFooter } from "../shell/PageChrome";
import type { Tab } from "../types";
import type { NotifyFn } from "./pageTypes";

export interface ReviewPageProps {
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
  return (
    <section>
      <PageHeader
        title="成片抽检"
        blurb="抽检成片、通过或重渲"
        actions={<span className="count">{readyOutputs.length} PENDING</span>}
      />
      <p className="hint">
        新渲成片默认含旁白+烧录字幕。旧片显示「无旁白/无烧录字幕」时，用「仅重渲」或下方批量补齐后再过审。通过后默认留在本页便于连续审片；快捷键：J/K
        上下、A 通过、R 重渲、F 影院。
      </p>
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
            <option value="all">全部待抽检</option>
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
        以下打回原因与批注应用于下一次通过/打回/重渲操作（全页共享，非每卡独立）。
      </p>
      <label>
        打回原因
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
          const videoPath = String(o.output_path || "");
          const mediaOk = o.media_ok !== false && Boolean(videoPath);
          const focused =
            reviewFocusId === oid ||
            (reviewFocusId == null &&
              readyOutputs[0] != null &&
              Number(readyOutputs[0].id) === oid);
          return (
            <article
              key={String(o.id)}
              className={`review-card${focused ? " is-focus" : ""}`}
              onClick={() => setReviewFocusId(oid)}
            >
              <div className="review-meta">
                <strong>#{String(o.id)}</strong>
                <span>{String(o.state)}</span>
                <span>{String(o.title || "(无标题)")}</span>
                {o.has_voice ? <em className="badge-ok">旁白</em> : <em className="badge-mute">无旁白</em>}
                {o.subtitle_burned ? (
                  <em className="badge-ok">字幕</em>
                ) : (
                  <em className="badge-mute">无烧录字幕</em>
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
                    音色违规 {String(o.tts_provider || o.tts_noncompliant || "say")}
                  </em>
                )}
                {!mediaOk && <span className="badge-warn">文件缺失</span>}
              </div>
              <div className="review-preview">
                {mediaOk ? (
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
                      draggable
                      onDragStart={(e) =>
                        bindOutputFileDrag(e, { id: oid, path: videoPath, mediaOk })
                      }
                      onError={() =>
                        notify(
                          `成片 #${oid} 预览失败：可点「访达中显示」直接打开文件；若持续失败请重启 App`,
                          "warn",
                        )
                      }
                    />
                    <div
                      className="publish-drag-zone"
                      draggable
                      onDragStart={(e) =>
                        bindOutputFileDrag(e, { id: oid, path: videoPath, mediaOk })
                      }
                      title="拖到浏览器上传框；无效时用「访达中显示」再拖文件"
                    >
                      <strong>拖拽成片</strong>
                      <span>按住拖向网页上传框；推荐访达中拖文件</span>
                    </div>
                  </div>
                ) : (
                  <div className="review-video missing">
                    <p>成片文件不在磁盘</p>
                    <p className="hint">路径失效或未同步，无法预览；可重渲生成</p>
                  </div>
                )}
                {covers.length > 0 && mediaOk && (
                  <div className="cover-row">
                    {covers.map((coverPath, i) => (
                      <img
                        key={`${mediaEpoch}-${oid}-cover-${i}`}
                        src={previewCoverSrc(
                          oid,
                          i,
                          typeof coverPath === "string" ? coverPath : null,
                          `${activeCustomerId ?? 0}-${mediaEpoch}`,
                        )}
                        alt=""
                        className="cover-thumb"
                      />
                    ))}
                  </div>
                )}
              </div>
              <p className="path">{videoPath}</p>
              <div className="actions-inline">
                <button
                  type="button"
                  onClick={async () => {
                    if (!videoPath) {
                      notify("无本地路径", "err");
                      return;
                    }
                    try {
                      if (isTauri()) {
                        await revealItemInDir(videoPath);
                        notify("已在访达中显示，可拖到网页上传", "ok");
                      } else {
                        notify(`本地路径：${videoPath}`, "info");
                      }
                    } catch (e) {
                      notify(String(e), "err");
                    }
                  }}
                  disabled={!mediaOk}
                >
                  访达中显示
                </button>
                <button
                  type="button"
                  className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                  onClick={() => decideReview(oid, "approved")}
                  disabled={!mediaOk || reviewBusyId === oid}
                  title={!mediaOk ? "成片文件缺失，无法通过" : undefined}
                >
                  {reviewBusyId === oid ? "处理中…" : "通过"}
                </button>
                <button
                  type="button"
                  onClick={() => decideReview(oid, "rejected")}
                  disabled={reviewBusyId === oid}
                  className={reviewBusyId === oid ? "is-busy" : undefined}
                >
                  打回
                </button>
                <button
                  type="button"
                  className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                  onClick={() => decideReview(oid, "rejected", true)}
                  disabled={reviewBusyId === oid}
                >
                  打回并重渲
                </button>
                <button
                  type="button"
                  onClick={() => rerenderOnly(oid)}
                  disabled={reviewBusyId === oid}
                  className={reviewBusyId === oid ? "is-busy" : undefined}
                >
                  仅重渲
                </button>
              </div>
            </article>
          );
        })}
        {readyOutputs.length === 0 && (
          <EmptyState
            title="暂无待抽检成片"
            actionLabel="去生产"
            onAction={() => setTab("produce")}
          />
        )}
      </div>
      <StepFooter current="review" onJump={setTab} />
    </section>
  );
}
