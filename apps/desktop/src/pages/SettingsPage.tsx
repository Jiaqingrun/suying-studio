import { convertFileSrc } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Customer } from "../api";
import { isTauri } from "../engineControl";
import { briefResult } from "../shell/briefResult";
import { PageHeader, StepFooter } from "../shell/PageChrome";
import {
  DEFAULT_PREFS,
  getSystemEventPrefs,
  setSystemEventPrefs,
  type SystemEventPrefs,
} from "../systemEvents";
import { AccountsManagePanel } from "../AccountsManagePanel";
import { PublishedCleanupControl } from "../PublishedCleanupControl";
import type { LayoutDensityPref, OpsSection, SettingsSection, Tab } from "../types";
import type { AskConfirmFn, NotifyFn, TitlePoolSummary } from "./pageTypes";
import { settingsPasswordChange } from "../settingsLock";

function localPreviewAudioSrc(path: string): string {
  const p = (path || "").trim();
  if (!p) return "";
  if (isTauri()) {
    try {
      return convertFileSrc(p);
    } catch {
      /* fall through */
    }
  }
  return "";
}

export function SystemEventControlPanel({ notify }: { notify: NotifyFn }) {
  const [prefs, setPrefs] = useState<SystemEventPrefs>(DEFAULT_PREFS);
  const [busy, setBusy] = useState(false);
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const [local, engine] = await Promise.all([
          getSystemEventPrefs(),
          api.getSystemEventControl().catch(() => null),
        ]);
        const merged = {
          ...DEFAULT_PREFS,
          ...local,
          ...(engine || {}),
        } as SystemEventPrefs;
        setPrefs(merged);
        setSupported(typeof window !== "undefined" && !!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
      } catch {
        /* ignore */
      }
    })();
  }, []);

  async function save(next: SystemEventPrefs) {
    setBusy(true);
    const previous = prefs;
    try {
      await api.updateSystemEventControl(next as unknown as Record<string, unknown>);
      try {
        await setSystemEventPrefs(next);
      } catch (localError) {
        await api.updateSystemEventControl(previous as unknown as Record<string, unknown>);
        throw localError;
      }
      setPrefs(next);
      notify("系统事件偏好已保存", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  function toggle<K extends keyof SystemEventPrefs>(key: K, value: SystemEventPrefs[K]) {
    void save({ ...prefs, [key]: value });
  }

  const rows: Array<{ key: keyof SystemEventPrefs; label: string; hint: string }> = [
    { key: "pause_on_system_sleep", label: "电脑睡眠时暂停", hint: "合盖且电脑真正进入睡眠时生效；外接屏模式下电脑未睡眠会继续工作" },
    { key: "pause_on_screen_sleep", label: "屏幕熄灭时暂停", hint: "外接屏使用时建议保持关闭" },
    { key: "pause_on_session_inactive", label: "锁屏时暂停", hint: "与“解锁后继续”配套使用" },
    { key: "auto_resume_on_wake", label: "电脑唤醒后继续", hint: "只恢复本次睡眠暂停，不会解除其他问题" },
    { key: "auto_resume_on_session_active", label: "解锁后继续", hint: "只恢复对应的锁屏暂停" },
  ];

  return (
    <div className="stack" style={{ gap: 10 }}>
      {!supported ? <p className="hint">当前为浏览器预览：系统事件监听不可用，仅可保存引擎侧偏好。</p> : null}
      <label className="row" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          type="checkbox"
          checked={!!prefs.enabled}
          disabled={busy}
          onChange={(e) => toggle("enabled", e.target.checked)}
        />
        启用系统事件控制
      </label>
      {rows.map((r) => (
        <label key={r.key} className="row" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={!!prefs[r.key]}
              disabled={busy || !prefs.enabled}
              onChange={(e) => toggle(r.key, e.target.checked as never)}
            />
            {r.label}
          </span>
          <span className="hint">{r.hint}</span>
        </label>
      ))}
      <label>
        唤醒后等待（秒）
        <input
          type="number"
          min={0}
          max={60}
          disabled={busy || !prefs.enabled}
          value={Number(prefs.wake_settle_seconds ?? 5)}
          onChange={(e) => {
            const v = Math.max(0, Math.min(60, Number(e.target.value) || 0));
            setPrefs((p) => ({ ...p, wake_settle_seconds: v }));
          }}
          onBlur={() => void save(prefs)}
        />
      </label>
    </div>
  );
}

const SETTINGS_SEGMENTS: Array<{ id: SettingsSection; label: string }> = [
  { id: "customer", label: "客户" },
  { id: "brand", label: "品牌" },
  { id: "accounts", label: "账号管理" },
  { id: "defaults", label: "偏好" },
  { id: "storage", label: "视频保留" },
  { id: "paths", label: "路径" },
];

type TtsVoiceStatus = Awaited<ReturnType<typeof api.getTtsVoice>>;

/** 规则实验室联动：引擎只以上方「声音与语言」为准，避免双下拉打架。 */
export type RuleVoiceLink = {
  provider: "edge" | "clone";
  voicePack: string;
  edgeVoice?: string;
  onDraftChange: (patch: {
    tts_provider?: "edge" | "clone";
    voice_pack?: string;
    tts_voice?: string;
  }) => void;
};

export function VoiceTtsPanel({
  notify,
  pickFile,
  ruleLink,
  activeCustomerId = null,
}: {
  notify: NotifyFn;
  pickFile: () => Promise<string | null>;
  /** 传入时为规则页从属面板：不重复选引擎，改声色包会写回规则草稿。 */
  ruleLink?: RuleVoiceLink;
  /** 切换客户后必须重拉音色状态，禁止沿用上一租户缓存。 */
  activeCustomerId?: number | null;
}) {
  const bound = Boolean(ruleLink);
  const [status, setStatus] = useState<TtsVoiceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState<"edge" | "clone">("edge");
  const [packId, setPackId] = useState("aunt_slow");
  const [edgeVoice, setEdgeVoice] = useState("zh-CN-XiaoxiaoNeural");
  const [edgeVoices, setEdgeVoices] = useState<
    Array<{ id: string; locale: string; gender: string; label: string }>
  >([]);
  const [edgeShowAll, setEdgeShowAll] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [importPath, setImportPath] = useState("");
  const [importLabel, setImportLabel] = useState("");
  const [importText, setImportText] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [renameLabel, setRenameLabel] = useState("");
  const [cloneAllowed, setCloneAllowed] = useState(true);
  const [importAllowed, setImportAllowed] = useState(true);
  const [previewPath, setPreviewPath] = useState("");
  const [previewSrc, setPreviewSrc] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const ttsLoadGen = useRef(0);

  async function reload() {
    const generation = ++ttsLoadGen.current;
    const [next, settings] = await Promise.all([
      api.getTtsVoice(),
      api.getSettings().catch(() => null),
    ]);
    if (generation !== ttsLoadGen.current) return;
    // Drop responses that belong to a different tenant after a customer switch race.
    if (
      activeCustomerId != null &&
      next.customer_id != null &&
      Number(next.customer_id) !== Number(activeCustomerId)
    ) {
      return;
    }
    setStatus(next);
    if (!ruleLink) {
      const eff = next.effective_provider === "clone" ? "clone" : "edge";
      setProvider(eff);
      setPackId(next.voice_pack || "aunt_slow");
      setEdgeVoice(next.edge_voice || next.tts_voice || "zh-CN-XiaoxiaoNeural");
    }
    setSpeed(Number(next.clone_speed) || 1);
    const packKey = ruleLink?.voicePack || next.voice_pack || "aunt_slow";
    const cur = (next.packs || []).find((p) => p.id === packKey);
    setRenameLabel(String(cur?.label || next.label || ""));
    if (settings) {
      setCloneAllowed(settings.clone_tts_allowed !== false);
      setImportAllowed(settings.custom_voice_import_allowed !== false);
    }
  }

  useEffect(() => {
    // Hard reset visible tenant label/lock before the new fetch lands.
    setStatus(null);
    setPreviewPath("");
    setPreviewSrc("");
    void reload().catch((e) => notify(String(e), "err"));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when tenant changes
  }, [notify, activeCustomerId]);

  useEffect(() => {
    if (!ruleLink) return;
    setProvider(ruleLink.provider);
    setPackId(ruleLink.voicePack || "aunt_slow");
    if (ruleLink.edgeVoice) setEdgeVoice(ruleLink.edgeVoice);
  }, [ruleLink?.provider, ruleLink?.voicePack, ruleLink?.edgeVoice]);

  useEffect(() => {
    if (bound) return; // 规则页已在上方选 Edge 音色
    let cancelled = false;
    void api
      .getEdgeVoices({ lang: edgeShowAll ? undefined : "zh", all_locales: edgeShowAll })
      .then((res) => {
        if (cancelled) return;
        setEdgeVoices(res.voices || []);
      })
      .catch(() => {
        /* ignore — save still works with known ShortName */
      });
    return () => {
      cancelled = true;
    };
  }, [bound, edgeShowAll]);

  async function save() {
    setBusy(true);
    try {
      const effProvider = ruleLink ? ruleLink.provider : provider;
      const effPack = packId;
      const effEdge = ruleLink?.edgeVoice || edgeVoice;
      await api.updateTtsVoice({
        provider: effProvider,
        voice_pack: effPack,
        voice: effProvider === "edge" ? effEdge : undefined,
        clone_speed: speed,
        update_video_lock: true,
      });
      if (ruleLink) {
        ruleLink.onDraftChange({
          tts_provider: effProvider,
          voice_pack: effPack,
          tts_voice: effProvider === "edge" ? effEdge : undefined,
        });
      }
      await reload();
      if (bound) {
        notify(
          effProvider === "clone"
            ? `已写入客户锁与当前规则草稿：克隆 · ${effPack}（请再点「保存并启用」生效出片）`
            : `已写入客户锁与当前规则草稿：Edge · ${effEdge}（请再点「保存并启用」生效出片）`,
          "ok",
        );
      } else {
        notify(
          effProvider === "clone"
            ? `已启用本地克隆音色：${effPack}`
            : `已切换为 Edge 旁白：${effEdge}`,
          "ok",
        );
      }
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function importPack() {
    setBusy(true);
    try {
      const r = await api.importVoicePack({
        source_path: importPath.trim(),
        label: importLabel.trim() || "自定义音色",
        ref_text: importText.trim(),
        speed,
        confirm_authorized: authorized,
      });
      const id = String((r.pack as { id?: string })?.id || "");
      if (id) {
        setPackId(id);
        if (ruleLink) {
          ruleLink.onDraftChange({ tts_provider: "clone", voice_pack: id });
        } else {
          setProvider("clone");
        }
      }
      setPreviewPath("");
      setPreviewSrc("");
      await reload();
      notify(`已生成音色包草稿，请试听并确认后再启用`, "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function previewPack() {
    if (!packId) return;
    setBusy(true);
    try {
      const r = await api.previewVoicePack({
        voice_pack: packId,
        speed,
        text: importText.trim() || "您好，这是自定义音色试听。",
      });
      const path = String(r.audio_path || "");
      setPreviewPath(path);
      const src = localPreviewAudioSrc(path);
      setPreviewSrc(src);
      if (r.playing) {
        notify("正在播放试听…", "ok");
      } else if (src) {
        notify("试听已生成，正在播放", "ok");
      } else if (path) {
        notify("试听已生成（请点下方播放）", "ok");
      } else {
        notify("试听完成，但未得到音频路径", "err");
      }
    } catch (e) {
      setPreviewPath("");
      setPreviewSrc("");
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function confirmPack() {
    if (!packId) return;
    setBusy(true);
    try {
      await api.confirmVoicePack(packId);
      await reload();
      notify("音色已确认，可保存为当前旁白", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function renamePack() {
    if (!packId) return;
    setBusy(true);
    try {
      await api.patchVoicePack(packId, { label: renameLabel.trim() });
      await reload();
      notify("音色名称已保存", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function removePack() {
    if (!packId) return;
    setBusy(true);
    try {
      await api.deleteVoicePack(packId);
      setPackId("aunt_slow");
      if (ruleLink) {
        ruleLink.onDraftChange({ voice_pack: "aunt_slow" });
      }
      await reload();
      notify("已删除自定义音色", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  const activeProvider = ruleLink ? ruleLink.provider : provider;
  const packs = status?.packs || [];
  const cloneOk = !!status?.clone_available && cloneAllowed;
  const canImport = cloneOk && importAllowed;
  const current = packs.find((p) => p.id === packId);
  const canEdit = Boolean(current?.custom && !current?.readonly);
  const needsConfirm = Boolean(current?.custom && current?.confirmed === false);
  const showCloneTools = activeProvider === "clone";

  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="hint">
        {bound
          ? "引擎只认上方「声音与语言 · 音色」。此处管理克隆声色包、试听与导入；点「保存到客户锁」会同步写 VIDEO_LOCK 与规则草稿，出片仍需「保存并启用」规则。"
          : "此处选择旁白引擎与声色包，并写入客户 VIDEO_LOCK。规则页请以「声音与语言」为准。"}
      </p>
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <span>
          客户：<strong>{status?.customer || "—"}</strong>
        </span>
        <span>
          客户锁当前：
          <strong>
            {status?.effective_provider === "clone"
              ? `克隆 · ${status.label || status.voice_pack}`
              : `Edge · ${status?.label || status?.edge_voice || status?.tts_voice || "晓晓"}`}
          </strong>
        </span>
        {bound ? (
          <span>
            本规则草稿：
            <strong>
              {activeProvider === "clone"
                ? `克隆 · ${packId}`
                : `Edge · ${ruleLink?.edgeVoice || edgeVoice}`}
            </strong>
          </span>
        ) : null}
        <span className="hint">
          本机 F5：{cloneOk ? "可用" : "未安装（clone 需 pip install f5-tts）"}
        </span>
      </div>

      {bound ? (
        <p className="hint" style={{ margin: 0 }}>
          {activeProvider === "clone"
            ? "当前规则为「本地克隆」——请在下方选声色包并试听。"
            : "当前规则为「系统自然音色」——Edge 具体声线请在上方选择；克隆声色包不会用在出片。"}
        </p>
      ) : (
        <>
          <label>
            音色引擎
            <select
              value={provider}
              disabled={busy}
              onChange={(e) => setProvider(e.target.value === "clone" ? "clone" : "edge")}
            >
              <option value="edge">Edge 多音色</option>
              <option value="clone" disabled={!cloneOk && provider !== "clone"}>
                本地克隆
                {!cloneAllowed
                  ? "（当前档位未开放）"
                  : !status?.clone_available
                    ? "（F5 未就绪）"
                    : ""}
              </option>
            </select>
          </label>
          {provider === "edge" ? (
            <label>
              Edge 音色
              <select
                value={edgeVoice}
                disabled={busy}
                onChange={(e) => setEdgeVoice(e.target.value)}
              >
                {(() => {
                  const has = edgeVoices.some((v) => v.id === edgeVoice);
                  const opts = has
                    ? edgeVoices
                    : [{ id: edgeVoice, locale: "", gender: "", label: edgeVoice }, ...edgeVoices];
                  return opts.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label || v.id}
                      {v.locale ? ` · ${v.locale}` : ""}
                      {` · ${v.id}`}
                    </option>
                  ));
                })()}
              </select>
              <span className="hint" style={{ display: "block", marginTop: 4 }}>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={edgeShowAll}
                    onChange={(e) => setEdgeShowAll(e.target.checked)}
                  />
                  显示全部语言音色
                </label>
              </span>
            </label>
          ) : null}
        </>
      )}

      {showCloneTools ? (
        <>
          <label>
            声色包
            <select
              value={packId}
              disabled={busy || packs.length === 0}
              onChange={(e) => {
                const id = e.target.value;
                setPackId(id);
                const hit = packs.find((p) => p.id === id);
                setRenameLabel(String(hit?.label || ""));
                if (ruleLink) {
                  ruleLink.onDraftChange({ tts_provider: "clone", voice_pack: id });
                }
              }}
            >
              {packs.length === 0 ? (
                <option value={packId}>{packId}</option>
              ) : (
                packs.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label || p.id}
                    {p.custom ? (p.confirmed === false ? " · 待确认" : " · 自定义") : " · 内置"}
                    {p.chars_per_sec_zh ? ` · ~${p.chars_per_sec_zh} 字/秒` : ""}
                  </option>
                ))
              )}
            </select>
          </label>
          <label>
            语速倍率（0.5–1.5）
            <input
              type="number"
              min={0.5}
              max={1.5}
              step={0.05}
              disabled={busy}
              value={speed}
              onChange={(e) => setSpeed(Math.max(0.5, Math.min(1.5, Number(e.target.value) || 1)))}
            />
          </label>
          {canEdit ? (
            <label>
              音色名称
              <input
                value={renameLabel}
                disabled={busy}
                onChange={(e) => setRenameLabel(e.target.value)}
                placeholder="可修改后保存名称"
              />
            </label>
          ) : null}
        </>
      ) : null}

      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy || (showCloneTools && needsConfirm)}
          onClick={() => void save()}
        >
          {busy
            ? "保存中…"
            : needsConfirm && showCloneTools
              ? "请先确认音色"
              : bound
                ? "保存到客户锁"
                : "保存旁白音色"}
        </button>
        {showCloneTools ? (
          <button type="button" disabled={busy || !cloneOk} onClick={() => void previewPack()}>
            试听一句
          </button>
        ) : null}
        {needsConfirm && showCloneTools ? (
          <button type="button" className="primary" disabled={busy} onClick={() => void confirmPack()}>
            确认启用此音色
          </button>
        ) : null}
        {canEdit && showCloneTools ? (
          <>
            <button type="button" disabled={busy} onClick={() => void renamePack()}>
              保存名称
            </button>
            <button type="button" disabled={busy} onClick={() => void removePack()}>
              删除自定义音色
            </button>
          </>
        ) : null}
        <button type="button" disabled={busy} onClick={() => void reload().catch((e) => notify(String(e), "err"))}>
          刷新状态
        </button>
      </div>
      {previewSrc ? (
        <audio
          key={previewSrc}
          controls
          autoPlay
          src={previewSrc}
          style={{ width: "100%", maxWidth: 420, marginTop: 4 }}
        />
      ) : null}
      {previewPath ? (
        <p className="hint">
          试听文件：<code>{previewPath}</code>
        </p>
      ) : null}

      {showCloneTools ? (
        <div
          className="stack"
          style={{
            gap: 8,
            marginTop: 8,
            paddingTop: 12,
            borderTop: "1px solid var(--line)",
            outline: dragOver ? "2px dashed var(--lime, #9acd32)" : undefined,
            borderRadius: 8,
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (canImport) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (!canImport) return;
            const file = e.dataTransfer.files?.[0];
            const path = (file as { path?: string } | undefined)?.path || "";
            if (path) {
              setImportPath(path);
              if (!importLabel.trim()) setImportLabel(file?.name?.replace(/\.[^.]+$/, "") || "");
              notify("已接收参考音频路径", "ok");
            } else {
              notify("请使用「选择文件」选取本地音频（浏览器拖放无绝对路径）", "warn");
            }
          }}
        >
          <strong>导入自定义克隆音色</strong>
          <p className="hint">
            拖入或选择 5–15 秒干净人声（wav/m4a/mp3），填写读准文本与名称后生成；再试听并确认。
            {!importAllowed ? " 当前机型档位未开放自定义音色导入。" : ""}
          </p>
          <label>
            参考音频
            <div className="path-row">
              <input
                value={importPath}
                disabled={busy || !canImport}
                onChange={(e) => setImportPath(e.target.value)}
                placeholder="/path/to/ref.wav 或拖入文件"
              />
              <button
                type="button"
                disabled={busy || !canImport}
                onClick={() => void pickFile().then((p) => p && setImportPath(p))}
              >
                选择文件
              </button>
            </div>
          </label>
          <label>
            音色名称
            <input
              value={importLabel}
              disabled={busy || !canImport}
              onChange={(e) => setImportLabel(e.target.value)}
              placeholder="例如：店长口播"
            />
          </label>
          <label>
            参考文本（与音频内容一致）
            <textarea
              value={importText}
              disabled={busy || !canImport}
              onChange={(e) => setImportText(e.target.value)}
              rows={3}
              placeholder="请输入音频中实际说的话"
            />
          </label>
          <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={authorized}
              disabled={busy || !canImport}
              onChange={(e) => setAuthorized(e.target.checked)}
            />
            我确认参考音已获本人/客户授权
          </label>
          <button
            type="button"
            className="primary"
            disabled={busy || !canImport || !importPath.trim() || !importText.trim() || !authorized}
            onClick={() => void importPack()}
          >
            生成音色包
          </button>
        </div>
      ) : null}
    </div>
  );
}

export interface SettingsPageProps {
  section: SettingsSection;
  onSectionChange: (s: SettingsSection) => void;
  setTab: (t: Tab, opts?: { settings?: SettingsSection; ops?: OpsSection; guideTarget?: string }) => void;
  notify: NotifyFn;
  askConfirm: AskConfirmFn;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  refreshAll: () => Promise<void>;

  libraryRoot: string;
  setLibraryRoot: (v: string) => void;
  libraryRootsText: string;
  setLibraryRootsText: (v: string) => void;
  outputRoot: string;
  setOutputRoot: (v: string) => void;
  keywordPackPath: string;
  setKeywordPackPath: (v: string) => void;
  pickDir: () => Promise<string | null>;
  pickFile: () => Promise<string | null>;
  savePaths: () => void | Promise<void>;
  activeCustomerId: number | null;
  titlePoolSummary: TitlePoolSummary | null;
  setTitlePoolSummary: (v: TitlePoolSummary | null) => void;

  customerName: string;
  customers: Customer[];
  newCustName: string;
  setNewCustName: (v: string) => void;
  remoteFolder: string;
  setRemoteFolder: (v: string) => void;
  remoteFolderOptions: string[];
  setRemoteFolderOptions: (v: string[]) => void;
  remoteFoldersBusy: boolean;
  setRemoteFoldersBusy: (v: boolean) => void;
  editSourceCustomer: string;
  setEditSourceCustomer: (v: string) => void;
  editRemoteFolder: string;
  setEditRemoteFolder: (v: string) => void;
  syncStatus: Record<string, unknown> | null;
  setSyncStatus: (v: Record<string, unknown> | null) => void;

  layoutPref: LayoutDensityPref;
  setLayoutPref: (v: LayoutDensityPref) => void;

  advancedUnlocked: boolean;
  passwordConfigured: boolean;
  passwordStatusRemaining: number;
  onUnlock: (password: string) => Promise<void> | void;
  onLock: () => Promise<void> | void;

  cacheRoot: string;
  setCacheRoot: (v: string) => void;
  renderRoot: string;
  setRenderRoot: (v: string) => void;
  dataRoot: string;
  setDataRoot: (v: string) => void;
  vectorDbPath: string;
}

export function SettingsPage({
  section,
  onSectionChange,
  setTab,
  notify,
  askConfirm,
  actionBusy,
  setActionBusy,
  refreshAll,
  libraryRoot,
  setLibraryRoot,
  libraryRootsText,
  setLibraryRootsText,
  outputRoot,
  setOutputRoot,
  keywordPackPath,
  setKeywordPackPath,
  pickDir,
  pickFile,
  savePaths,
  activeCustomerId,
  titlePoolSummary,
  setTitlePoolSummary,
  customerName,
  customers,
  newCustName,
  setNewCustName,
  remoteFolder,
  setRemoteFolder,
  remoteFolderOptions,
  setRemoteFolderOptions,
  remoteFoldersBusy,
  setRemoteFoldersBusy,
  editSourceCustomer,
  setEditSourceCustomer,
  editRemoteFolder,
  setEditRemoteFolder,
  syncStatus,
  setSyncStatus,
  layoutPref,
  setLayoutPref,
  advancedUnlocked,
  passwordConfigured,
  passwordStatusRemaining,
  onUnlock,
  onLock,
  cacheRoot,
  setCacheRoot,
  renderRoot,
  setRenderRoot,
  dataRoot,
  setDataRoot,
  vectorDbPath,
}: SettingsPageProps) {
  const [unlockPassword, setUnlockPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [brandDisplayName, setBrandDisplayName] = useState("");
  const [brandBusy, setBrandBusy] = useState(false);

  const mediaSources = Array.isArray(syncStatus?.media_sources)
    ? (syncStatus.media_sources as Array<Record<string, string>>)
    : [];
  /** 相册↔片库团队同步为 legacy，默认关；仅启用后建客才展示远端文件夹绑定。 */
  const mediaSyncEnabled = Boolean(syncStatus?.media_sync_enabled);

  const navItems = SETTINGS_SEGMENTS.filter((it) => it.id !== "notify");

  const activeCustomer = customers.find((c) => c.id === activeCustomerId) || null;
  const profileBrand =
    (activeCustomer?.profile?.brand as Record<string, unknown> | undefined) || {};
  const savedBrandName = String(profileBrand.display_name || "");

  useEffect(() => {
    if (section === "brand") setBrandDisplayName(savedBrandName);
  }, [section, savedBrandName, activeCustomerId]);

  async function loadRemoteFolders(preferSelectFirst: boolean) {
    setRemoteFoldersBusy(true);
    try {
      const r = await api.zspaceListMediaRemoteFolders("手机相册备份");
      const folders = r.folders || [];
      setRemoteFolderOptions(folders);
      if (preferSelectFirst && !remoteFolder && folders.length) setRemoteFolder(folders[0]);
      notify(`远端文件夹已加载：${folders.length} 项`, "ok");
    } catch (e: unknown) {
      notify(String(e), "err");
    } finally {
      setRemoteFoldersBusy(false);
    }
  }

  return (
    <section className="page-stack settings-page">
      <PageHeader
        title="设置"
        blurb="客户、品牌、旁白音色、偏好与路径"
        actions={
          <button type="button" onClick={() => void refreshAll()}>
            刷新
          </button>
        }
      />

      <div className="workspace-split">
        <nav className="side-nav" aria-label="设置侧栏">
          {navItems.map((it) => (
            <button
              key={it.id}
              type="button"
              className={`side-nav-btn${section === it.id ? " active" : ""}`}
              onClick={() => {
                onSectionChange(it.id);
                if (it.id === "brand") setBrandDisplayName(savedBrandName);
              }}
            >
              {it.label}
            </button>
          ))}
        </nav>

        <div>
          {section === "customer" && (
            <>
              <h3 className="section-title">新建客户（标准布局）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                填写客户名称即可：速影在本机标准布局下准备片库 / 成片 / 词池目录（Local-first，默认同步仅载体）。
                品牌资料可在左侧「品牌」继续完善。
                {mediaSyncEnabled
                  ? " 当前已启用片库同步，可在下方选填远端子文件夹并一并登记。"
                  : " 相册↔片库同步默认关闭，无需加载远端文件夹。"}
              </p>
              <div className="grid2" style={{ marginBottom: 16 }}>
                <label>
                  客户全称
                  <div className="path-row">
                    <input
                      value={newCustName}
                      onChange={(e) => setNewCustName(e.target.value)}
                      placeholder="客户全称"
                    />
                  </div>
                  {mediaSyncEnabled ? (
                    <>
                      <div className="path-row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                        <select
                          value={remoteFolder}
                          onChange={(e) => setRemoteFolder(e.target.value)}
                          disabled={remoteFoldersBusy || remoteFolderOptions.length === 0}
                        >
                          <option value="">选填：远端文件夹（/public/手机相册备份）</option>
                          {remoteFolderOptions.map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          disabled={remoteFoldersBusy}
                          onClick={() => void loadRemoteFolders(true)}
                        >
                          {remoteFoldersBusy ? "加载中…" : "加载远端文件夹"}
                        </button>
                      </div>
                      <div className="hint" style={{ marginTop: 8 }}>
                        {remoteFolder
                          ? <>
                              将登记：远端 <code>/public/手机相册备份/{remoteFolder}</code> → 本地{" "}
                              <code>01-片库/{remoteFolder}</code>
                            </>
                          : "未选远端文件夹时仅建本机客户；可稍后在「客户媒体来源」绑定。"}
                      </div>
                    </>
                  ) : null}
                  <div className="actions" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      className="primary"
                      onClick={() => {
                        if (!newCustName.trim()) {
                          notify("请填写客户名", "err");
                          return;
                        }
                        const name = newCustName.trim();
                        const bindRemote = mediaSyncEnabled && Boolean(remoteFolder);
                        api
                          .zspaceEnsureCustomer(name, true)
                          .then(async (ensured) => {
                            const p = ensured.paths || {};
                            if (bindRemote) {
                              await api.zspaceSetMediaSource({
                                customer_name: name,
                                remote_person: remoteFolder,
                                remote_base: "手机相册备份",
                              });
                            }
                            const c = await api.createCustomer({
                              name,
                              library_root: String(p.library_root || ""),
                              output_root: String(p.output_root || ""),
                              keyword_pack_path: String(p.keyword_pack_path || "") || undefined,
                            });
                            await api.activateCustomer(c.name);
                            setNewCustName("");
                            setRemoteFolder("");
                            setRemoteFolderOptions([]);
                            notify(
                              bindRemote
                                ? `已建客户并登记媒体来源: ${c.name} ← ${remoteFolder}`
                                : `已建客户「${c.name}」（本机标准目录；未绑定远端媒体来源）`,
                              "ok",
                            );
                            await refreshAll();
                            return api.zspaceSyncStatus().then(setSyncStatus);
                          })
                          .catch((e: unknown) => notify(String(e), "err"));
                      }}
                    >
                      {mediaSyncEnabled && remoteFolder ? "创建并登记" : "创建客户"}
                    </button>
                  </div>
                </label>
              </div>

              <h3 className="section-title">客户媒体来源（legacy · 默认关闭）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                片库团队同步默认关闭（仅载体）。仅当运维显式{" "}
                <code>media_sync_enabled</code> 后，才需为每个客户绑定团队空间下的
                <strong>一个</strong>子文件夹；更换来源会归档旧目录 ready 素材。
                {!mediaSyncEnabled
                  ? " 当前未启用：加载列表与更换来源仅作登记，不会拉取片库。"
                  : null}
              </p>
              {mediaSources.length > 0 ? (
                <ul className="hint" style={{ marginBottom: 12, paddingLeft: 18 }}>
                  {mediaSources.map((s) => (
                    <li key={`${s.customer_key}-${s.remote_root}`}>
                      <strong>{s.display_name || s.customer_key}</strong>：远端 /public/{s.remote_root} →
                      本地 {s.local_target}
                      {s.pull_only ? "（仅拉取）" : ""}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="hint" style={{ marginBottom: 12 }}>
                  当前未登记任何客户媒体来源（默认仅载体同步）。
                </p>
              )}
              <div className="grid2" style={{ marginBottom: 12 }}>
                <label>
                  更换已有客户来源
                  <div className="path-row" style={{ marginTop: 6 }}>
                    <select
                      value={editSourceCustomer || customerName}
                      onChange={(e) => {
                        setEditSourceCustomer(e.target.value);
                        const hit = mediaSources.find((s) => s.customer_key === e.target.value);
                        if (hit?.remote_root) {
                          const parts = hit.remote_root.split("/");
                          setEditRemoteFolder(parts[parts.length - 1] || "");
                        } else {
                          setEditRemoteFolder("");
                        }
                      }}
                    >
                      <option value="">选择客户</option>
                      {customers.map((c) => (
                        <option key={c.id} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="path-row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                    <select
                      value={editRemoteFolder}
                      onChange={(e) => setEditRemoteFolder(e.target.value)}
                      disabled={remoteFoldersBusy || remoteFolderOptions.length === 0}
                    >
                      <option value="">选择新远端文件夹</option>
                      {remoteFolderOptions.map((f) => (
                        <option key={`edit-${f}`} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={remoteFoldersBusy}
                      onClick={() => void loadRemoteFolders(false)}
                    >
                      加载远端列表
                    </button>
                  </div>
                  <div className="actions" style={{ marginTop: 10 }}>
                    <button
                      type="button"
                      disabled={!(editSourceCustomer || customerName) || !editRemoteFolder}
                      onClick={() => {
                        void (async () => {
                          const cust = (editSourceCustomer || customerName).trim();
                          if (!cust || !editRemoteFolder) return;
                          try {
                            const prev = await api.zspacePreviewMediaSource({
                              customer_name: cust,
                              remote_person: editRemoteFolder,
                            });
                            if (prev.unchanged) {
                              notify("来源未变化", "info");
                              return;
                            }
                            const removed = (prev.removed_local_targets as string[]) || [];
                            const ok = await askConfirm({
                              title: "更换媒体来源",
                              body:
                                `客户「${cust}」将改为仅同步 /public/手机相册备份/${editRemoteFolder}。\n` +
                                (removed.length
                                  ? `旧来源目录将被退役并归档对应素材（${removed.join("、")}）。`
                                  : "无旧来源需要归档。") +
                                "\n\n确认继续？",
                              confirmLabel: "更换并归档旧来源",
                            });
                            if (!ok) return;
                            const r = await api.zspaceSetMediaSource({
                              customer_name: cust,
                              remote_person: editRemoteFolder,
                            });
                            notify(
                              `已更新来源：${cust} ← ${editRemoteFolder}` +
                                (r.archived_assets ? ` · 归档 ${r.archived_assets} 条素材` : ""),
                              "ok",
                            );
                            await api.zspaceSyncStatus().then(setSyncStatus);
                          } catch (e: unknown) {
                            notify(String(e), "err");
                          }
                        })();
                      }}
                    >
                      更换来源
                    </button>
                  </div>
                </label>
              </div>
            </>
          )}

          {section === "brand" && (
            <>
              <h3 className="section-title">品牌资料</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                品牌显示名用于作品署名。Logo 角标可在生产页调整。
              </p>
              {!activeCustomerId ? (
                <p className="hint" style={{ color: "var(--danger)" }}>
                  请先在顶栏选择或新建客户。
                </p>
              ) : (
                <>
                  <p className="path" style={{ marginBottom: 8 }}>
                    当前客户：{customerName || activeCustomer?.name || "—"}
                    {savedBrandName ? ` · 已存「${savedBrandName}」` : " · 尚未设置显示名"}
                  </p>
                  <label>
                    品牌显示名
                    <div className="path-row" style={{ marginTop: 6 }}>
                      <input
                        value={brandDisplayName}
                        onChange={(e) => setBrandDisplayName(e.target.value)}
                        placeholder="对外署名，例如：客户品牌名"
                      />
                      <button
                        type="button"
                        className="primary"
                        disabled={brandBusy || !brandDisplayName.trim()}
                        onClick={() => {
                          void (async () => {
                            if (!activeCustomerId) return;
                            setBrandBusy(true);
                            try {
                              await api.updateCustomer(activeCustomerId, {
                                brand: { display_name: brandDisplayName.trim() },
                              });
                              notify("品牌显示名已保存", "ok");
                              await refreshAll();
                            } catch (e: unknown) {
                              notify(String(e), "err");
                            } finally {
                              setBrandBusy(false);
                            }
                          })();
                        }}
                      >
                        {brandBusy ? "保存中…" : "保存"}
                      </button>
                    </div>
                  </label>
                  <p className="hint" style={{ marginTop: 12 }}>
                    成片 Logo 文件放在客户目录 <code>05-品牌/logo.png</code>；位置开关在生产页。
                  </p>
                </>
              )}
            </>
          )}

          {section === "accounts" && (
            <>
              <h3 className="section-title">账号管理</h3>
              <AccountsManagePanel notify={notify} askConfirm={askConfirm} />
            </>
          )}

          {section === "defaults" && (
            <>
              <h3 className="section-title">布局密度</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                auto 随窗口宽度；comfort 偏宽松；compact 压缩间距。仅影响本机界面。
              </p>
              <div className="actions">
                {(
                  [
                    ["auto", "自动"],
                    ["comfort", "舒适"],
                    ["compact", "紧凑"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    className={layoutPref === id ? "primary" : undefined}
                    onClick={() => setLayoutPref(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <h3 className="section-title">审片决策策略</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                出片门禁明确通过的成片强制自动通过并进入历史；硬失败保持失败/已拒绝。
                只有「待审」状态或证据冲突会进入待人工队列。该安全策略不可关闭。
              </p>
              <div className="row">
                <strong>强制启用</strong>
                <span className="hint">当前客户：{customerName || activeCustomer?.name || "—"}</span>
              </div>
            </>
          )}

          {section === "storage" && (
            <>
              <p className="hint" style={{ marginBottom: 12 }}>
                统一磁盘清理（失败品 / 工作缓存 / frames·proxies / 已发布）在「运维 → 数据保护」。
                下方仅管理已发布视频保留。
              </p>
              <button
                type="button"
                style={{ marginBottom: 12 }}
                onClick={() => setTab("ops", { ops: "backup", guideTarget: "ops-disk-cleanup" })}
              >
                打开运维 · 磁盘清理
              </button>
              <PublishedCleanupControl notify={notify} askConfirm={askConfirm} />
            </>
          )}

          {section === "paths" && (
            <>
              <h3 className="section-title">路径与词池</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                片库与输出路径可修改；词池始终使用唯一 keyword-pack.json，更新稿通过受控导入提升。缓存 / 渲染 / 数据库路径在「高级」。
              </p>
              <div className="grid2">
                <label>
                  主片库
                  <div className="path-row">
                    <input value={libraryRoot} onChange={(e) => setLibraryRoot(e.target.value)} />
                    <button type="button" onClick={() => void pickDir().then((p) => p && setLibraryRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
                <label>
                  输出目录
                  <div className="path-row">
                    <input value={outputRoot} onChange={(e) => setOutputRoot(e.target.value)} />
                    <button type="button" onClick={() => void pickDir().then((p) => p && setOutputRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
              </div>
              <label>
                追加片库（每行一个）
                <textarea
                  rows={2}
                  value={libraryRootsText}
                  onChange={(e) => setLibraryRootsText(e.target.value)}
                />
              </label>
              <label>
                唯一词池路径
                <div className="path-row">
                  <input
                    value={keywordPackPath}
                    readOnly
                  />
                  <button
                    type="button"
                    onClick={() =>
                      void pickFile().then(async (path) => {
                        if (!path) return;
                        try {
                          const result = await api.installKeywordsFromPath(customerName, path);
                          setKeywordPackPath(result.path);
                          notify(`词池已提升至 revision ${result.revision} · ${result.sha256.slice(0, 8)}`, "ok");
                          await refreshAll();
                        } catch (error) {
                          notify(String(error), "err");
                        }
                      })
                    }
                  >
                    选择更新稿
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      api
                        .reloadKeywordsFromPath(activeCustomerId ?? undefined)
                        .then(async (r) => {
                          notify(briefResult("词池已重载", r), "ok");
                          try {
                            const s = await api.keywordsActiveSummary(activeCustomerId ?? undefined);
                            setTitlePoolSummary({
                              loaded: s.loaded,
                              title_pool_count: s.title_pool_count,
                              title_pool_sample: s.title_pool_sample || [],
                              hooks_count: s.hooks_count,
                              max_chars_per_line: s.max_chars_per_line,
                              version: s.version,
                            revision: s.revision,
                            sha256: s.sha256,
                            schema: s.schema,
                            });
                          } catch {
                            /* ignore */
                          }
                        })
                        .catch((e: unknown) => notify(String(e), "err"))
                    }
                  >
                    从路径重载
                  </button>
                </div>
              </label>
              {titlePoolSummary ? (
                <div className="hint" style={{ margin: "8px 0 12px" }}>
                  <div style={{ marginBottom: 6 }}>
                    片上标题语库：{titlePoolSummary.loaded ? titlePoolSummary.title_pool_count : 0} 条
                    {titlePoolSummary.max_chars_per_line
                      ? `（单句≤${titlePoolSummary.max_chars_per_line}字）`
                      : ""}
                    {titlePoolSummary.revision != null ? ` · revision ${titlePoolSummary.revision}` : ""}
                    {titlePoolSummary.sha256 ? ` · ${titlePoolSummary.sha256.slice(0, 8)}` : ""}
                    {` · hooks ${titlePoolSummary.hooks_count}`}
                    <button
                      type="button"
                      style={{ marginLeft: 8 }}
                      onClick={() =>
                        api
                          .keywordsActiveSummary(activeCustomerId ?? undefined)
                          .then((s) =>
                            setTitlePoolSummary({
                              loaded: s.loaded,
                              title_pool_count: s.title_pool_count,
                              title_pool_sample: s.title_pool_sample || [],
                              hooks_count: s.hooks_count,
                              max_chars_per_line: s.max_chars_per_line,
                              version: s.version,
                              revision: s.revision,
                              sha256: s.sha256,
                              schema: s.schema,
                            }),
                          )
                          .catch((e: unknown) => notify(String(e), "err"))
                      }
                    >
                      刷新预览
                    </button>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {(titlePoolSummary.title_pool_sample || []).slice(0, 24).map((t) => (
                      <span
                        key={t}
                        style={{
                          border: "1px solid var(--border, #ccc)",
                          borderRadius: 4,
                          padding: "2px 6px",
                          whiteSpace: "pre-line",
                          fontSize: 12,
                        }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="actions">
                <button
                  type="button"
                  className={`primary${actionBusy === "savePaths" ? " is-busy" : ""}`}
                  disabled={actionBusy === "savePaths"}
                  onClick={() => void savePaths()}
                >
                  {actionBusy === "savePaths" ? "保存中…" : "保存设置"}
                </button>
              </div>
            </>
          )}

          {section === "advanced" && (
            <>
              <h3 className="section-title">高级配置</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                为防止误操作，设备路径和清理功能需要高级密码。解锁约 15 分钟，关闭并重新打开速影后会自动锁定。
              </p>

              {!advancedUnlocked ? (
                <div className="advanced-lock">
                  <div className="advanced-lock-icon" aria-hidden>
                    锁
                  </div>
                  <p className="hint" style={{ marginBottom: 12 }}>
                    {passwordConfigured
                      ? "高级配置已锁定，请输入高级密码。"
                      : "正在初始化高级密码，请稍后重试。"}
                  </p>
                  {passwordConfigured ? (
                    <>
                      <label>
                        高级密码
                        <input
                          type="password"
                          autoComplete="off"
                          value={unlockPassword}
                          onChange={(e) => setUnlockPassword(e.target.value)}
                          placeholder="输入高级密码"
                        />
                      </label>
                      <div className="actions" style={{ marginTop: 10, justifyContent: "center" }}>
                        <button
                          type="button"
                          className="primary"
                          disabled={pwBusy || !unlockPassword}
                          onClick={() => {
                            setPwBusy(true);
                            Promise.resolve(onUnlock(unlockPassword))
                              .then(() => {
                                setUnlockPassword("");
                                notify("高级配置已解锁", "ok");
                              })
                              .catch((e: unknown) => notify(String(e), "err"))
                              .finally(() => setPwBusy(false));
                          }}
                        >
                          解锁
                        </button>
                      </div>
                    </>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="actions" style={{ marginBottom: 12 }}>
                    <span className="hint">
                      已解锁
                      {passwordStatusRemaining > 0
                        ? ` · 约 ${Math.ceil(passwordStatusRemaining / 60)} 分钟后自动锁定`
                        : ""}
                    </span>
                    <button
                      type="button"
                      disabled={pwBusy}
                      onClick={() => {
                        setPwBusy(true);
                        Promise.resolve(onLock())
                          .then(() => notify("高级配置已锁定", "ok"))
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setPwBusy(false));
                      }}
                    >
                      立即锁定
                    </button>
                  </div>
                  <label>
                    修改高级密码
                    <div className="path-row">
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        placeholder="输入至少 8 个字符的新密码"
                      />
                      <button
                        type="button"
                        disabled={pwBusy || newPassword.length < 8}
                        onClick={() => {
                          setPwBusy(true);
                          void settingsPasswordChange(newPassword)
                            .then(() => {
                              setNewPassword("");
                              notify("高级密码已修改", "ok");
                            })
                            .catch((error: unknown) => notify(String(error), "err"))
                            .finally(() => setPwBusy(false));
                        }}
                      >
                        保存新密码
                      </button>
                    </div>
                  </label>

                  <label>
                    缓存目录（抽帧 / 代理 / 规范化）
                    <div className="path-row">
                      <input
                        value={cacheRoot}
                        onChange={(e) => setCacheRoot(e.target.value)}
                        placeholder="/Users/…/Movies/速影工作区/cache"
                      />
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setCacheRoot(p))}
                      >
                        选择
                      </button>
                    </div>
                  </label>
                  <label>
                    渲染临时目录（不同步）
                    <div className="path-row">
                      <input
                        value={renderRoot}
                        onChange={(e) => setRenderRoot(e.target.value)}
                        placeholder="/Users/…/Movies/速影工作区/render"
                      />
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setRenderRoot(p))}
                      >
                        选择
                      </button>
                    </div>
                  </label>
                  <label>
                    SQLite 数据目录（montage.db）
                    <div className="path-row">
                      <input
                        value={dataRoot}
                        onChange={(e) => setDataRoot(e.target.value)}
                        placeholder="/Users/…/Suying/data"
                      />
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setDataRoot(p))}
                      >
                        选择
                      </button>
                    </div>
                  </label>
                  <p className="hint" style={{ margin: "6px 0 12px" }}>
                    当前库文件：{vectorDbPath || "…/montage.db"}（只读提示；权威库在本机 ~/Suying/data，不进极空间同步）
                  </p>
                  <div className="actions">
                    <button
                      type="button"
                      className={`primary${actionBusy === "savePaths" ? " is-busy" : ""}`}
                      disabled={actionBusy === "savePaths"}
                      onClick={() => void savePaths()}
                    >
                      {actionBusy === "savePaths" ? "保存中…" : "保存高级路径"}
                    </button>
                    <button
                      type="button"
                      disabled={actionBusy === "cleanCache"}
                      className={actionBusy === "cleanCache" ? "is-busy" : undefined}
                      onClick={() => {
                        setActionBusy("cleanCache");
                        api
                          .cleanCache(24)
                          .then((r) =>
                            notify(`清理缓存: ${r.removed_files} 文件 / ${r.freed_mb} MB`, "ok"),
                          )
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setActionBusy(null));
                      }}
                    >
                      {actionBusy === "cleanCache" ? "清理中…" : "清理缓存"}
                    </button>
                  </div>

                </>
              )}
            </>
          )}
        </div>
      </div>

      <StepFooter current="settings" onJump={setTab} />
    </section>
  );
}
