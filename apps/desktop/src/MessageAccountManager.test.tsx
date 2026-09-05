// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageAccountManager } from "./MessageAccountManager";

const account = {
  id: 7,
  customer_id: 1,
  platform: "douyin",
  profile_name: "message-7",
  profile_role: "message",
  display_name: "品牌主账号",
  enabled: true,
  cooldown_sec: 1800,
  message_url: "",
  // Engine persists login failures as needs_human + error_code=login_required
  last_status: "needs_human",
  last_error_code: "login_required",
  last_error: "官方页尚未登录，请在该 Chrome profile 中人工登录",
  readonly_verified: false,
};

describe("MessageAccountManager", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    cleanup();
  });

  it("creates and manages an independent message account", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const onOpenLogin = vi.fn().mockResolvedValue(undefined);
    const onToggle = vi.fn();
    const onDelete = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <MessageAccountManager
        domainLabel="视频"
        accounts={[account]}
        busy={false}
        platforms={[{ id: "douyin", label: "抖音" }]}
        onCreate={onCreate}
        onOpenLogin={onOpenLogin}
        onToggle={onToggle}
        onDelete={onDelete}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("例如：品牌主账号"), {
      target: { value: "第二账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建消息账号" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("douyin", "第二账号"));

    fireEvent.click(screen.getByRole("button", { name: "打开登录" }));
    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(onOpenLogin).toHaveBeenCalledWith(account);
    expect(onToggle).toHaveBeenCalledWith(account);
    expect(onDelete).toHaveBeenCalledWith(account);
    expect(screen.getByText(/message · 需登录/)).toBeInTheDocument();
    expect(screen.getByText(/需重新登录 · 点「打开登录」/)).toBeInTheDocument();
  });

  it("distinguishes publish chrome busy from login failure", () => {
    const busyAccount = {
      id: 8,
      customer_id: 1,
      platform: "kuaishou",
      profile_name: "message-8",
      profile_role: "shared_legacy",
      display_name: "快手账号",
      enabled: true,
      cooldown_sec: 1800,
      message_url: "",
      last_status: "deferred",
      last_error_code: "chrome_busy",
      last_error: "Chrome 正由 publish_run:abc 使用",
      readonly_verified: true,
    };
    const { container } = render(
      <MessageAccountManager
        domainLabel="视频"
        accounts={[busyAccount]}
        busy={false}
        platforms={[{ id: "kuaishou", label: "快手" }]}
        onCreate={vi.fn()}
        onOpenLogin={vi.fn()}
        onToggle={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(container.textContent).toMatch(/等待浏览器/);
    expect(container.textContent).toMatch(/发布占用浏览器/);
    expect(container.textContent).not.toMatch(/需重新登录/);
  });

  it("treats publish_priority as browser delay not login", () => {
    const prio = {
      id: 9,
      customer_id: 1,
      platform: "channels",
      profile_name: "视频号-5345",
      profile_role: "shared_legacy",
      display_name: "视频号",
      enabled: true,
      cooldown_sec: 1800,
      message_url: "",
      last_status: "deferred",
      last_error_code: "publish_priority",
      last_error:
        "publish_priority: 浏览器正由发布任务占用，消息巡检已自动延后（非账号登录问题）",
      readonly_verified: false,
    };
    const { container } = render(
      <MessageAccountManager
        domainLabel="视频"
        accounts={[prio]}
        busy={false}
        platforms={[{ id: "channels", label: "视频号" }]}
        onCreate={vi.fn()}
        onOpenLogin={vi.fn()}
        onToggle={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(container.textContent).toMatch(/发布优先延后/);
    expect(container.textContent).not.toMatch(/需重新登录/);
  });
});
