import type { ReactNode } from "react";
import type { Tab } from "../types";
import { nextPipelineTab, TAB_BLURB } from "../types";

type Props = {
  title: string;
  blurb?: string;
  actions?: ReactNode;
  guide?: ReactNode;
};

export function PageHeader({ title, blurb, actions, guide }: Props) {
  return (
    <div className="page-header">
      <div className="page-header-main">
        <div className="panel-head">
          <h2>{title}</h2>
          {actions}
        </div>
        {blurb ? <p className="page-blurb">{blurb}</p> : null}
        {guide ? <div className="page-guide">{guide}</div> : null}
      </div>
    </div>
  );
}

type StepProps = {
  current: Tab;
  onJump: (t: Tab) => void;
  primaryLabel?: string;
  disabled?: boolean;
  hint?: string;
  extra?: ReactNode;
};

export function StepFooter({
  current,
  onJump,
  primaryLabel,
  disabled,
  hint,
  extra,
}: StepProps) {
  const next = nextPipelineTab(current);
  if (!next && !extra) return null;
  const label =
    primaryLabel ||
    (next === "produce"
      ? "下一步：去生产"
      : next === "review"
        ? "下一步：去审片"
        : next === "publish"
          ? "下一步：去发布"
          : next
            ? `下一步：${TAB_BLURB[next]}`
            : "");

  return (
    <div className="step-footer">
      {hint ? <p className="hint">{hint}</p> : null}
      <div className="actions">
        {next ? (
          <button
            type="button"
            className="primary"
            disabled={disabled}
            onClick={() => onJump(next)}
          >
            {label}
          </button>
        ) : null}
        {current !== "overview" ? (
          <button type="button" onClick={() => onJump("overview")}>
            返回总览
          </button>
        ) : null}
        {extra}
      </div>
    </div>
  );
}

type SegItem<T extends string> = { id: T; label: string; badge?: number };

type SegProps<T extends string> = {
  items: SegItem<T>[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel?: string;
};

export function SegmentNav<T extends string>({ items, value, onChange, ariaLabel }: SegProps<T>) {
  return (
    <div className="segment-nav" role="tablist" aria-label={ariaLabel || "分区"}>
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="tab"
          aria-selected={value === it.id}
          className={`segment-btn${value === it.id ? " active" : ""}`}
          onClick={() => onChange(it.id)}
        >
          {it.label}
          {it.badge && it.badge > 0 ? <em className="nav-badge">{it.badge}</em> : null}
        </button>
      ))}
    </div>
  );
}

type StatProps = {
  label: string;
  value: ReactNode;
  hint?: string;
  warn?: boolean;
  onClick?: () => void;
};

export function StatCard({ label, value, hint, warn, onClick }: StatProps) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      className={`stat${warn ? " is-warn" : ""}${onClick ? " is-clickable" : ""}`}
      onClick={onClick}
    >
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </Tag>
  );
}

type EmptyProps = {
  title: string;
  body?: string;
  actionLabel?: string;
  onAction?: () => void;
};

export function EmptyState({ title, body, actionLabel, onAction }: EmptyProps) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {body ? <p className="hint">{body}</p> : null}
      {actionLabel && onAction ? (
        <button type="button" className="primary" onClick={onAction}>
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

type PageSectionProps = {
  id?: string;
  title: string;
  description?: string;
  status?: ReactNode;
  actions?: ReactNode;
  tone?: "default" | "attention" | "success";
  children: ReactNode;
};

export function PageSection({
  id,
  title,
  description,
  status,
  actions,
  tone = "default",
  children,
}: PageSectionProps) {
  return (
    <section id={id} className={`page-section page-section-${tone}`}>
      <SectionHeader title={title} description={description} status={status} actions={actions} />
      <div className="page-section-body">{children}</div>
    </section>
  );
}

function SectionHeader({
  title,
  description,
  status,
  actions,
}: {
  title: string;
  description?: string;
  status?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="section-header">
      <div>
        <div className="section-title-row">
          <h3>{title}</h3>
          {status ? <span className="section-status">{status}</span> : null}
        </div>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="section-actions">{actions}</div> : null}
    </header>
  );
}

export function InPageNav({
  items,
  active,
  onChange,
}: {
  items: Array<{ id: string; label: string; badge?: number }>;
  active?: string;
  onChange?: (id: string) => void;
}) {
  return (
    <nav className="in-page-nav" aria-label="页内导航">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={active === item.id ? "active" : ""}
          onClick={() => {
            onChange?.(item.id);
            document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
        >
          <span>{item.label}</span>
          {item.badge ? <em>{item.badge}</em> : null}
        </button>
      ))}
    </nav>
  );
}

export function StatusStrip({
  items,
}: {
  items: Array<{ label: string; value: ReactNode; tone?: "neutral" | "ok" | "warn" | "danger" }>;
}) {
  return (
    <div className="status-strip">
      {items.map((item) => (
        <div key={item.label} className={`status-strip-item ${item.tone || "neutral"}`}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}
