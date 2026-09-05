import { useEffect, useState } from "react";
import { api } from "./api";
import type { NotifyFn } from "./pages/pageTypes";
import {
  settingsPasswordChange,
  settingsPasswordLock,
  settingsPasswordStatus,
  settingsPasswordVerify,
  type SettingsPasswordStatus,
} from "./settingsLock";

export function AdvancedOpsPanel({ notify }: { notify: NotifyFn }) {
  const [status, setStatus] = useState<SettingsPasswordStatus>({
    configured: true,
    unlocked: false,
    unlocked_remaining_sec: 0,
    fail_cooldown_sec: 0,
  });
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const [lock, current] = await Promise.all([
      settingsPasswordStatus(),
      api.getSettings(),
    ]);
    setStatus(lock);
    setSettings(current as unknown as Record<string, unknown>);
  }

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, []);

  if (!status.unlocked) {
    return (
      <section className="advanced-lock" data-guide="ops-advanced">
        <h3>高级功能</h3>
        <p className="hint">这里会改变设备存储和缓存。为防误操作，请先输入高级密码。</p>
        <label>
          高级密码
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="输入高级密码"
          />
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy || !password}
          onClick={() => {
            setBusy(true);
            void settingsPasswordVerify(password)
              .then((next) => {
                setStatus(next);
                setPassword("");
                notify("高级功能已解锁", "ok");
              })
              .catch((error: unknown) => notify(String(error), "err"))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? "正在验证…" : "解锁"}
        </button>
      </section>
    );
  }

  const paths = (settings.paths || {}) as Record<string, unknown>;
  return (
    <section className="panel-block" data-guide="ops-advanced">
      <div className="panel-head">
        <div>
          <h3>高级功能</h3>
          <p className="hint">仅在迁移设备或排查存储问题时修改。</p>
        </div>
        <button
          type="button"
          onClick={() =>
            void settingsPasswordLock().then((next) => {
              setStatus(next);
              notify("高级功能已锁定", "ok");
            })
          }
        >
          立即锁定
        </button>
      </div>
      <div className="grid2">
        {[
          ["data_root", "业务数据库目录"],
          ["cache_root", "缓存目录"],
          ["render_root", "渲染临时目录"],
        ].map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              value={String(paths[key] || "")}
              onChange={(event) =>
                setSettings((previous) => ({
                  ...previous,
                  paths: {
                    ...((previous.paths || {}) as Record<string, unknown>),
                    [key]: event.target.value,
                  },
                }))
              }
            />
          </label>
        ))}
      </div>
      <div className="actions-inline">
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void api
              .updateSettings({
                data_root: String(((settings.paths || {}) as Record<string, unknown>).data_root || ""),
                cache_root: String(((settings.paths || {}) as Record<string, unknown>).cache_root || ""),
                render_root: String(((settings.paths || {}) as Record<string, unknown>).render_root || ""),
              })
              .then(() => notify("设备存储位置已保存，重启服务后生效", "ok"))
              .catch((error: unknown) => notify(String(error), "err"))
              .finally(() => setBusy(false));
          }}
        >
          保存设备路径
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void api
              .cleanCache(24)
              .then((result) =>
                notify(`已清理 ${result.removed_files} 个缓存文件`, "ok"),
              )
              .catch((error: unknown) => notify(String(error), "err"))
              .finally(() => setBusy(false));
          }}
        >
          清理缓存
        </button>
      </div>
      <label style={{ marginTop: 16 }}>
        修改高级密码
        <div className="path-row">
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder="至少 8 个字符"
          />
          <button
            type="button"
            disabled={busy || newPassword.length < 8}
            onClick={() => {
              setBusy(true);
              void settingsPasswordChange(newPassword)
                .then((next) => {
                  setStatus(next);
                  setNewPassword("");
                  notify("高级密码已修改", "ok");
                })
                .catch((error: unknown) => notify(String(error), "err"))
                .finally(() => setBusy(false));
            }}
          >
            保存新密码
          </button>
        </div>
      </label>
    </section>
  );
}
