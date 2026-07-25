import { useMemo, useState } from "react";

export type LangItem = {
  code: string;
  label_zh: string;
  label_native: string;
  region: string;
};

type Props = {
  value: string;
  onChange: (code: string) => void;
  languages: LangItem[];
  includeNone?: boolean;
  disabled?: boolean;
  id?: string;
};

const REGION_ORDER = ["东亚", "东南亚", "南亚", "中东/北非", "欧亚", "欧美", "其他"];

export function LangCombobox({ value, onChange, languages, includeNone, disabled, id }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const list = useMemo(() => {
    let rows = languages.slice();
    if (!includeNone) rows = rows.filter((l) => l.code !== "none");
    const s = q.trim().toLowerCase();
    if (s) {
      rows = rows.filter(
        (l) =>
          l.code.toLowerCase().includes(s) ||
          l.label_zh.toLowerCase().includes(s) ||
          l.label_native.toLowerCase().includes(s) ||
          l.region.toLowerCase().includes(s),
      );
    }
    rows.sort((a, b) => {
      const ra = REGION_ORDER.indexOf(a.region);
      const rb = REGION_ORDER.indexOf(b.region);
      return (ra < 0 ? 99 : ra) - (rb < 0 ? 99 : rb) || a.label_zh.localeCompare(b.label_zh, "zh");
    });
    return rows;
  }, [languages, includeNone, q]);

  const grouped = useMemo(() => {
    const map = new Map<string, LangItem[]>();
    for (const l of list) {
      const r = l.region || "其他";
      if (!map.has(r)) map.set(r, []);
      map.get(r)!.push(l);
    }
    const regions = [...map.keys()].sort((a, b) => {
      const ra = REGION_ORDER.indexOf(a);
      const rb = REGION_ORDER.indexOf(b);
      return (ra < 0 ? 99 : ra) - (rb < 0 ? 99 : rb);
    });
    return regions.map((region) => ({ region, items: map.get(region)! }));
  }, [list]);

  const selected = languages.find((l) => l.code === value);
  const label = selected
    ? `${selected.label_zh} · ${selected.label_native}`
    : value || "选择语言";

  return (
    <div className={`lang-combo${open ? " is-open" : ""}`} id={id}>
      <button
        type="button"
        className="lang-combo-trigger"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>{label}</span>
        <span className="lang-combo-code">{value}</span>
      </button>
      {open ? (
        <div className="lang-combo-pop">
          <input
            className="lang-combo-search"
            placeholder="搜索语言 / 地区 / 代码…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            autoFocus
          />
          <ul className="lang-combo-list">
            {grouped.map(({ region, items }) => (
              <li key={region} className="lang-combo-group">
                <div className="lang-combo-region">{region}</div>
                <ul>
                  {items.map((l) => (
                    <li key={l.code}>
                      <button
                        type="button"
                        className={`lang-combo-item${l.code === value ? " is-active" : ""}`}
                        onClick={() => {
                          onChange(l.code);
                          setOpen(false);
                          setQ("");
                        }}
                      >
                        <span>
                          {l.label_zh} · {l.label_native}
                        </span>
                        <kbd>{l.code}</kbd>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
            {list.length === 0 ? <li className="lang-combo-empty">无匹配语言</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
