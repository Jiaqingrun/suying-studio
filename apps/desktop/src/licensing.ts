import { invoke } from "@tauri-apps/api/core";

export type LicenseStatus = {
  authorized: boolean;
  development_build: boolean;
  reason: string;
  license_id: string | null;
  device_key_id: string | null;
  delivery_id: string | null;
  customer_ref: string | null;
  issue_seq: number | null;
  features: string[];
  license_kind: "trial" | "perpetual" | "term" | "development" | "";
  expires_at: string | null;
  trial_remaining_sec: number | null;
  remaining_sec?: number | null;
  ops_unlock_allowed: boolean;
  code?: string;
};

export type LicenseRequest = {
  schema_version: number;
  product_id: string;
  device_key_id: string;
  machine_hint: string;
  created_at_unix: number;
};

export function getLicenseStatus(): Promise<LicenseStatus> {
  return invoke<LicenseStatus>("license_status");
}

export function createLicenseRequest(): Promise<LicenseRequest> {
  return invoke<LicenseRequest>("license_request");
}

export function installLicense(path: string): Promise<LicenseStatus> {
  return invoke<LicenseStatus>("license_install_path", { path });
}
