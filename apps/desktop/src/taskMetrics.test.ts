import { describe, expect, it } from "vitest";

import { remainingProductionCount } from "./taskMetrics";

describe("remainingProductionCount", () => {
  it("sums each active task's unfinished outputs instead of counting jobs", () => {
    expect(
      remainingProductionCount([
        { status: "running", target_count: 10, produced_count: 4 },
        { status: "queued", target_count: 3, produced_count: 1 },
        { status: "completed", target_count: 20, produced_count: 20 },
        { status: "pending", target_count: 1, produced_count: 3 },
      ]),
    ).toBe(8);
  });
});
