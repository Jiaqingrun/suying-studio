import { useState } from "react";

import type { ReachMessageAccount } from "./types";

type Props = {
  domainLabel: string;
  accounts: ReachMessageAccount[];
  busy: boolean;
  platforms: Array<{ id: string; label: string }>;
  onCreate: (platform: string, displayName: string) => Promise<void>;
  onOpenLogin: (account: ReachMessageAccount) => Promise<void>;
  onToggle: (account: ReachMessageAccount) => void;
  onDelete: (account: ReachMessageAccount) => Promise<void>;
};

function isLoginIssue(account: ReachMessageAccount): boolean {
  const code = String(account.last_error_code || "").toLowerCase();
  // Busy / deferred never counts as login — even if copy mentions 登录.
  if (
    code === "chrome_busy" ||
    code === "profile_busy" ||
    code === "publish_priority"
  ) {
    return false;
  }
  if (
    code === "login_required" ||
    code === "need_login" ||
    code === "verification_required"
  ) {
    return true;
  }
  const status = String(account.last_status || "").toLowerCase();
  if (status === "login_required" || status === "need_login") {
    return true;
  }
  if (status === "deferred") return false;
  const err = String(account.last_error || "");
  if (
    /Chrome 正由|另一个速影任务|profile 已被占用|浏览器忙|浏览器被发布占用|publish_priority|消息巡检已自动延后|非账号登录问题|非登录损坏/i.test(
      err,
    )
  ) {
    return false;
  }
  // Backend finishes login failures as status=needs_human + error text / code.
  if (
    /尚未登录|请先登录|请在该 Chrome|人工登录|登录态|need[_\s-]?login|not\s+logged/i.test(
      err,
    )
  ) {
    return true;
  }
  if (status === "needs_human" && /登录|login/i.test(err)) {
    return true;
  }
  return false;
}

function isChromeBusy(account: ReachMessageAccount): boolean {
  if (isLoginIssue(account)) return false;
  const code = String(account.last_error_code || "").toLowerCase();
  if (
    code === "chrome_busy" ||
    code === "profile_busy" ||
    code === "publish_priority"
  ) {
    return true;
  }
  const err = String(account.last_error || "");
  return /Chrome 正由|另一个速影任务|profile 已被占用|浏览器忙|浏览器被发布占用|publish_priority|消息巡检已自动延后/i.test(
    err,
  );
}

function accountStatus(account: ReachMessageAccount): string {
  if (!account.enabled) return "已停用";
  if (isLoginIssue(account)) return "需登录";
  if (isChromeBusy(account)) {
    const code = String(account.last_error_code || "").toLowerCase();
    if (code === "publish_priority") return "发布优先延后";
    return "等待浏览器";
  }
  if (account.last_status === "deferred") return "延后";
  if (account.last_error) return "异常";
  return account.last_status || "尚未检查";
}

export function MessageAccountManager({
  domainLabel,
  accounts,
  busy,
  platforms,
  onCreate,
  onOpenLogin,
  onToggle,
  onDelete,
}: Props) {
  const [platform, setPlatform] = useState(platforms[0]?.id || "");
  const [displayName, setDisplayName] = useState("");
  const [saving, setSaving] = useState(false);

  async function createAccount() {
    const name = displayName.trim();
    if (!platform || !name) return;
    setSaving(true);
    try {
      await onCreate(platform, name);
      setDisplayName("");
    } catch {
      // Parent reports the API error; keep the entered name for retry.
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="message-account-manager">
      <div className="messages-section-head">
        <div>
          <h3>{domainLabel}消息账号</h3>
          <span>独立登录态，仅用于消息巡检</span>
        </div>
      </div>
      <div className="window-grid">
        <label>
          平台
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            {platforms.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          账号名称
          <input
            value={displayName}
            placeholder="例如：品牌主账号"
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy || saving || !platform || !displayName.trim()}
          onClick={() => void createAccount()}
        >
          {saving ? "创建中…" : "创建消息账号"}
        </button>
      </div>
      <div className="messages-account-list">
        {accounts.map((account) => {
          const loginIssue = isLoginIssue(account);
          const chromeBusy = isChromeBusy(account);
          return (
            <div className="message-account-row" key={account.id}>
              <div className="message-account-copy">
                <strong>{account.display_name || account.profile_name || `账号 ${account.id}`}</strong>
                <small>
                  {account.platform} · {account.profile_role || "消息专用"} · {accountStatus(account)}
                </small>
                {account.reply_supported === false ? (
                  <small>仅同步官方通知，平台未提供网页私信回复</small>
                ) : null}
                {account.readonly_verified === false ? (
                  <small className="warn-chip">待校准 · 禁止当已完成</small>
                ) : null}
                {loginIssue ? (
                  <small className="warn-chip">需重新登录 · 点「打开登录」</small>
                ) : null}
                {chromeBusy ? (
                  <small className="warn-chip">发布占用浏览器 · 结束后自动重试（非登录损坏）</small>
                ) : null}
                {!loginIssue && account.last_status === "readonly_unverified" ? (
                  <small className="warn-chip">只读校准未完成</small>
                ) : null}
              </div>
              <div className="actions-inline">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onOpenLogin(account).catch(() => undefined)}
                >
                  打开登录
                </button>
                <button type="button" disabled={busy} onClick={() => onToggle(account)}>
                  {account.enabled ? "停用" : "启用"}
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`删除消息账号“${account.display_name || account.platform}”？`)) {
                      void onDelete(account).catch(() => undefined);
                    }
                  }}
                >
                  删除
                </button>
              </div>
              {account.last_error ? (
                <p className={loginIssue || !chromeBusy ? "inline-error" : "inline-warn"}>
                  {account.last_error}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
