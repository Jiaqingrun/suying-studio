import type { FlashKind, Tab } from "../types";

export type NotifyFn = (
  text: string,
  kind?: FlashKind,
  opts?: { actionLabel?: string; actionTab?: Tab; holdMs?: number },
) => void;

export type AskConfirmFn = (opts: {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}) => Promise<boolean>;

export type TitlePoolSummary = {
  loaded: boolean;
  title_pool_count: number;
  title_pool_sample: string[];
  hooks_count: number;
  max_chars_per_line?: number;
  version?: number;
};

export type OpsReportView = {
  pending_ready?: number;
  ready?: number;
  published_count?: number;
  published_ready?: number;
  ready_today?: number;
  published_today?: number;
  failure_rate?: number;
  ready_rate?: number;
  missing_voice?: number;
  tts_noncompliant?: number;
  tts_say?: number;
  quota?: Record<string, unknown>;
  library_ok?: boolean;
  output_ok?: boolean;
  db_ok?: boolean;
  health_line?: string;
};

export type OllamaInfo = {
  reachable?: boolean;
  embed_ready?: boolean;
  vision_ready?: boolean;
  escalate_ready?: boolean | null;
  ready?: boolean;
  message?: string;
  install_url?: string;
  embed_model?: string;
  vision_model?: string;
  escalate_model?: string | null;
  cascade?: boolean;
  vision_timeout_sec?: number;
  escalate_timeout_sec?: number;
  pull?: Record<string, unknown>;
  host?: Record<string, unknown>;
  recommended?: {
    tier?: string;
    embed_model?: string;
    vision_model?: string;
    escalate_model?: string | null;
    cascade?: boolean;
    vision_label?: string;
    reason?: string;
  };
  setup_steps?: Array<{ id: string; title: string; ok: boolean; detail: string }>;
};

export type LangCatalogItem = {
  code: string;
  label_zh: string;
  label_native: string;
  region: string;
};
