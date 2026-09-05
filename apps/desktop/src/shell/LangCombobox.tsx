import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

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
  /** Override label for code=none (e.g. 无旁白 / 无字幕). */
  noneLabel?: string;
  disabled?: boolean;
  id?: string;
};

const REGION_ORDER = ["东亚", "东南亚", "南亚", "中东/北非", "欧亚", "欧美", "其他"];
const GAP = 4;
const EDGE = 8;
const SEARCH_H = 42;
const MIN_LIST_H = 120;
const DEFAULT_LIST_H = 240;
const MIN_POP_W = 320;
const MAX_POP_W = 460;

type PopStyle = {
  top: number;
  left: number;
  width: number;
  listMaxHeight: number;
};

function measurePop(trigger: HTMLElement): PopStyle {
  const rect = trigger.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const preferredW = Math.min(MAX_POP_W, Math.max(MIN_POP_W, Math.round(vw * 0.36)));
  const width = Math.min(
    Math.max(rect.width, preferredW),
    vw - EDGE * 2,
    MAX_POP_W,
  );
  const left = Math.min(Math.max(rect.left, EDGE), Math.max(EDGE, vw - width - EDGE));
  const spaceBelow = vh - rect.bottom - GAP - EDGE;
  const spaceAbove = rect.top - GAP - EDGE;
  const placeBelow = spaceBelow >= MIN_LIST_H + SEARCH_H || spaceBelow >= spaceAbove;
  const available = placeBelow ? spaceBelow : spaceAbove;
  const listMaxHeight = Math.max(
    80,
    Math.min(DEFAULT_LIST_H, available - SEARCH_H),
  );
  const top = placeBelow
    ? rect.bottom + GAP
    : Math.max(EDGE, rect.top - GAP - SEARCH_H - listMaxHeight);
  return { top, left, width, listMaxHeight };
}

export function LangCombobox({
  value,
  onChange,
  languages,
  includeNone,
  noneLabel,
  disabled,
  id,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [popStyle, setPopStyle] = useState<PopStyle | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  const close = () => {
    setOpen(false);
    setQ("");
    setPopStyle(null);
  };

  const list = useMemo(() => {
    let rows = languages.slice();
    if (!includeNone) {
      rows = rows.filter((l) => l.code !== "none");
    } else {
      const hasNone = rows.some((l) => l.code === "none");
      if (!hasNone) {
        rows = [
          ...rows,
          {
            code: "none",
            label_zh: noneLabel || "关闭",
            label_native: "Off",
            region: "其他",
          },
        ];
      } else if (noneLabel) {
        rows = rows.map((l) =>
          l.code === "none" ? { ...l, label_zh: noneLabel, label_native: noneLabel } : l,
        );
      }
    }
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
  }, [languages, includeNone, noneLabel, q]);

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

  const selected = list.find((l) => l.code === value) || languages.find((l) => l.code === value);
  const label = selected
    ? selected.code === "none"
      ? selected.label_zh
      : `${selected.label_zh} · ${selected.label_native}`
    : value || "选择语言";

  const updatePosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    setPopStyle(measurePop(trigger));
  };

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (rootRef.current?.contains(t) || popRef.current?.contains(t)) return;
      close();
      triggerRef.current?.focus();
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        triggerRef.current?.focus();
      }
    };

    const onReposition = (e: Event) => {
      if (e.type === "scroll" && popRef.current) {
        const target = e.target;
        if (target instanceof Node && popRef.current.contains(target)) return;
      }
      updatePosition();
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", onReposition);
    document.addEventListener("scroll", onReposition, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("resize", onReposition);
      document.removeEventListener("scroll", onReposition, true);
    };
  }, [open]);

  const pop =
    open && popStyle
      ? createPortal(
          <div
            ref={popRef}
            className="lang-combo-pop"
            style={{
              top: popStyle.top,
              left: popStyle.left,
              width: popStyle.width,
            }}
          >
            <input
              className="lang-combo-search"
              placeholder="搜索语言 / 地区 / 代码…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              autoFocus
            />
            <ul className="lang-combo-list" style={{ maxHeight: popStyle.listMaxHeight }}>
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
                            close();
                          }}
                        >
                          <span>
                            {l.code === "none" ? l.label_zh : `${l.label_zh} · ${l.label_native}`}
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
          </div>,
          document.body,
        )
      : null;

  return (
    <div ref={rootRef} className={`lang-combo${open ? " is-open" : ""}`} id={id}>
      <button
        ref={triggerRef}
        type="button"
        className="lang-combo-trigger"
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          setOpen((v) => {
            if (v) {
              setQ("");
              setPopStyle(null);
              return false;
            }
            return true;
          });
        }}
        aria-expanded={open}
      >
        <span>{label}</span>
        <span className="lang-combo-code">{value}</span>
      </button>
      {pop}
    </div>
  );
}
