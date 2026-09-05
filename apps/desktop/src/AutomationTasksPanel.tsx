import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { ChromeProfile, PublishRunItem, PublishTrigger } from "./types";

type Notify = (text: string, kind?: "ok" | "err" | "info" | "warn") => void;

type AutomationTask = {
  id: string;
  name: string;
  enabled: boolean;
  auto_production: boolean;
  targets: Array<{
    schedule_id: number;
    platform: string;
    chrome_profile: string;
    publish_count: number;
  }>;
};

type MakeupTrigger = {
  id: number;
  schedule_id: number | null;
  status: string;
  planned_at: string | null;
  local_date: string | null;
  task_name: string;
  chrome_profile: string;
  platform: string;
  note?: string | null;
};

type Props = {
  chromeProfiles: ChromeProfile[];
  notify: Notify;
};

const WEEKDAYS = [
  { id: 0, label: "周一" },
  { id: 1, label: "周二" },
  { id: 2, label: "周三" },
  { id: 3, label: "周四" },
  { id: 4, label: "周五" },
  { id: 5, label: "周六" },
  { id: 6, label: "周日" },
];
const CLOCK_RE = /^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/;

export function AutomationTasksPanel({ chromeProfiles, notify }: Props) {
  const accounts = useMemo(
    () =>
      chromeProfiles.filter(
        (profile) =>
          profile.platform &&
          profile.provisioning_status === "explicit" &&
          profile.login_status === "verified_logged_in",
      ),
    [chromeProfiles],
  );
  const [tasks, setTasks] = useState<AutomationTask[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [name, setName] = useState("每日自动发布");
  const [start, setStart] = useState("09:00:00");
  const [end, setEnd] = useState("11:00:00");
  const [weekdays, setWeekdays] = useState(WEEKDAYS.map((day) => day.id));
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<PublishTrigger[]>([]);
  const [makeupDeferred, setMakeupDeferred] = useState<PublishRunItem[]>([]);
  const [makeupTriggers, setMakeupTriggers] = useState<MakeupTrigger[]>([]);
  const [makeupCounts, setMakeupCounts] = useState({
    deferred: 0,
    missed_human_confirm: 0,
    makeup_pending: 0,
  });
  /** Seed full selection once; do not re-select after user clears. */
  const accountSelectionSeededRef = useRef(false);

  async function refreshTasks() {
    const result = await api.reachPublishTasks();
    setTasks((result.tasks || []) as AutomationTask[]);
  }

  async function refreshMakeup() {
    try {
      const result = await api.reachPublishMakeup();
      setMakeupDeferred(result.deferred || []);
      setMakeupTriggers(result.triggers || []);
      setMakeupCounts({
        deferred: result.counts?.deferred || 0,
        missed_human_confirm: result.counts?.missed_human_confirm || 0,
        makeup_pending: result.counts?.makeup_pending || 0,
      });
    } catch {
      /* optional older engines */
    }
  }

  useEffect(() => {
    void refreshTasks().catch(() => null);
    void refreshMakeup();
  }, []);

  useEffect(() => {
    setSelected((previous) => {
      const valid = previous.filter((key) => accounts.some((account) => account.name === key));
      if (valid.length) return valid;
      if (!accountSelectionSeededRef.current && accounts.length) {
        accountSelectionSeededRef.current = true;
        return accounts.map((account) => account.name);
      }
      return valid;
    });
    setCounts((previous) => {
      const next = { ...previous };
      for (const account of accounts) next[account.name] = Math.max(1, next[account.name] || 1);
      return next;
    });
  }, [accounts]);

  async function createTask() {
    if (!CLOCK_RE.test(start) || !CLOCK_RE.test(end) || start === end) {
      notify("时间须精确填写为 HH:MM:SS，且开始和结束不能相同", "err");
      return;
    }
    if (!selected.length || !weekdays.length) {
      notify("请至少选择一个账号和一个星期", "err");
      return;
    }
    setBusy(true);
    try {
      await api.reachPublishTaskCreate({
        name: name.trim() || "自动发布任务",
        timezone: "Asia/Shanghai",
        targets: accounts
          .filter((account) => selected.includes(account.name))
          .map((account) => ({
            platform: String(account.platform),
            chrome_profile: account.name,
            publish_count: Math.max(1, counts[account.name] || 1),
          })),
        windows: [{ start, end }],
        weekdays,
        auto_production: true,
        min_gap_minutes: 0,
        accept_risk: true,
      });
      await refreshTasks();
      notify("自动任务已创建，每次发布都会在窗口内独立随机到秒", "ok");
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function toggleTask(task: AutomationTask) {
    try {
      await api.reachPublishTaskUpdate(task.id, { enabled: !task.enabled });
      await refreshTasks();
    } catch (error) {
      notify(String(error), "err");
    }
  }

  async function deleteTask(task: AutomationTask) {
    if (!window.confirm(`确定删除自动任务“${task.name}”？未开始的未来随机排期会释放。`)) return;
    try {
      await api.reachPublishTaskDelete(task.id);
      await refreshTasks();
      notify("自动任务已删除", "ok");
    } catch (error) {
      notify(String(error), "err");
    }
  }

  async function previewTask(task: AutomationTask) {
    try {
      const results = await Promise.all(
        task.targets.map((target) => api.reachPublishSchedulePreview(target.schedule_id)),
      );
      setPreview(
        results
          .flatMap((result) => result.triggers || [])
          .sort((a, b) => new Date(a.planned_at).getTime() - new Date(b.planned_at).getTime()),
      );
    } catch (error) {
      notify(String(error), "err");
    }
  }

  async function retryMakeupItems() {
    const ids = makeupDeferred
      .map((item) => item.id)
      .filter((id): id is number => typeof id === "number");
    if (!ids.length) {
      notify("暂无安全待补发条目；过窗需人确认，不会静默重开窗口", "info");
      return;
    }
    setBusy(true);
    try {
      await api.reachPublishMakeupRetry(ids);
      notify(`已提交 ${ids.length} 条待补发，发布槽空闲后执行`, "ok");
      await refreshMakeup();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  const allSelected = accounts.length > 0 && selected.length === accounts.length;
  const makeupTotal =
    makeupCounts.deferred + makeupCounts.missed_human_confirm + makeupCounts.makeup_pending;


  return (
    <section className="automation-task-panel">
      <div className="panel-head">
        <div>
          <h3>自动任务</h3>
          <p className="hint">可建立多个任务；点选账号后为每个账号单独设条数，全机仍按单槽串行发布。</p>
        </div>
        <span className="count">{tasks.filter((task) => task.enabled).length} 个启用</span>
      </div>

      <div className="panel-head" style={{ marginTop: 12 }}>
        <div>
          <h3>待补发 / 过窗确认</h3>
          <p className="hint">
            单号掉登录约 90 秒宽限后自动跳过并排队补发，其它账号继续。过窗不得静默重开，只能人点补发。
          </p>
        </div>
        <span className="count">{makeupTotal} 项积压</span>
      </div>
      <div className="actions-inline" style={{ marginBottom: 12 }}>
        <button type="button" disabled={busy} onClick={() => void refreshMakeup()}>
          刷新补发
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || !makeupDeferred.length}
          onClick={() => void retryMakeupItems()}
        >
          补发待补条目
        </button>
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        待补发 {makeupCounts.deferred} · 过窗待确认 {makeupCounts.missed_human_confirm} · makeup{" "}
        {makeupCounts.makeup_pending}
      </p>
      {makeupDeferred.length ? (
        <ul className="automation-preview" style={{ marginBottom: 12 }}>
          {makeupDeferred.slice(0, 12).map((item) => (
            <li key={item.id}>
              #{item.id} · {item.chrome_profile || "账号"} · {item.platform || ""} · {item.phase}
              {item.error ? ` · ${String(item.error).slice(0, 80)}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {makeupTriggers.length ? (
        <ul className="automation-preview" style={{ marginBottom: 12 }}>
          {makeupTriggers.slice(0, 12).map((trig) => (
            <li key={trig.id}>
              触发 #{trig.id} · {trig.status} · {trig.task_name || ""} · {trig.chrome_profile || ""}
              {trig.planned_at
                ? ` · ${new Date(trig.planned_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                    hour12: false,
                  })}`
                : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {!makeupDeferred.length && !makeupTriggers.length ? (
        <p className="hint" style={{ marginBottom: 12 }}>
          当前无补发积压。
        </p>
      ) : null}

      <div className="automation-task-form">
        <label>
          任务名称
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          窗口开始
          <input
            inputMode="numeric"
            placeholder="09:00:00"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label>
          窗口结束
          <input
            inputMode="numeric"
            placeholder="11:00:00"
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </label>
        <p className="hint" style={{ margin: "8px 0 0" }}>
          READY 足够时直接发；不够立刻补产（生产 → 审片 → 发布）。待发池与立即发布共享，无内容预留。
        </p>
      </div>

      <div className="actions-inline" style={{ marginBottom: 8 }}>
        <h3 style={{ margin: 0, flex: 1 }}>选择要使用的账号</h3>
        <button
          type="button"
          disabled={!accounts.length}
          onClick={() =>
            setSelected(allSelected ? [] : accounts.map((account) => account.name))
          }
        >
          {allSelected ? "取消全选" : "全选"}
        </button>
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        已登录可建任务 {accounts.length} 个 · 已选 {selected.length} 个
      </p>
      {accounts.length ? (
        <div className="publish-account-grid publish-account-grid--compact">
          {accounts.map((account) => {
            const checked = selected.includes(account.name);
            return (
              <label
                key={account.name}
                className={`publish-account-choice${checked ? " is-selected" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    setSelected((previous) =>
                      previous.includes(account.name)
                        ? previous.filter((value) => value !== account.name)
                        : [...previous, account.name],
                    )
                  }
                />
                <span>
                  {account.name}
                  <small>
                    {account.platform || "平台"} · 可选择
                    {checked ? " · 已选入任务" : ""}
                  </small>
                  {checked ? (
                    <span className="automation-account-count">
                      条数
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={counts[account.name] || 1}
                        onClick={(event) => event.stopPropagation()}
                        onChange={(event) =>
                          setCounts((previous) => ({
                            ...previous,
                            [account.name]: Math.max(1, Number(event.target.value) || 1),
                          }))
                        }
                      />
                    </span>
                  ) : null}
                </span>
              </label>
            );
          })}
        </div>
      ) : (
        <p className="hint">暂无已登录视频账号，请先到「设置 → 账号」创建并登录。</p>
      )}

      <div className="actions-inline automation-weekdays">
        {WEEKDAYS.map((day) => (
          <label key={day.id}>
            <input
              type="checkbox"
              checked={weekdays.includes(day.id)}
              onChange={() =>
                setWeekdays((previous) =>
                  previous.includes(day.id)
                    ? previous.filter((value) => value !== day.id)
                    : [...previous, day.id].sort(),
                )
              }
            />
            {day.label}
          </label>
        ))}
      </div>
      <button
        type="button"
        className="primary"
        disabled={busy || !accounts.length || !selected.length}
        onClick={() => void createTask()}
      >
        {busy ? "创建中…" : "创建自动任务"}
      </button>

      <div className="review-list automation-task-list">
        {tasks.map((task) => (
          <article key={task.id} className="review-card">
            <strong>{task.name}</strong>
            <p className="hint">
              {task.targets.map((target) => `${target.chrome_profile} ${target.publish_count} 条`).join(" · ")}
              {" · "}
              READY 优先，缺口补产
            </p>
            <div className="actions-inline">
              <button type="button" onClick={() => void previewTask(task)}>查看随机时间</button>
              <button type="button" onClick={() => void toggleTask(task)}>
                {task.enabled ? "停用" : "启用"}
              </button>
              <button type="button" className="danger" onClick={() => void deleteTask(task)}>删除</button>
            </div>
          </article>
        ))}
      </div>
      {preview.length ? (
        <div className="automation-preview">
          <strong>未来随机启动时间</strong>
          <ol>
            {preview.slice(0, 30).map((trigger) => (
              <li key={trigger.id}>
                {new Date(trigger.planned_at).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                  hour12: false,
                })}
                {" · "}
                {trigger.status}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
