#!/usr/bin/env python3
"""Remote monitor for customer-machine message receive + jump-to-reply.

Operator Mac only: SSH probes the customer engine, writes local JSONL under
~/Suying/logs/remote-message-monitor/, and applies safe auto-fixes.

Safe fixes:
  - reopen App / engine when /health or message scheduler is down
  - resume orphan PAUSED_BLOCKED (empty reasons) or stale power_off
  - disable enabled message accounts whose Chrome profile is missing and
    already covered by another account (phantom duplicate)

Non-fixes (record only):
  - login / page load timeouts
  - unique alias rebind (needs human)
  - invalid reply_url rows

Usage:
  python3 scripts/monitor_remote_messages.py --host xlf-remote
  python3 scripts/monitor_remote_messages.py --host xlf-remote --duration 7200 --interval 30
  python3 scripts/monitor_remote_messages.py --host xlf-remote --once --no-fix
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_DURATION_SEC = 2 * 60 * 60
DEFAULT_INTERVAL_SEC = 30
PORT_DEFAULT = 8766
LOCAL_LOG_ROOT = Path.home() / "Suying" / "logs" / "remote-message-monitor"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def run_ssh(host: str, remote: str, *, timeout: int = 60) -> tuple[int, str, str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        host,
        "bash",
        "-s",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=remote,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, out or "", "ssh timeout"


def _parse_last_json(out: str) -> dict[str, Any] | None:
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return None


def probe(host: str, port: int) -> dict[str, Any]:
    remote = f"""
set +e
PORT={int(port)}
API="http://127.0.0.1:${{PORT}}"
fetch() {{
  local path="$1"
  curl -fsS --max-time 8 "${{API}}${{path}}" 2>/dev/null \\
    || echo '{{"error":"unreachable"}}'
}}
APP=""
for c in \\
  "$HOME/Applications/速影 Studio.app" \\
  "/Applications/速影 Studio.app" \\
  "$HOME/Desktop/速影 Studio.app" \\
  "/Applications/速影.app" \\
  "$HOME/Desktop/速影.app"; do
  if [[ -d "$c" ]]; then APP="$c"; break; fi
done
ENGINE_PID="$(lsof -t -iTCP:${{PORT}} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
ENGINE_CMD=""
if [[ -n "$ENGINE_PID" ]]; then
  ENGINE_CMD="$(ps -p "$ENGINE_PID" -o command= 2>/dev/null || true)"
fi
HEALTH="$(fetch /health)"
READINESS="$(fetch /readiness)"
RUNTIME="$(fetch /ops/runtime-health)"
PAUSE="$(fetch /system/pause-state)"
MSG_STATUS="$(fetch /reach/messages/status)"
MSG_ACCOUNTS="$(fetch /reach/message-accounts)"
MESSAGES="$(fetch '/reach/messages?limit=30')"
CHROME="$(fetch /reach/chrome-profiles)"
CONTENT_STATUS="$(fetch /content/messages/status)"
export HEALTH READINESS RUNTIME PAUSE MSG_STATUS MSG_ACCOUNTS MESSAGES CHROME CONTENT_STATUS APP ENGINE_PID ENGINE_CMD
python3 - <<'PY'
import json, os
from datetime import datetime

def loads(name):
    try:
        return json.loads(os.environ.get(name) or "{{}}")
    except Exception as exc:
        return {{"error": f"parse_failed:{{exc}}"}}

print(json.dumps({{
    "probed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "app_path": os.environ.get("APP") or None,
    "engine_pid": os.environ.get("ENGINE_PID") or None,
    "engine_cmd": os.environ.get("ENGINE_CMD") or None,
    "health": loads("HEALTH"),
    "readiness": loads("READINESS"),
    "runtime_health": loads("RUNTIME"),
    "pause_state": loads("PAUSE"),
    "messages_status": loads("MSG_STATUS"),
    "message_accounts": loads("MSG_ACCOUNTS"),
    "messages": loads("MESSAGES"),
    "chrome_profiles": loads("CHROME"),
    "content_messages_status": loads("CONTENT_STATUS"),
}}, ensure_ascii=False))
PY
"""
    code, out, err = run_ssh(host, remote, timeout=90)
    data = _parse_last_json(out)
    if data is None:
        return {
            "error": "ssh_failed" if code else "probe_parse_failed",
            "exit_code": code,
            "stderr": (err or "")[-2000:],
            "stdout_tail": (out or "")[-1000:],
            "probed_at": _now_iso(),
        }
    data["_ssh_exit"] = code
    if err.strip():
        data["_ssh_stderr"] = err[-1000:]
    return data


def remote_api(
    host: str,
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.dumps(body or {}, ensure_ascii=False)
    payload_q = payload.replace("'", "'\"'\"'")
    method_u = method.upper()
    remote = f"""
