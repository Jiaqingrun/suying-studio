import { useEffect, useMemo, useState } from "react";

import type {
  NotificationPermissionState,
} from "./notifications";
import type {
  MessageListQuery,
  ReachMessage,
  ReachMessageAccount,
  ReachMessageScanStatus,
  ReachNtfyConfig,
} from "./types";
import { MessageAccountManager } from "./MessageAccountManager";

type Props = {
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
  showNotificationSettings?: boolean;
};

function statusLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    ok: "已检查",
    completed: "已检查",
    no_unread: "暂无未读",
    need_login: "需要登录",
    need_human: "需要人工处理",
    needs_human: "需要人工处理",
    profile_busy: "配置正被占用",
    readonly_unverified: "只读校准待完成",
    adapter_changed: "平台页面已变化",
    timeout: "检查超时",
    page_load_timeout: "页面加载超时（将自动重试）",
    failed: "检查失败",
  };
  return labels[String(status || "")] || String(status || "尚未检查");
}

export function ReachMessagesPanel({
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
  replyDrafts = [],
  replyDraftMessageId = null,
  onCopyReplyDraft,
  showNotificationSettings = true,
}: Props) {
  const [ntfyEnabled, setNtfyEnabled] = useState(false);
  const [ntfyServer, setNtfyServer] = useState("");
  const [ntfyTopic, setNtfyTopic] = useState("");
  const [ntfyAuth, setNtfyAuth] = useState<"none" | "token" | "basic">("none");
  const [ntfyToken, setNtfyToken] = useState("");
  const [ntfyUsername, setNtfyUsername] = useState("");
  const [ntfyPassword, setNtfyPassword] = useState("");
  const [ntfyBusy, setNtfyBusy] = useState(false);
  const [ntfyError, setNtfyError] = useState("");
  const [accountFilter, setAccountFilter] = useState<number | "all">("all");
  const [showHistory, setShowHistory] = useState(false);
  const [draftContextByMessage, setDraftContextByMessage] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!ntfyConfig) return;
    setNtfyEnabled(ntfyConfig.enabled);
    setNtfyServer(ntfyConfig.server_url);
    setNtfyTopic(ntfyConfig.topic);
    setNtfyAuth(ntfyConfig.auth_mode);
    setNtfyToken("");
    setNtfyUsername("");
    setNtfyPassword("");
  }, [ntfyConfig]);

  async function saveNtfy() {
    setNtfyBusy(true);
    setNtfyError("");
    try {
      await onSaveNtfy({
        enabled: ntfyEnabled,
        server_url: ntfyServer,
        topic: ntfyTopic,
        auth_mode: ntfyAuth,
        ...(ntfyToken ? { token: ntfyToken } : {}),
        ...(ntfyUsername ? { username: ntfyUsername } : {}),
        ...(ntfyPassword ? { password: ntfyPassword } : {}),
      });
      setNtfyToken("");
      setNtfyUsername("");
      setNtfyPassword("");
    } catch (error) {
      setNtfyError(String(error));
    } finally {
      setNtfyBusy(false);
    }
  }

  async function testNtfy() {
    setNtfyBusy(true);
    setNtfyError("");
    try {
      await onTestNtfy();
    } catch (error) {
      setNtfyError(String(error));
    } finally {
      setNtfyBusy(false);
    }
  }

  const visibleMessages = useMemo(
    () =>
      messages.filter(
        (message) =>
          (accountFilter === "all" || message.account_id === accountFilter) &&
          (showHistory || message.unread),
      ),
    [accountFilter, messages, showHistory],
  );

  const enabledAccounts = accounts.filter((account) => account.enabled).length;
  const attentionAccounts = accounts.filter((account) =>
    ["need_login", "need_human", "needs_human", "failed", "adapter_changed"].includes(
      String(account.last_status || ""),
    ),
  ).length;

  return (
    <div className="messages-workspace">
      <div className="messages-summary">
        <div className="message-kpi is-accent">
          <span>真实未读</span>
          <strong>{unreadCount}</strong>
        </div>
        <div className="message-kpi">
          <span>受管账号</span>
          <strong>{enabledAccounts}/{accounts.length}</strong>
        </div>
        <div className={`message-kpi${attentionAccounts ? " is-warn" : ""}`}>
          <span>需处理账号</span>
          <strong>{attentionAccounts}</strong>
        </div>
        <div className="messages-toolbar">
          <button type="button" className="primary" disabled={busy || accounts.length === 0} onClick={onScan}>
            {busy ? "正在检查…" : "检查全部账号"}
          </button>
          {busy ? (
            <button type="button" onClick={onCancel}>停止检查</button>
          ) : null}
          <button
            type="button"
            disabled={notificationPermission === "granted"}
            onClick={onEnableNotifications}
          >
            {notificationPermission === "granted"
              ? "系统提醒已开启"
              : notificationPermission === "denied"
                ? "提醒权限被拒绝"
                : "开启系统提醒"}
          </button>
        </div>
      </div>

      {status && (status.active || status.message || status.error) ? (
        <div className={`message-scan-status${status.error ? " is-error" : ""}`}>
          <strong>巡检 {status.phase || "—"}</strong>
          <span>{status.platform ? `${status.platform} · ` : ""}{status.message || status.error || ""}</span>
        </div>
      ) : null}

      <MessageAccountManager
        domainLabel="视频"
        accounts={accounts}
        busy={busy}
        platforms={[
          { id: "douyin", label: "抖音" },
          { id: "channels", label: "视频号" },
          { id: "xhs", label: "小红书" },
          { id: "kuaishou", label: "快手" },
        ]}
        onCreate={onCreateAccount}
        onOpenLogin={onOpenAccount}
        onToggle={onToggleAccount}
        onDelete={onDeleteAccount}
      />

      <div className="messages-layout">
        <aside className="messages-account-sidebar" aria-label="消息账号">
          <div className="messages-section-head">
            <div>
              <h3>账号</h3>
              <span>固定每 30 分钟静默巡检</span>
            </div>
            <button
              type="button"
              className={accountFilter === "all" ? "is-selected" : undefined}
              onClick={() => {
                setAccountFilter("all");
                onQueryChange({
                  unread: !showHistory,
                  history: showHistory,
                  limit: showHistory ? 300 : 100,
                });
              }}
            >
              全部
            </button>
          </div>
          <div className="messages-account-list">
            {accounts.map((account) => {
              const accountUnread = messages.filter(
                (message) => message.account_id === account.id && message.unread,
              ).length;
              return (
                <div
                  key={account.id}
                  className={`message-account-row${accountFilter === account.id ? " is-active" : ""}`}
                >
                  <button
                    type="button"
                    className="message-account-select"
                    onClick={() => {
                      setAccountFilter(account.id);
                      onQueryChange({
                        unread: !showHistory,
                        history: showHistory,
                        account_id: account.id,
                        limit: showHistory ? 300 : 100,
                      });
                    }}
                  >
                    <span className={`health-dot ${account.last_error ? "warn" : account.enabled ? "ok" : ""}`} />
                    <span className="message-account-copy">
                      <strong>{account.display_name || account.profile_name}</strong>
                      <small>
                        {account.platform} · {account.profile_role || "消息专用"} ·{" "}
                        {statusLabel(account.last_status)}
                      </small>
                      {account.last_scanned_at ? (
                        <small>{new Date(account.last_scanned_at).toLocaleString()}</small>
                      ) : null}
                    </span>
                    {accountUnread > 0 ? <em className="nav-badge">{accountUnread}</em> : null}
                  </button>
                  <label className="message-account-toggle">
                    <input
                      type="checkbox"
                      checked={account.enabled}
                      disabled={busy}
                      onChange={() => onToggleAccount(account)}
                    />
                    启用
                  </label>
                  {account.last_error ? <p className="inline-error">{account.last_error}</p> : null}
                </div>
              );
            })}
            {accounts.length === 0 ? (
              <p className="empty">尚未创建视频消息账号，请在上方选择平台并创建。</p>
            ) : null}
          </div>
        </aside>

        <section className="messages-inbox">
          <div className="messages-section-head">
            <div>
              <h3>消息摘要</h3>
              <span>共 {visibleMessages.length} 条 · 回复在官方页面完成</span>
            </div>
            <div className="messages-filter">
              <button
                type="button"
                className={!showHistory ? "is-selected" : undefined}
                onClick={() => {
                  setShowHistory(false);
                  onQueryChange({
                    unread: true,
                    account_id: accountFilter === "all" ? undefined : accountFilter,
                    limit: 100,
                  });
                }}
              >
                只看未读
              </button>
              <button
                type="button"
                className={showHistory ? "is-selected" : undefined}
                onClick={() => {
                  setShowHistory(true);
                  onQueryChange({
                    unread: false,
                    history: true,
                    account_id: accountFilter === "all" ? undefined : accountFilter,
                    limit: 300,
                  });
                }}
              >
                30 天历史
              </button>
              <button
                type="button"
                disabled={busy || unreadCount === 0}
                onClick={() => {
                  const label =
                    accountFilter === "all"
                      ? "全部视频消息账号"
                      : accounts.find((item) => item.id === accountFilter)?.display_name || "当前账号";
                  if (window.confirm(`确认将“${label}”范围内的消息全部标为已读？`)) {
                    void onReadAll(accountFilter === "all" ? undefined : accountFilter).catch(
                      () => undefined,
                    );
                  }
                }}
              >
                当前范围全部已读
              </button>
            </div>
          </div>
          <div className="messages-list">
            {visibleMessages.map((message) => (
              <article
                key={message.id}
                className={`message-row${message.unread ? " is-unread" : ""}`}
              >
                <div className="message-row-meta">
                  <span className="message-platform">{message.platform}</span>
                  <strong>{message.sender || "平台用户"}</strong>
                  <time>
                    {message.platform_event_at
                      ? new Date(message.platform_event_at).toLocaleString()
                      : message.last_seen_at
                      ? new Date(message.last_seen_at).toLocaleString()
                      : message.first_seen_at
                        ? new Date(message.first_seen_at).toLocaleString()
                        : "时间未知"}
                  </time>
                  <em>{message.unread ? "未读" : "已读"}</em>
                </div>
                <p>{message.summary || "平台报告有新消息，请进入官方页面查看。"}</p>
                <div className="message-row-foot">
                  <span>
                    {message.source === "dom_unread_marker" || message.source === "dom_heuristic"
                      ? "官方页面只读摘要"
                      : message.source}
                    {message.confidence ? ` · 置信度 ${Math.round(message.confidence * 100)}%` : ""}
                  </span>
                  <div className="actions-inline">
                    <button
                      type="button"
                      className="primary"
                      disabled={
                        !accounts.some(
                          (account) =>
                            account.id === message.account_id &&
                            account.reply_supported !== false,
                        )
                      }
                      onClick={() => onOpen(message)}
                    >
                      {!accounts.some(
                        (account) =>
                          account.id === message.account_id &&
                          account.reply_supported !== false,
                      )
                        ? "不可打开回复页"
                        : "去官方页回复"}
                    </button>
                    {onSuggestReplies ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          void onSuggestReplies(
                            message,
                            draftContextByMessage[message.id] || "",
                          );
                        }}
                      >
                        生成回复草稿
                      </button>
                    ) : null}
                    {message.unread ? (
                      <button type="button" onClick={() => onRead(message)}>标为已读</button>
                    ) : null}
                  </div>
                </div>
                {onSuggestReplies ? (
                  <label className="field-inline" style={{ display: "block", marginTop: 8 }}>
                    补充上下文（仅此消息，可选）
                    <input
                      value={draftContextByMessage[message.id] || ""}
                      onChange={(e) => {
                        const value = e.target.value;
                        setDraftContextByMessage((current) => ({
                          ...current,
                          [message.id]: value,
                        }));
                      }}
                      placeholder="可补充该用户具体诉求后再点生成"
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </label>
                ) : null}
                {replyDraftMessageId === message.id && replyDrafts.length > 0 ? (
                  <div className="message-reply-drafts" style={{ marginTop: 8 }}>
                    <p className="hint">
                      基于脱敏摘要生成，上下文可能不完整；复制后请在官方页人工发送，不会自动发送。
                    </p>
                    {replyDrafts.map((d) => (
                      <article key={String(d.id)} className="review-card" style={{ marginBottom: 6 }}>
                        <p>{String(d.body || "")}</p>
                        <div className="actions-inline">
                          <button
                            type="button"
                            className="primary"
                            onClick={() =>
                              onCopyReplyDraft?.(Number(d.id), String(d.body || ""))
                            }
                          >
                            复制草稿
                          </button>
                          <em className="badge-mute">
                            {d.based_on_summary_only ? "仅摘要" : "含补充上下文"}
                          </em>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : null}
              </article>
            ))}
            {visibleMessages.length === 0 ? (
              <p className="empty">{showHistory ? "近 30 天暂无平台消息摘要" : "当前筛选下暂无未读消息"}</p>
            ) : null}
          </div>
        </section>
      </div>

      {showNotificationSettings ? <details className="messages-notify-settings">
        <summary>
          <span>远程推送设置</span>
          <small>ntfy 可选 · 仅发送脱敏摘要与官方回复链接</small>
        </summary>
        <div className="ntfy-config">
          <div className="messages-notify-head">
            <label className="toggle">
              <input
                type="checkbox"
                checked={ntfyEnabled}
                onChange={(event) => setNtfyEnabled(event.target.checked)}
              />
              启用 ntfy
            </label>
          </div>
          <div className="ntfy-grid">
            <label>
              HTTPS Server URL
              <input value={ntfyServer} placeholder="https://ntfy.sh" onChange={(event) => setNtfyServer(event.target.value)} />
            </label>
            <label>
              Topic
              <input value={ntfyTopic} placeholder="suying-private-topic" onChange={(event) => setNtfyTopic(event.target.value)} />
            </label>
            <label>
              认证方式
              <select value={ntfyAuth} onChange={(event) => setNtfyAuth(event.target.value as "none" | "token" | "basic")}>
                <option value="none">无需认证</option>
                <option value="token">Access Token</option>
                <option value="basic">用户名 + 密码</option>
              </select>
            </label>
            {ntfyAuth === "token" ? (
              <label>
                Token
                <input
                  type="password"
                  autoComplete="new-password"
                  value={ntfyToken}
                  placeholder={ntfyConfig?.token_configured ? "已配置（留空保持不变）" : "填写 token"}
                  onChange={(event) => setNtfyToken(event.target.value)}
                />
              </label>
            ) : null}
            {ntfyAuth === "basic" ? (
              <>
                <label>
                  用户名
                  <input
                    autoComplete="off"
                    value={ntfyUsername}
                    placeholder={ntfyConfig?.username_configured ? "已配置（留空保持不变）" : "填写用户名"}
                    onChange={(event) => setNtfyUsername(event.target.value)}
                  />
                </label>
                <label>
                  密码
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={ntfyPassword}
                    placeholder={ntfyConfig?.password_configured ? "已配置（留空保持不变）" : "填写密码"}
                    onChange={(event) => setNtfyPassword(event.target.value)}
                  />
                </label>
              </>
            ) : null}
          </div>
          {ntfyError ? <p className="inline-error">{ntfyError}</p> : null}
          <div className="actions-inline">
            <button type="button" className="primary" disabled={ntfyBusy} onClick={() => void saveNtfy()}>
              {ntfyBusy ? "处理中…" : "保存推送配置"}
            </button>
            <button type="button" disabled={ntfyBusy || !ntfyConfig} onClick={() => void testNtfy()}>
              发送测试通知
            </button>
          </div>
        </div>
      </details> : null}
    </div>
  );
}
