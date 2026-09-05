import { useCallback, useEffect, useRef, useState } from "react";
import type { FlashKind, Tab } from "./types";
import { playAlertSound } from "./notifications";

export type NotifyPlacement = "toast" | "banner" | "celebrate";

export type NotifyTarget = {
  tab: Tab;
  section?: string;
  guideTarget?: string;
  outputId?: number;
};

export type NotifyItem = {
  id: number;
  text: string;
  kind: FlashKind;
  action?: NotifyTarget;
  placement: NotifyPlacement;
  holdMs: number;
  withSound: boolean;
};

export type NotifyOptions = {
  action?: NotifyTarget;
  /** Compatibility shorthand; new callers should use action for exact navigation. */
  actionTab?: Tab;
  holdMs?: number;
  /** toast = 右上角；banner = 顶栏横幅；celebrate = 顶部居中高亮完成提示 */
  placement?: NotifyPlacement;
  sound?: boolean;
};

const DEFAULT_HOLD: Record<FlashKind, number> = {
  err: 15_000,
  warn: 15_000,
  ok: 15_000,
  info: 15_000,
};

let _seq = 0;

export function buildNotifyItem(
  text: string,
  kind: FlashKind = "info",
  opts?: NotifyOptions,
): NotifyItem {
  const placement = opts?.placement ?? "toast";
  const celebrate = placement === "celebrate";
  return {
    id: ++_seq,
    text,
    kind: celebrate ? "ok" : kind,
    action: opts?.action ?? (opts?.actionTab ? { tab: opts.actionTab } : undefined),
    placement,
    holdMs: opts?.holdMs ?? (celebrate || placement === "toast" ? 15_000 : DEFAULT_HOLD[kind]),
    withSound:
      opts?.sound ?? (placement === "toast" || celebrate || kind === "err" || kind === "warn"),
  };
}

type HubProps = {
  toasts: NotifyItem[];
  celebration: NotifyItem | null;
  banner: NotifyItem | null;
  onDismissToast: (id: number) => void;
  onDismissCelebration: () => void;
  onDismissBanner: () => void;
  onAction?: (target: NotifyTarget) => void;
};

export function AppNotifyHub({
  toasts,
  celebration,
  banner,
  onDismissToast,
  onDismissCelebration,
  onDismissBanner,
  onAction,
}: HubProps) {
  return (
    <>
      <div className="notify-toast-stack" aria-live="polite">
        {toasts.map((item) => (
          <NotifyCard
            key={item.id}
            item={item}
            className="notify-toast"
            onDismiss={() => onDismissToast(item.id)}
            onAction={onAction}
          />
        ))}
      </div>
      {celebration ? (
        <div className="notify-celebrate-wrap" role="status" aria-live="polite">
          <NotifyCard
            item={celebration}
            className="notify-celebrate"
            onDismiss={onDismissCelebration}
            onAction={onAction}
          />
        </div>
      ) : null}
      {banner && (
        <div
          className={`banner ${banner.kind === "err" ? "error" : banner.kind}`}
          role="status"
          aria-live="polite"
          onClick={() => {
            if (banner.action) onAction?.(banner.action);
            onDismissBanner();
          }}
          style={{ cursor: banner.action ? "pointer" : undefined }}
        >
          <span className="banner-text">{banner.text}</span>
        </div>
      )}
    </>
  );
}

function NotifyCard({
  item,
  className,
  onDismiss,
  onAction,
}: {
  item: NotifyItem;
  className: string;
  onDismiss: () => void;
  onAction?: (target: NotifyTarget) => void;
}) {
  const sounded = useRef(false);
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);
  useEffect(() => {
    if (item.withSound && !sounded.current) {
      sounded.current = true;
      playAlertSound("mario");
    }
  }, [item.withSound]);

  useEffect(() => {
    const t = window.setTimeout(() => dismissRef.current(), item.holdMs);
    return () => window.clearTimeout(t);
  }, [item.id, item.holdMs]);

  const kindClass =
    item.kind === "err" ? "error" : item.kind === "ok" ? "ok" : item.kind === "warn" ? "warn" : "info";

  return (
    <div
      className={`${className} notify-card notify-card--${kindClass}${item.action ? " notify-card--clickable" : ""}`}
      role="status"
      onClick={() => {
        if (item.action) onAction?.(item.action);
        onDismiss();
      }}
    >
      <span className="notify-card-text">{item.text}</span>
    </div>
  );
}

/** Hook state helper for App root */
export function useNotifyHub() {
  const [toasts, setToasts] = useState<NotifyItem[]>([]);
  const [celebration, setCelebration] = useState<NotifyItem | null>(null);
  const [banner, setBanner] = useState<NotifyItem | null>(null);

  // Stable identity required: App mounts health/engine poll on [notify]; unstable
  // notify remounts that effect every render → /health flood + UI stutter.
  const notify = useCallback((text: string, kind: FlashKind = "info", opts?: NotifyOptions) => {
    const item = buildNotifyItem(text, kind, opts);
    if (item.withSound) {
      playAlertSound("mario");
      item.withSound = false; // card won't double-play
    }
    if (item.placement === "celebrate") {
      setCelebration(item);
      return;
    }
    if (item.placement === "toast") {
      setToasts((prev) => [...prev.slice(-4), item]);
      return;
    }
    setBanner(item);
  }, []);

  const celebrate = useCallback(
    (text: string, opts?: Omit<NotifyOptions, "placement">) => {
      notify(text, "ok", {
        ...opts,
        placement: "celebrate",
        holdMs: opts?.holdMs ?? 15_000,
        sound: opts?.sound !== false,
      });
    },
    [notify],
  );

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const dismissCelebration = useCallback(() => setCelebration(null), []);

  const dismissBanner = useCallback(() => {
    setBanner(null);
  }, []);

  return {
    toasts,
    celebration,
    banner,
    notify,
    celebrate,
    dismissToast,
    dismissCelebration,
    dismissBanner,
  };
}

