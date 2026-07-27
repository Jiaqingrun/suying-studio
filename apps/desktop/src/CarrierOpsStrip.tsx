import { useEffect, useState } from "react";
import { api } from "./api";

export function CarrierOpsStrip() {
  const [carrier, setCarrier] = useState<Awaited<ReturnType<typeof api.carrierStatus>> | null>(null);
  const [kw, setKw] = useState<Awaited<ReturnType<typeof api.keywordStats>> | null>(null);
  const [upd, setUpd] = useState<Awaited<ReturnType<typeof api.appUpdateCheck>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [restorePath, setRestorePath] = useState("");
  const [narrOn, setNarrOn] = useState<boolean | null>(null);

  async function refresh() {
    try {
      const [c, k, u, h] = await Promise.all([
        api.carrierStatus(),
        api.keywordStats(),
        api.appUpdateCheck(),
        api.health(),
      ]);
      setCarrier(c);
      setKw(k);
      setUpd(u);
      setNarrOn(Boolean(h.ollama_narration_enabled));
    } catch (e) {
      setMsg(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 60000);
    return () => window.clearInterval(t);
  }, []);

  async function onInstallUpdate() {
    setBusy(true);
    setMsg("");
    try {
      const verify = await api.appUpdateInstall(false);
      if (verify.error) {
        setMsg(String(verify.error));
        return;
      }
      const applied = await api.appUpdateInstall(true);
      setMsg(applied.installed ? "已安装并尝试重启" : String(applied.error || "安装未完成"));
      await refresh();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ops-bar" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }}>
      <span>
        载体{" "}
        {carrier?.carrier_visible ? "可见" : "未找到"}
        {carrier?.primary_root ? ` · ${carrier.primary_root}` : ""}
        {carrier?.latest?.version ? ` · 远端 ${carrier.latest.version}` : ""}
        {carrier?.backup_count != null ? ` · 备份 ${carrier.backup_count}` : ""}
      </span>
      <span>
        词库 {kw ? (kw.empty ? "空" : `${kw.total} 条 / ${kw.theme_count} 主题`) : "—"}
        {kw?.version != null ? ` · v${kw.version}` : ""}
      </span>
      <span title="在左侧「运维」→「Ollama 旁白文案」开关">
        Ollama 旁白 {narrOn == null ? "…" : narrOn ? "开" : "关"}（运维页设置）
      </span>
      <span>
        版本 {upd?.current_version ?? "—"}
        {upd?.update_available
          ? ` → ${upd.remote_version}${upd.force ? "（强制）" : ""}`
          : " · 已是最新或无清单"}
      </span>
      {upd?.update_available ? (
        <button type="button" className="primary" disabled={busy} onClick={() => void onInstallUpdate()}>
          {upd.force ? "强制更新并安装" : "更新并安装"}
        </button>
      ) : null}
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          api
            .carrierBackup()
            .then((r) => {
              setMsg(r.ok ? `备份 → ${r.path}` : "备份失败");
              if (r.ok && r.path) setRestorePath(r.path);
            })
            .catch((e) => setMsg(String(e)))
            .finally(() => setBusy(false));
        }}
      >
        配置备份到载体
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          api
            .carrierEnsure()
            .then((r) => setMsg(r.ok ? `载体就绪 ${r.root}` : "ensure 失败"))
            .catch((e) => setMsg(String(e)))
            .finally(() => setBusy(false));
        }}
      >
        确保载体+种子
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          api
            .carrierInstallUpdateAgent()
            .then((r) => setMsg(r.ok ? "更新服务已安装" : `安装失败: ${r.stderr || r.stdout}`))
            .catch((e) => setMsg(String(e)))
            .finally(() => setBusy(false));
        }}
      >
        安装更新服务
      </button>
      <input
        style={{ minWidth: 220, flex: 1 }}
        placeholder="备份目录路径（从 T2S 恢复）"
        value={restorePath}
        onChange={(e) => setRestorePath(e.target.value)}
      />
      <button
        type="button"
        disabled={busy || !restorePath.trim()}
        onClick={() => {
          setBusy(true);
          api
            .carrierRestore(restorePath.trim())
            .then((r) => setMsg(r.ok ? `已读取备份 keys=${(r.keys || []).join(",")}` : String(r.error || "恢复失败")))
            .catch((e) => setMsg(String(e)))
            .finally(() => setBusy(false));
        }}
      >
        从载体恢复配置
      </button>
      <button type="button" disabled={busy} onClick={() => void refresh()}>
        刷新载体
      </button>
      {msg ? <span className="hint">{msg}</span> : null}
    </div>
  );
}
