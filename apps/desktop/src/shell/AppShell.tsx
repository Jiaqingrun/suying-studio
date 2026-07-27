import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import type { LayoutDensity, Tab } from "../types";
import { TABS, TAB_BLURB } from "../types";

type NavBadge = Partial<Record<Tab, number>>;

type Props = {
  tab: Tab;
  onSetTab: (t: Tab) => void;
  density: LayoutDensity;
  badges?: NavBadge;
  brandSlot?: ReactNode;
  railFoot?: ReactNode;
  topbarTitle?: ReactNode;
  topbarActions?: ReactNode;
  children: ReactNode;
  assistantOpen?: boolean;
  tabIcons: Record<Tab, LucideIcon>;
};

export function AppShell({
  tab,
  onSetTab,
  density,
  badges = {},
  brandSlot,
  railFoot,
  topbarTitle,
  topbarActions,
  children,
  assistantOpen,
  tabIcons,
}: Props) {
  return (
    <div className={`app app--gui`} data-density={density} data-assistant={assistantOpen ? "1" : "0"}>
      <aside className="rail" aria-label="主导航">
        <div className="brand">
          {brandSlot ?? (
            <div className="brand-mark">
              <h1 className="brand-name">速影</h1>
              <span className="brand-ver">SUYING</span>
            </div>
          )}
          <p className="brand-tag">本地日更工作室</p>
        </div>
        <nav className="nav">
          {TABS.map(([t, label, idx]) => {
            const Icon = tabIcons[t];
            const badge = badges[t] ?? 0;
            return (
              <button
                key={t}
                type="button"
                className={`nav-btn${tab === t ? " active" : ""}`}
                onClick={() => onSetTab(t)}
                title={`${label} · ${TAB_BLURB[t]}`}
              >
                <span className="nav-idx">{idx}</span>
                {Icon ? <Icon className="nav-icon" size={16} aria-hidden /> : null}
                <span className="nav-label">{label}</span>
                {badge > 0 ? <em className="nav-badge">{badge > 99 ? "99+" : badge}</em> : null}
              </button>
            );
          })}
        </nav>
        {railFoot}
      </aside>

      <header className="topbar">
        <div className="topbar-title">
          {topbarTitle}
        </div>
        <div className="topbar-actions">{topbarActions}</div>
      </header>

      <div className={`workspace${assistantOpen ? " workspace--assistant" : ""}`}>{children}</div>
    </div>
  );
}
