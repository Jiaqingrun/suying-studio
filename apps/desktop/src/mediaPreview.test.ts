import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  convertFileSrc: (p: string) => `asset://localhost/${encodeURIComponent(p)}`,
}));

vi.mock("./engineControl", () => ({
  isTauri: () => true,
}));

vi.mock("./api", () => ({
  outputVideoUrl: (id: number, bust?: string | number | null) =>
    `http://127.0.0.1:8766/outputs/${id}/video${bust != null ? `?c=${bust}` : ""}`,
  outputCoverUrl: (id: number, index: number, bust?: string | number | null) =>
    `http://127.0.0.1:8766/outputs/${id}/cover/${index}${bust != null ? `?c=${bust}` : ""}`,
}));

import { previewCoverSrc, previewVideoSrc } from "./mediaPreview";

describe("mediaPreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses asset:// when localOk and path present", () => {
    const src = previewVideoSrc(12, "/tmp/a.mp4", "1");
    expect(src.startsWith("asset://")).toBe(true);
  });

  it("skips asset:// when localOk=false (missing on disk)", () => {
    const src = previewVideoSrc(12, "/tmp/missing.mp4", "1", { localOk: false });
    expect(src).toContain("/outputs/12/video");
  });

  it("preferHttp forces engine URL", () => {
    const src = previewCoverSrc(9, 2, "/tmp/c.jpg", "b", { preferHttp: true });
    expect(src).toBe("http://127.0.0.1:8766/outputs/9/cover/2?c=b");
  });

  it("falls back to HTTP when path empty", () => {
    expect(previewVideoSrc(3, "", null)).toContain("/outputs/3/video");
    expect(previewCoverSrc(3, 0, null)).toContain("/outputs/3/cover/0");
  });
});
