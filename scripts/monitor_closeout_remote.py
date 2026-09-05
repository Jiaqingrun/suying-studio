#!/usr/bin/env python3
"""关账真机实验 · 远程客户机监控（运维 Mac 侧，经 SSH 探针）。

默认只采样落盘，不做自动修复（实验期安全）。对应 CLOSEOUT_ACCEPTANCE A–F 证据：

  A  pause / PAUSED_BLOCKED / power_off / accepts_new_work
  B/C 最近 ready 成片（display_no / 画幅 / 旁白字幕 / tts）
  D  消息账号 readonly_verified + last_status
  E  /voice/tts clone 可用性
  F  向量/语义运行态（轻量快照，不触发全库 VLM）

用法（先准备好，等用户说「开始」再跑）:
  python3 scripts/monitor_closeout_remote.py --host xlf-remote --once
  python3 scripts/monitor_closeout_remote.py --host xlf-remote --duration 7200 --interval 20
  ./scripts/monitor-closeout-remote.sh --host xlf-remote --start   # 后台
  ./scripts/monitor-closeout-remote.sh --host xlf-remote --status
  ./scripts/monitor-closeout-remote.sh --host xlf-remote --stop

日志默认: ~/Suying/logs/closeout-remote-monitor/<host>/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DURATION_SEC = 3 * 60 * 60
DEFAULT_INTERVAL_SEC = 20
PORT_DEFAULT = 8766
LOCAL_LOG_ROOT = Path.home() / "Suying" / "logs" / "closeout-remote-monitor"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def run_ssh(host: str, remote: str, *, timeout: int = 90) -> tuple[int, str, str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "ServerAliveInterval=20",
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
TMP="$(mktemp -d /tmp/suying-closeout-probe.XXXXXX)"
cleanup() {{ rm -rf "$TMP"; }}
trap cleanup EXIT
fetch() {{
  local path="$1"
  local out="$2"
  curl -fsS --max-time 8 "${{API}}${{path}}" >"$out" 2>/dev/null \\
    || echo '{{"error":"unreachable"}}' >"$out"
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
BUNDLE_VERSION=""
BUNDLE_FLAVOR=""
if [[ -n "$APP" ]]; then
  BUNDLE_VERSION="$(cat "$APP/Contents/Resources/runtime/BUNDLE_VERSION" 2>/dev/null || true)"
  BUNDLE_FLAVOR="$(cat "$APP/Contents/Resources/runtime/BUNDLE_FLAVOR" 2>/dev/null || true)"
fi
ENGINE_PID="$(lsof -t -iTCP:${{PORT}} -sTCP:LISTEN 2>/dev/null | head -1 || true)"
fetch /health "$TMP/health.json"
fetch /ops/runtime-health "$TMP/runtime.json"
fetch /system/pause-state "$TMP/pause.json"
fetch /voice/tts "$TMP/voice.json"
fetch /reach/message-accounts "$TMP/reach_accounts.json"
fetch /content/message-accounts "$TMP/content_accounts.json"
fetch /reach/messages/status "$TMP/msg_status.json"
fetch /outputs "$TMP/outputs.json"
fetch /assets/vectorization-status "$TMP/vector.json"
export APP ENGINE_PID BUNDLE_VERSION BUNDLE_FLAVOR TMP
python3 - <<'PY'
import json, os
from datetime import datetime
from pathlib import Path

tmp = Path(os.environ["TMP"])

def load(name):
    try:
        return json.loads((tmp / name).read_text(encoding="utf-8"))
    except Exception as exc:
        return {{"error": f"parse_failed:{{exc}}"}}

outputs = load("outputs.json")
if isinstance(outputs, list):
    slim = []
    for row in outputs[:40]:
        if not isinstance(row, dict):
            continue
        slim.append({{
            "id": row.get("id"),
            "display_no": row.get("display_no") or row.get("serial"),
            "state": row.get("state"),
            "job_id": row.get("job_id"),
            "has_voice": row.get("has_voice"),
            "subtitle_burned": row.get("subtitle_burned"),
            "tts_provider": row.get("tts_provider") or row.get("effective_tts_provider"),
            "frame": row.get("frame") or row.get("aspect") or row.get("orientation"),
            "width": row.get("width"),
            "height": row.get("height"),
            "ready_gate_ok": row.get("ready_gate_ok"),
            "created_at": row.get("created_at") or row.get("updated_at"),
        }})
    outputs = slim

def account_slim(payload):
    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return payload
    out = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        out.append({{
            "id": a.get("id"),
            "platform": a.get("platform"),
            "display_name": a.get("display_name"),
            "enabled": a.get("enabled"),
            "readonly_verified": a.get("readonly_verified"),
            "reply_supported": a.get("reply_supported"),
            "last_status": a.get("last_status"),
            "last_error": (str(a.get("last_error") or ""))[:160] or None,
            "last_scanned_at": a.get("last_scanned_at"),
        }})
    return {{"accounts": out, "count": len(out)}}

print(json.dumps({{
    "probed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "app_path": os.environ.get("APP") or None,
    "bundle_version": os.environ.get("BUNDLE_VERSION") or None,
    "bundle_flavor": os.environ.get("BUNDLE_FLAVOR") or None,
    "engine_pid": os.environ.get("ENGINE_PID") or None,
    "health": load("health.json"),
    "runtime_health": load("runtime.json"),
    "pause_state": load("pause.json"),
    "voice_tts": load("voice.json"),
    "reach_message_accounts": account_slim(load("reach_accounts.json")),
    "content_message_accounts": account_slim(load("content_accounts.json")),
    "reach_messages_status": load("msg_status.json"),
    "outputs_recent": outputs,
    "vectorization": load("vector.json"),
}}, ensure_ascii=False))
PY
"""
    code, out, err = run_ssh(host, remote)
    data = _parse_last_json(out)
    if data is None:
        return {
            "probed_at": _now_iso(),
            "error": "probe_failed",
            "ssh_code": code,
            "stderr": (err or "")[:800],
            "stdout_tail": (out or "")[-800:],
        }
    data["ssh_code"] = code
    if err.strip():
        data["ssh_stderr_tail"] = err.strip()[-400:]
    return data


