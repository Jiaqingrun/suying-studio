/** Customer-facing sole video UID: #001, #002, … */

export function formatDisplayNo(n: unknown): string {
  const value = Number(n);
  if (!Number.isFinite(value) || value < 1) return "";
  const width = value < 1000 ? 3 : String(Math.trunc(value)).length;
  return `#${String(Math.trunc(value)).padStart(width, "0")}`;
}

export function outputDisplayLabel(row: {
  display_label?: unknown;
  display_no?: unknown;
  id?: unknown;
}): string {
  const labeled = String(row.display_label || "").trim();
  if (labeled) return labeled;
  const fromNo = formatDisplayNo(row.display_no);
  if (fromNo) return fromNo;
  // Legacy rows before backfill — never invent a second identity scheme.
  return "";
}
