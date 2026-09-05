import type { ReportSummary } from "../api";
import type { FlashKind, Tab } from "../types";

export type NotifyFn = (
  text: string,
  kind?: FlashKind,
  opts?: {
    actionLabel?: string;
    actionTab?: Tab;
    holdMs?: number;
    placement?: "toast" | "banner" | "celebrate";
    sound?: boolean;
  },
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
  revision?: number;
  sha256?: string;
  schema?: string;
};

export type OpsReportView = {
  generated_at: string;
  timezone: "Asia/Shanghai";
  business_date: string;
  source: "database";
  ready_available: number;
  production_passed: number;
  failed: number;
  quality_pass_rate: number | null;
  failure_rate: number | null;
  auto_approved: number;
  auto_rejected: number;
  uncertain_open: number;
  manual_decided: number;
  published: number;
  retired: number;
  production_passed_today: number;
  published_today: number;
  reconciliation: ReportSummary["reconciliation"];
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
  // Cached functional status (not re-probed on every poll) so the UI never
  // shows false green when only tag listing (`reachable`/`ready`) succeeded.
  inference_available?: boolean;
  circuit_open?: boolean;
  status_layers?: {
    service_reachable?: boolean;
    model_present?: boolean;
    inference_available?: boolean;
    circuit_open?: boolean;
  };
  circuit?: {
    state?: string;
    consecutive_failures?: number;
    open_remaining_sec?: number;
    last_error?: string;
    last_error_kind?: string;
  };
};

export type LangCatalogItem = {
  code: string;
  label_zh: string;
  label_native: string;
  region: string;
};
