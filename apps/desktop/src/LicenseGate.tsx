import { useEffect, useState, type ReactNode } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { isTauri, stopEngine } from "./engineControl";
import {
  createLicenseRequest,
  getLicenseStatus,
  installLicense,
  type LicenseRequest,
  type LicenseStatus,
} from "./licensing";
import { settingsPasswordVerify } from "./settingsLock";

type Props = {
  children: ReactNode;
};

function tailRef(value: string | null | undefined, n = 6): string {
  if (!value) return "";
  const cleaned = value.replace(/[^A-Za-z0-9]/g, "");
  if (cleaned.length <= n) return cleaned;
  return cleaned.slice(-n);
}

/** 授权页面向客户展示时，绝不泄露本机路径 / runtime 布局。 */
function sanitizePublicText(value: string): string {
  const cleaned = value
    .replace(/\/(?:Users|home|var|tmp|private|nvme\d*|opt|root)\/[^\s"'`]+/gi, "")
    .replace(/[A-Za-z]:\\[^\s"'`]+/g, "")
    .replace(/~\/[^\s"'`]+/g, "")
    .replace(/:\s*$/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  if (!cleaned) return "授权校验失败";
  if (cleaned.startsWith("未安装许可证")) return "未安装许可证";
  return cleaned;
}

function LicenseBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={compact ? "license-brand license-brand--compact" : "license-brand"}
      aria-label="速影 Studio"
    >
      <div className="license-mark" aria-hidden="true">
        <span className="license-mark-glyph">速</span>
      </div>
      <div className="license-brand-copy">
        <span className="license-brand-name">速影</span>
        <span className="license-brand-product">Studio</span>
      </div>
    </div>
  );
}

export function LicenseGate({ children }: Props) {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [request, setRequest] = useState<LicenseRequest | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [opsPassword, setOpsPassword] = useState("");

  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await getLicenseStatus();
        if (cancelled) return;
        setStatus(next);
        if (!next.authorized) {
          await stopEngine().catch(() => null);
          // term 无感路径：到期不生成许可请求给客户自行签发，仍可内部缓存供运维
          if (next.license_kind !== "term") {
            try {
              setRequest(await createLicenseRequest());
            } catch (reason) {
              if (!cancelled) setError(String(reason));
            }
          }
        }
      } catch (reason) {
        if (cancelled) return;
        await stopEngine().catch(() => null);
        setError(String(reason));
        setStatus({
          authorized: false,
          development_build: false,
          reason: String(reason),
          license_id: null,
          device_key_id: null,
          delivery_id: null,
          customer_ref: null,
          issue_seq: null,
          features: [],
          license_kind: "",
          expires_at: null,
          trial_remaining_sec: null,
          remaining_sec: null,
          ops_unlock_allowed: false,
          code: "INVALID",
        });
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, []);

  if (!isTauri()) return <>{children}</>;

  // GCustomerUX: while checking, show neutral splash — never flash the unauthorized card.
  if (status === null) {
    return (
      <main className="license-gate license-gate--checking" aria-busy="true" aria-label="正在校验授权">
        <section className="license-splash">
          <LicenseBrand compact />
          <p className="muted">正在启动…</p>
        </section>
      </main>
    );
  }

  if (status.authorized) return <>{children}</>;

  // term 到期 / 回拨：客户无感合同 — 仅联系运维，无导入与 ops 密码主路径
  if (
    status.license_kind === "term" ||
    status.code === "TERM_EXPIRED" ||
    (status.reason && status.reason.includes("授权已到期"))
  ) {
    const refHint = [tailRef(status.license_id), tailRef(status.delivery_id)]
      .filter(Boolean)
      .join(" · ");
    return (
      <main className="license-gate">
        <section className="license-card">
          <LicenseBrand />
          <p className="eyebrow">授权状态</p>
          <h1>授权已到期，请联系运维人员</h1>
          <p className="muted">业务功能已全部冻结。恢复后请重新打开或稍候自动解锁。</p>
          {refHint ? <p className="license-reason">核对编号：{refHint}</p> : null}
          {status.reason && !status.reason.includes("授权已到期") ? (
            <p className="license-reason">{sanitizePublicText(status.reason)}</p>
          ) : null}
        </section>
      </main>
    );
  }

  async function unlockTrialForSession() {
    if (!status) return;
    setBusy(true);
    setError("");
    try {
      await settingsPasswordVerify(opsPassword);
      setStatus({ ...status, authorized: true, reason: "trial_ops_session_unlocked" });
      setOpsPassword("");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  if (status.license_kind === "trial" && status.ops_unlock_allowed) {
    return (
      <main className="license-gate">
        <section className="license-card">
          <LicenseBrand />
          <p className="eyebrow">体验期已结束</p>
          <h1>业务功能已锁定</h1>
          <p className="muted">正式使用请由运维导入许可证。运维可使用高级设置密码临时解锁本次会话。</p>
          {error ? <p className="license-error">{sanitizePublicText(error)}</p> : null}
          <label className="license-request-label" htmlFor="trial-ops-password">
            运维密码
          </label>
          <input
            id="trial-ops-password"
            type="password"
            autoComplete="current-password"
            value={opsPassword}
            onChange={(event) => setOpsPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void unlockTrialForSession();
            }}
          />
          <div className="license-actions">
            <button type="button" onClick={() => void unlockTrialForSession()} disabled={busy || !opsPassword}>
              {busy ? "正在验证…" : "本次会话解锁"}
            </button>
          </div>
        </section>
      </main>
    );
  }

  const requestText = request ? JSON.stringify(request, null, 2) : "";

  async function chooseLicense() {
    setBusy(true);
    setError("");
    try {
      const selected = await open({
        multiple: false,
        directory: false,
        filters: [{ name: "速影许可证", extensions: ["suying-license", "json"] }],
      });
      if (!selected) return;
      const next = await installLicense(selected);
      setStatus(next);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function copyRequest() {
    if (!requestText) return;
    try {
      await navigator.clipboard.writeText(requestText);
    } catch {
      setError("复制失败，请手动选中下面的许可请求。");
    }
  }

  return (
    <main className="license-gate">
      <section className="license-card">
        <LicenseBrand />
        <p className="eyebrow">单机授权</p>
        <h1>此设备尚未授权</h1>
        <p className="muted">将许可请求发给运维人员，收到与本机绑定的许可证后导入。许可证不能复制到其他电脑使用。</p>

        {error ? <p className="license-error">{sanitizePublicText(error)}</p> : null}
        {status?.reason ? <p className="license-reason">{sanitizePublicText(status.reason)}</p> : null}

        <label className="license-request-label" htmlFor="license-request">
          本机许可请求
        </label>
        <textarea id="license-request" readOnly value={requestText || "正在生成设备请求…"} />

        <div className="license-actions">
          <button type="button" className="secondary" onClick={() => void copyRequest()} disabled={!request}>
            复制请求
          </button>
          <button type="button" onClick={() => void chooseLicense()} disabled={busy}>
            {busy ? "正在验证…" : "导入许可证"}
          </button>
        </div>
      </section>
    </main>
  );
}
