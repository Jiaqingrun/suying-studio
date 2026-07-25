import { useEffect, useMemo, useRef, useState } from "react";
import type { Tab } from "../types";
import { TABS } from "../types";

export type CmdItem = {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
};

type Props = {
  open: boolean;
  onClose: () => void;
  onSetTab: (t: Tab) => void;
  extra?: CmdItem[];
};

export function CommandPalette({ open, onClose, onSetTab, extra = [] }: Props) {
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() => {
    const base: CmdItem[] = TABS.map(([id, label, num]) => ({
      id: `tab-${id}`,
      label: `前往 ${label}`,
      hint: num,
      run: () => onSetTab(id),
    }));
    return [...base, ...extra];
  }, [extra, onSetTab]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter((i) => i.label.toLowerCase().includes(s) || i.id.includes(s) || (i.hint || "").includes(s));
  }, [items, q]);

  useEffect(() => {
    if (!open) return;
    setQ("");
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
            if (e.key === "Enter" && filtered[0]) {
              filtered[0].run();
              onClose();
            }
          }}
        />
        <ul className="cmdk-list">
          {filtered.map((i) => (
            <li key={i.id}>
              <button
                type="button"
                className="cmdk-item"
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
