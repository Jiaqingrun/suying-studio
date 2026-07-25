import { useEffect, useState } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { api } from "./api";
import { isTauri } from "./engineControl";
import { bindOutputFileDrag } from "./mediaDrag";
import { previewVideoSrc } from "./mediaPreview";

type PlatformCopy = {
  platform?: string;
  title?: string;
  body?: string;
  hashtags?: string[];
};

type PublishCard = {
  id: number;
  title: string;
  description?: string;
  hashtags?: string[];
  platforms?: Record<string, PlatformCopy>;
  output_path?: string;
  media_ok?: boolean;
  has_narration?: boolean;
  subtitle_burned?: boolean;
};

const PLATFORM_LABELS: Record<string, string> = {
  douyin: "抖音",
  channels: "视频号",
  xhs: "小红书",
  wechat_mp: "公众号",
  kuaishou: "快手",
};

type Props = {
  outputs: Array<Record<string, unknown>>;
  onNotify: (text: string, kind?: "ok" | "err" | "info" | "warn") => void;
  onRefresh: () => Promise<void>;
  onGoReach?: () => void;
};

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      return true;
    } catch {
      return false;
    }
  }
}

export function PublishDesk({ outputs, onNotify, onRefresh, onGoReach }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [card, setCard] = useState<PublishCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [plat, setPlat] = useState("douyin");

  const publishable = outputs
    .filter((o) => {
      const state = String(o.state || "");
      const review = String(o.review_status || "");
      // 过审优先展示；ready 且未拒审也可发
      return state === "ready" && review !== "rejected";
    })
    .slice()
    .sort((a, b) => {
      const score = (o: Record<string, unknown>) =>
        o.review_status === "approved" ? 2 : o.review_status === "pending" ? 1 : 0;
      return score(b) - score(a) || Number(b.id) - Number(a.id);
    });

  useEffect(() => {
    if (!publishable.length) {
      setSelectedId(null);
      setCard(null);
      return;
    }
    const still = publishable.some((o) => Number(o.id) === selectedId);
    if (!still) setSelectedId(Number(publishable[0].id));
  }, [outputs, selectedId, publishable.length]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setBusy(true);
    api
      .outputPublishCard(selectedId)
      .then((r) => {
        if (cancelled) return;
        setCard(r as PublishCard);
        const keys = Object.keys((r.platforms as Record<string, unknown>) || {});
        if (keys.length && !keys.includes(plat)) setPlat(keys[0]);
      })
      .catch((e) => onNotify(String(e), "err"))
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const platformEntry = (card?.platforms || {})[plat] || {};
  const titleText = String(platformEntry.title || card?.title || "");
  const bodyText = String(platformEntry.body || card?.description || "");
  const tagsText = (platformEntry.hashtags || card?.hashtags || []).join(" ");
  const path = String(card?.output_path || "");

  async function onReveal() {
    if (!path) {
      onNotify("无本地成片路径", "err");
      return;
    }
    try {
      if (isTauri()) {
        await revealItemInDir(path);
        onNotify("已在访达中显示成片，可拖到浏览器上传框", "ok");
      } else {
        onNotify(`本地路径：${path}`, "info");
      }
    } catch (e) {
      onNotify(String(e), "err");
    }
  }

  function onDragStart(e: React.DragEvent) {
    if (!path || !card?.media_ok || !card) return;
    bindOutputFileDrag(e, { id: card.id, path, mediaOk: card.media_ok });
  }

  return (
    <section className="publish-desk">
      <div className="panel-head">
        <h2>发布台</h2>
        <span className="count">{publishable.length} READY</span>
      </div>
      <p className="hint">
        优先使用「已过审」成片。复制各平台标题/正文后，点「访达中显示」把文件拖到网页上传框（页内拖拽为尽力支持）。
        新成片默认含旁白+烧录字幕；旧片请先在审片页「仅重渲」。
      </p>

      <div className="publish-layout">
        <div className="publish-list">
          {publishable.map((o) => {
            const id = Number(o.id);
            const approved = o.review_status === "approved";
            return (
              <button
                key={id}
                type="button"
                className={`publish-item${selectedId === id ? " active" : ""}`}
                onClick={() => setSelectedId(id)}
              >
                <strong>#{id}</strong>
                <span className="publish-item-title">{String(o.title || "(无标题)")}</span>
                <span className="publish-badges">
                  {approved ? <em className="badge-ok">已过审</em> : <em className="badge-mute">待过审</em>}
                  {o.subtitle_burned ? <em className="badge-ok">字幕</em> : <em className="badge-mute">无字幕</em>}
                  {o.has_voice ? <em className="badge-ok">旁白</em> : <em className="badge-mute">无旁白</em>}
                  {o.published || (Array.isArray(o.publish_trail) && (o.publish_trail as unknown[]).length > 0) ? (
                    <em className="badge-ok">已发</em>
                  ) : null}
                </span>
              </button>
            );
          })}
          {publishable.length === 0 && <p className="empty">暂无 ready 成片，请先生产并审片通过</p>}
        </div>

        <div className="publish-detail">
          {!card && <p className="empty">{busy ? "加载中…" : "选择左侧成片"}</p>}
          {card && (
            <>
              <div className="publish-preview">
                {card.media_ok ? (
                  <video
                    key={`pub-${card.id}`}
                    className="publish-video"
                    controls
                    playsInline
                    preload="metadata"
                    src={previewVideoSrc(card.id, path)}
                    draggable
                    onDragStart={onDragStart}
                  />
                ) : (
                  <div className="publish-video missing">文件缺失</div>
                )}
                <div
                  className="publish-drag-zone"
                  draggable={Boolean(card.media_ok && path)}
                  onDragStart={onDragStart}
                  title="拖到支持的上传区；若无效请用「访达中显示」再拖文件"
                >
                  <strong>拖拽成片</strong>
                  <span>按住此处拖向浏览器上传框；推荐「访达中显示」后拖文件</span>
                </div>
              </div>

              <div className="actions-inline" style={{ marginBottom: 12 }}>
                <button type="button" className="primary" onClick={onReveal} disabled={!path}>
                  访达中显示
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={async () => {
                    const blob = [titleText, bodyText, tagsText].filter(Boolean).join("\n\n");
                    if (await copyText(blob)) onNotify("已复制标题+正文+话题", "ok");
                    else onNotify("复制失败", "err");
                  }}
                >
                  一键复制文案
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    if (await copyText(path)) onNotify("已复制本地路径", "ok");
                    else onNotify("复制失败", "err");
                  }}
                  disabled={!path}
                >
                  复制路径
                </button>
                <button type="button" onClick={() => onRefresh()}>
                  刷新
                </button>
                {onGoReach ? (
                  <button type="button" onClick={onGoReach}>
                    去触达
                  </button>
                ) : null}
              </div>
              <p className="hint" style={{ marginBottom: 10 }}>
                {card.has_narration ? "含旁白" : "旧成片可能无旁白"}
                {" · "}
                {card.subtitle_burned ? "已烧录字幕" : "未烧录字幕（重渲后生效）"}
              </p>

              <label>
                平台文案
                <select value={plat} onChange={(e) => setPlat(e.target.value)}>
                  {Object.keys(card.platforms || {}).map((k) => (
                    <option key={k} value={k}>
                      {PLATFORM_LABELS[k] || k}
                    </option>
                  ))}
                </select>
              </label>

              <div className="publish-copy-block">
                <div className="publish-copy-row">
                  <span>标题</span>
                  <button
                    type="button"
                    className="primary"
                    onClick={async () => {
                      if (await copyText(titleText)) onNotify("已复制标题", "ok");
                    }}
                  >
                    复制标题
                  </button>
                </div>
                <pre className="publish-copy-text">{titleText || "—"}</pre>
              </div>

              <div className="publish-copy-block">
                <div className="publish-copy-row">
                  <span>正文</span>
                  <button
                    type="button"
                    className="primary"
                    onClick={async () => {
                      if (await copyText(bodyText)) onNotify("已复制正文", "ok");
                    }}
                  >
                    复制正文
                  </button>
                </div>
                <pre className="publish-copy-text">{bodyText || "—"}</pre>
              </div>

              <div className="publish-copy-block">
                <div className="publish-copy-row">
                  <span>话题</span>
                  <button
                    type="button"
                    onClick={async () => {
                      if (await copyText(tagsText)) onNotify("已复制话题", "ok");
                    }}
                  >
                    复制话题
                  </button>
                </div>
                <pre className="publish-copy-text">{tagsText || "—"}</pre>
              </div>

              <p className="path">{path}</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
