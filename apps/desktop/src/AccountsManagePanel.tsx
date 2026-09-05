import { useCallback, useEffect, useState } from "react";
import { AccountDomainPanel, type PlatformOpt } from "./AccountDomainPanel";
import { api } from "./api";
import type { AskConfirmFn, NotifyFn } from "./pages/pageTypes";
import type { ChromeProfile } from "./types";

type Domain = "video" | "content";

export function AccountsManagePanel({
  notify,
  askConfirm,
}: {
  notify: NotifyFn;
  askConfirm: AskConfirmFn;
}) {
  const [domain, setDomain] = useState<Domain>("video");
  const [busy, setBusy] = useState(false);
  const [videoProfiles, setVideoProfiles] = useState<ChromeProfile[]>([]);
  const [videoSelected, setVideoSelected] = useState("");
  const [videoPlatforms, setVideoPlatforms] = useState<PlatformOpt[]>([]);
  const [videoCreatePlatform, setVideoCreatePlatform] = useState("douyin");
  const [videoCreateName, setVideoCreateName] = useState("");
  const [videoRename, setVideoRename] = useState("");
  const [contentProfiles, setContentProfiles] = useState<ChromeProfile[]>([]);
  const [contentSelected, setContentSelected] = useState("");
  const [contentPlatforms, setContentPlatforms] = useState<PlatformOpt[]>([]);
  const [contentCreatePlatform, setContentCreatePlatform] = useState("baijiahao");
  const [contentCreateName, setContentCreateName] = useState("");
  const [contentRename, setContentRename] = useState("");
  const [chromeInstalled, setChromeInstalled] = useState(true);

  const reload = useCallback(async () => {
    const [video, content, platforms] = await Promise.all([
      api.reachChromeProfiles().catch(() => null),
      api.contentChromeProfiles().catch(() => null),
      api.reachPlatforms().catch(() => null),
    ]);
    if (video) {
      setVideoProfiles(video.profiles || []);
      setChromeInstalled(Boolean(video.chrome_installed));
      const sel = video.selected || video.profiles?.[0]?.name || "";
      setVideoSelected(sel);
      setVideoRename(sel);
    }
    if (content) {
      setContentProfiles(content.profiles || []);
      const sel = content.selected || content.profiles?.[0]?.name || "";
      setContentSelected(sel);
      setContentRename(sel);
      const plats = (content.platforms || []) as PlatformOpt[];
      if (plats.length) {
        setContentPlatforms(plats);
        setContentCreatePlatform((prev) => prev || plats[0].id);
      }
    }
    if (platforms?.platforms?.length) {
      const mapped = platforms.platforms.map(
        (p: { id: string; label?: string; short?: string }) => ({
          id: p.id,
          label: p.label || p.short || p.id,
          short: p.short,
        }),
      );
      setVideoPlatforms(mapped);
      setVideoCreatePlatform((prev) => prev || mapped[0].id);
    } else {
      setVideoPlatforms((prev) =>
        prev.length
          ? prev
          : [
              { id: "douyin", label: "抖音" },
              { id: "channels", label: "视频号" },
              { id: "xhs", label: "小红书" },
              { id: "kuaishou", label: "快手" },
            ],
      );
    }
  }, []);

  useEffect(() => {
    void reload().catch((e) => notify(String(e), "err"));
  }, [notify, reload]);

  async function createVideo() {
    setBusy(true);
    try {
      await api.reachChromeCreate({
        platform: videoCreatePlatform,
        count: 1,
        name_prefix: videoCreateName.trim() || undefined,
      });
      await reload();
      notify("已创建视频发布账号", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function openVideo() {
    if (!videoSelected) return;
    setBusy(true);
    try {
      await api.reachChromeOpen(videoSelected);
      notify("已打开视频账号官方页，请完成登录", "info");
      await reload();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function selectVideo(name: string) {
    setVideoSelected(name);
    setVideoRename(name);
    setBusy(true);
    try {
      await api.reachChromeSelect(name);
      await reload();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function renameVideo() {
    if (!videoSelected || !videoRename.trim()) return;
    setBusy(true);
    try {
      await api.reachChromeRename(videoSelected, videoRename.trim());
      await reload();
      notify("视频账号名称已更新", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function deleteVideo(names: string[]) {
    const targets = names.filter(Boolean);
    if (!targets.length) return;
    const ok = await askConfirm({
      title: targets.length > 1 ? "批量删除视频账号" : "删除视频账号",
      body:
        targets.length > 1
          ? `确定删除 ${targets.length} 个视频账号及其本地 Chrome 配置？\n${targets.slice(0, 8).join("、")}${targets.length > 8 ? "…" : ""}`
          : `确定删除视频账号「${targets[0]}」及其本地 Chrome 配置？`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.reachChromeDelete(targets);
      await reload();
      notify(targets.length > 1 ? `已删除 ${targets.length} 个视频账号` : "已删除视频账号", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function createContent() {
    setBusy(true);
    try {
      const created = await api.contentChromeCreate({
        platform: contentCreatePlatform,
        count: 1,
        name_prefix: contentCreateName.trim() || undefined,
      });
      // Belt-and-suspenders: older engines may not auto-bind message accounts.
      for (const item of created.created || []) {
        try {
          await api.contentMessageAccountCreate({
            platform: item.platform || contentCreatePlatform,
            profile_name: item.name,
            display_name: item.name,
            enabled: true,
          });
        } catch {
          /* already bound / unsupported platform */
        }
      }
      await reload();
      notify("已创建软文发布账号", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function openContent() {
    if (!contentSelected) return;
    setBusy(true);
    try {
      await api.contentChromeOpen(contentSelected);
      notify("已打开软文账号登录页", "info");
      await reload();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function selectContent(name: string) {
    setContentSelected(name);
    setContentRename(name);
    setBusy(true);
    try {
      await api.contentChromeSelect(name);
      await reload();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function renameContent() {
    if (!contentSelected || !contentRename.trim()) return;
    setBusy(true);
    try {
      await api.contentChromeRename(contentSelected, contentRename.trim());
      await reload();
      notify("软文账号名称已更新", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function deleteContent(names: string[]) {
    const targets = names.filter(Boolean);
    if (!targets.length) return;
    const ok = await askConfirm({
      title: targets.length > 1 ? "批量删除软文账号" : "删除软文账号",
      body:
        targets.length > 1
          ? `确定删除 ${targets.length} 个软文账号及其本地 Chrome 配置？\n${targets.slice(0, 8).join("、")}${targets.length > 8 ? "…" : ""}`
          : `确定删除软文账号「${targets[0]}」及其本地 Chrome 配置？`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.contentChromeDelete(targets);
      await reload();
      notify(targets.length > 1 ? `已删除 ${targets.length} 个软文账号` : "已删除软文账号", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 12 }} data-guide="accounts-manage">
      <p className="hint">
        视频与软文账号统一在此管理（物理隔离的 Chrome 配置）。发布页只负责排队与执行。
        {!chromeInstalled ? " · 未检测到 Google Chrome" : ""}
      </p>
      <div className="actions">
        <button
          type="button"
          className={domain === "video" ? "primary" : undefined}
          onClick={() => setDomain("video")}
        >
          视频账号 {videoProfiles.length}
        </button>
        <button
          type="button"
          className={domain === "content" ? "primary" : undefined}
          onClick={() => setDomain("content")}
        >
          软文账号 {contentProfiles.length}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void reload().catch((e) => notify(String(e), "err"))}
        >
          刷新状态
        </button>
      </div>

      {domain === "video" ? (
        <AccountDomainPanel
          title="视频账号"
          profiles={videoProfiles}
          platforms={videoPlatforms}
          selected={videoSelected}
          renameValue={videoRename}
          createPlatform={videoCreatePlatform}
          createName={videoCreateName}
          busy={busy}
          createLabel="创建视频账号"
          openLabel="打开官方页登录"
          onSelect={(name) => void selectVideo(name)}
          onRenameChange={setVideoRename}
          onCreatePlatformChange={setVideoCreatePlatform}
          onCreateNameChange={setVideoCreateName}
          onCreate={() => void createVideo()}
          onOpen={() => void openVideo()}
          onRename={() => void renameVideo()}
          onDelete={(names) => void deleteVideo(names)}
        />
      ) : (
        <AccountDomainPanel
          title="软文账号"
          profiles={contentProfiles}
          platforms={
            contentPlatforms.length
              ? contentPlatforms
              : [
                  { id: "baijiahao", label: "百家号" },
                  { id: "toutiao", label: "头条号" },
                  { id: "zhihu", label: "知乎" },
                  { id: "wechat_mp", label: "公众号" },
                ]
          }
          selected={contentSelected}
          renameValue={contentRename}
          createPlatform={contentCreatePlatform}
          createName={contentCreateName}
          busy={busy}
          createLabel="创建软文账号"
          openLabel="打开登录"
          onSelect={(name) => void selectContent(name)}
          onRenameChange={setContentRename}
          onCreatePlatformChange={setContentCreatePlatform}
          onCreateNameChange={setContentCreateName}
          onCreate={() => void createContent()}
          onOpen={() => void openContent()}
          onRename={() => void renameContent()}
          onDelete={(names) => void deleteContent(names)}
        />
      )}
    </div>
  );
}
