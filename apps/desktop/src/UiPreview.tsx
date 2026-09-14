import { useState } from "react";
import {
  LayoutDashboard,
  Clapperboard,
  SlidersHorizontal,
  Film,
  Send,
  MessageSquare,
  Database,
  Wrench,
  Settings,
} from "lucide-react";
import { AppShell } from "./shell/AppShell";
import { EmptyState, ErrorState, LoadingState, PageHeader, PageSection } from "./shell/PageChrome";
import { OverviewPipeline } from "./shell/OverviewPipeline";
import type { Tab } from "./types";

const ICONS: Record<Tab, typeof LayoutDashboard> = {
  overview: LayoutDashboard,
  produce: Clapperboard,
  rules: SlidersHorizontal,
  review: Film,
  publish: Send,
  messages: MessageSquare,
  data: Database,
  ops: Wrench,
  settings: Settings,
};

/** 浏览器静态预览：不依赖引擎 / Tauri，用于 UI 验收截图 */
export function UiPreview() {
  const params = new URLSearchParams(window.location.search);
  const initialGate =
    params.get("gate") === "splash" || params.get("gate") === "license"
      ? (params.get("gate") as "splash" | "license")
      : "shell";
  const [tab, setTab] = useState<Tab>("overview");
  const [gate, setGate] = useState<"shell" | "splash" | "license">(initialGate);

  if (gate === "splash") {
    return (
      <main className="license-gate license-gate--checking">
        <div className="license-hero" aria-hidden="true">
          <img className="license-hero-img" src="/hero-stone-ink.jpg" alt="" />
          <div className="license-hero-veil" />
        </div>
        <div className="license-stage">
          <section className="license-splash">
            <div className="license-brand license-brand--compact">
              <div className="license-mark">
                <span className="license-mark-glyph">速</span>
              </div>
              <div className="license-brand-copy">
                <span className="license-brand-name">速影</span>
                <span className="license-brand-product">Studio</span>
              </div>
            </div>
            <h1 className="license-hero-title">速影</h1>
            <p className="license-hero-support">本地智能混剪 · 正在启动工作室</p>
            <span className="loading-bar" />
            <button type="button" className="secondary" onClick={() => setGate("shell")}>
              返回壳层预览
            </button>
          </section>
        </div>
      </main>
    );
  }

  if (gate === "license") {
    return (
      <main className="license-gate">
        <div className="license-hero" aria-hidden="true">
          <img className="license-hero-img" src="/hero-stone-ink.jpg" alt="" />
          <div className="license-hero-veil" />
        </div>
        <div className="license-stage">
          <section className="license-card">
            <div className="license-brand">
              <div className="license-mark">
                <span className="license-mark-glyph">速</span>
              </div>
              <div className="license-brand-copy">
                <span className="license-brand-name">速影</span>
                <span className="license-brand-product">Studio</span>
              </div>
            </div>
            <p className="eyebrow">单机授权</p>
            <h1>此设备尚未授权</h1>
            <p className="muted">将许可请求发给运维人员，收到与本机绑定的许可证后导入。</p>
            <div className="license-actions">
              <button type="button" className="secondary" onClick={() => setGate("shell")}>
                返回壳层预览
              </button>
              <button type="button" className="primary">
                导入许可证
              </button>
            </div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <AppShell
      tab={tab}
      onSetTab={setTab}
      density="standard"
      tabIcons={ICONS}
      topbarTitle={
        <>
          <h2>UI 预览台</h2>
          <span>石青墨纸 MAX · 不连引擎</span>
        </>
      }
      topbarActions={
        <div className="actions-inline">
          <button type="button" onClick={() => setGate("splash")}>
            启动 splash
          </button>
          <button type="button" className="primary" onClick={() => setGate("license")}>
            授权页
          </button>
        </div>
      }
      railFoot={
        <div className="rail-foot">
          <p className="hint">预览模式 · 功能逻辑未接线</p>
        </div>
      }
    >
      <section className="page-stack overview-page tab-pane">
        <div className="overview-hero">
          <PageHeader title="总览" blurb="先看动态与泳道：有日历则开跑，无计划去生产填日历" />
          <OverviewPipeline
            nodes={[
              { id: "produce", label: "生产", count: 3 },
              { id: "review", label: "审片", count: 2, warn: true },
              { id: "publish", label: "发布", count: 5 },
              { id: "messages", label: "消息", count: 0 },
            ]}
            onJump={setTab}
          />
        </div>
        <PageSection title="刚完成" description="最近成片（唯一编号）。" status="暂无">
          <EmptyState
            title="还没有刚完成的成片"
            body="从生产页填日历并开跑，成片会出现在这里。"
            actionLabel="去生产"
            onAction={() => setTab("produce")}
          />
        </PageSection>
        <PageSection title="加载与异常" description="共用状态语汇，避免各页自造样式。">
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
            <LoadingState label="正在同步片库…" />
            <ErrorState
              title="引擎暂时离线"
              body="请确认本机引擎已启动，或从侧栏重新上线。"
              actionLabel="重试"
              onAction={() => undefined}
            />
          </div>
        </PageSection>
        <PageSection title="石青墨纸色票" description="墨 / 纸 / 朱砂 / 石青 — 仅预览台展示。">
          <div className="ui-preview-swatch" aria-hidden="true">
            <span>
              <i className="sw-ink" />
              ink #1c1916
            </span>
            <span>
              <i className="sw-paper" />
              paper #e9e6df
            </span>
            <span>
              <i className="sw-seal" />
              seal #c43c28
            </span>
            <span>
              <i className="sw-stone" />
              stone #3a647c
            </span>
          </div>
        </PageSection>
        <PageSection title="动效样例" description="尊重 prefers-reduced-motion；正式路径同套变量。">
          <div className="ui-preview-motion-row">
            <span className="ui-preview-pulse" aria-hidden="true" />
            <span className="loading-bar" aria-hidden="true" />
            <button type="button" className="primary">
              主按钮
            </button>
            <button type="button">次按钮</button>
            <span className="status-chip ok">引擎在线</span>
          </div>
        </PageSection>
      </section>
    </AppShell>
  );
}
