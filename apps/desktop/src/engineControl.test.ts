import { describe, expect, it } from "vitest";
import { offlineClassLabel } from "./engineControl";

describe("offlineClassLabel", () => {
  it("maps readiness vocabulary used by ENGINE_SUPERVISOR", () => {
    expect(offlineClassLabel({ offline_class: "control_plane_not_ready" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "control_plane" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "boot_failed" } as never)).toContain("启动失败");
    expect(offlineClassLabel({ offline_class: "not_ready" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "listen_unhealthy" } as never)).toContain("控制面");
  });
});
