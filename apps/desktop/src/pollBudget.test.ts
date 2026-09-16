import { describe, expect, it } from "vitest";
import { POLL_BUDGET_MS, POLL_TICK } from "./pollBudget";

describe("pollBudget S1/S3/S4 floors", () => {
  it("keeps messages ≥30s and services slower than health", () => {
    expect(POLL_BUDGET_MS.messages).toBeGreaterThanOrEqual(30_000);
    expect(POLL_BUDGET_MS.services).toBeGreaterThanOrEqual(30_000);
    expect(POLL_BUDGET_MS.services).toBeGreaterThan(POLL_BUDGET_MS.health);
    expect(POLL_TICK.servicesEvery).toBeGreaterThanOrEqual(6);
  });

  it("keeps pipeline idle slower than busy", () => {
    expect(POLL_BUDGET_MS.semanticIdle).toBeGreaterThanOrEqual(15_000);
    expect(POLL_BUDGET_MS.semanticFast).toBe(3_000);
    expect(POLL_BUDGET_MS.semanticIdle).toBeGreaterThan(POLL_BUDGET_MS.semanticFast);
  });
});
