import { useEffect, useMemo, useRef, useState } from "react";

export type CmdItem = {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
};

type Props = {
  open: boolean;
  onClose: () => void;
  extra?: CmdItem[];
};

export function CommandPalette({ open, onClose, extra = [] }: Props) {
  const [q, setQ] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() => [...extra], [extra]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter((i) => i.label.toLowerCase().includes(s) || i.id.includes(s) || (i.hint || "").includes(s));
  }, [items, q]);

  useEffect(() => setActiveIndex(0), [q]);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setActiveIndex(0);
    const t = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="cmdk-overlay" role="dialog" aria-modal="true" aria-label="命令面板" onClick={onClose}>
      <div className="cmdk-panel" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder="搜索命令…（切页、操作）"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActiveIndex((value) => Math.min(filtered.length - 1, value + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActiveIndex((value) => Math.max(0, value - 1));
            } else if (e.key === "Enter" && filtered[activeIndex]) {
              filtered[activeIndex].run();
              onClose();
            }
          }}
        />
        <ul className="cmdk-list">
          {filtered.map((i, index) => (
            <li key={i.id}>
              <button
                type="button"
                className={`cmdk-item${index === activeIndex ? " is-active" : ""}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => {
                  i.run();
                  onClose();
                }}
              >
                <span>{i.label}</span>
                {i.hint ? <kbd>{i.hint}</kbd> : null}
              </button>
            </li>
          ))}
          {filtered.length === 0 && <li className="cmdk-empty">无匹配命令</li>}
        </ul>
      </div>
    </div>
  );
}
