import { useEffect, useState } from "react";

import type { NotificationPermissionState } from "../notifications";
import { ContentMessagesPanel } from "../ContentMessagesPanel";
import { ReachMessagesPanel } from "../ReachMessagesPanel";
import { InPageNav, PageHeader, PageSection, SegmentNav, StatusStrip } from "../shell/PageChrome";
import type {
  MessageListQuery,
  ReachMessage,
  ReachMessageAccount,
  ReachMessageScanStatus,
  ReachNtfyConfig,
} from "../types";

export type MessageScopeTab = "video" | "content";

export type MessagesPageProps = {
  accounts: ReachMessageAccount[];
  messages: ReachMessage[];
  unreadCount: number;
  status: ReachMessageScanStatus | null;
  notificationPermission: NotificationPermissionState;
  ntfyConfig: ReachNtfyConfig | null;
  busy: boolean;
  onScan: () => void;
  onCancel: () => void;
  onOpen: (message: ReachMessage) => void;
  onRead: (message: ReachMessage) => void;
  onToggleAccount: (account: ReachMessageAccount) => void;
  onCreateAccount: (platform: string, displayName: string) => Promise<void>;
  onOpenAccount: (account: ReachMessageAccount) => Promise<void>;
  onDeleteAccount: (account: ReachMessageAccount) => Promise<void>;
  onQueryChange: (query: MessageListQuery) => void;
  onReadAll: (accountId?: number) => Promise<void>;
  onEnableNotifications: () => void;
  onSaveNtfy: (body: {
    enabled: boolean;
    server_url: string;
    topic: string;
    auth_mode: "none" | "token" | "basic";
    token?: string;
    username?: string;
    password?: string;
  }) => Promise<void>;
  onTestNtfy: () => Promise<void>;
  onSuggestReplies?: (message: ReachMessage, extraContext: string) => Promise<void>;
  replyDrafts?: Array<Record<string, unknown>>;
  replyDraftMessageId?: number | null;
  onCopyReplyDraft?: (draftId: number, body: string) => void;
  // Soft-article (content) scope
  contentAccounts: ReachMessageAccount[];
  contentMessages: ReachMessage[];
  contentUnreadCount: number;
  contentStatus: ReachMessageScanStatus | null;
  contentBusy: boolean;
  onContentScan: () => void;
  onContentCancel: () => void;
  onContentOpen: (message: ReachMessage) => void;
  onContentPreview: (message: ReachMessage) => void;
  onContentToggleAccount: (account: ReachMessageAccount) => void;
  onContentCreateAccount: (platform: string, displayName: string) => Promise<void>;
  onContentOpenAccount: (account: ReachMessageAccount) => Promise<void>;
  onContentDeleteAccount: (account: ReachMessageAccount) => Promise<void>;
  onContentQueryChange: (query: MessageListQuery) => void;
  onContentReadAll: (accountId?: number) => Promise<void>;
  onContentSuggestReplies?: (message: ReachMessage, extraContext: string) => Promise<void>;
  contentReplyDrafts?: Array<Record<string, unknown>>;
  contentReplyDraftMessageId?: number | null;
  onContentCopyReplyDraft?: (draftId: number, body: string) => void;
};

