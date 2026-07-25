/** Turn opaque API objects into short Chinese banners. */

export function briefResult(prefix: string, data: unknown): string {
  if (data == null) return prefix;
  if (typeof data === "string") return `${prefix}：${data}`;
  if (typeof data !== "object") return `${prefix}：${String(data)}`;
  const o = data as Record<string, unknown>;
  const bits: string[] = [];
  const pick = (keys: string[], label?: string) => {
    for (const k of keys) {
      if (o[k] != null && o[k] !== "") {
        bits.push(label ? `${label} ${String(o[k])}` : String(o[k]));
        return;
      }
    }
  };
  pick(["message", "msg", "detail"]);
  pick(["status", "state"], "状态");
  pick(["count", "n", "scanned", "added"], "数量");
  pick(["ok"]);
  if (bits.length === 0) {
    const keys = Object.keys(o).slice(0, 4);
    if (keys.length) bits.push(keys.map((k) => `${k}=${String(o[k])}`).join(" · "));
  }
  return bits.length ? `${prefix}：${bits.join(" · ")}` : prefix;
}

export function dryRunSummary(result: Record<string, unknown>): {
  count: number;
  theme: string;
  category: string;
  samples: string[];
} {
  const candidates = Array.isArray(result.candidates) ? result.candidates : [];
  const count =
    candidates.length ||
    Number(result.candidate_count ?? result.count ?? 0) ||
    0;
  const samples = candidates.slice(0, 5).map((c) => {
    if (c && typeof c === "object") {
      const row = c as Record<string, unknown>;
      return String(row.title || row.name || row.id || JSON.stringify(row).slice(0, 40));
    }
    return String(c);
  });
  return {
    count,
    theme: String(result.theme || "—"),
    category: String(result.category || "—"),
    samples,
  };
}
