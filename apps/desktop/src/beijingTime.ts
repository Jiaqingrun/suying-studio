/** Display timestamps in Beijing time (Asia/Shanghai) for customer-facing UI. */

const TZ = "Asia/Shanghai";

export function formatBeijingTime(
  value: string | number | Date | null | undefined,
  opts?: Intl.DateTimeFormatOptions,
): string {
  if (value == null || value === "") return "—";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    ...opts,
  }).format(d);
}
