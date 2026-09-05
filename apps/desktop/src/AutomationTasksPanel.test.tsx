// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { AutomationTasksPanel } from "./AutomationTasksPanel";
import type { ChromeProfile } from "./types";

vi.mock("./api", () => ({
  api: {
    reachPublishTasks: vi.fn(),
    reachPublishTaskCreate: vi.fn(),
    reachPublishTaskUpdate: vi.fn(),
    reachPublishTaskDelete: vi.fn(),
    reachPublishSchedulePreview: vi.fn(),
    reachPublishMakeup: vi.fn(),
    reachPublishMakeupRetry: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.reachPublishTasks.mockResolvedValue({ ok: true, tasks: [] });
  mockedApi.reachPublishTaskCreate.mockResolvedValue({ ok: true });
  mockedApi.reachPublishMakeup.mockResolvedValue({
    ok: true,
    deferred: [],
    triggers: [],
    counts: { deferred: 0, triggers: 0, missed_human_confirm: 0, makeup_pending: 0 },
  });
});

afterEach(() => {
  cleanup();
});

describe("AutomationTasksPanel", () => {
  it("submits multiple accounts with per-account counts and second precision", async () => {
    render(
      <AutomationTasksPanel
        notify={vi.fn()}
        chromeProfiles={[
          {
            name: "抖音-01",
            path: "/tmp/douyin",
            platform: "douyin",
            provisioning_status: "explicit",
            login_status: "verified_logged_in",
          },
          {
            name: "小红书-01",
            path: "/tmp/xhs",
            platform: "xhs",
            provisioning_status: "explicit",
            login_status: "verified_logged_in",
          },
        ]}
      />,
    );

    await screen.findByText("自动任务");
    const countInputs = screen.getAllByDisplayValue("1");
    fireEvent.change(countInputs[0], { target: { value: "2" } });
    fireEvent.change(countInputs[1], { target: { value: "3" } });
    fireEvent.change(screen.getByDisplayValue("09:00:00"), {
      target: { value: "09:00:07" },
    });
    fireEvent.change(screen.getByDisplayValue("11:00:00"), {
      target: { value: "11:00:59" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建自动任务" }));

    await waitFor(() => expect(mockedApi.reachPublishTaskCreate).toHaveBeenCalledTimes(1));
    expect(mockedApi.reachPublishTaskCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        windows: [{ start: "09:00:07", end: "11:00:59" }],
        auto_production: true,
        targets: [
          {
            platform: "douyin",
            chrome_profile: "抖音-01",
            publish_count: 2,
          },
          {
            platform: "xhs",
            chrome_profile: "小红书-01",
            publish_count: 3,
          },
        ],
      }),
    );
  });

  it("keeps empty selection after cancel-all when accounts prop refreshes", async () => {
    const profiles: ChromeProfile[] = [
      {
        name: "抖音-01",
        path: "/tmp/douyin",
        platform: "douyin",
        provisioning_status: "explicit",
        login_status: "verified_logged_in",
      },
    ] as ChromeProfile[];
    const selectionHint = (selected: number) => (_: unknown, node: Element | null) =>
      node?.tagName === "P" &&
      node.textContent === `已登录可建任务 1 个 · 已选 ${selected} 个`;

    const { rerender } = render(
      <AutomationTasksPanel notify={vi.fn()} chromeProfiles={profiles} />,
    );
    expect(await screen.findByText(selectionHint(1))).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消全选" }));
    expect(await screen.findByText(selectionHint(0))).toBeInTheDocument();
    rerender(
      <AutomationTasksPanel notify={vi.fn()} chromeProfiles={[...profiles]} />,
    );
    await waitFor(() => expect(screen.getByText(selectionHint(0))).toBeInTheDocument());
  });
});
