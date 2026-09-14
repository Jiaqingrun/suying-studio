import type { ReactNode } from "react";

type Task = {
  id: string;
  title: string;
  meta: string;
  status: string;
  statusTone?: "pending" | "ok" | "revise";
  thumb?: string | null;
};

type Props = {
  tasks: Task[];
  publishTitle?: string;
  publishMeta?: string;
  onOpenAllTasks?: () => void;
  onOpenPublish?: () => void;
  onOpenGallery?: () => void;
  extraLeft?: ReactNode;
};

function toneClass(tone?: Task["statusTone"]) {
  if (tone === "ok") return "pill ok";
  if (tone === "revise") return "pill warn";
  return "pill info";
}

/** AV 概念图中央双卡：审片任务列表 + 发布圆形水彩图腾（数据仍走真源） */
export function AvReviewPublishGallery({
  tasks,
  publishTitle = "暮色长安 · 正式版",
  publishMeta = "画廊已公开 · 4K · 05:48",
  onOpenAllTasks,
  onOpenPublish,
  onOpenGallery,
  extraLeft,
}: Props) {
  return (
    <div className="av-gallery">
      <section className="av-card" aria-label="审片任务">
        <div className="av-card-head">
          <div>
            <h3>审片任务</h3>
            <p>待处理成片 · 证据冲突与人工抽检</p>
          </div>
          <button type="button" className="av-card-action" onClick={onOpenAllTasks}>
            进行中 ›
          </button>
        </div>
        {tasks.length === 0 ? (
          <p className="hint" style={{ padding: "24px 4px" }}>
            暂无待人工成片。门禁通过的成片会自动过审。
          </p>
        ) : (
          tasks.slice(0, 3).map((t) => (
            <div key={t.id} className="av-task-row">
              {t.thumb ? (
                <img className="av-thumb" src={t.thumb} alt="" />
              ) : (
                <div className="av-thumb" aria-hidden />
              )}
              <div className="av-task-meta">
                <strong>{t.title}</strong>
                <span>{t.meta}</span>
              </div>
              <em className={toneClass(t.statusTone)}>{t.status}</em>
            </div>
          ))
        )}
        {extraLeft}
        <div className="av-card-foot">
          <button type="button" className="linkish" onClick={onOpenAllTasks}>
            查看全部审片任务 →
          </button>
        </div>
      </section>

      <section className="av-card" aria-label="发布项目">
        <div className="av-card-head">
          <div>
            <h3>发布项目</h3>
            <p>画廊触达 · 优雅归档</p>
          </div>
          <button type="button" className="av-card-action" onClick={onOpenPublish}>
            已发布
          </button>
        </div>
        <div className="av-publish-orb" aria-hidden>
          <div className="av-publish-orb-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 12l16-7-7 16-2-6-7-3z" />
            </svg>
          </div>
        </div>
        <div className="av-publish-meta">
          <strong>{publishTitle}</strong>
          <span>{publishMeta}</span>
          <div>
            <button type="button" className="ghost-btn" onClick={onOpenGallery ?? onOpenPublish}>
              访问画廊
            </button>
          </div>
        </div>
        <div className="av-card-foot">
          <button type="button" className="linkish" onClick={onOpenPublish}>
            查看发布历史 →
          </button>
        </div>
      </section>
    </div>
  );
}