set +e
API="http://127.0.0.1:{int(port)}"
if [[ "{method_u}" == "GET" ]]; then
  RESP="$(curl -fsS --max-time 15 "${{API}}{path}" 2>/dev/null)"
  CODE=$?
else
  RESP="$(curl -fsS --max-time 20 -X {method_u} "${{API}}{path}" \\
    -H 'Content-Type: application/json' \\
    -H 'Origin: http://tauri.localhost' \\
    -d '{payload_q}' 2>/dev/null)"
  CODE=$?
fi
export RESP CODE
python3 - <<'PY'
import json, os
raw = os.environ.get("RESP") or ""
code = int(os.environ.get("CODE") or "1")
try:
    body = json.loads(raw) if raw else {{"error": "empty"}}
except Exception as exc:
    body = {{"error": f"parse_failed:{{exc}}", "raw": raw[:500]}}
print(json.dumps({{"curl_exit": code, "body": body}}, ensure_ascii=False))
PY
"""
    code, out, err = run_ssh(host, remote, timeout=45)
    data = _parse_last_json(out)
    if data is None:
        return {"curl_exit": code or 1, "body": {"error": "api_failed", "stderr": (err or "")[-500:]}}
    return data


def reopen_app(host: str, port: int) -> dict[str, Any]:
    remote = f"""
set +e
PORT={int(port)}
APP=""
for c in \\
  "$HOME/Applications/速影 Studio.app" \\
  "/Applications/速影 Studio.app" \\
  "$HOME/Desktop/速影 Studio.app" \\
  "/Applications/速影.app" \\
  "$HOME/Desktop/速影.app"; do
  if [[ -d "$c" ]]; then APP="$c"; break; fi
done
if [[ -z "$APP" ]]; then
  echo '{{"ok":false,"error":"app_not_found"}}'
  exit 0
fi
for pid in $(lsof -t -iTCP:${{PORT}} -sTCP:LISTEN 2>/dev/null || true); do
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$cmd" == *"engine.main"* || "$cmd" == *"/速影 Studio.app/"* || "$cmd" == *"/速影.app/"* ]]; then
    kill "$pid" 2>/dev/null || true
  fi
done
killall appsdesktop 2>/dev/null || true
sleep 2
open -a "$APP"
ok=0
for i in $(seq 1 40); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${{PORT}}/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
ready=0
if [[ "$ok" == "1" ]]; then
  if curl -fsS --max-time 3 "http://127.0.0.1:${{PORT}}/readiness" 2>/dev/null | grep -q '"ready": true'; then ready=1; fi
fi
printf '{{"ok":%s,"ready":%s,"app":%s}}\\n' \
  "$([[ "$ok" == "1" ]] && echo true || echo false)" \
  "$([[ "$ready" == "1" ]] && echo true || echo false)" \
  "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$APP")"
