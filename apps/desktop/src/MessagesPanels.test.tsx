// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReachMessagesPanel } from "./ReachMessagesPanel";

const account = {
  id: 3,
  customer_id: 1,
  platform: "douyin",
  profile_name: "message-3",
  profile_role: "message",
  display_name: "视频主账号",
  enabled: true,
  cooldown_sec: 1800,
  message_url: "",
  last_status: "ok",
};

describe("ReachMessagesPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("re-requests history and confirms account-scoped read-all", () => {
    const onQueryChange = vi.fn();
    const onReadAll = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <ReachMessagesPanel
        accounts={[account]}
        messages={[]}
        unreadCount={2}
        status={null}
        notificationPermission="granted"
        ntfyConfig={null}
        busy={false}
        onScan={vi.fn()}
        onCancel={vi.fn()}
        onOpen={vi.fn()}
        onRead={vi.fn()}
        onToggleAccount={vi.fn()}
        onCreateAccount={vi.fn().mockResolvedValue(undefined)}
        onOpenAccount={vi.fn().mockResolvedValue(undefined)}
        onDeleteAccount={vi.fn().mockResolvedValue(undefined)}
        onQueryChange={onQueryChange}
        onReadAll={onReadAll}
        onEnableNotifications={vi.fn()}
        onSaveNtfy={vi.fn().mockResolvedValue(undefined)}
        onTestNtfy={vi.fn().mockResolvedValue(undefined)}
        showNotificationSettings={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /视频主账号/ }));
    fireEvent.click(screen.getByRole("button", { name: "30 天历史" }));
    expect(onQueryChange).toHaveBeenLastCalledWith({
      unread: false,
      history: true,
      account_id: 3,
      limit: 300,
    });

    fireEvent.click(screen.getByRole("button", { name: "当前范围全部已读" }));
    expect(window.confirm).toHaveBeenCalled();
    expect(onReadAll).toHaveBeenCalledWith(3);
  });
});
