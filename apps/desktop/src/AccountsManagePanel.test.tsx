// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountsManagePanel } from "./AccountsManagePanel";
import { api } from "./api";

vi.mock("./api", () => ({
  api: {
    reachChromeProfiles: vi.fn(),
    contentChromeProfiles: vi.fn(),
    reachPlatforms: vi.fn(),
    reachChromeOpen: vi.fn(),
    contentChromeOpen: vi.fn(),
    reachChromeSelect: vi.fn(),
    contentChromeSelect: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.reachChromeProfiles.mockResolvedValue({
    profiles: [{ name: "video-one", login_status: "verified_logged_in" }],
    selected: "video-one",
    chrome_installed: true,
  } as never);
  mockedApi.contentChromeProfiles.mockResolvedValue({
    profiles: [{ name: "content-one", login_status: "logged_out" }],
    selected: "content-one",
    platforms: [{ id: "baijiahao", label: "百家号" }],
  } as never);
  mockedApi.reachPlatforms.mockResolvedValue({
    platforms: [{ id: "douyin", label: "抖音" }],
  } as never);
  mockedApi.reachChromeOpen.mockResolvedValue({} as never);
  mockedApi.contentChromeOpen.mockResolvedValue({} as never);
});

describe("AccountsManagePanel", () => {
  it("keeps video and content actions on their own domain APIs", async () => {
    render(
      <AccountsManagePanel
        notify={vi.fn()}
        askConfirm={vi.fn().mockResolvedValue(false)}
      />,
    );
    await screen.findByText("视频账号 1");

    fireEvent.click(screen.getByRole("button", { name: "打开官方页登录" }));
    await waitFor(() => expect(mockedApi.reachChromeOpen).toHaveBeenCalledWith("video-one"));
    expect(mockedApi.contentChromeOpen).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "软文账号 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "打开登录" }));
    await waitFor(() => expect(mockedApi.contentChromeOpen).toHaveBeenCalledWith("content-one"));
  });
});
