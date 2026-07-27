import { useEffect, useState } from "react";
import type { Tab } from "../types";

export type ActivityItem = {
  id: string;
  text: string;
  kind?: "ok" | "warn" | "err" | "info";
  at?: string;
  tab?: Tab;
};

type Props = {
  items: ActivityItem[];
  paused?: boolean;
  onJump?: (t: Tab) => void;
  intervalMs?: number;
};

/**
 * Rotating activity ticker for overview — does not scroll the page.
 * Respects prefers-reduced-motion and manual pause.
 */
export function ActivityTicker({ items, paused, onJump, intervalMs = 4500 }: Props) {
  const [idx, setIdx] = useState(0);
  const [userPaused, setUserPaused] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduceMotion(mq.matches);
    apply();
    mq.addEventListener?.("change", apply);
    return () => mq.removeEventListener?.("change", apply);
  }, []);

  useEffect(() => {
    if (!items.length || paused || userPaused || reduceMotion) return;
    const t = window.setInterval(() => {
      setIdx((i) => (i + 1) % items.length);
    }, intervalMs);
    return () => window.clearInterval(t);
  }, [items, paused, userPaused, reduceMotion, intervalMs]);

  if (!items.length) {
    return <div className="activity-ticker is-empty">暂无动态 · 产线空闲</div>;
  }

  const cur = items[Math.min(idx, items.length - 1)]!;

  return (
    <div className={`activity-ticker kind-${cur.kind || "info"}`}>
      <div className="activity-main">
        <span className="activity-label">动态</span>
        <button
          type="button"
          className="activity-text"
          disabled={!cur.tab || !onJump}
          onClick={() => cur.tab && onJump?.(cur.tab)}
        >
          {cur.text}
        </button>
        {cur.at ? <span className="activity-at">{cur.at}</span> : null}
      </div>
      <div className="activity-controls">
        <button type="button" onClick={() => setIdx((i) => (i - 1 + items.length) % items.length)}>
          上一条
        </button>
        <button type="button" onClick={() => setIdx((i) => (i + 1) % items.length)}>
          下一条
        </button>
        <button type="button" onClick={() => setUserPaused((v) => !v)}>
          {userPaused || reduceMotion ? "继续" : "暂停"}
        </button>
        <span className="activity-count">
          {idx + 1}/{items.length}
        </span>
      </div>
    </div>
  );
}
