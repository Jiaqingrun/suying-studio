import { useState } from "react";
import type { ChromeProfile } from "./types";

export type PlatformOpt = { id: string; label: string; short?: string };

export type AccountDomainPanelProps = {
  title: string;
  profiles: ChromeProfile[];
  platforms: PlatformOpt[];
  selected: string;
  renameValue: string;
  createPlatform: string;
  createName: string;
  busy: boolean;
  createLabel: string;
  openLabel: string;
  onSelect: (name: string) => void;
  onRenameChange: (value: string) => void;
  onCreatePlatformChange: (value: string) => void;
  onCreateNameChange: (value: string) => void;
  onCreate: () => void;
  onOpen: () => void;
  onRename: () => void;
  onDelete: (names: string[]) => void;
};

function loginLabel(status?: string | null) {
  if (status === "verified_logged_in") return "已登录";
  if (status === "logged_out") return "需扫码";
  if (status === "checking") return "检查中";
  // No live managed Chrome: do not paint as 未登录.
  return "状态未知";
}

/** Isomorphic video/content account CRUD surface for Settings. */
export function AccountDomainPanel({
  title,
  profiles,
  platforms,
  selected,
  renameValue,
  createPlatform,
  createName,
  busy,
  createLabel,
  openLabel,
  onSelect,
  onRenameChange,
  onCreatePlatformChange,
  onCreateNameChange,
  onCreate,
  onOpen,
  onRename,
  onDelete,
}: AccountDomainPanelProps) {
  const [fallbackPlatforms] = useState<PlatformOpt[]>(platforms);
  const [selectMode, setSelectMode] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const plats = platforms.length ? platforms : fallbackPlatforms;
  const selectedNames = checked.filter((name) => profiles.some((profile) => profile.name === name));
  const deleteTargets = selectMode
    ? selectedNames
    : selected
      ? [selected]
      : [];

  function exitSelectMode() {
    setSelectMode(false);
    setChecked([]);
  }

  return (
    <div className="stack account-domain-panel" style={{ gap: 10 }} data-guide={`accounts-${title}`}>
      <h3 className="section-title" style={{ margin: 0 }}>
        {title}
      </h3>
      <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8 }}>
        <select
          value={createPlatform}
          disabled={busy || !plats.length}
          onChange={(e) => onCreatePlatformChange(e.target.value)}
        >
          {(plats.length ? plats : [{ id: "", label: "无平台" }]).map((p) => (
            <option key={p.id || "empty"} value={p.id}>
              {p.short || p.label}
            </option>
          ))}
        </select>
        <input
          value={createName}
          disabled={busy}
          onChange={(e) => onCreateNameChange(e.target.value)}
          placeholder="配置名（可选）"
          style={{ minWidth: 180 }}
        />
        <button type="button" className="primary" disabled={busy || !createPlatform} onClick={onCreate}>
          {createLabel}
        </button>
      </div>

      <div className="publish-account-grid publish-account-grid--compact">
        {profiles.map((profile) => {
          const active = selected === profile.name;
          const isChecked = selectedNames.includes(profile.name);
          if (selectMode) {
            return (
              <label
                key={profile.name}
                className={`publish-account-choice${active || isChecked ? " is-selected" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  disabled={busy}
                  onChange={() =>
                    setChecked((previous) =>
                      previous.includes(profile.name)
                        ? previous.filter((name) => name !== profile.name)
                        : [...previous, profile.name],
                    )
                  }
                />
                <span>
                  {profile.label || profile.name}
                  <small>
                    {profile.platform || "平台"} · {loginLabel(profile.login_status)}
                    {active ? " · 当前" : ""}
                  </small>
                </span>
              </label>
            );
          }
          return (
            <button
              key={profile.name}
              type="button"
              className={`publish-account-choice${active ? " is-selected" : ""}`}
              disabled={busy}
              onClick={() => onSelect(profile.name)}
            >
              <span>
                {profile.label || profile.name}
                <small>
                  {profile.platform || "平台"} · {loginLabel(profile.login_status)}
                  {active ? " · 当前" : ""}
                </small>
              </span>
            </button>
          );
        })}
      </div>
      {!profiles.length ? <p className="hint">暂无账号</p> : null}

      <div className="actions-inline" style={{ flexWrap: "wrap", gap: 8 }}>
        <button
          type="button"
          disabled={busy || !profiles.length}
          className={selectMode ? "is-selected" : undefined}
          onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
        >
          {selectMode ? "退出批量选择" : "批量选择"}
        </button>
        {selectMode ? (
          <button
            type="button"
            disabled={busy || !profiles.length}
            onClick={() =>
              setChecked(
                selectedNames.length === profiles.length
                  ? []
                  : profiles.map((profile) => profile.name),
              )
            }
          >
            {selectedNames.length === profiles.length && profiles.length ? "取消全选" : "全选"}
          </button>
        ) : null}
        <button type="button" className="primary" disabled={busy || !selected} onClick={onOpen}>
          {openLabel}
        </button>
        <input
          value={renameValue}
          disabled={busy || !selected}
          onChange={(e) => onRenameChange(e.target.value)}
          placeholder="改名"
          style={{ minWidth: 160 }}
        />
        <button
          type="button"
          disabled={busy || !selected || !renameValue.trim() || renameValue.trim() === selected}
          onClick={onRename}
        >
          保存名称
        </button>
        <button
          type="button"
          className="danger"
          disabled={busy || !deleteTargets.length}
          onClick={() => onDelete(deleteTargets)}
        >
          {selectMode && deleteTargets.length > 1
            ? `删除已选（${deleteTargets.length}）`
            : "删除"}
        </button>
      </div>
    </div>
  );
}
