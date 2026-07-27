import type { NotificationPermissionState } from "../notifications";
import { ReachMessagesPanel } from "../ReachMessagesPanel";
import { PageHeader } from "../shell/PageChrome";
import type {
  ReachMessage,
  ReachMessageAccount,
  ReachMessageScanStatus,
  ReachNtfyConfig,
  Tab,
} from "../types";

export type MessagesPageProps = {
  setTab: (tab: Tab) => void;
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
};

export function MessagesPage({
  setTab,
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
  onEnableNotifications,
  onSaveNtfy,
  onTestNtfy,
}: MessagesPageProps) {
  return (
    <section className="messages-page">
      <PageHeader
        title="消息回复"
        blurb="集中查看各平台消息摘要，跳转官方页面人工回复"
        actions={
          <button type="button" onClick={() => setTab("publish")}>
            返回发布
          </button>
        }
      />
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
        onEnableNotifications={onEnableNotifications}
        onSaveNtfy={onSaveNtfy}
        onTestNtfy={onTestNtfy}
      />
    </section>
  );
}
