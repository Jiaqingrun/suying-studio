#!/usr/bin/env python3
"""客户机真机 E2E：生产 → 审片/READY → publish_pack → 入队 → 自动上传（可多轮）。

设计目标：落盘「最完整」运行证据，供事后对照通道占用与流程细节：
  - 每轮墙钟分段（创建 / 领取 / 旁白·TTS·渲染 / READY / 打包 / 入队 / 上传）
  - 资源槽时间线（produce/render/tts/publish/ollama_heavy 占用秒、持有者、切换事件）
  - JobEvent 全量、pipeline phase、Ollama 熔断与健康分层
  - 上传 phase / need_human / outcome

用法（建议在客户机本机跑，或经 remote-e2e-acceptance.sh 投递）:
  python3 scripts/remote_e2e_acceptance.py --rounds 3 --accept-risk
  python3 scripts/remote_e2e_acceptance.py --rounds 3 --accept-risk \\
      --chrome-profile '视频号-5331' --platform channels --interval 2

输出目录默认: ~/Suying/logs/e2e-acceptance/<run_id>/
  summary.json  REPORT.md  samples.jsonl  slot_events.jsonl  rounds/R0N.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_DEFAULT = "http://127.0.0.1:8766"
DB_DEFAULT = Path.home() / "Suying" / "data" / "montage.db"
LOG_ROOT_DEFAULT = Path.home() / "Suying" / "logs" / "e2e-acceptance"
POOLS = ("produce", "render", "tts", "publish", "ollama_heavy")
JOB_TERMINAL = {
    "completed",
    "failed",
    "circuit_open",
    "cancelled",
    "canceled",
    "paused",
}
UPLOAD_TERMINAL_PHASES = {"done", "error", "idle", "cancelled", "canceled"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe(obj: Any, limit: int = 4000) -> Any:
    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        raw = repr(obj)
    if len(raw) > limit:
        return json.loads(json.dumps({"_truncated": True, "preview": raw[:limit]}, ensure_ascii=False))
    return obj


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "suying-e2e-acceptance/1.0"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), (json.loads(raw) if raw else {})
            except json.JSONDecodeError:
                return int(resp.status), {"_raw": raw[:2000]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw) if raw else {"detail": str(e)}
        except json.JSONDecodeError:
            payload = {"_raw": raw[:2000]}
        return int(e.code), payload
    except Exception as e:  # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


@dataclass
class SlotHold:
    pool: str
    holder: str
    started_at: str
    started_mono: float
    ended_at: str | None = None
    duration_sec: float | None = None


@dataclass
class RoundResult:
    round_index: int
    ok: bool = False
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_sec: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    marks: dict[str, str] = field(default_factory=dict)
    job_id: int | None = None
    job_status: str = ""
    output_id: int | None = None
    display_no: int | None = None
    pack_dir: str = ""
    queue_id: int | None = None
    queue_status: str = ""
    upload_phase: str = ""
    upload_outcome: str = ""
    upload_need_human: bool = False
    upload_error: str = ""
    slot_holds: list[dict[str, Any]] = field(default_factory=list)
    slot_occupied_sec: dict[str, float] = field(default_factory=dict)
    phase_seen: list[str] = field(default_factory=list)
    job_events: list[dict[str, Any]] = field(default_factory=list)
    sample_count: int = 0
    anomalies: list[str] = field(default_factory=list)
    output_snapshot: dict[str, Any] = field(default_factory=dict)
    upload_final: dict[str, Any] = field(default_factory=dict)


class Recorder:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "rounds").mkdir(exist_ok=True)
        self.samples_path = self.out_dir / "samples.jsonl"
        self.slot_events_path = self.out_dir / "slot_events.jsonl"
        self.console_path = self.out_dir / "console.log"
        self._slot_open: dict[str, SlotHold] = {}
        self._slot_history: list[SlotHold] = []
        self._last_used: dict[str, int] = {p: 0 for p in POOLS}

    def log(self, msg: str) -> None:
        line = f"[{_now_iso()}] {msg}"
        print(line, flush=True)
        with self.console_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_sample(self, row: dict[str, Any]) -> None:
        with self.samples_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def write_slot_event(self, row: dict[str, Any]) -> None:
        with self.slot_events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def observe_gate(self, gate: dict[str, Any], *, round_index: int, job_id: int | None) -> None:
        pools = gate.get("pools") if isinstance(gate, dict) else None
        if not isinstance(pools, dict):
            return
        mono = time.monotonic()
        iso = _now_iso()
        for name in POOLS:
            info = pools.get(name) or {}
            used = int(info.get("used") or 0)
            holders = info.get("holders") or []
            detail = info.get("holders_detail") or []
            holder_key = ",".join(str(h) for h in holders) if holders else (
                ",".join(str(d.get("holder") or d) for d in detail) if detail else ""
            )
            prev = self._last_used.get(name, 0)
            open_hold = self._slot_open.get(name)
            if used > 0 and open_hold is None:
                hold = SlotHold(
                    pool=name,
                    holder=holder_key or f"used={used}",
                    started_at=iso,
                    started_mono=mono,
                )
                self._slot_open[name] = hold
                self.write_slot_event(
                    {
                        "ts": iso,
                        "event": "acquire",
                        "pool": name,
                        "holder": hold.holder,
                        "used": used,
                        "round": round_index,
                        "job_id": job_id,
                        "detail": _safe(detail, 1500),
                    }
                )
            elif used == 0 and open_hold is not None:
                open_hold.ended_at = iso
                open_hold.duration_sec = round(mono - open_hold.started_mono, 3)
                self._slot_history.append(open_hold)
                self.write_slot_event(
                    {
                        "ts": iso,
                        "event": "release",
                        "pool": name,
                        "holder": open_hold.holder,
                        "duration_sec": open_hold.duration_sec,
                        "round": round_index,
                        "job_id": job_id,
                    }
                )
                del self._slot_open[name]
            elif used > 0 and open_hold is not None and holder_key and holder_key != open_hold.holder:
                # holder changed while still occupied
                open_hold.ended_at = iso
                open_hold.duration_sec = round(mono - open_hold.started_mono, 3)
                self._slot_history.append(open_hold)
                self.write_slot_event(
                    {
                        "ts": iso,
                        "event": "holder_switch",
                        "pool": name,
                        "from_holder": open_hold.holder,
                        "to_holder": holder_key,
                        "duration_sec": open_hold.duration_sec,
                        "round": round_index,
                        "job_id": job_id,
                    }
                )
                hold = SlotHold(
                    pool=name,
                    holder=holder_key,
                    started_at=iso,
                    started_mono=mono,
                )
                self._slot_open[name] = hold
            elif used != prev:
                self.write_slot_event(
                    {
                        "ts": iso,
                        "event": "used_change",
                        "pool": name,
                        "used": used,
                        "prev_used": prev,
                        "holder": holder_key,
                        "round": round_index,
                        "job_id": job_id,
                    }
                )
            self._last_used[name] = used

    def close_open_holds(self, *, round_index: int, job_id: int | None) -> list[dict[str, Any]]:
        mono = time.monotonic()
        iso = _now_iso()
        for name, hold in list(self._slot_open.items()):
            hold.ended_at = iso
            hold.duration_sec = round(mono - hold.started_mono, 3)
            self._slot_history.append(hold)
            self.write_slot_event(
                {
                    "ts": iso,
                    "event": "force_close",
                    "pool": name,
                    "holder": hold.holder,
                    "duration_sec": hold.duration_sec,
                    "round": round_index,
                    "job_id": job_id,
                }
            )
            del self._slot_open[name]
        return [asdict(h) for h in self._slot_history]

    def occupied_sec_for_round(self, holds: list[dict[str, Any]], round_index: int) -> dict[str, float]:
        # holds in this recorder are global; filter by events already tagged — use history since last clear
        out = {p: 0.0 for p in POOLS}
        for h in holds:
            pool = str(h.get("pool") or "")
            if pool in out and h.get("duration_sec") is not None:
                out[pool] = round(out[pool] + float(h["duration_sec"]), 3)
        return out


class E2ERunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.api = args.api.rstrip("/")
        self.db_path = Path(args.db)
        self.run_id = args.run_id or f"e2e-{_utc_stamp()}-r{args.rounds}"
        self.out_dir = Path(args.out_dir) / self.run_id
        self.rec = Recorder(self.out_dir)
        self.meta: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": _now_iso(),
            "args": vars(args),
            "host": os.uname().nodename if hasattr(os, "uname") else "",
        }
        self.rounds: list[RoundResult] = []

    # ---------- HTTP / DB helpers ----------
    def get(self, path: str, *, timeout: float = 20.0) -> tuple[int, Any]:
        return http_json("GET", f"{self.api}{path}", timeout=timeout)

    def post(self, path: str, body: dict[str, Any] | None = None, *, timeout: float = 90.0) -> tuple[int, Any]:
        return http_json("POST", f"{self.api}{path}", body or {}, timeout=timeout)

    def db(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def job_row(self, job_id: int) -> dict[str, Any] | None:
        with self.db() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def job_events(self, job_id: int, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self.db() as con:
            rows = con.execute(
                "SELECT id, level, message, payload_json, created_at FROM job_events "
                "WHERE job_id=? AND id>? ORDER BY id ASC",
                (job_id, after_id),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                pj = d.get("payload_json")
                if isinstance(pj, str) and pj:
                    try:
                        d["payload_json"] = json.loads(pj)
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out

    def latest_output_for_job(self, job_id: int) -> dict[str, Any] | None:
        with self.db() as con:
            row = con.execute(
                "SELECT * FROM render_outputs WHERE job_id=? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            qc = d.get("qc_json")
            if isinstance(qc, str) and qc:
                try:
                    d["qc_json"] = json.loads(qc)
                except json.JSONDecodeError:
                    pass
            return d

    def queue_row(self, queue_id: int) -> dict[str, Any] | None:
        with self.db() as con:
            row = con.execute("SELECT * FROM reach_queue WHERE id=?", (queue_id,)).fetchone()
            return dict(row) if row else None

    # ---------- snapshot ----------
    def snapshot(self, *, round_index: int, job_id: int | None, label: str) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "ts": _now_iso(),
            "mono": time.monotonic(),
            "round": round_index,
            "job_id": job_id,
            "label": label,
        }
        endpoints = [
            ("health", "/health"),
            ("pause", "/system/pause-state"),
            ("gate", "/ops/resource-gate"),
            ("pipeline", "/jobs/pipeline"),
            ("ollama", "/health/ollama"),
            ("runtime_health", "/ops/runtime-health"),
            ("upload", "/reach/auto-upload/status"),
        ]
        for key, path in endpoints:
            code, body = self.get(path, timeout=12.0)
            snap[key] = {"http": code, "body": _safe(body, 6000 if key != "ollama" else 8000)}
        if job_id:
            snap["job_db"] = _safe(self.job_row(job_id), 4000)
            snap["job_events_tail"] = self.job_events(job_id)[-8:]
            out = self.latest_output_for_job(job_id)
            if out:
                slim = {
                    k: out.get(k)
                    for k in (
                        "id",
                        "state",
                        "display_no",
                        "pack_status",
                        "pack_dir",
                        "output_path",
                        "created_at",
                        "serial",
                    )
                }
                qc = out.get("qc_json") if isinstance(out.get("qc_json"), dict) else {}
                slim["ready_gate"] = (qc or {}).get("ready_gate")
                snap["output"] = slim
        gate_body = (snap.get("gate") or {}).get("body") or {}
        if isinstance(gate_body, dict) and "pools" in gate_body:
            self.rec.observe_gate(gate_body, round_index=round_index, job_id=job_id)
        elif isinstance(gate_body, dict) and gate_body.get("pools") is None and "error" not in gate_body:
            # some responses nest differently
            self.rec.observe_gate(gate_body, round_index=round_index, job_id=job_id)
        self.rec.write_sample(snap)
        return snap

    def preflight(self) -> dict[str, Any]:
        self.rec.log("=== preflight ===")
        info: dict[str, Any] = {"ts": _now_iso()}
        code, health = self.get("/health")
        info["health_http"] = code
        info["engine_version"] = (health or {}).get("engine_version") if isinstance(health, dict) else None
        info["customer"] = (health or {}).get("active_customer") if isinstance(health, dict) else None
        info["workspace_state"] = (health or {}).get("workspace_state") if isinstance(health, dict) else None
        code, pause = self.get("/system/pause-state")
        info["pause"] = _safe(pause)
        accepts = bool((pause or {}).get("accepts_new_work")) if isinstance(pause, dict) else False
        info["accepts_new_work"] = accepts
        code, gate = self.get("/ops/resource-gate")
        info["gate"] = _safe(gate)
        code, ollama = self.get("/health/ollama")
        if isinstance(ollama, dict):
            info["ollama"] = {
                "ready": ollama.get("ready"),
                "reachable": ollama.get("reachable"),
                "circuit_open": ollama.get("circuit_open"),
                "inference_available": ollama.get("inference_available"),
                "status_layers": ollama.get("status_layers"),
                "embed_gateway": _safe(ollama.get("embed_gateway"), 1500),
                "message": ollama.get("message"),
            }
        code, profiles = self.get("/reach/chrome-profiles")
        names = []
        if isinstance(profiles, dict):
            for p in profiles.get("profiles") or []:
                names.append(
                    {
                        "name": p.get("name"),
                        "platform": p.get("platform"),
                        "login_status": p.get("login_status"),
                    }
                )
        info["chrome_profiles"] = names
        if self.args.chrome_profile and not any(p.get("name") == self.args.chrome_profile for p in names):
            info["chrome_warning"] = f"profile not listed: {self.args.chrome_profile}"
        if not accepts:
            raise RuntimeError(f"runtime does not accept_new_work: {pause}")
        pools = ((gate or {}).get("pools") or {}) if isinstance(gate, dict) else {}
        busy = {k: v.get("used") for k, v in pools.items() if isinstance(v, dict) and int(v.get("used") or 0) > 0}
        if busy and not self.args.allow_busy_slots:
            raise RuntimeError(f"slots busy at start: {busy} (pass --allow-busy-slots to override)")
        self.meta["preflight"] = info
        (self.out_dir / "preflight.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        self.rec.log(
            f"engine={info.get('engine_version')} customer={info.get('customer')} "
            f"accepts={accepts} ollama_ready={(info.get('ollama') or {}).get('ready')}"
        )
        return info

    # ---------- round phases ----------
    def create_job(self) -> int:
        body = {
            "mode": "count",
            "target_count": int(self.args.target_count),
            "template_name": self.args.template,
            "theme": self.args.theme,
            "category": self.args.category,
            "orientation": self.args.orientation,
            "rush": True,
            "use_active_rule": True,
        }
        if self.args.customer:
            body["customer_name"] = self.args.customer
        code, resp = self.post("/jobs", body, timeout=60)
        if code >= 400 or not isinstance(resp, dict) or not resp.get("id"):
            raise RuntimeError(f"create job failed http={code} body={resp}")
        return int(resp["id"])

    def wait_production(self, rr: RoundResult) -> None:
        assert rr.job_id is not None
        deadline = time.monotonic() + float(self.args.produce_timeout)
        last_event_id = 0
        phases: list[str] = []
        t0 = time.monotonic()
        marked_claimed = False
        while time.monotonic() < deadline:
            snap = self.snapshot(round_index=rr.round_index, job_id=rr.job_id, label="produce_poll")
            rr.sample_count += 1
            job = self.job_row(rr.job_id) or {}
            status = str(job.get("status") or "")
            rr.job_status = status
            pipe = ((snap.get("pipeline") or {}).get("body") or {}) if isinstance(snap.get("pipeline"), dict) else {}
            running = pipe.get("running") if isinstance(pipe, dict) else None
            phase = None
            if isinstance(running, dict) and running.get("id") == rr.job_id:
                phase = running.get("phase")
            if phase and (not phases or phases[-1] != phase):
                phases.append(str(phase))
                rr.marks[f"phase:{phase}"] = _now_iso()
                self.rec.log(f"R{rr.round_index} job#{rr.job_id} phase={phase} status={status}")
            events = self.job_events(rr.job_id, after_id=last_event_id)
            for ev in events:
                last_event_id = max(last_event_id, int(ev["id"]))
                rr.job_events.append(ev)
                msg = str(ev.get("message") or "")
                self.rec.log(f"R{rr.round_index} event#{ev['id']} [{ev.get('level')}] {msg[:180]}")
                if not marked_claimed and status == "running":
                    rr.marks["job_claimed"] = _now_iso()
                    rr.timings["create_to_claim_sec"] = round(time.monotonic() - t0, 3)
                    marked_claimed = True
            out = self.latest_output_for_job(rr.job_id)
            if out and out.get("state") == "ready":
                rr.output_id = int(out["id"])
                rr.display_no = int(out.get("display_no") or out["id"])
                rr.pack_dir = str(out.get("pack_dir") or "")
                rr.output_snapshot = {
                    k: out.get(k)
                    for k in (
                        "id",
                        "state",
                        "display_no",
                        "pack_status",
                        "pack_dir",
                        "output_path",
                        "serial",
                        "orientation",
                    )
                }
                qc = out.get("qc_json") if isinstance(out.get("qc_json"), dict) else {}
                rr.output_snapshot["ready_gate"] = (qc or {}).get("ready_gate")
                if "output_ready" not in rr.marks:
                    rr.marks["output_ready"] = _now_iso()
                    rr.timings["create_to_ready_sec"] = round(time.monotonic() - t0, 3)
                    self.rec.log(
                        f"R{rr.round_index} READY output#{rr.output_id} "
                        f"display_no={rr.display_no} pack={out.get('pack_status')}"
                    )
            if status in JOB_TERMINAL:
                if status == "completed" and not rr.output_id:
                    # completed but output not yet visible — one more beat
                    time.sleep(1.0)
                    out = self.latest_output_for_job(rr.job_id)
                    if out:
                        rr.output_id = int(out["id"])
                        rr.display_no = int(out.get("display_no") or out["id"])
                        rr.pack_dir = str(out.get("pack_dir") or "")
                rr.phase_seen = phases
                rr.timings["produce_wall_sec"] = round(time.monotonic() - t0, 3)
                if status != "completed":
                    rr.anomalies.append(f"job_terminal_status={status}")
                if status == "completed" and not rr.output_id:
                    raise RuntimeError(f"job {rr.job_id} completed but no render_output")
                if status != "completed":
                    raise RuntimeError(f"job {rr.job_id} ended as {status}")
                return
            time.sleep(float(self.args.interval))
        rr.phase_seen = phases
        raise TimeoutError(f"produce timeout after {self.args.produce_timeout}s job={rr.job_id} status={rr.job_status}")

    def ensure_pack_and_enqueue(self, rr: RoundResult) -> None:
        assert rr.output_id is not None
        t0 = time.monotonic()
        code, pack = self.post(f"/outputs/{rr.output_id}/publish-pack", {}, timeout=180)
        rr.marks["publish_pack"] = _now_iso()
        if code >= 400 or not (isinstance(pack, dict) and pack.get("ok")):
            raise RuntimeError(f"publish-pack failed http={code} body={pack}")
        manifest = pack.get("manifest") if isinstance(pack, dict) else {}
        pack_dir = ""
        if isinstance(manifest, dict):
            pack_dir = str(manifest.get("pack_dir") or "")
        if not pack_dir:
            out = self.latest_output_for_job(rr.job_id or 0) or {}
            pack_dir = str(out.get("pack_dir") or "")
        if not pack_dir:
            raise RuntimeError("pack_dir missing after publish-pack")
        rr.pack_dir = pack_dir
        rr.timings["pack_sec"] = round(time.monotonic() - t0, 3)
        self.rec.log(f"R{rr.round_index} pack_dir={pack_dir}")

        t1 = time.monotonic()
        body = {
            "pack_dir": pack_dir,
            "platforms": [self.args.platform],
            "output_id": rr.output_id,
            "mark_ready": True,
        }
        code, enq = self.post("/reach/queue/from-pack", body, timeout=90)
        if code >= 400 or not (isinstance(enq, dict) and enq.get("ok")):
            raise RuntimeError(f"from-pack enqueue failed http={code} body={enq}")
        items = enq.get("items") or []
        if not items:
            raise RuntimeError(f"from-pack returned no items: {enq}")
        item = items[0]
        rr.queue_id = int(item["id"])
        rr.queue_status = str(item.get("status") or "")
        rr.marks["enqueued"] = _now_iso()
        rr.timings["enqueue_sec"] = round(time.monotonic() - t1, 3)
        self.rec.log(f"R{rr.round_index} queue#{rr.queue_id} status={rr.queue_status}")

    def wait_upload(self, rr: RoundResult) -> None:
        if self.args.skip_upload:
            rr.upload_outcome = "skipped"
            return
        if not self.args.accept_risk:
            raise RuntimeError("upload requires --accept-risk")
        assert rr.queue_id is not None
        # cancel leftover done-state holder if needed is not required; start with queue_id
        t0 = time.monotonic()
        body = {
            "queue_id": rr.queue_id,
            "chrome_profile": self.args.chrome_profile,
            "platform": self.args.platform,
            "accept_risk": True,
            "timeout_sec": float(self.args.upload_timeout),
            "dry_run": bool(self.args.dry_run_upload),
        }
        code, start = self.post("/reach/auto-upload/start", body, timeout=60)
        rr.marks["upload_start"] = _now_iso()
        if code >= 400 or not (isinstance(start, dict) and start.get("ok")):
            raise RuntimeError(f"auto-upload start failed http={code} body={start}")
        self.rec.log(
            f"R{rr.round_index} upload started run={start.get('run_id')} phase={start.get('phase')}"
        )
        deadline = time.monotonic() + float(self.args.upload_timeout) + 60.0
        last_phase = ""
        while time.monotonic() < deadline:
            snap = self.snapshot(round_index=rr.round_index, job_id=rr.job_id, label="upload_poll")
            rr.sample_count += 1
            up = ((snap.get("upload") or {}).get("body") or {}) if isinstance(snap.get("upload"), dict) else {}
            if not isinstance(up, dict):
                up = {}
            phase = str(up.get("phase") or "")
            qid = up.get("queue_id")
            if phase and phase != last_phase:
                rr.marks[f"upload_phase:{phase}"] = _now_iso()
                self.rec.log(
                    f"R{rr.round_index} upload phase={phase} q={qid} "
                    f"msg={up.get('message')} need_human={up.get('need_human')} err={up.get('error')}"
                )
                last_phase = phase
            rr.upload_phase = phase
            rr.upload_need_human = bool(up.get("need_human"))
            rr.upload_error = str(up.get("error") or "")
            qrow = self.queue_row(rr.queue_id) or {}
            rr.queue_status = str(qrow.get("status") or rr.queue_status)
            # terminal: this queue published / failed, or need_human, or phase done for this queue
            if rr.upload_need_human:
                rr.upload_outcome = "need_human"
                rr.anomalies.append("upload_need_human")
                rr.upload_final = _safe(up, 3000)
                rr.timings["upload_sec"] = round(time.monotonic() - t0, 3)
                return
            if qid == rr.queue_id and phase in UPLOAD_TERMINAL_PHASES:
                rr.upload_final = _safe(up, 3000)
                rr.timings["upload_sec"] = round(time.monotonic() - t0, 3)
                if rr.queue_status == "published" or str(up.get("message") or "").lower() in {
                    "completed",
                    "published",
                }:
                    rr.upload_outcome = "published"
                elif phase == "error" or rr.upload_error:
                    rr.upload_outcome = "error"
                    raise RuntimeError(f"upload error: {rr.upload_error or up}")
                else:
                    rr.upload_outcome = phase
                if rr.queue_status != "published" and rr.upload_outcome == "published":
                    # status lag
                    time.sleep(1.0)
                    qrow = self.queue_row(rr.queue_id) or {}
                    rr.queue_status = str(qrow.get("status") or "")
                if rr.upload_outcome == "published" and rr.queue_status != "published":
                    rr.anomalies.append(f"upload_done_but_queue_status={rr.queue_status}")
                return
            if rr.queue_status in {"published", "failed", "error"}:
                rr.upload_final = _safe(up, 3000)
                rr.timings["upload_sec"] = round(time.monotonic() - t0, 3)
                rr.upload_outcome = rr.queue_status
                if rr.queue_status != "published":
                    raise RuntimeError(f"queue ended as {rr.queue_status}")
                return
            time.sleep(float(self.args.interval))
        raise TimeoutError(f"upload timeout queue={rr.queue_id} phase={rr.upload_phase}")

    def assert_slots_idle(self, rr: RoundResult) -> None:
        code, gate = self.get("/ops/resource-gate")
        pools = (gate or {}).get("pools") if isinstance(gate, dict) else {}
        busy = {
            k: {"used": v.get("used"), "holders": v.get("holders")}
            for k, v in (pools or {}).items()
            if isinstance(v, dict) and int(v.get("used") or 0) > 0
        }
        if busy:
            rr.anomalies.append(f"slots_busy_after_round:{busy}")
            self.rec.log(f"WARN R{rr.round_index} slots still busy: {busy}")

    def run_round(self, index: int) -> RoundResult:
        rr = RoundResult(round_index=index, started_at=_now_iso())
        # reset slot history accounting per round
        holds_before = len(self.rec._slot_history)
        t_round = time.monotonic()
        self.rec.log(f"======== ROUND {index}/{self.args.rounds} START ========")
        try:
            self.snapshot(round_index=index, job_id=None, label="round_baseline")
            rr.marks["create_job"] = _now_iso()
            rr.job_id = self.create_job()
            self.rec.log(f"R{index} created job#{rr.job_id}")
            self.wait_production(rr)
            self.ensure_pack_and_enqueue(rr)
            self.wait_upload(rr)
            self.assert_slots_idle(rr)
            rr.ok = rr.upload_outcome in {"published", "skipped"} and rr.job_status == "completed"
            if not rr.ok and rr.upload_outcome == "need_human":
                rr.ok = False
        except Exception as e:  # noqa: BLE001
            rr.ok = False
            rr.error = f"{type(e).__name__}: {e}"
            rr.anomalies.append(rr.error)
            self.rec.log(f"R{index} FAIL {rr.error}")
            self.rec.log(traceback.format_exc())
        finally:
            rr.finished_at = _now_iso()
            rr.duration_sec = round(time.monotonic() - t_round, 3)
            # collect holds opened during this round
            new_holds = [asdict(h) for h in self.rec._slot_history[holds_before:]]
            # also close any still-open for accounting snapshot (but keep open for next observe)
            open_now = [asdict(h) for h in self.rec._slot_open.values()]
            for h in open_now:
                h = dict(h)
                h["duration_sec"] = round(time.monotonic() - float(h["started_mono"]), 3)
                h["ended_at"] = None
                new_holds.append(h)
            rr.slot_holds = new_holds
            occ: dict[str, float] = {p: 0.0 for p in POOLS}
            for h in new_holds:
                p = str(h.get("pool") or "")
                if p in occ and h.get("duration_sec") is not None:
                    occ[p] = round(occ[p] + float(h["duration_sec"]), 3)
            rr.slot_occupied_sec = occ
            path = self.out_dir / "rounds" / f"R{index:02d}.json"
            path.write_text(json.dumps(asdict(rr), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self.rec.log(
                f"======== ROUND {index} END ok={rr.ok} dur={rr.duration_sec}s "
                f"job={rr.job_id} out={rr.output_id} q={rr.queue_id} upload={rr.upload_outcome} ========"
            )
        return rr

    def write_report(self) -> None:
        self.meta["finished_at"] = _now_iso()
        self.meta["rounds_ok"] = sum(1 for r in self.rounds if r.ok)
        self.meta["rounds_total"] = len(self.rounds)
        summary = {
            "meta": self.meta,
            "rounds": [asdict(r) for r in self.rounds],
            "slot_history_all": [asdict(h) for h in self.rec._slot_history],
        }
        (self.out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        lines = [
            f"# 速影客户机 E2E 验收报告 `{self.run_id}`",
            "",
            f"- 开始: {self.meta.get('started_at')}",
            f"- 结束: {self.meta.get('finished_at')}",
            f"- 轮次: **{self.meta['rounds_ok']}/{self.meta['rounds_total']} 通过**",
            f"- 引擎: {(self.meta.get('preflight') or {}).get('engine_version')}",
            f"- 客户: {(self.meta.get('preflight') or {}).get('customer')}",
            f"- 模板: `{self.args.template}` ×{self.args.target_count}",
            f"- 上传: `{self.args.platform}` / `{self.args.chrome_profile}`"
            + (" (skipped)" if self.args.skip_upload else ""),
            "",
            "## 各轮摘要",
            "",
            "| 轮 | OK | 耗时s | Job | 成片 | 队列 | 上传 | 异常数 |",
            "|---:|:--:|------:|----:|-----:|-----:|:----:|-------:|",
        ]
        for r in self.rounds:
            lines.append(
                f"| {r.round_index} | {'✅' if r.ok else '❌'} | {r.duration_sec} | "
                f"{r.job_id or '-'} | {r.output_id or '-'} | {r.queue_id or '-'} | "
                f"{r.upload_outcome or r.upload_phase or '-'} | {len(r.anomalies)} |"
            )
        lines += ["", "## 分段耗时（秒）", ""]
        for r in self.rounds:
            lines.append(f"### Round {r.round_index}")
            if r.error:
                lines.append(f"- **错误**: `{r.error}`")
            for k, v in sorted((r.timings or {}).items()):
                lines.append(f"- `{k}`: {v}")
            if r.slot_occupied_sec:
                lines.append("- 槽位累计占用秒: " + ", ".join(f"{k}={v}" for k, v in r.slot_occupied_sec.items()))
            if r.phase_seen:
                lines.append("- pipeline phases: " + " → ".join(r.phase_seen))
            if r.anomalies:
                lines.append("- anomalies:")
                for a in r.anomalies:
                    lines.append(f"  - {a}")
            lines.append("")
        lines += [
            "## 产物路径",
            "",
            f"- 目录: `{self.out_dir}`",
            f"- `summary.json` / `samples.jsonl` / `slot_events.jsonl` / `rounds/R*.json` / `console.log`",
            "",
        ]
        (self.out_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.rec.log(f"report written: {self.out_dir / 'REPORT.md'}")

    def run(self) -> int:
        self.rec.log(f"run_id={self.run_id} out={self.out_dir}")
        self.preflight()
        for i in range(1, int(self.args.rounds) + 1):
            rr = self.run_round(i)
            self.rounds.append(rr)
            if not rr.ok and self.args.stop_on_fail:
                self.rec.log("stop_on_fail: aborting remaining rounds")
                break
            if i < int(self.args.rounds):
                gap = float(self.args.round_gap)
                if gap > 0:
                    self.rec.log(f"round gap {gap}s …")
                    # keep sampling during gap for residual slot leaks
                    end = time.monotonic() + gap
                    while time.monotonic() < end:
                        self.snapshot(round_index=i, job_id=rr.job_id, label="round_gap")
                        time.sleep(min(float(self.args.interval), end - time.monotonic()))
        self.rec.close_open_holds(round_index=0, job_id=None)
        self.write_report()
        ok_n = sum(1 for r in self.rounds if r.ok)
        return 0 if ok_n == len(self.rounds) and self.rounds else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="速影客户机多轮生产+上传 E2E（密采样）")
    p.add_argument("--api", default=os.environ.get("SUYING_API", API_DEFAULT))
    p.add_argument("--db", default=str(DB_DEFAULT))
    p.add_argument("--out-dir", default=str(LOG_ROOT_DEFAULT))
    p.add_argument("--run-id", default="")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--interval", type=float, default=2.0, help="密采样间隔秒")
    p.add_argument("--round-gap", type=float, default=15.0, help="轮间等待秒（继续采样）")
    p.add_argument("--produce-timeout", type=float, default=900.0)
    p.add_argument("--upload-timeout", type=float, default=420.0)
    p.add_argument("--template", default="fast-ship")
    p.add_argument("--target-count", type=int, default=1)
    p.add_argument("--theme", default="default")
    p.add_argument("--category", default="default")
    p.add_argument("--orientation", default="portrait")
    p.add_argument("--customer", default="")
    p.add_argument("--platform", default="channels")
    p.add_argument("--chrome-profile", default="视频号-5331")
    p.add_argument("--accept-risk", action="store_true", help="G5.V 风控自负，允许自动上传")
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--dry-run-upload", action="store_true")
    p.add_argument("--allow-busy-slots", action="store_true")
    p.add_argument("--stop-on-fail", action="store_true", default=True)
    p.add_argument("--no-stop-on-fail", action="store_false", dest="stop_on_fail")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.skip_upload and not args.accept_risk:
        print("ERROR: 真机上传须传 --accept-risk（或改用 --skip-upload）", file=sys.stderr)
        return 2
    try:
        return E2ERunner(args).run()
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
