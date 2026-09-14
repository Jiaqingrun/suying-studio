import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import type { LayoutDensity, Tab } from "../types";
import { TABS, TAB_BLURB, NAV_MIND_GROUPS } from "../types";

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
  tabIcons: Record<Tab, LucideIcon>;
};

const TAB_LABEL = Object.fromEntries(TABS.map(([id, label]) => [id, label])) as Record<Tab, string>;

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
  tabIcons,
}: Props) {
  return (
    <div className="app app--gui app--immersive" data-density={density}>
      <aside className="rail" aria-label="主导航">
        <div className="brand">
          {brandSlot ?? (
            <div className="brand-mark">
              <h1 className="brand-name">速影 Studio</h1>
              <span className="brand-ver">SUYING</span>
            </div>
          )}
          <p className="brand-tag">本地日更 · 水彩工作室</p>
        </div>
        <nav className="nav" aria-label="心智分组导航">
          {NAV_MIND_GROUPS.map((group) => (
            <div key={group.id} className="nav-group" data-mind={group.id}>
              <div className="nav-group-label" aria-hidden="true">
                {group.label}
              </div>
              {group.tabs.map((t) => {
                const Icon = tabIcons[t];
                const badge = badges[t] ?? 0;
                const meta = TABS.find(([id]) => id === t);
                const idx = meta?.[2] ?? "";
                const label = TAB_LABEL[t];
                return (
                  <button
                    key={t}
                    type="button"
                    className={`nav-btn${tab === t ? " active" : ""}`}
                    onClick={() => onSetTab(t)}
                    title={`${label} · ${TAB_BLURB[t]}`}
                    aria-current={tab === t ? "page" : undefined}
                  >
                    <span className="nav-idx">{idx}</span>
                    {Icon ? <Icon className="nav-icon" size={16} aria-hidden /> : null}
                    <span className="nav-label">{label}</span>
                    {badge > 0 ? <em className="nav-badge">{badge > 99 ? "99+" : badge}</em> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        {railFoot}
      </aside>

      <header className="topbar">
        <div className="topbar-title">{topbarTitle}</div>
        <div className="topbar-actions">{topbarActions}</div>
      </header>

      <div className="workspace">{children}</div>
    </div>
  );
}
