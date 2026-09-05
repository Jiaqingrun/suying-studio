"""Process-wide resource slots so produce/publish/TTS do not blindly overlap.

Publish remains a hard single machine slot. Render/TTS limits come from
settings.max_render_concurrency (Install Profile / host tier).

Holders carry a monotonic lease timestamp so orphaned acquires
(e.g. hung Ollama narration) can be force-released without restarting.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


# Default lease ceilings (seconds). Worker tick also sweeps these.
LEASE_MAX_AGE_SEC: dict[str, float] = {
    "ollama_heavy": 120.0,
    "tts": 600.0,
    # render intentionally omitted from auto-sweep defaults — long ffmpeg is valid;
    # ops API may still force-release with an explicit older_than_sec.
}


@dataclass
class _SlotPool:
    name: str
    capacity: int
    # token -> monotonic acquire time
    holders: dict[str, float] = field(default_factory=dict)

    def try_acquire(self, token: str) -> bool:
        if self.capacity <= 0:
            return False
        if token in self.holders:
            return True
        if len(self.holders) >= self.capacity:
            return False
        self.holders[token] = time.monotonic()
        return True

    def release(self, token: str) -> None:
        self.holders.pop(token, None)

    def force_release_expired(self, max_age_sec: float, now: float | None = None) -> list[str]:
        """Drop holders older than max_age_sec. Returns released tokens."""
        now = time.monotonic() if now is None else float(now)
        ceiling = max(1.0, float(max_age_sec))
        expired = [tok for tok, at in self.holders.items() if (now - at) >= ceiling]
        for tok in expired:
            self.holders.pop(tok, None)
        return expired

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        detail = [
            {"token": tok, "age_sec": round(max(0.0, now - at), 2)}
            for tok, at in sorted(self.holders.items())
        ]
        return {
            "name": self.name,
            "capacity": self.capacity,
            "used": len(self.holders),
            "holders": sorted(self.holders.keys()),
            "holders_detail": detail,
        }


class ResourceGate:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pools: dict[str, _SlotPool] = {
            "produce": _SlotPool("produce", 1),
            "render": _SlotPool("render", 1),
            "tts": _SlotPool("tts", 1),
            "publish": _SlotPool("publish", 1),
            "ollama_heavy": _SlotPool("ollama_heavy", 1),
        }
        # slot -> {reason, token, at}
        self._last_refuse: dict[str, dict[str, Any]] = {}
        self._last_lease_reap: dict[str, Any] = {}

    def configure(
        self,
        *,
        render_slots: int | None = None,
        tts_slots: int | None = None,
        produce_slots: int | None = None,
        publish_slots: int | None = None,
        ollama_heavy_slots: int | None = None,
    ) -> None:
        with self._lock:
            mapping = {
                "render": render_slots,
                "tts": tts_slots,
                "produce": produce_slots,
                "publish": publish_slots,
                "ollama_heavy": ollama_heavy_slots,
            }
            for name, capacity in mapping.items():
                if capacity is None:
                    continue
                pool = self._pools[name]
                pool.capacity = max(0, int(capacity))
                # Drop excess holders only by refusing new acquires; keep current
                # holders until they release to avoid yanking mid-work.

    def sync_from_settings(self) -> None:
        from engine.config.settings import load_settings

        settings = load_settings()
        render = max(1, int(getattr(settings, "max_render_concurrency", 1) or 1))
        # TTS stays single-process: F5-TTS / unified memory is the bottleneck.
        self.configure(
            produce_slots=min(2, render),
            render_slots=render,
            tts_slots=1,
            publish_slots=1,
            ollama_heavy_slots=1,
        )

    def try_acquire(self, slot: str, token: str) -> bool:
        with self._lock:
            pool = self._pools.get(slot)
            if pool is None:
                self._last_refuse[slot] = {
                    "reason": "unknown_slot",
                    "token": token,
                    "at": time.time(),
                }
                return False
            if not self._memory_ok_unlocked() and slot in {"tts", "render", "ollama_heavy"}:
                self._last_refuse[slot] = {
                    "reason": "host_pressure",
                    "token": token,
                    "at": time.time(),
                    "holders": sorted(pool.holders.keys()),
                }
                return False
            ok = pool.try_acquire(token)
            if not ok:
                self._last_refuse[slot] = {
                    "reason": "slot_busy",
                    "token": token,
                    "at": time.time(),
                    "capacity": pool.capacity,
                    "holders": sorted(pool.holders.keys()),
                }
            return ok

    def release(self, slot: str, token: str) -> None:
        with self._lock:
            pool = self._pools.get(slot)
            if pool is not None:
                pool.release(token)

    def release_all(self, token: str) -> None:
        with self._lock:
            for pool in self._pools.values():
                pool.release(token)

    def force_release_expired(
        self,
        slot: str | None = None,
        *,
        max_age_sec: float | None = None,
        older_than_sec: float | None = None,
    ) -> dict[str, list[str]]:
        """Force-drop holders older than the lease ceiling.

        ``slot`` None or \"all\" sweeps every pool that has a default or explicit age.
        ``older_than_sec`` aliases ``max_age_sec`` for the ops API.
        """
        age_override = max_age_sec if max_age_sec is not None else older_than_sec
        with self._lock:
            now = time.monotonic()
            targets: list[str]
            if slot is None or slot == "all":
                targets = list(self._pools.keys())
            else:
                targets = [slot]
            released: dict[str, list[str]] = {}
            for name in targets:
                pool = self._pools.get(name)
                if pool is None:
                    continue
                if age_override is not None:
                    ceiling = float(age_override)
                else:
                    if name not in LEASE_MAX_AGE_SEC:
                        continue
                    ceiling = float(LEASE_MAX_AGE_SEC[name])
                dropped = pool.force_release_expired(ceiling, now=now)
                if dropped:
                    released[name] = dropped
                    self._last_refuse[name] = {
                        "reason": "lease_expired",
                        "tokens": dropped,
                        "max_age_sec": ceiling,
                        "at": time.time(),
                    }
            if released:
                self._last_lease_reap = {
                    "at": time.time(),
                    "released": {k: list(v) for k, v in released.items()},
                }
            return released

    def sweep_default_leases(self) -> dict[str, list[str]]:
        """Worker tick helper: ollama_heavy + tts only."""
        released: dict[str, list[str]] = {}
        for name, age in LEASE_MAX_AGE_SEC.items():
            part = self.force_release_expired(name, max_age_sec=age)
            for k, v in part.items():
                released.setdefault(k, []).extend(v)
        return released

    @contextmanager
    def hold(self, slot: str, token: str, *, timeout_sec: float = 0.0) -> Iterator[bool]:
        """Acquire slot or wait up to timeout_sec. Yields True if held."""
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        acquired = False
        while True:
            if self.try_acquire(slot, token):
                acquired = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(slot, token)

    def _memory_ok_unlocked(self) -> bool:
        # Soft gate: refuse heavy slots when free disk on data root is critical.
        # Memory pressure is checked via host probe when available.
        try:
            from engine.catalog.host_profile import probe_host

            host = probe_host()
            ram = float(getattr(host, "ram_gb", 0) or 0)
            if ram and ram < 8:
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            home = os.path.expanduser("~/Suying/data")
            path = home if os.path.isdir(home) else os.path.expanduser("~")
            free_gb = shutil.disk_usage(path).free / (1024**3)
            if free_gb < 5.0:
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    def snapshot(self) -> dict[str, Any]:
        from engine.runtime.pause_coordinator import coordinator as pause_coordinator

        with self._lock:
            self.sync_from_settings()
            pools = {name: pool.snapshot() for name, pool in self._pools.items()}
            last_lease_reap = dict(self._last_lease_reap) if self._last_lease_reap else {}
        disk_free_gb = None
        try:
            home = os.path.expanduser("~/Suying/data")
            path = home if os.path.isdir(home) else os.path.expanduser("~")
            disk_free_gb = round(shutil.disk_usage(path).free / (1024**3), 2)
        except Exception:  # noqa: BLE001
            disk_free_gb = None
        ram_gb = None
        try:
            from engine.catalog.host_profile import probe_host

            ram_gb = float(getattr(probe_host(), "ram_gb", 0) or 0) or None
        except Exception:  # noqa: BLE001
            ram_gb = None
        pause = pause_coordinator.snapshot()
        with self._lock:
            last_refuse = {k: dict(v) for k, v in self._last_refuse.items()}
        return {
            "pools": pools,
            "last_refuse": last_refuse,
            "last_lease_reap": last_lease_reap,
            "lease_max_age_sec": dict(LEASE_MAX_AGE_SEC),
            "disk_free_gb": disk_free_gb,
            "ram_gb": ram_gb,
            "pause_active": not pause_coordinator.should_claim_jobs(),
            "pause_reasons": pause.get("pause_reasons") or [],
            "pid": os.getpid(),
            "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE") == "1",
            "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE") == "1",
            "tier_policy": {
                "lite_standard_pro_render": 1,
                "max_render": 2,
                "tts": 1,
                "publish": 1,
                "note": "render slots from settings.max_render_concurrency / Install Profile",
            },
        }


gate = ResourceGate()