def classify_anomalies(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Map probe → CLOSEOUT-relevant anomaly codes (record only)."""
    notes: list[dict[str, Any]] = []
    if snap.get("error"):
        notes.append({"code": "SSH_OR_PROBE", "severity": "A", "detail": snap.get("error")})
        return notes

    health = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    runtime = snap.get("runtime_health") if isinstance(snap.get("runtime_health"), dict) else {}
    pause = snap.get("pause_state") if isinstance(snap.get("pause_state"), dict) else {}
    if pause.get("error") and not dig(runtime, "pause"):
        pause = dig(runtime, "pause", default={}) or {}

    if dig(health, "status") not in (None, "ok") and "error" not in health:
        notes.append(
            {
                "code": "HEALTH_NOT_OK",
                "severity": "A",
                "detail": f"status={dig(health, 'status')}",
            }
        )
    if health.get("error") == "unreachable" or runtime.get("error") == "unreachable":
        notes.append({"code": "ENGINE_UNREACHABLE", "severity": "A", "detail": "8766"})

    reasons = dig(pause, "pause_reasons", default=dig(pause, "reasons", default=[]))
    state = dig(pause, "state", default=dig(runtime, "pause", "state"))
    is_paused = dig(pause, "is_paused", default=dig(runtime, "pause", "is_paused"))
    if state == "PAUSED_BLOCKED" or (
        is_paused and (reasons in ([], None, {}, "")) and state not in (None, "ACTIVE")
    ):
        notes.append(
            {
                "code": "PAUSE_ORPHAN_OR_BLOCKED",
                "severity": "A",
                "detail": f"state={state} reasons={reasons}",
            }
        )
    reason_text = json.dumps(reasons, ensure_ascii=False) if reasons is not None else ""
    if "power_off" in reason_text.lower() or dig(pause, "power_off"):
        notes.append({"code": "POWER_OFF_PRESENT", "severity": "A", "detail": reason_text[:200]})

    qps = dig(runtime, "health", "qps", default=dig(runtime, "health", "recent_qps"))
    try:
        if qps is not None and float(qps) > 1.5:
            notes.append({"code": "HEALTH_QPS_HIGH", "severity": "A", "detail": f"qps={qps}"})
    except (TypeError, ValueError):
        pass

    # D · message calibration
    for scope, key in (
        ("video", "reach_message_accounts"),
        ("content", "content_message_accounts"),
    ):
        payload = snap.get(key) if isinstance(snap.get(key), dict) else {}
        for acc in payload.get("accounts") or []:
            if not isinstance(acc, dict) or not acc.get("enabled", True):
                continue
            plat = str(acc.get("platform") or "")
            verified = acc.get("readonly_verified")
            status = str(acc.get("last_status") or "")
            if verified is False and status in ("completed", "ok", "no_unread"):
                notes.append(
                    {
                        "code": "MSG_FALSE_COMPLETED",
                        "severity": "D",
                        "detail": f"{scope}/{plat} id={acc.get('id')} status={status}",
                    }
                )

    voice = snap.get("voice_tts") if isinstance(snap.get("voice_tts"), dict) else {}
    if voice and not voice.get("error"):
        eff = dig(voice, "effective_provider", default=dig(voice, "provider"))
        if eff == "clone" and dig(voice, "clone_available") is False:
            notes.append(
                {
                    "code": "CLONE_UNAVAILABLE",
                    "severity": "E",
                    "detail": "effective_provider=clone but clone_available=false",
                }
            )

    return notes


def summarize(snap: dict[str, Any], anomalies: list[dict[str, Any]]) -> str:
    health = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    runtime = snap.get("runtime_health") if isinstance(snap.get("runtime_health"), dict) else {}
    pause = snap.get("pause_state") if isinstance(snap.get("pause_state"), dict) else {}
    if pause.get("error"):
        pause = dig(runtime, "pause", default={}) or {}
    voice = snap.get("voice_tts") if isinstance(snap.get("voice_tts"), dict) else {}
    outs = snap.get("outputs_recent") if isinstance(snap.get("outputs_recent"), list) else []
    ready = [o for o in outs if isinstance(o, dict) and str(o.get("state") or "") == "ready"]
    reach = dig(snap, "reach_message_accounts", "accounts", default=[]) or []
    content = dig(snap, "content_message_accounts", "accounts", default=[]) or []
    unverified = [
        f"{a.get('platform')}:{a.get('last_status')}"
        for a in list(reach) + list(content)
        if isinstance(a, dict) and a.get("readonly_verified") is False and a.get("enabled", True)
    ]
    lines = [
        f"[{snap.get('probed_at') or _now_iso()}] "
        f"v={snap.get('bundle_version') or dig(health, 'engine_version') or '?'} "
        f"flavor={snap.get('bundle_flavor') or '?'} "
        f"health={dig(health, 'status', default='?')} "
        f"accepts={dig(runtime, 'accepts_new_work', default='?')} "
        f"pause={dig(pause, 'state', default=dig(runtime, 'pause', 'state', default='?'))} "
        f"qps={dig(runtime, 'health', 'qps', default=dig(runtime, 'health', 'recent_qps', default='?'))} "
        f"ready_outputs={len(ready)} "
        f"tts={dig(voice, 'effective_provider', default='?')}/clone={dig(voice, 'clone_available', default='?')} "
        f"anomalies={len(anomalies)}"
    ]
    if unverified:
        lines.append("  unverified_msg: " + ", ".join(unverified[:12]))
    for a in anomalies[:8]:
        lines.append(f"  ! {a.get('code')} [{a.get('severity')}] {a.get('detail')}")
    return "\n".join(lines)


def ensure_dirs(host: str) -> Path:
    root = LOCAL_LOG_ROOT / host.replace("/", "_")
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_sample(root: Path, snap: dict[str, Any], anomalies: list[dict[str, Any]]) -> None:
    record = {"snap": snap, "anomalies": anomalies, "logged_at": _now_iso()}
    with (root / "samples.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    (root / "latest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if anomalies:
        with (root / "anomalies.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"logged_at": _now_iso(), "anomalies": anomalies, "probed_at": snap.get("probed_at")},
                    ensure_ascii=False,
                )
                + "\n"
            )
    # Human-readable running log
    with (root / "monitor.log").open("a", encoding="utf-8") as fh:
        fh.write(summarize(snap, anomalies) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="关账真机实验 · 远程监控（只读采样）")
    ap.add_argument("--host", required=True, help="SSH 别名，如 xlf-remote")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--once", action="store_true", help="只采一次")
    ap.add_argument("--duration", type=int, default=DEFAULT_DURATION_SEC, help="总时长秒")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC, help="间隔秒")
    ap.add_argument("--json", action="store_true", help="每次采样向 stdout 打完整 JSON")
    args = ap.parse_args()

    if args.interval < 5:
        print("ERROR: --interval 至少 5 秒，避免打扰客户机", file=sys.stderr)
        return 2

    root = ensure_dirs(args.host)
    started = time.time()
    print(
        f"closeout-monitor host={args.host} port={args.port} "
        f"log={root} once={args.once} duration={args.duration}s interval={args.interval}s",
        flush=True,
    )
    n = 0
    while True:
        n += 1
        snap = probe(args.host, args.port)
        anomalies = classify_anomalies(snap)
        write_sample(root, snap, anomalies)
        print(summarize(snap, anomalies), flush=True)
        if args.json:
            print(json.dumps({"snap": snap, "anomalies": anomalies}, ensure_ascii=False), flush=True)
        if args.once:
            break
        if time.time() - started >= max(1, args.duration):
            print(f"duration reached after {n} samples", flush=True)
            break
        time.sleep(args.interval)

    print(f"done samples={n} latest={root / 'latest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
