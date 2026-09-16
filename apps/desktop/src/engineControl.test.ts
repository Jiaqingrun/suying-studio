import { describe, expect, it } from "vitest";
import { offlineClassLabel } from "./engineControl";
import { formatApiHttpError } from "./api/all";

describe("offlineClassLabel", () => {
  it("maps readiness vocabulary used by ENGINE_SUPERVISOR", () => {
    expect(offlineClassLabel({ offline_class: "control_plane_not_ready" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "control_plane" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "boot_failed" } as never)).toContain("启动失败");
    expect(offlineClassLabel({ offline_class: "not_ready" } as never)).toContain("业务未就绪");
    expect(offlineClassLabel({ offline_class: "listen_unhealthy" } as never)).toContain("控制面");
  });
});

describe("formatApiHttpError", () => {
  it("does not claim 路径 for login/slot conflict 409", () => {
    const msg = formatApiHttpError(409, "软文发布账号须由当前受管 Chrome 实时确认已登录");
    expect(msg).toContain("当前状态不允许此操作");
    expect(msg).not.toContain("路径");
    expect(formatApiHttpError(409, "发布槽正忙，请稍后再试")).not.toContain("路径");
    expect(formatApiHttpError(423, "paused")).toContain("运行时已暂停");
  });
});
