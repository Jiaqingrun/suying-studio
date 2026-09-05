"""GSystemPause: durable runtime pause coordinator (machine-local, not on external disk)."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


STATE_ACTIVE = "ACTIVE"
STATE_PAUSING = "PAUSING"
STATE_PAUSED = "PAUSED"
STATE_RESUMING = "RESUMING"
STATE_PAUSED_BLOCKED = "PAUSED_BLOCKED"

REASON_SYSTEM_SLEEP = "system_sleep"
REASON_SCREEN_SLEEP = "screen_sleep"
REASON_SESSION_INACTIVE = "session_inactive"
REASON_POWER_OFF = "power_off"
REASON_MANUAL = "manual"

PAUSE_EVENT_KINDS = {
    "will_sleep": REASON_SYSTEM_SLEEP,
    "screens_sleep": REASON_SCREEN_SLEEP,
    "session_inactive": REASON_SESSION_INACTIVE,
    "will_power_off": REASON_POWER_OFF,
}

RESUME_EVENT_KINDS = {
    "did_wake": "wake",
    "screens_wake": "screens_wake",
    "session_active": "session_active",
}

SAFE_AUTO_RESUME_UNITS = {
    "worker",
    "watcher",
    "scheduler",
    "message_scheduler",
    "scan",
    "vector_reconcile",
    "semantic_backfill",
}

SIDE_EFFECT_UNITS = {
    "reach_auto_upload",
    "content_publish",
    "ollama_pull",
    "carrier_sync",
    "app_update",
}


class SystemEventControl(BaseModel):
    """Machine-level macOS pause/resume preferences."""

    enabled: bool = True
    pause_on_system_sleep: bool = True
    pause_on_power_off: bool = True
    pause_on_session_inactive: bool = False
    pause_on_screen_sleep: bool = False
    auto_resume_on_wake: bool = True
    auto_resume_on_session_active: bool = False
    wake_settle_seconds: int = Field(default=5, ge=0, le=60)
    quiesce_timeout_seconds: int = Field(default=15, ge=1, le=120)
    event_debounce_ms: int = Field(default=500, ge=0, le=5000)
    resume_requires_path_health: bool = True
    resume_requires_disk_health: bool = True


def default_system_event_control() -> SystemEventControl:
    return SystemEventControl()


def runtime_dir() -> Path:
    override = os.environ.get("SUYING_APP_RUNTIME_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        home = Path.home()
        path = home / "Library" / "Application Support" / "com.qr.suying" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _state_path() -> Path:
    return runtime_dir() / "pause_state.json"


def _events_path() -> Path:
    return runtime_dir() / "system-events.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PauseState:
    state: str = STATE_ACTIVE
    generation: int = 0
    pause_reasons: list[str] = field(default_factory=list)
    pause_holds: dict[str, dict[str, Any]] = field(default_factory=dict)
    owned_units: dict[str, Any] = field(default_factory=dict)
    pending_resume: dict[str, Any] | None = None
    side_effect_interruptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    resume_blockers: list[str] = field(default_factory=list)
    last_event_id: str | None = None
    last_event_kind: str | None = None
    last_event_at: str | None = None
    paused_at: str | None = None
    wake_received_at: str | None = None
    last_error: str | None = None
    seen_event_ids: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "generation": self.generation,
            "pause_reasons": list(self.pause_reasons),
            "pause_holds": dict(self.pause_holds),
            "owned_units": dict(self.owned_units),
            "pending_resume": dict(self.pending_resume) if self.pending_resume else None,
            "side_effect_interruptions": dict(self.side_effect_interruptions),
            "resume_blockers": list(self.resume_blockers),
            "last_event_id": self.last_event_id,
            "last_event_kind": self.last_event_kind,
            "last_event_at": self.last_event_at,
            "paused_at": self.paused_at,
            "wake_received_at": self.wake_received_at,
            "last_error": self.last_error,
            "is_paused": self.state in (STATE_PAUSING, STATE_PAUSED, STATE_PAUSED_BLOCKED, STATE_RESUMING)
            and bool(self.pause_reasons),
            "accepts_new_work": self.state == STATE_ACTIVE and not self.pause_reasons,
        }


class PauseCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = PauseState()
        self._token: str | None = None
        self._load()

    def set_system_token(self, token: str | None) -> None:
        with self._lock:
            self._token = (token or "").strip() or None

    def system_token(self) -> str | None:
        with self._lock:
            return self._token

    def ensure_system_token(self) -> str:
        with self._lock:
            if not self._token:
                try:
                    p = runtime_dir() / "system_token.txt"
                    t = p.read_text(encoding="utf-8").strip()
                    if t:
                        self._token = t
                except OSError:
                    pass
            if not self._token:
                raise HTTPException(
                    status_code=503,
                    detail="系统事件令牌尚未由桌面端初始化",
                )
            return self._token

    def require_system_token(self, header: str | None) -> None:
        expected = self.ensure_system_token()
        got = (header or "").strip()
        if not got or got != expected:
            raise HTTPException(status_code=401, detail="无效的系统事件令牌")

    def _load(self) -> None:
        path = _state_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._state = PauseState(
                state=str(raw.get("state") or STATE_ACTIVE),
                generation=int(raw.get("generation") or 0),
                pause_reasons=list(raw.get("pause_reasons") or []),
                pause_holds=dict(raw.get("pause_holds") or {}),
                owned_units=dict(raw.get("owned_units") or {}),
                pending_resume=raw.get("pending_resume"),
                side_effect_interruptions=dict(raw.get("side_effect_interruptions") or {}),
                resume_blockers=list(raw.get("resume_blockers") or []),
                last_event_id=raw.get("last_event_id"),
                last_event_kind=raw.get("last_event_kind"),
                last_event_at=raw.get("last_event_at"),
                paused_at=raw.get("paused_at"),
                wake_received_at=raw.get("wake_received_at"),
                last_error=raw.get("last_error"),
                seen_event_ids=list(raw.get("seen_event_ids") or [])[-200:],
            )
            # Crash while sleeping: never boot as ACTIVE with pause reasons
            if self._state.pause_reasons and self._state.state == STATE_ACTIVE:
                self._state.state = STATE_PAUSED
            if self._state.state in (STATE_PAUSING, STATE_RESUMING):
                self._state.state = STATE_PAUSED_BLOCKED
                self._state.resume_blockers = list(
                    dict.fromkeys(self._state.resume_blockers + ["restart_during_transition"])
                )
            if self._state.pause_reasons and not self._state.pause_holds:
                self._state.pause_holds = {
                    reason: {"owner": "legacy", "generation": self._state.generation}
                    for reason in self._state.pause_reasons
                }
            # Cold boot: power_off cannot still be in progress once this process starts.
            self._drop_stale_power_off_locked()
            # Engine started under a live GUI session: residual sleep holds from a previous
            # process cannot still mean "the machine is sleeping". Drop them the same way
            # as power_off, or wake never delivered / generation_hint stale locks production.
            self._drop_stale_sleep_holds_locked()
            # Orphan block: PAUSED_BLOCKED with no reasons (late quiesce after wake)
            self._heal_orphan_blocked_locked()
        except Exception as e:  # noqa: BLE001
            self._state.last_error = f"load_failed: {e}"

    def _save(self) -> None:
        path = _state_path()
        payload = asdict(self._state)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)

    def _append_event(self, record: dict[str, Any]) -> None:
        path = _events_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._heal_orphan_blocked_locked()
            return self._state.to_public()

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        path = _events_path()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 500)) :]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _finalize_after_drop_system_reasons_locked(self, *, kind: str) -> None:
        """Normalize state after dropping one or more auto system reasons."""
        self._state.resume_blockers = [
            b
            for b in self._state.resume_blockers
            if b
            not in (
                "restart_during_transition",
                "path_health",
                "disk_health",
                "quiesce_failed",
            )
        ]
        if not self._state.pause_reasons and not self._state.pause_holds:
            self._state.state = STATE_ACTIVE
            self._state.pending_resume = None
            self._state.paused_at = None
            self._state.wake_received_at = None
            self._state.last_error = None
            self._state.last_event_kind = kind
            self._state.last_event_id = f"{kind}-{uuid.uuid4().hex[:12]}"
            self._state.last_event_at = _utc_now()
        elif self._state.state in (STATE_PAUSED_BLOCKED, STATE_PAUSING, STATE_RESUMING):
            self._state.state = STATE_PAUSED

    def _drop_stale_power_off_locked(self) -> bool:
        """Drop power_off holds after process start — the machine is already powered on.

        will_power_off often races reboot: state is persisted with power_off, but macOS never
        delivers did_wake for a cold boot. did_wake historically only cleared system_sleep,
        so power_off left PAUSED forever and UI/API looked frozen.
        """
        if REASON_POWER_OFF not in self._state.pause_holds and REASON_POWER_OFF not in self._state.pause_reasons:
            return False
        self._state.pause_holds.pop(REASON_POWER_OFF, None)
        self._state.pause_reasons = [r for r in self._state.pause_reasons if r != REASON_POWER_OFF]
        self._finalize_after_drop_system_reasons_locked(kind="boot_clear_power_off")
        self._save()
        return True

    def _drop_stale_sleep_holds_locked(self) -> bool:
        """Drop residual sleep holds after process start — the engine is already running.

        system_sleep / screen_sleep are machine-level; a live engine process (started by GUI
        or watchdog) means the Mac is not asleep. Persisted holds commonly strand production
        when did_wake was lost, screen_wake never arrived, or generation_hint was stale.
        Manual pause is never dropped here.
        """
        stale = (REASON_SYSTEM_SLEEP, REASON_SCREEN_SLEEP)
        dropped = False
        for reason in stale:
            hold = self._state.pause_holds.get(reason) or {}
            if reason not in self._state.pause_holds and reason not in self._state.pause_reasons:
                continue
            # Keep only owner=system (or legacy rewrite without owner).
            owner = str(hold.get("owner") or "system")
            if owner not in ("system", "legacy", ""):
                continue
            self._state.pause_holds.pop(reason, None)
            dropped = True
        if not dropped:
            return False
        self._state.pause_reasons = [
            r for r in self._state.pause_reasons if r in self._state.pause_holds
        ]
        self._finalize_after_drop_system_reasons_locked(kind="boot_clear_sleep")
        self._save()
        return True

    def _heal_orphan_blocked_locked(self) -> bool:
        """Clear PAUSED_BLOCKED that has no pause holds (unrecoverable by normal resume).

        Happens when quiesce times out *after* wake already cleared system_sleep holds:
        mark_blocked leaves empty-reason PAUSED_BLOCKED and accepts_new_work stays false.
        """
        if (
            self._state.state == STATE_PAUSED_BLOCKED
            and not self._state.pause_reasons
            and not self._state.pause_holds
        ):
            self._state.state = STATE_ACTIVE
            self._state.resume_blockers = []
            self._state.pending_resume = None
            # Keep last_error for audit until next clean pause/resume cycle.
            self._state.paused_at = None
            self._state.wake_received_at = None
            self._save()
            return True
        return False

    def is_active_for_new_work(self) -> bool:
        with self._lock:
            self._heal_orphan_blocked_locked()
            return self._state.state == STATE_ACTIVE and not self._state.pause_reasons

    def should_claim_jobs(self) -> bool:
        """Worker may finish current output but must not start new outputs when pausing/paused."""
        with self._lock:
            self._heal_orphan_blocked_locked()
            return self._state.state == STATE_ACTIVE and not self._state.pause_reasons

    def assert_runtime_active(self, operation: str = "write") -> None:
        with self._lock:
            if self._state.state == STATE_ACTIVE and not self._state.pause_reasons:
                return
            raise HTTPException(
                status_code=423,
                detail={
                    "code": "runtime_paused",
                    "message": f"运行时已暂停，暂不可执行：{operation}",
                    "state": self._state.state,
                    "pause_reasons": list(self._state.pause_reasons),
                    "resume_blockers": list(self._state.resume_blockers),
                },
            )

    def _policy_allows_pause(self, kind: str, policy: SystemEventControl) -> bool:
        if not policy.enabled:
            return False
        if kind == "will_sleep":
            return bool(policy.pause_on_system_sleep)
        if kind == "will_power_off":
            return bool(policy.pause_on_power_off)
        if kind == "session_inactive":
            return bool(policy.pause_on_session_inactive)
        if kind == "screens_sleep":
            return bool(policy.pause_on_screen_sleep)
        return False

    def _policy_allows_resume(self, kind: str, policy: SystemEventControl) -> bool:
        if not policy.enabled:
            return False
        if kind == "did_wake":
            return bool(policy.auto_resume_on_wake)
        if kind == "session_active":
            return bool(policy.auto_resume_on_session_active)
        if kind == "screens_wake":
            # Only resume screen-sleep ownership if that reason is held
            return REASON_SCREEN_SLEEP in self._state.pause_reasons
        return False

    def has_seen_event(self, event_id: str) -> bool:
        eid = (event_id or "").strip()
        if not eid:
            return False
        with self._lock:
            return eid in self._state.seen_event_ids

    def handle_event(
        self,
        *,
        event_id: str,
        kind: str,
        occurred_at: str | None = None,
        source: str = "tauri",
        generation_hint: int | None = None,
        policy: SystemEventControl | None = None,
        owned_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = policy or default_system_event_control()
        event_id = (event_id or "").strip() or uuid.uuid4().hex
        kind = (kind or "").strip()
        occurred_at = occurred_at or _utc_now()

        with self._lock:
            if event_id in self._state.seen_event_ids:
                record = {
                    "event_id": event_id,
                    "kind": kind,
                    "occurred_at": occurred_at,
                    "source": source,
                    "duplicate": True,
                    "generation": self._state.generation,
                    "state": self._state.state,
                }
                self._append_event(record)
                return {**self._state.to_public(), "duplicate": True, "applied": False}

            self._state.seen_event_ids = (self._state.seen_event_ids + [event_id])[-200:]
            self._state.last_event_id = event_id
            self._state.last_event_kind = kind
            self._state.last_event_at = occurred_at

            applied = False
            if kind in PAUSE_EVENT_KINDS:
                if self._policy_allows_pause(kind, policy):
                    reason = PAUSE_EVENT_KINDS[kind]
                    applied = self._begin_pause(
                        reason=reason,
                        owner="system",
                        owned_snapshot=owned_snapshot,
                        settle_hint=False,
                    )
            elif kind in RESUME_EVENT_KINDS:
                if self._policy_allows_resume(kind, policy):
                    applied = self._begin_resume(
                        trigger=kind,
                        policy=policy,
                        generation_hint=generation_hint,
                    )
            else:
                self._state.last_error = f"unknown_kind:{kind}"

            record = {
                "event_id": event_id,
                "kind": kind,
                "occurred_at": occurred_at,
                "source": source,
                "duplicate": False,
                "applied": applied,
                "generation": self._state.generation,
                "state": self._state.state,
                "pause_reasons": list(self._state.pause_reasons),
            }
            self._append_event(record)
            self._save()
            return {**self._state.to_public(), "duplicate": False, "applied": applied}

    def pause_manual(self, reason: str = REASON_MANUAL, owned_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._begin_pause(
                reason=reason or REASON_MANUAL,
                owner="manual",
                owned_snapshot=owned_snapshot,
                settle_hint=False,
            )
            self._append_event(
                {
                    "event_id": uuid.uuid4().hex,
                    "kind": "manual_pause",
                    "occurred_at": _utc_now(),
                    "source": "api",
                    "applied": True,
                    "generation": self._state.generation,
                    "state": self._state.state,
                }
            )
            self._save()
            return self._state.to_public()

    def prepare_manual_resume(self, reasons: list[str] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._heal_orphan_blocked_locked():
                self._append_event(
                    {
                        "event_id": uuid.uuid4().hex,
                        "kind": "manual_resume",
                        "occurred_at": _utc_now(),
                        "source": "api",
                        "applied": True,
                        "requested": list(reasons or []),
                        "generation": self._state.generation,
                        "state": self._state.state,
                        "note": "orphan_paused_blocked_healed",
                    }
                )
                return self._state.to_public()
            targets = reasons or list(self._state.pause_reasons)
            held = [r for r in targets if r in self._state.pause_holds]
            if not held:
                return self._state.to_public()
            self._state.pending_resume = {
                "owner": "manual",
                "generation": self._state.generation,
                "reasons": held,
                "trigger": "manual",
            }
            self._state.state = STATE_RESUMING
            self._append_event(
                {
                    "event_id": uuid.uuid4().hex,
                    "kind": "manual_resume",
                    "occurred_at": _utc_now(),
                    "source": "api",
                    "applied": True,
                    "requested": held,
                    "generation": self._state.generation,
                    "state": self._state.state,
                }
            )
            self._save()
            return self._state.to_public()

    def rearm_health_blocked_resume(self) -> bool:
        """If PAUSED_BLOCKED only for path/disk and pending or system reasons exist, go RESUMING.

        Caller must re-run apply_resume_after_checks after verifying health is green.
        """
        with self._lock:
            if self._state.state != STATE_PAUSED_BLOCKED:
                return False
            blockers = set(self._state.resume_blockers or [])
            if not blockers.issubset({"path_health", "disk_health"}):
                return False
            if not self._state.pending_resume:
                system_reasons = (
                    REASON_SYSTEM_SLEEP,
                    REASON_SCREEN_SLEEP,
                    REASON_POWER_OFF,
                    REASON_SESSION_INACTIVE,
                )
                reasons = [
                    reason
                    for reason in self._state.pause_reasons
                    if reason in system_reasons
                    and (self._state.pause_holds.get(reason) or {}).get("owner") == "system"
                ]
                if not reasons:
                    return False
                self._state.pending_resume = {
                    "owner": "system",
                    "generation": int(self._state.generation),
                    "reasons": reasons,
                    "trigger": "health_retry",
                }
                self._state.wake_received_at = _utc_now()
            self._state.state = STATE_RESUMING
            self._state.resume_blockers = []
            self._save()
            return True

    def mark_paused(self, *, generation: int | None = None) -> dict[str, Any]:
        """Quiesce finished → PAUSED (or keep PAUSED_BLOCKED)."""
        with self._lock:
            if generation is not None and generation != self._state.generation:
                return self._state.to_public()
            if self._state.state == STATE_PAUSING and self._state.pause_reasons:
                # A wake received during quiesce waits for this safe boundary.
                self._state.state = (
                    STATE_RESUMING if self._state.pending_resume else STATE_PAUSED
                )
            self._save()
            return self._state.to_public()

    def mark_blocked(
        self,
        blockers: list[str],
        error: str | None = None,
        *,
        generation: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            # Stale async quiesce must not re-block after wake cleared holds.
            if generation is not None and generation != self._state.generation:
                return self._state.to_public()
            if not self._state.pause_reasons and self._state.state in (
                STATE_ACTIVE,
                STATE_RESUMING,
            ):
                if error:
                    self._state.last_error = f"ignored_stale_block:{error}"
                    self._save()
                return self._state.to_public()
            if error:
                self._state.last_error = error
            # Wake already queued during quiesce: same safe boundary as mark_paused.
            # Stranding pending_resume in PAUSED_BLOCKED freezes accepts_new_work
            # (publish / 一键上传 get 423) until an operator manually resumes.
            if self._state.pending_resume and self._state.pause_reasons:
                self._state.state = STATE_RESUMING
                # Keep blockers for audit until apply_resume_after_checks clears them.
                self._state.resume_blockers = list(dict.fromkeys(blockers))
                self._save()
                return self._state.to_public()
            self._state.state = STATE_PAUSED_BLOCKED
            self._state.resume_blockers = list(dict.fromkeys(blockers))
            self._save()
            # Block with no holds cannot be cleared by normal wake/manual resume.
            self._heal_orphan_blocked_locked()
            return self._state.to_public()

    def complete_resume(self) -> dict[str, Any]:
        with self._lock:
            pending = dict(self._state.pending_resume or {})
            if self._state.state != STATE_RESUMING or not pending:
                return self._state.to_public()
            expected_generation = int(pending.get("generation") or -1)
            expected_owner = str(pending.get("owner") or "")
            trigger = str(pending.get("trigger") or "")
            live_gen = int(self._state.generation)
            for reason in list(pending.get("reasons") or []):
                hold = self._state.pause_holds.get(reason) or {}
                hold_gen = int(hold.get("generation") or -2)
                gen_ok = hold_gen == expected_generation or hold_gen == live_gen
                owner_ok = str(hold.get("owner") or "") == expected_owner
                # Manual resume lists reasons explicitly and must clear system/legacy holds too.
                if gen_ok and (owner_ok or trigger == "manual"):
                    self._state.pause_holds.pop(reason, None)
            self._state.pause_reasons = [
                reason for reason in self._state.pause_reasons if reason in self._state.pause_holds
            ]
            self._state.pending_resume = None
            if self._state.pause_reasons:
                self._state.state = STATE_PAUSED
            else:
                self._state.state = STATE_ACTIVE
                self._state.resume_blockers = []
                self._state.paused_at = None
                self._state.wake_received_at = None
                self._state.last_error = None
            self._save()
            return self._state.to_public()

    def mark_side_effect_interrupted(
        self, unit: str, *, status: str = "interrupted_system", detail: str = ""
    ) -> None:
        with self._lock:
            self._state.side_effect_interruptions[unit] = {
                "status": status,
                "detail": detail,
                "generation": self._state.generation,
                "at": _utc_now(),
            }
            self._save()

    def set_owned_units(self, units: dict[str, Any]) -> None:
        with self._lock:
            self._state.owned_units = dict(units or {})
            self._save()

    def owned_units(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.owned_units)

    def _begin_pause(
        self,
        *,
        reason: str,
        owner: str,
        owned_snapshot: dict[str, Any] | None,
        settle_hint: bool,
    ) -> bool:
        _ = settle_hint
        if reason not in self._state.pause_reasons:
            if not self._state.pause_reasons:
                self._state.generation += 1
            self._state.pause_reasons.append(reason)
            self._state.pause_holds[reason] = {
                "owner": owner,
                "generation": self._state.generation,
            }
        if owned_snapshot is not None:
            self._state.owned_units = dict(owned_snapshot)
        if self._state.state == STATE_ACTIVE:
            self._state.state = STATE_PAUSING
            self._state.paused_at = _utc_now()
        elif self._state.state not in (STATE_PAUSED, STATE_PAUSED_BLOCKED, STATE_PAUSING):
            self._state.state = STATE_PAUSING
            self._state.paused_at = self._state.paused_at or _utc_now()
        if self._state.state == STATE_PAUSING:
            self._state.pending_resume = None
        return True

    def _begin_resume(
        self,
        *,
        trigger: str,
        policy: SystemEventControl,
        generation_hint: int | None,
    ) -> bool:
        if not self._state.pause_reasons:
            return False
        # Prefer desktop's hint, but never strand forever when outbox gen is stale
        # (engine restarted, Tauri missed the last pause generation).
        live_gen = int(self._state.generation)
        if generation_hint is None:
            generation_hint = live_gen
        acceptable_gens = {live_gen, int(generation_hint)}

        targets: list[str]
        if trigger == "screens_wake":
            targets = [REASON_SCREEN_SLEEP]
        elif trigger == "session_active":
            # Unlock/activate also implies the machine is awake — clear residual sleep
            # when did_wake was lost or only session_active is policy-enabled.
            targets = [
                REASON_SESSION_INACTIVE,
                REASON_SYSTEM_SLEEP,
                REASON_SCREEN_SLEEP,
            ]
        elif trigger == "did_wake":
            # Wake ends both system sleep and display sleep; macOS often omits screens_wake.
            # Also clear power_off from shutdown races (cold boot does boot self-heal).
            targets = [REASON_SYSTEM_SLEEP, REASON_SCREEN_SLEEP, REASON_POWER_OFF]
        else:
            return False
        targets = [
            reason
            for reason in targets
            if (self._state.pause_holds.get(reason) or {}).get("owner") == "system"
            and int((self._state.pause_holds.get(reason) or {}).get("generation") or -1)
            in acceptable_gens
        ]
        if not targets:
            return False

        self._state.wake_received_at = _utc_now()
        self._state.pending_resume = {
            "owner": "system",
            # complete_resume matches hold.generation to this field — always live.
            "generation": live_gen,
            "reasons": targets,
            "trigger": trigger,
        }
        # Never race restore against an in-flight quiesce. mark_paused() moves
        # this pending wake to RESUMING at the safe boundary.
        if self._state.state != STATE_PAUSING:
            self._state.state = STATE_RESUMING
        return True

    def apply_resume_after_checks(
        self,
        *,
        path_ok: bool,
        disk_ok: bool,
        policy: SystemEventControl | None = None,
    ) -> dict[str, Any]:
        policy = policy or default_system_event_control()
        settle = int(policy.wake_settle_seconds or 0)
        wake_at: str | None = None
        expected_pending: dict[str, Any] | None = None
        with self._lock:
            if self._state.state not in (STATE_RESUMING, STATE_PAUSED_BLOCKED, STATE_PAUSED):
                return self._state.to_public()
            if self._state.state == STATE_PAUSED and not self._state.wake_received_at:
                return self._state.to_public()
            wake_at = self._state.wake_received_at
            expected_pending = dict(self._state.pending_resume or {})

        if wake_at and settle > 0:
            try:
                woke = datetime.fromisoformat(wake_at)
                if woke.tzinfo is None:
                    woke = woke.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - woke).total_seconds()
                if elapsed < settle:
                    time.sleep(min(settle - elapsed, settle))
            except Exception:  # noqa: BLE001
                time.sleep(min(settle, 5))

        with self._lock:
            if (
                self._state.state != STATE_RESUMING
                or not expected_pending
                or self._state.pending_resume != expected_pending
            ):
                return self._state.to_public()
            blockers: list[str] = []
            if policy.resume_requires_path_health and not path_ok:
                blockers.append("path_health")
            if policy.resume_requires_disk_health and not disk_ok:
                blockers.append("disk_health")
            if blockers:
                self._state.state = STATE_PAUSED_BLOCKED
                self._state.resume_blockers = list(dict.fromkeys(blockers))
                self._state.last_error = "resume_blocked_by_health"
                self._save()
                return self._state.to_public()

            self._state.resume_blockers = []
            self._state.last_error = None
            self._save()
            return {**self._state.to_public(), "restore_ready": True}


# Process singleton
coordinator = PauseCoordinator()


def get_system_event_control_from_settings(settings: Any) -> SystemEventControl:
    raw = getattr(settings, "system_event_control", None)
    if isinstance(raw, SystemEventControl):
        return raw
    if isinstance(raw, dict):
        return SystemEventControl.model_validate(raw)
    return default_system_event_control()


def assert_runtime_active(operation: str = "write") -> None:
    coordinator.assert_runtime_active(operation)
