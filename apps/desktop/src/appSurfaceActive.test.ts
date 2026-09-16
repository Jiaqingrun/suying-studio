import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetAppSurfaceForTests,
  applyNativeAppSurface,
  isAppSurfaceActive,
  subscribeAppSurfaceActive,
} from "./appSurfaceActive";

describe("appSurfaceActive P0.3", () => {
  beforeEach(() => {
    __resetAppSurfaceForTests({ docVisible: true, winMinimized: false, winFocused: true });
  });

  it("is active when document visible, focused, not minimized", () => {
    expect(isAppSurfaceActive()).toBe(true);
  });

  it("stops when minimized even if still focused flag lags", () => {
    applyNativeAppSurface({ minimized: true, focused: true });
    expect(isAppSurfaceActive()).toBe(false);
  });

  it("stops on blur (失焦)", () => {
    applyNativeAppSurface({ focused: false });
    expect(isAppSurfaceActive()).toBe(false);
  });

  it("notifies subscribers when activity flips", () => {
    let flips = 0;
    const un = subscribeAppSurfaceActive(() => {
      flips += 1;
    });
    applyNativeAppSurface({ focused: false });
    applyNativeAppSurface({ focused: true });
    un();
    expect(flips).toBe(2);
  });
});
