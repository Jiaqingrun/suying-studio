// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppNotifyHub, buildNotifyItem } from "./AppNotifyHub";
import { playAlertSound } from "./notifications";

vi.mock("./notifications", () => ({
  playAlertSound: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("AppNotifyHub", () => {
  it("builds every ordinary business notification as a 15 second toast", () => {
    for (const kind of ["info", "ok", "warn", "err"] as const) {
      const item = buildNotifyItem("消息", kind);
      expect(item.placement).toBe("toast");
      expect(item.holdMs).toBe(15_000);
      expect(item).not.toHaveProperty("actionLabel");
    }
  });

  it("renders no close button and makes the whole card actionable", () => {
    const onAction = vi.fn();
    const onDismiss = vi.fn();
    const item = {
      ...buildNotifyItem("去发布", "ok", { actionTab: "publish" }),
      withSound: false,
    };
    render(
      <AppNotifyHub
        toasts={[item]}
        celebration={null}
        banner={null}
        onDismissToast={onDismiss}
        onDismissCelebration={vi.fn()}
        onDismissBanner={vi.fn()}
        onAction={onAction}
      />,
    );

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("去发布"));
    expect(onAction).toHaveBeenCalledWith({ tab: "publish" });
    expect(onDismiss).toHaveBeenCalledWith(item.id);
  });

  it("does not restart the 15 second timer when parent callbacks change", () => {
    vi.useFakeTimers();
    const item = { ...buildNotifyItem("完成", "ok"), withSound: false };
    const firstDismiss = vi.fn();
    const view = render(
      <AppNotifyHub
        toasts={[item]}
        celebration={null}
        banner={null}
        onDismissToast={firstDismiss}
        onDismissCelebration={vi.fn()}
        onDismissBanner={vi.fn()}
      />,
    );
    vi.advanceTimersByTime(10_000);
    const latestDismiss = vi.fn();
    view.rerender(
      <AppNotifyHub
        toasts={[item]}
        celebration={null}
        banner={null}
        onDismissToast={latestDismiss}
        onDismissCelebration={vi.fn()}
        onDismissBanner={vi.fn()}
      />,
    );
    vi.advanceTimersByTime(5_000);
    expect(latestDismiss).toHaveBeenCalledWith(item.id);
  });

  it("plays the Mario sound once for one mounted card", () => {
    const item = buildNotifyItem("警告", "warn");
    const props = {
      toasts: [item],
      celebration: null,
      banner: null,
      onDismissToast: vi.fn(),
      onDismissCelebration: vi.fn(),
      onDismissBanner: vi.fn(),
    };
    const view = render(<AppNotifyHub {...props} />);
    view.rerender(<AppNotifyHub {...props} />);
    expect(playAlertSound).toHaveBeenCalledTimes(1);
    expect(playAlertSound).toHaveBeenCalledWith("mario");
  });
});
