export type LogoPosition = "top_left" | "top_right" | "bottom_left" | "bottom_right";

export type BrandLogoState = {
  logo_enabled: boolean;
  logo_position: LogoPosition;
  logo_margin: number;
  logo_max_width: number;
};

const CORNERS: Array<{ id: LogoPosition; label: string; grid: string }> = [
  { id: "top_left", label: "左上", grid: "tl" },
  { id: "top_right", label: "右上", grid: "tr" },
  { id: "bottom_left", label: "左下", grid: "bl" },
  { id: "bottom_right", label: "右下", grid: "br" },
];

type Props = {
  customerName: string;
  state: BrandLogoState;
  busy?: boolean;
  onChange: (patch: Partial<BrandLogoState>) => void;
};

export function brandFromProfile(profile?: Record<string, unknown> | null): BrandLogoState {
  const brand = (profile?.brand as Record<string, unknown> | undefined) || {};
  const pos = String(brand.logo_position || "bottom_right");
  const allowed: LogoPosition[] = ["top_left", "top_right", "bottom_left", "bottom_right"];
  return {
    logo_enabled: brand.logo_enabled !== false,
    logo_position: (allowed.includes(pos as LogoPosition) ? pos : "bottom_right") as LogoPosition,
    logo_margin: Number(brand.logo_margin) > 0 ? Number(brand.logo_margin) : 36,
    logo_max_width: Number(brand.logo_max_width) > 0 ? Number(brand.logo_max_width) : 160,
  };
}

export function BrandLogoPanel({ customerName, state, busy, onChange }: Props) {
  return (
    <div className="brand-logo-panel">
      <div className="brand-logo-head">
        <h3>品牌 Logo 角标</h3>
        <p className="hint">
          成片叠在画面四角之一；文件取自客户目录 <code>05-品牌/logo.png</code>
          {customerName ? `（当前：${customerName}）` : ""}。缺文件则自动跳过。
          <strong> 更改即时保存。</strong>
        </p>
      </div>

      <label className="brand-logo-toggle">
        <input
          type="checkbox"
          checked={state.logo_enabled}
          disabled={busy}
          onChange={(e) => onChange({ logo_enabled: e.target.checked })}
        />
        <span>启用 Logo 角标</span>
      </label>

      <div className={`brand-logo-body${state.logo_enabled ? "" : " is-disabled"}`}>
        <div className="logo-frame" aria-label="画面四角定位">
          <span className="logo-frame-label">竖屏画面示意</span>
          {CORNERS.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`logo-corner logo-corner--${c.grid}${
                state.logo_position === c.id ? " is-active" : ""
              }`}
              disabled={busy || !state.logo_enabled}
              onClick={() => onChange({ logo_position: c.id })}
              title={c.label}
            >
              <span className="logo-corner-dot" />
              <span className="logo-corner-text">{c.label}</span>
            </button>
          ))}
        </div>

        <div className="brand-logo-fields">
          <label>
            边距 (px)
            <input
              type="number"
              min={0}
              max={200}
              value={state.logo_margin}
              disabled={busy || !state.logo_enabled}
              onChange={(e) => onChange({ logo_margin: Number(e.target.value) || 0 })}
            />
          </label>
          <label>
            最大宽度 (px)
            <input
              type="number"
              min={40}
              max={480}
              value={state.logo_max_width}
              disabled={busy || !state.logo_enabled}
              onChange={(e) => onChange({ logo_max_width: Number(e.target.value) || 160 })}
            />
          </label>
          <p className="path">
            当前位置：
            {CORNERS.find((c) => c.id === state.logo_position)?.label || state.logo_position}
          </p>
        </div>
      </div>
    </div>
  );
}
