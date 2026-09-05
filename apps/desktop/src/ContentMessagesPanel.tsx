import { useMemo, useState } from "react";

import type {
  MessageListQuery,
  ReachMessage,
  ReachMessageAccount,
  ReachMessageScanStatus,
} from "./types";
import { MessageAccountManager } from "./MessageAccountManager";

type Props = {
  accounts: ReachMessageAccount[];
  messages: ReachMessage[];
  unreadCount: number;
  status: ReachMessageScanStatus | null;
  busy: boolean;
  onScan: () => void;
  onCancel: () => void;
  onOpen: (message: ReachMessage) => void;
  onPreview: (message: ReachMessage) => void;
  onToggleAccount: (account: ReachMessageAccount) => void;
  onCreateAccount: (platform: string, displayName: string) => Promise<void>;
  onOpenAccount: (account: ReachMessageAccount) => Promise<void>;
  onDeleteAccount: (account: ReachMessageAccount) => Promise<void>;
  onQueryChange: (query: MessageListQuery) => void;
  onReadAll: (accountId?: number) => Promise<void>;
  onSuggestReplies?: (message: ReachMessage, extraContext: string) => Promise<void>;
  replyDrafts?: Array<Record<string, unknown>>;
  replyDraftMessageId?: number | null;
  onCopyReplyDraft?: (draftId: number, body: string) => void;
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

/**
 * Soft-article messages: same workspace layout as video messages.
 * Red-dot clears only after each unread item is explicitly previewed.
 */
export function ContentMessagesPanel({
  accounts,
  messages,
  unreadCount,
  status,
  busy,
  onScan,
  onCancel,
  onOpen,
  onPreview,
  onToggleAccount,
  onCreateAccount,
  onOpenAccount,
  onDeleteAccount,
  onQueryChange,
  onReadAll,
  onSuggestReplies,
  replyDrafts = [],
  replyDraftMessageId = null,
  onCopyReplyDraft,
}: Props) {
  const [accountFilter, setAccountFilter] = useState<number | "all">("all");
  const [showHistory, setShowHistory] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [draftContextByMessage, setDraftContextByMessage] = useState<Record<number, string>>({});

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

  async function handlePreview(message: ReachMessage) {
    setExpandedId(message.id);
    if (message.unread) onPreview(message);
  }

  return (
    <div className="messages-workspace">
      <div className="messages-summary">
        <div className="message-kpi is-accent">
          <span>真实未读</span>
          <strong>{unreadCount}</strong>
        </div>
        <div className="message-kpi">
          <span>受管账号</span>
          <strong>
            {enabledAccounts}/{accounts.length}
          </strong>
        </div>
        <div className={`message-kpi${attentionAccounts ? " is-warn" : ""}`}>
          <span>需处理账号</span>
          <strong>{attentionAccounts}</strong>
        </div>
        <div className="messages-toolbar">
          <button type="button" className="primary" disabled={busy || accounts.length === 0} onClick={onScan}>
            {busy ? "正在检查…" : "检查软文未读"}
          </button>
          {busy || status?.active ? (
            <button type="button" disabled={busy && !status?.active} onClick={onCancel}>
              停止检查
            </button>
          ) : null}
        </div>
      </div>

      {status && (status.active || status.message || status.error) ? (
        <div className={`message-scan-status${status.error ? " is-error" : ""}`}>
          <strong>巡检 {status.phase || "—"}</strong>
          <span>
            {status.platform ? `${status.platform} · ` : ""}
            {status.message || status.error || ""}
          </span>
        </div>
      ) : null}

      <MessageAccountManager
        domainLabel="软文"
        accounts={accounts}
        busy={busy}
        platforms={[
          { id: "baijiahao", label: "百家号" },
          { id: "toutiao", label: "头条号" },
          { id: "wechat_mp", label: "公众号" },
          { id: "zhihu", label: "知乎" },
        ]}
        onCreate={onCreateAccount}
        onOpenLogin={onOpenAccount}
        onToggle={onToggleAccount}
        onDelete={onDeleteAccount}
      />

      <div className="messages-layout">
        <aside className="messages-account-sidebar" aria-label="软文消息账号">
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
              <p className="empty">尚未创建软文消息账号，请在上方选择平台并创建。</p>
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
                      ? "全部软文消息账号"
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
            {visibleMessages.map((message) => {
              const open = expandedId === message.id;
              return (
                <article
                  key={message.id}
                  className={`message-row${message.unread ? " is-unread" : ""}`}
                >
                  <div className="message-row-meta">
                    <span className="message-platform">{message.platform}</span>
                    <strong>{message.sender || "访客"}</strong>
                    <time>
                      {message.platform_event_at
                        ? new Date(message.platform_event_at).toLocaleString()
                        : message.last_seen_at
                        ? new Date(message.last_seen_at).toLocaleString()
                        : message.first_seen_at
                          ? new Date(message.first_seen_at).toLocaleString()
                          : "时间未知"}
                    </time>
                    <em>{message.unread ? "未预览" : "已预览"}</em>
                  </div>
                  <p>
                    {open
                      ? message.summary || "平台报告有新消息，请进入官方页面查看。"
                      : `${(message.summary || "").slice(0, 48)}${(message.summary || "").length > 48 ? "…" : ""}`}
                  </p>
                  <div className="message-row-foot">
                    <span>摘要默认折叠，点预览后才计已读</span>
                    <div className="actions-inline">
                      <button
                        type="button"
                        className="primary"
                        disabled={busy}
                        onClick={() => void handlePreview(message)}
                      >
                        {open ? "已展开" : "预览"}
                      </button>
                      {open ? (
                        <>
                          <button
                            type="button"
                            disabled={
                              busy ||
                              !accounts.some((account) => account.id === message.account_id)
                            }
                            onClick={() => onOpen(message)}
                          >
                            {accounts.some((account) => account.id === message.account_id)
                              ? "去官方页回复"
                              : "账号已归档"}
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
                        </>
                      ) : null}
                    </div>
                  </div>
                  {open && onSuggestReplies ? (
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
                  {open && replyDraftMessageId === message.id && replyDrafts.length > 0 ? (
                    <div className="review-list" style={{ marginTop: 8 }}>
                      {replyDrafts.map((draft) => (
                        <article key={String(draft.id)} className="review-card">
                          <p>{String(draft.body || "")}</p>
                          {onCopyReplyDraft ? (
                            <button
                              type="button"
                              onClick={() =>
                                onCopyReplyDraft(Number(draft.id), String(draft.body || ""))
                              }
                            >
                              复制草稿
                            </button>
                          ) : null}
                        </article>
                      ))}
                    </div>
                  ) : null}
                </article>
              );
            })}
            {visibleMessages.length === 0 ? <p className="empty">暂无软文消息</p> : null}
          </div>
        </section>
      </div>
    </div>
  );
}
