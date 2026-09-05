export function remainingProductionCount(
  jobs: Array<Record<string, unknown>>,
): number {
  return jobs.reduce((total, job) => {
    const status = String(job.status || "");
    if (!["running", "queued", "pending"].includes(status)) return total;
    const target = Math.max(0, Number(job.target_count || 0));
    const produced = Math.max(0, Number(job.produced_count || 0));
    return total + Math.max(0, target - produced);
  }, 0);
}
