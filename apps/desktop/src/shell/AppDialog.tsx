import { useEffect, useRef, useState } from "react";

type ConfirmProps = {
  mode?: "confirm";
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

type PromptProps = {
  mode: "prompt";
  open: boolean;
  title: string;
  body: string;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
};

type Props = ConfirmProps | PromptProps;

export function AppDialog(props: Props) {
  const okRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(props.mode === "prompt" ? props.defaultValue || "" : "");

  useEffect(() => {
    if (!props.open) return;
    if (props.mode === "prompt") {
      setValue(props.defaultValue || "");
      const t = window.setTimeout(() => inputRef.current?.focus(), 20);
      return () => window.clearTimeout(t);
    }
    okRef.current?.focus();
  }, [props.open, props.mode]);

  if (!props.open) return null;

  const isPrompt = props.mode === "prompt";

  return (
    <div className="app-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="app-dialog-title">
      <div className="app-dialog">
        <h3 id="app-dialog-title">{props.title}</h3>
        <p className="app-dialog-body">{props.body}</p>
        {isPrompt ? (
          <input
            ref={inputRef}
            className="app-dialog-input"
            value={value}
            placeholder={props.placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && value.trim()) {
                props.onConfirm(value.trim());
              }
            }}
          />
        ) : null}
        <div className="app-dialog-actions">
          <button type="button" onClick={props.onCancel}>
            {props.cancelLabel || "取消"}
          </button>
          <button
            ref={okRef}
            type="button"
            className={!isPrompt && props.danger ? "danger primary" : "primary"}
            disabled={isPrompt && !value.trim()}
            onClick={() => {
              if (isPrompt) props.onConfirm(value.trim());
              else props.onConfirm();
            }}
          >
            {props.confirmLabel || "确定"}
          </button>
        </div>
      </div>
    </div>
  );
}