"""
    code, out, err = run_ssh(host, remote, timeout=180)
    data = _parse_last_json(out)
    if data is None:
        return {"ok": False, "error": "reopen_failed", "stderr": (err or "")[-800:], "ssh_exit": code}
    data["ssh_exit"] = code
    return data


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    fixable: bool = False
    fix_action: str | None = None


def profile_names(chrome: dict[str, Any]) -> set[str]:
    rows = chrome.get("profiles") if isinstance(chrome, dict) else None
    if not isinstance(rows, list):
        return set()
    return {str(r["name"]) for r in rows if isinstance(r, dict) and r.get("name")}


def account_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    block = payload.get("message_accounts") or {}
    rows = block.get("accounts") if isinstance(block, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def message_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    block = payload.get("messages") or {}
    if isinstance(block, list):
        return [r for r in block if isinstance(r, dict)]
    rows = block.get("messages") if isinstance(block, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def alias_candidates(missing: str, available: set[str], platform: str) -> list[str]:
    if missing in available:
        return [missing]
    variants = {missing}
    if missing.startswith("抖音-") and not missing.startswith("抖音号-"):
        variants.add("抖音号-" + missing[len("抖音-") :])
    if missing.startswith("抖音号-"):
        variants.add("抖音-" + missing[len("抖音号-") :])
    hits: list[str] = []
    for name in sorted(available):
        if name in variants:
            hits.append(name)
            continue
        m1 = re.search(r"-(\d+)$", missing)
        m2 = re.search(r"-(\d+)$", name)
        if not (m1 and m2 and m1.group(1) == m2.group(1)):
            continue
        if platform == "douyin" and "抖音" in name:
            hits.append(name)
        elif platform == "xhs" and ("小红书" in name):
            hits.append(name)
        elif platform == "channels" and ("视频号" in name):
            hits.append(name)
        elif platform == "kuaishou" and ("快手" in name):
            hits.append(name)
    return list(dict.fromkeys(hits))


def analyze(payload: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    if payload.get("error"):
        findings.append(
            Finding(
                code="probe_failed",
                severity="error",
                message=f"远程探测失败: {payload.get('error')}",
                data={"stderr": payload.get("stderr") or payload.get("_ssh_stderr")},
                fixable=True,
                fix_action="reopen_app",
            )
        )
        return findings

    health = payload.get("health") or {}
    readiness = payload.get("readiness") or {}
    pause = payload.get("pause_state") or {}
    runtime = payload.get("runtime_health") or {}
    msg_status = payload.get("messages_status") or {}
    worker = msg_status.get("worker") if isinstance(msg_status, dict) else {}
    scans = msg_status.get("scans") if isinstance(msg_status, dict) else []
    chrome = payload.get("chrome_profiles") or {}
    profiles = profile_names(chrome if isinstance(chrome, dict) else {})

    if dig(health, "status") != "ok" or "error" in health:
        findings.append(
            Finding(
                code="engine_down",
                severity="error",
                message="引擎 /health 不可用",
                data={"health": health, "engine_pid": payload.get("engine_pid")},
                fixable=True,
                fix_action="reopen_app",
            )
        )
        return findings

    if dig(readiness, "ready", default=None) is False:
        findings.append(
            Finding(
                code="not_ready",
                severity="error",
                message="引擎假健康：/readiness ready=false",
                data={
                    "license_authorized": dig(readiness, "license_authorized"),
                    "workspace_ready": dig(readiness, "workspace_ready"),
                    "runtime_source_valid": dig(readiness, "runtime_source_valid"),
                },
                fixable=True,
                fix_action="reopen_app",
            )
        )

    pause_state = dig(pause, "state", default=dig(runtime, "pause", "state", default="?"))
    reasons = dig(pause, "pause_reasons", default=dig(runtime, "pause", "pause_reasons", default=[])) or []
    accepts = dig(runtime, "accepts_new_work", default=dig(pause, "accepts_new_work", default=True))
    if pause_state in {"PAUSED_BLOCKED", "PAUSED"} and not reasons:
        findings.append(
            Finding(
                code="orphan_pause",
                severity="error",
                message=f"孤儿暂停态 {pause_state}（空 pause_reasons）",
                data={"pause": pause},
                fixable=True,
                fix_action="resume_orphan",
            )
        )
    elif "power_off" in reasons and accepts is False:
        findings.append(
            Finding(
                code="stale_power_off",
                severity="error",
                message="残留 power_off 暂停，阻止接单",
                data={"reasons": reasons},
                fixable=True,
                fix_action="resume_power_off",
            )
        )
    elif accepts is False:
        findings.append(
            Finding(
                code="paused",
                severity="warn",
                message=f"运行时暂停 accepts_new_work=false state={pause_state}",
                data={"reasons": reasons},
            )
        )

    if isinstance(worker, dict) and worker.get("scheduler_running") is False:
        findings.append(
            Finding(
                code="message_scheduler_down",
                severity="error",
                message="消息巡检 scheduler 未运行",
                data={"worker": worker},
                fixable=True,
                fix_action="reopen_app",
            )
        )

    if isinstance(worker, dict) and worker.get("active"):
        findings.append(
            Finding(
                code="scan_active",
                severity="info",
                message=f"消息扫描进行中 account={worker.get('account_id')} phase={worker.get('phase')}",
                data={"worker": worker},
            )
        )

    if isinstance(worker, dict) and worker.get("phase") == "failed":
        err = str(worker.get("error") or "")
        chrome_missing = "Chrome 配置不存在" in err
        findings.append(
            Finding(
                code="worker_failed",
                severity="warn",
                message=f"最近消息 worker 失败: {err[:160]}",
                data={"worker": worker},
                fixable=chrome_missing,
                fix_action="disable_missing_profile_accounts" if chrome_missing else None,
            )
        )

    fail_scans = [r for r in scans[:8] if isinstance(r, dict) and r.get("status") == "failed"] if isinstance(scans, list) else []
    if fail_scans:
        findings.append(
            Finding(
                code="recent_scan_failures",
                severity="warn",
                message=f"最近失败扫描 {len(fail_scans)} 条",
                data={"scans": fail_scans[:5]},
            )
        )

    accounts = account_rows(payload)
    bound_profiles = {
        str(a.get("profile_name"))
        for a in accounts
        if a.get("enabled") and a.get("profile_name")
    }
    missing_accounts: list[dict[str, Any]] = []
    for acc in accounts:
        if not acc.get("enabled"):
            continue
        name = str(acc.get("profile_name") or "")
        if not name or name in profiles:
            continue
        candidates = alias_candidates(name, profiles, str(acc.get("platform") or ""))
        free = [c for c in candidates if c not in bound_profiles or c == name]
        missing_accounts.append(
            {
                "id": acc.get("id"),
                "platform": acc.get("platform"),
                "profile_name": name,
                "display_name": acc.get("display_name"),
                "last_error": acc.get("last_error"),
                "candidates": candidates,
                "free_candidates": free,
                "duplicate_of": [
                    a.get("id")
                    for a in accounts
                    if a.get("enabled")
                    and a.get("id") != acc.get("id")
                    and a.get("profile_name") in candidates
                ],
            }
        )

    if missing_accounts:
        findings.append(
            Finding(
                code="chrome_profile_missing",
                severity="error",
                message=f"{len(missing_accounts)} 个启用消息账号绑定了不存在的 Chrome 配置",
                data={"accounts": missing_accounts},
                fixable=True,
                fix_action="disable_missing_profile_accounts",
            )
        )

    msgs = message_rows(payload)
    bad_urls: list[dict[str, Any]] = []
    for msg in msgs:
        url = str(msg.get("reply_url") or "")
        if not url:
            bad_urls.append({"id": msg.get("id"), "reason": "empty_reply_url"})
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            bad_urls.append({"id": msg.get("id"), "reason": "bad_url", "reply_url": url[:200]})
    if bad_urls:
        findings.append(
            Finding(
                code="reply_url_invalid",
                severity="error",
                message=f"{len(bad_urls)} 条消息 reply_url 无效（跳转回复会失败）",
                data={"messages": bad_urls[:10]},
            )
        )
    elif msgs:
        findings.append(
            Finding(
                code="messages_present",
                severity="info",
                message=f"本地消息库有 {len(msgs)} 条，reply_url 形状正常",
                data={"count": len(msgs)},
            )
        )
    else:
        findings.append(
            Finding(
                code="messages_empty",
                severity="info",
                message="当前无入库消息（空库本身不一定是故障；依赖巡检成功）",
            )
        )

    login_timeouts = [
        a
        for a in accounts
        if a.get("enabled") and "未在时限内完成加载" in str(a.get("last_error") or "")
    ]
    if login_timeouts:
        findings.append(
            Finding(
                code="login_or_page_timeout",
                severity="warn",
                message=f"{len(login_timeouts)} 个账号最近巡检超时（需人工确认登录/页面）",
                data={
                    "accounts": [
                        {
                            "id": a.get("id"),
                            "profile_name": a.get("profile_name"),
                            "platform": a.get("platform"),
                        }
                        for a in login_timeouts[:12]
                    ]
                },
            )
        )

    return findings


def _disable_missing_accounts(
    host: str,
    port: int,
    finding: Finding,
) -> dict[str, Any]:
    accounts = dig(finding.data, "accounts", default=[]) or []
    disabled: list[dict[str, Any]] = []

    if not accounts and "Chrome 配置不存在" in str(dig(finding.data, "worker", "error", default="")):
        err = str(dig(finding.data, "worker", "error", default=""))
        m = re.search(r"Chrome 配置不存在于 video:\s*(.+)$", err)
        if m:
            name = m.group(1).strip()
            snap = probe(host, port)
            accounts = []
            for acc in account_rows(snap):
                if acc.get("enabled") and acc.get("profile_name") == name:
                    accounts.append(
                        {
                            "id": acc.get("id"),
                            "profile_name": name,
                            "duplicate_of": [1],  # force disable phantom
                            "free_candidates": [],
                        }
                    )

    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        aid = acc.get("id")
        if aid is None:
            continue
        dupes = acc.get("duplicate_of") or []
        free = acc.get("free_candidates") or []
        if dupes or not free:
            api = remote_api(
                host,
                port,
                "PATCH",
                f"/reach/message-accounts/{aid}",
                {"enabled": False},
            )
            disabled.append({"id": aid, "api": api, "reason": "phantom_or_unresolvable"})
        else:
            disabled.append(
                {
                    "id": aid,
                    "skipped": True,
                    "reason": "unique_alias_needs_human_rebind",
                    "free_candidates": free,
                }
            )
    return {"disabled": disabled}


def apply_fixes(
    host: str,
    port: int,
    findings: list[Finding],
    *,
    apply: bool,
    log_fix,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not apply:
        for f in findings:
            if f.fixable and f.fix_action:
                results.append(
                    {
                        "at": _now_iso(),
                        "skipped": True,
                        "reason": "dry_run",
                        "code": f.code,
                        "action": f.fix_action,
                    }
                )
        return results

    done: set[str] = set()
    for f in findings:
        if not f.fixable or not f.fix_action:
            continue
        action = f.fix_action
        if action in done:
            continue
        done.add(action)
        result: dict[str, Any] = {"at": _now_iso(), "code": f.code, "action": action}
        try:
            if action == "reopen_app":
                result["result"] = reopen_app(host, port)
            elif action == "resume_orphan":
                result["result"] = remote_api(host, port, "POST", "/system/resume", {"reasons": []})
            elif action == "resume_power_off":
                result["result"] = remote_api(
                    host,
                    port,
                    "POST",
                    "/system/resume",
                    {"reasons": ["power_off", "system_sleep", "quiesce_failed"]},
                )
            elif action == "disable_missing_profile_accounts":
                result["result"] = _disable_missing_accounts(host, port, f)
            else:
                result["result"] = {"skipped": True, "reason": f"unknown_action:{action}"}
        except Exception as exc:
            result["error"] = str(exc)
        log_fix(result)
        results.append(result)
    return results


def dry_run_open_sample(
    host: str, port: int, payload: dict[str, Any], *, limit: int = 3
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in message_rows(payload)[:limit]:
        mid = msg.get("id")
        if mid is None:
            continue
        api = remote_api(host, port, "POST", f"/reach/messages/{mid}/open", {"dry_run": True})
        body = api.get("body") if isinstance(api, dict) else {}
        out.append(
            {
                "message_id": mid,
                "platform": msg.get("platform"),
                "reply_url": (msg.get("reply_url") or "")[:180],
                "curl_exit": api.get("curl_exit"),
                "ok": dig(body, "ok", default=False),
                "error": dig(body, "error") or dig(body, "detail"),
                "opened": dig(body, "opened"),
            }
        )
    return out


class LocalRecorder:
    def __init__(self, host: str) -> None:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        self.dir = LOCAL_LOG_ROOT / f"{host}-{stamp}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = self.dir / "events.jsonl"
        self.fixes = self.dir / "fixes.jsonl"
        self.summary = self.dir / "summary.json"
        self.latest = self.dir / "latest.json"
        (self.dir / "README.txt").write_text(
            "速影远程消息监测记录\n"
            f"host={host}\n"
            "events.jsonl = 每轮探测与发现问题\n"
            "fixes.jsonl = 自动修正动作\n"
            "summary.json = 结束汇总\n",
            encoding="utf-8",
        )

    def write_event(self, obj: dict[str, Any]) -> None:
        with self.events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.latest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_fix(self, obj: dict[str, Any]) -> None:
        with self.fixes.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def write_summary(self, obj: dict[str, Any]) -> None:
        self.summary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def print_tick(tick: int, findings: list[Finding], fixes: list[dict[str, Any]]) -> None:
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    print(
        f"[{_now_iso()}] tick={tick} findings={len(findings)} "
        f"error={len(errors)} warn={len(warns)} fixes={len(fixes)}"
    )
    for f in findings:
        if f.severity == "info" and f.code in {"messages_present", "messages_empty", "scan_active"}:
            continue
        mark = {"error": "E", "warn": "W", "info": "I"}.get(f.severity, "?")
        print(f"  {mark} {f.code}: {f.message}")
    for fx in fixes:
        payload = fx.get("result") or fx.get("error") or fx
        print(f"  FIX {fx.get('action')}: {json.dumps(payload, ensure_ascii=False)[:240]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="远程监测客户机消息接收与跳转回复")
    parser.add_argument("--host", required=True, help="SSH 别名，如 xlf-remote")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SEC, help="监测时长秒，默认 7200")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC, help="轮询间隔秒，默认 30")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--no-fix", action="store_true", help="只记录不自动修正")
    parser.add_argument("--no-open-check", action="store_true", help="关闭 dry_run 跳转校验")
    args = parser.parse_args()
    if args.interval < 5:
        print("ERROR: --interval 至少 5 秒", file=sys.stderr)
        return 2

    rec = LocalRecorder(args.host)
    apply = not args.no_fix
    open_check = not args.no_open_check
    deadline = time.time() + (0 if args.once else args.duration)
    tick = 0
    counters: dict[str, Any] = {"ticks": 0, "errors": 0, "warns": 0, "fixes": 0, "codes": {}}

    print(
        f"==> 监测开始 host={args.host} "
        f"duration={'once' if args.once else f'{args.duration}s'} "
        f"interval={args.interval}s apply_fix={apply}"
    )
    print(f"==> 本机记录: {rec.dir}")

    while True:
        tick += 1
        counters["ticks"] = tick
        payload = probe(args.host, args.port)
        findings = analyze(payload)
        open_results: list[dict[str, Any]] = []
        if open_check and not payload.get("error") and dig(payload, "health", "status") == "ok":
            try:
                open_results = dry_run_open_sample(args.host, args.port, payload)
                bad_open = [r for r in open_results if not r.get("ok")]
                if bad_open:
                    findings.append(
                        Finding(
                            code="jump_reply_dry_run_failed",
                            severity="error",
                            message=f"跳转回复 dry_run 失败 {len(bad_open)}/{len(open_results)}",
                            data={"results": bad_open},
                        )
                    )
            except Exception as exc:
                findings.append(
                    Finding(
                        code="jump_reply_check_error",
                        severity="warn",
                        message=f"跳转回复校验异常: {exc}",
                    )
                )

        for f in findings:
            counters["codes"][f.code] = counters["codes"].get(f.code, 0) + 1
            if f.severity == "error":
                counters["errors"] += 1
            elif f.severity == "warn":
                counters["warns"] += 1

        fixes = apply_fixes(
            args.host,
            args.port,
            findings,
            apply=apply,
            log_fix=rec.write_fix,
        )
        counters["fixes"] += len([f for f in fixes if not f.get("skipped")])

        event = {
            "at": _now_iso(),
            "tick": tick,
            "host": args.host,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "fixable": f.fixable,
                    "fix_action": f.fix_action,
                    "data": f.data,
                }
                for f in findings
            ],
            "open_check": open_results,
            "fixes": fixes,
            "snapshot": {
                "probed_at": payload.get("probed_at"),
                "app_path": payload.get("app_path"),
                "engine_pid": payload.get("engine_pid"),
                "health_status": dig(payload, "health", "status"),
                "ready": dig(payload, "readiness", "ready"),
                "pause_state": dig(payload, "pause_state", "state"),
                "worker_phase": dig(payload, "messages_status", "worker", "phase"),
                "scheduler_running": dig(payload, "messages_status", "worker", "scheduler_running"),
                "enabled_accounts": len([a for a in account_rows(payload) if a.get("enabled")]),
                "message_count": len(message_rows(payload)),
                "error": payload.get("error"),
            },
        }
        rec.write_event(event)
        print_tick(tick, findings, fixes)

        if args.once or time.time() >= deadline:
            break
        time.sleep(args.interval)

    summary = {
        "finished_at": _now_iso(),
        "host": args.host,
        "log_dir": str(rec.dir),
        "counters": counters,
        "apply_fix": apply,
    }
    rec.write_summary(summary)
    print("==> 监测结束")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if counters["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
