import { useEffect, useState } from "react";
import { api } from "./api";

type Step = "zspace" | "bind" | "carrier" | "workspace";

type Props = {
  wizName: string;
  setWizName: (v: string) => void;
  wizLib: string;
  setWizLib: (v: string) => void;
  wizOut: string;
  setWizOut: (v: string) => void;
  wizKw: string;
  setWizKw: (v: string) => void;
  onFinish: (enableVec: boolean, seedId?: string) => Promise<void>;
  onDismiss: () => void;
  pickDir: () => Promise<string | null>;
  pickFile: () => Promise<string | null>;
  applyStandardLayout: () => Promise<void>;
  notify: (msg: string, kind?: "ok" | "err" | "warn" | "info") => void;
};

/**
 * Two-phase first-run:
 * A) 极空间登录 + 绑定 T2S + 载体可见
 * B) 本机片库/成片工作区（不进 T2S）
 */
export function CarrierInstallWizard(props: Props) {
  const {
    wizName,
    setWizName,
    wizLib,
    setWizLib,
    wizOut,
    setWizOut,
    wizKw,
    setWizKw,
    onFinish,
    onDismiss,
    pickDir,
    pickFile,
    applyStandardLayout,
    notify,
  } = props;

  const [step, setStep] = useState<Step>("zspace");
  const [busy, setBusy] = useState(false);
  const [bindOk, setBindOk] = useState(false);
  const [carrierOk, setCarrierOk] = useState(false);
  const [statusHint, setStatusHint] = useState("");
  const [accounts, setAccounts] = useState<Array<Record<string, unknown>>>([]);
  const [expectedNas, setExpectedNas] = useState("");
  const [seeds, setSeeds] = useState<
    Array<{ id: string; name: string; description?: string; files?: Array<{ source: string; target: string }> }>
  >([]);
  const [seedId, setSeedId] = useState("");

  async function refreshZSpace() {
    setBusy(true);
    try {
      const st = await api.zspaceSyncStatus();
      const accountsRaw = (st.zspace_accounts as Array<Record<string, unknown>>) || [];
      setAccounts(accountsRaw);
      const match = Boolean(st.zspace_match);
      setBindOk(match);
      const active = st.zspace_active as Record<string, string> | undefined;
      const lines = [
        active?.username
          ? `当前登录: ${active.username} / ${active.nas_id || "—"} (${active.nas_name || "—"})`
          : "当前登录: 无（请打开极空间客户端注册或登录对方账号）",
        st.zspace_match
          ? "绑定匹配 ✓"
          : st.zspace_bound_ok
            ? `绑定未匹配: ${String(st.zspace_reason || "")}`
            : "尚未绑定 T2S（选中下方账号，nas_id 须为运维指定的 T2S）",
      ];
      setStatusHint(lines.join("\n"));
      const car = await api.carrierStatus();
      setCarrierOk(Boolean(car.carrier_visible));
      if (car.primary_root) {
        setStatusHint((h) => `${h}\n载体: ${car.primary_root}`);
      } else {
        setStatusHint((h) => `${h}\n载体: 未找到（请在极空间仅同步「速影载体/」到 ~/Suying/carrier）`);
      }
    } catch (e) {
      setStatusHint(String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refreshZSpace();
    void api
      .carrierSeeds()
      .then((r) => setSeeds(r.seeds || []))
      .catch(() => setSeeds([]));
  }, []);

  async function bindAccount(acc: Record<string, unknown>) {
    const username = String(acc.username || "");
    const nasId = String(acc.nas_id || "");
    const nasName = String(acc.nas_name || "");
    if (!username || !nasId) {
      notify("账号缺 username 或 nas_id", "err");
      return;
    }
    if (expectedNas.trim() && expectedNas.trim() !== nasId) {
      notify(`nas_id 与约定 T2S 不符（期望 ${expectedNas.trim()}）`, "err");
      return;
    }
    setBusy(true);
    try {
      const r = await api.zspaceSyncBind({ username, nas_id: nasId, nas_name: nasName });
      notify(
        r.active_matches
          ? `已绑定 T2S: ${username} / ${nasId}`
          : `已写入绑定；请确认客户端已登录该账号并选中 T2S`,
        r.active_matches ? "ok" : "warn",
      );
      await refreshZSpace();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  async function ensureCarrier() {
    setBusy(true);
    try {
      await api.carrierEnsure();
      try {
        await api.carrierInstallUpdateAgent();
      } catch {
        /* 运维页可补装 */
      }
      await refreshZSpace();
      notify("已确保本机载体目录 + 更新服务", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setBusy(false);
    }
  }

  function canLeaveBind(): boolean {
    return bindOk;
  }

  function canLeaveCarrier(): boolean {
    return carrierOk || Boolean(wizLib && wizOut);
  }

  return (
    <div className="panel wizard">
      <div className="panel-head">
        <h2>安装向导 · T2S 载体</h2>
      </div>
      <p className="wizard-lead">
        段 A：在本机登录对方的极空间账号并绑定 T2S，只同步「速影载体/」（安装包/种子/配置备份）。段 B：在本机创建片库与成片路径——片库不进
        T2S。
      </p>
      <div className="actions" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        {(
          [
            ["zspace", "1. 极空间"],
            ["bind", "2. 绑定 T2S"],
            ["carrier", "3. 载体"],
            ["workspace", "4. 本机工作区"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={step === id ? "primary" : undefined}
            disabled={busy}
            onClick={() => setStep(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {step === "zspace" && (
        <div>
          <p className="hint">
            未安装极空间？请先安装官方客户端，用<strong>对方自己的账号</strong>注册或登录。速影不会静默造号。
          </p>
          <div className="actions">
            <a href="https://www.zspace.cn/" target="_blank" rel="noreferrer">
              打开极空间官网
            </a>
            <button type="button" className="primary" disabled={busy} onClick={() => void refreshZSpace()}>
              我已登录 · 复查
            </button>
            <button type="button" disabled={busy || !accounts.some((a) => a.active)} onClick={() => setStep("bind")}>
              下一步：绑定 T2S
            </button>
          </div>
          <pre className="hint" style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>
            {statusHint || "—"}
          </pre>
        </div>
      )}

      {step === "bind" && (
        <div>
          <label>
            约定 T2S nas_id（可选校验）
            <input
              value={expectedNas}
              onChange={(e) => setExpectedNas(e.target.value)}
              placeholder="运维提供的 T2S nasId"
            />
          </label>
          <div className="hint" style={{ margin: "8px 0" }}>
            选择当前登录账号；绑错 NAS 不可继续完成安装。
          </div>
          <div className="actions" style={{ flexWrap: "wrap" }}>
            {accounts.map((acc) => {
              const username = String(acc.username || "");
              const nasId = String(acc.nas_id || "");
              return (
                <button
                  key={`${username}-${nasId}`}
                  type="button"
                  disabled={busy}
                  onClick={() => void bindAccount(acc)}
                >
                  绑定 {username} · {String(acc.nas_name || nasId)}
                  {acc.active ? "（当前）" : ""}
                </button>
              );
            })}
            {accounts.length === 0 ? <span className="hint">无账号记录 — 请先在客户端登录</span> : null}
          </div>
          <div className="actions" style={{ marginTop: 12 }}>
            <button type="button" disabled={busy} onClick={() => void refreshZSpace()}>
              复查绑定
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !canLeaveBind()}
              onClick={() => setStep("carrier")}
            >
              下一步：确认载体
            </button>
          </div>
          {!bindOk ? <p className="hint" style={{ color: "var(--danger, #b00)" }}>未绑定匹配前不可进入载体步骤完成安装。</p> : null}
          <pre className="hint" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
            {statusHint}
          </pre>
        </div>
      )}

      {step === "carrier" && (
        <div>
          <p className="hint">
            在极空间客户端将 T2S 上的「速影载体/」同步到本机镜像（推荐 ~/Suying/carrier）。禁止把 01-片库 配进默认同步。
          </p>
          <div className="actions">
            <button type="button" disabled={busy} onClick={() => void ensureCarrier()}>
              生成本机载体骨架
            </button>
            <button type="button" disabled={busy} onClick={() => void refreshZSpace()}>
              复查载体可见
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || (!carrierOk && !canLeaveCarrier())}
              onClick={() => {
                if (!bindOk) {
                  notify("请先完成 T2S 绑定", "err");
                  setStep("bind");
                  return;
                }
                setStep("workspace");
              }}
            >
              下一步：本机工作区
            </button>
          </div>
          <pre className="hint" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
            {statusHint}
          </pre>
        </div>
      )}

      {step === "workspace" && (
        <div>
          <p className="hint">
            片库 / 成片 / 词池在客户本机；可从载体 seed 落行业包。向量化默认关。
            若日后启用极空间片库同步，请在运维页为每个客户单独绑定团队空间子文件夹（与 T2S 载体通道隔离）。
          </p>
          <div className="actions" style={{ marginBottom: 12 }}>
            <button type="button" onClick={() => void applyStandardLayout()}>
              使用标准本机目录布局
            </button>
          </div>
          <div className="grid2">
            <label>
              客户名称
              <input value={wizName} onChange={(e) => setWizName(e.target.value)} placeholder="例如：我的门店" />
            </label>
            <label>
              词池文件（json/md）
              <div className="path-row">
                <input value={wizKw} onChange={(e) => setWizKw(e.target.value)} />
                <button type="button" onClick={() => pickFile().then((p) => p && setWizKw(p))}>
                  选择
                </button>
              </div>
            </label>
            <label>
              片库目录
              <div className="path-row">
                <input value={wizLib} onChange={(e) => setWizLib(e.target.value)} />
                <button type="button" onClick={() => pickDir().then((p) => p && setWizLib(p))}>
                  选择
                </button>
              </div>
            </label>
            <label>
              输出目录
              <div className="path-row">
                <input value={wizOut} onChange={(e) => setWizOut(e.target.value)} />
                <button type="button" onClick={() => pickDir().then((p) => p && setWizOut(p))}>
                  选择
                </button>
              </div>
            </label>
            <label>
              客户配置种子（可选）
              <select value={seedId} onChange={(e) => setSeedId(e.target.value)}>
                <option value="">不导入，使用空白配置</option>
                {seeds.map((seed) => (
                  <option key={seed.id} value={seed.id}>
                    {seed.name}
                  </option>
                ))}
              </select>
              <span className="hint">
                {seedId
                  ? seeds.find((seed) => seed.id === seedId)?.description || "仅导入配置，不含视频、数据库或密钥"
                  : "稍后也可在运维页导入；默认不会覆盖已有文件"}
              </span>
              {seedId && (
                <span className="hint">
                  文件预览：
                  {(seeds.find((seed) => seed.id === seedId)?.files || [])
                    .map((file) => file.target)
                    .join("、")}
                </span>
              )}
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              disabled={busy || !bindOk}
              onClick={() => {
                if (!bindOk) {
                  notify("未绑定 T2S，不可完成安装", "err");
                  return;
                }
                void onFinish(false, seedId || undefined).catch((e) => notify(String(e), "err"));
              }}
            >
              完成（稍后开启向量化）
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy || !bindOk}
              onClick={() => {
                if (!bindOk) {
                  notify("未绑定 T2S，不可完成安装", "err");
                  return;
                }
                void onFinish(true, seedId || undefined).catch((e) => notify(String(e), "err"));
              }}
            >
              完成并开启向量化
            </button>
            <button type="button" onClick={onDismiss}>
              稍后再说
            </button>
          </div>
          {!bindOk ? (
            <p className="hint" style={{ color: "var(--danger, #b00)" }}>
              硬边界：未登录极空间 / 绑错 NAS 时不可完成安装。
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}