export function MessagesPage({
  accounts,
  messages,
  unreadCount,
  status,
  notificationPermission,
  ntfyConfig,
  busy,
  onScan,
  onCancel,
  onOpen,
  onRead,
  onToggleAccount,
  onCreateAccount,
  onOpenAccount,
  onDeleteAccount,
  onQueryChange,
  onReadAll,
  onEnableNotifications,
  onSaveNtfy,
  onTestNtfy,
  onSuggestReplies,
  replyDrafts,
  replyDraftMessageId,
  onCopyReplyDraft,
  contentAccounts,
  contentMessages,
  contentUnreadCount,
  contentStatus,
  contentBusy,
  onContentScan,
  onContentCancel,
  onContentOpen,
  onContentPreview,
  onContentToggleAccount,
  onContentCreateAccount,
  onContentOpenAccount,
  onContentDeleteAccount,
  onContentQueryChange,
  onContentReadAll,
  onContentSuggestReplies,
  contentReplyDrafts,
  contentReplyDraftMessageId,
  onContentCopyReplyDraft,
}: MessagesPageProps) {
  const [scope, setScope] = useState<MessageScopeTab>("video");
  const [ntfyEnabled, setNtfyEnabled] = useState(false);
  const [ntfyServer, setNtfyServer] = useState("");
  const [ntfyTopic, setNtfyTopic] = useState("");
  const [notificationBusy, setNotificationBusy] = useState(false);

  useEffect(() => {
    setNtfyEnabled(Boolean(ntfyConfig?.enabled));
    setNtfyServer(ntfyConfig?.server_url || "");
    setNtfyTopic(ntfyConfig?.topic || "");
  }, [ntfyConfig]);

  return (
    <section className="messages-page page-stack">
      <PageHeader
        title="消息回复"
        blurb="视频与软文消息、登录会话、回复草稿完全隔离；仅草稿，跳官方页人工发送"
      />
      <InPageNav
        active="notification-channels"
        items={[
          { id: "notification-channels", label: "通知通道" },
          {
            id: "message-domain",
            label: "账号与未读",
            badge: unreadCount + contentUnreadCount,
          },
        ]}
      />
      <PageSection
        id="notification-channels"
        title="通知与唤回"
        description="App、macOS 与 ntfy 配置只在这里出现一次；登录/验证码按立即、5、15、30 分钟升级。"
      >
        <StatusStrip
          items={[
            {
              label: "macOS 通知",
              value: notificationPermission === "granted" ? "已授权" : "未授权",
              tone: notificationPermission === "granted" ? "ok" : "warn",
            },
            {
              label: "ntfy",
              value: ntfyConfig?.enabled ? "已启用" : "未启用",
              tone: ntfyConfig?.enabled ? "ok" : "warn",
            },
          ]}
        />
        <div className="window-grid" style={{ marginTop: 12 }}>
          <label>
            <span>启用 ntfy</span>
            <input
              type="checkbox"
              checked={ntfyEnabled}
              onChange={(event) => setNtfyEnabled(event.target.checked)}
            />
          </label>
          <label>
            HTTPS Server URL
            <input value={ntfyServer} onChange={(event) => setNtfyServer(event.target.value)} />
          </label>
          <label>
            Topic
            <input value={ntfyTopic} onChange={(event) => setNtfyTopic(event.target.value)} />
          </label>
        </div>
        <div className="actions-inline" style={{ marginTop: 12 }}>
          {notificationPermission !== "granted" ? (
            <button type="button" onClick={onEnableNotifications}>
              授权 macOS 通知
            </button>
          ) : null}
          <button
            type="button"
            className="primary"
            disabled={notificationBusy}
            onClick={() => {
              setNotificationBusy(true);
              void onSaveNtfy({
                enabled: ntfyEnabled,
                server_url: ntfyServer,
                topic: ntfyTopic,
                auth_mode: ntfyConfig?.auth_mode || "none",
              }).finally(() => setNotificationBusy(false));
            }}
          >
            保存通知配置
          </button>
          <button
            type="button"
            disabled={notificationBusy || !ntfyConfig}
            onClick={() => void onTestNtfy()}
          >
            发送测试通知
          </button>
        </div>
      </PageSection>
      <div id="message-domain">
      <SegmentNav
        ariaLabel="消息业务域"
        value={scope}
        onChange={setScope}
        items={[
          { id: "video", label: "视频消息", badge: unreadCount },
          { id: "content", label: "软文消息", badge: contentUnreadCount },
        ]}
      />
      {scope === "video" ? (
        <ReachMessagesPanel
          accounts={accounts}
          messages={messages}
          unreadCount={unreadCount}
          status={status}
          notificationPermission={notificationPermission}
          ntfyConfig={ntfyConfig}
          busy={busy}
          onScan={onScan}
          onCancel={onCancel}
          onOpen={onOpen}
          onRead={onRead}
          onToggleAccount={onToggleAccount}
          onCreateAccount={onCreateAccount}
          onOpenAccount={onOpenAccount}
          onDeleteAccount={onDeleteAccount}
          onQueryChange={onQueryChange}
          onReadAll={onReadAll}
          onEnableNotifications={onEnableNotifications}
          onSaveNtfy={onSaveNtfy}
          onTestNtfy={onTestNtfy}
          onSuggestReplies={onSuggestReplies}
          replyDrafts={replyDrafts}
          replyDraftMessageId={replyDraftMessageId}
          onCopyReplyDraft={onCopyReplyDraft}
          showNotificationSettings={false}
        />
      ) : (
        <ContentMessagesPanel
          accounts={contentAccounts}
          messages={contentMessages}
          unreadCount={contentUnreadCount}
          status={contentStatus}
          busy={contentBusy}
          onScan={onContentScan}
          onCancel={onContentCancel}
          onOpen={onContentOpen}
          onPreview={onContentPreview}
          onToggleAccount={onContentToggleAccount}
          onCreateAccount={onContentCreateAccount}
          onOpenAccount={onContentOpenAccount}
          onDeleteAccount={onContentDeleteAccount}
          onQueryChange={onContentQueryChange}
          onReadAll={onContentReadAll}
          onSuggestReplies={onContentSuggestReplies}
          replyDrafts={contentReplyDrafts}
          replyDraftMessageId={contentReplyDraftMessageId}
          onCopyReplyDraft={onContentCopyReplyDraft}
        />
      )}
      </div>
    </section>
  );
}
