#!/usr/bin/env python3
"""Offline smoke for GSystemPause — temporary dirs only."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="suying-gsp-") as td:
        td_path = Path(td)
        boot = td_path / "boot"
        data = td_path / "data"
        lib = td_path / "lib"
        out = td_path / "out"
        runtime = td_path / "runtime"
        for p in (boot, data, lib, out, runtime):
            p.mkdir(parents=True, exist_ok=True)

        os.environ["SUYING_DATA_ROOT"] = str(boot)
        os.environ["MONTAGE_DATA_ROOT"] = str(boot)
        os.environ["SUYING_APP_RUNTIME_DIR"] = str(runtime)

        from engine.catalog.customer_scope import get_or_create_customer
        from engine.catalog.db import get_session, init_db, reset_engine
        from engine.config.settings import AppSettings, PathConfig, save_settings
        from engine.runtime.pause_coordinator import SystemEventControl, coordinator
        from fastapi.testclient import TestClient

        reset_engine()
        coordinator._state = type(coordinator._state)()  # noqa: SLF001
        coordinator._token = None  # noqa: SLF001
        system_token = "a" * 64
        (runtime / "system_token.txt").write_text(system_token, encoding="utf-8")

        settings = AppSettings(
            paths=PathConfig(
                data_root=data,
                library_root=lib,
                library_roots=[str(lib)],
                output_root=out,
                cache_root=data / "cache",
                render_root=data / "render",
            ),
            active_customer="GSP演示",
            onboarded=True,
            system_event_control=SystemEventControl(
                wake_settle_seconds=0,
                resume_requires_path_health=False,
                resume_requires_disk_health=False,
            ),
        )
        save_settings(settings)
        init_db(settings)
        session = get_session()
        try:
            get_or_create_customer(session, "GSP演示", library_root=str(lib), output_root=str(out))
        finally:
            session.close()

        from engine.api.app import app

        client = TestClient(app)

        health = client.get("/health")
        assert health.status_code == 200, health.text
        h = health.json()
        assert "runtime_state" in h, h
        assert "system_event_control" in h, h

        # Pause via system event
        ev = client.post(
            "/system/events",
            json={"event_id": "smoke-sleep-1", "kind": "will_sleep", "source": "smoke"},
            headers={"X-Suying-System-Token": system_token},
        )
        assert ev.status_code == 200, ev.text
        body = ev.json()
        assert body.get("applied") is True
        assert "system_sleep" in (body.get("pause_reasons") or [])

        # Duplicate
        ev2 = client.post(
            "/system/events",
            json={"event_id": "smoke-sleep-1", "kind": "will_sleep"},
            headers={"X-Suying-System-Token": system_token},
        )
        assert ev2.json().get("duplicate") is True

        # New work blocked
        job = client.post("/jobs", json={"mode": "count", "target_count": 1})
        assert job.status_code == 423, job.text

        deadline = time.monotonic() + 3
        while coordinator.snapshot()["state"] == "PAUSING" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert coordinator.snapshot()["state"] == "PAUSED", coordinator.snapshot()

        # Manual pause reason retention path
        client.post("/system/pause", json={"reason": "manual"})
        wake = client.post(
            "/system/events",
            json={
                "event_id": "smoke-wake-1",
                "kind": "did_wake",
                "generation_hint": body["generation"],
            },
            headers={"X-Suying-System-Token": system_token},
        )
        assert wake.status_code == 200, wake.text
        assert wake.json().get("applied") is True, wake.json()
        # Force deterministic checks; async path may be racing in the background.
        from engine.runtime.pause_coordinator import get_system_event_control_from_settings

        policy = get_system_event_control_from_settings(settings)
        st = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
        if st.get("restore_ready"):
            st = coordinator.complete_resume()
        assert "manual" in st.get("pause_reasons", []), st
        assert "system_sleep" not in st.get("pause_reasons", []), st

        # Clear manual
        cleared = client.post("/system/resume", json={"reasons": ["manual"]})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["state"] == "ACTIVE", cleared.json()

        # Real bootstrap settings must not contain our temp path
        home_boot = Path.home() / "Suying" / "data" / "settings.json"
        if home_boot.exists():
            raw = home_boot.read_text(encoding="utf-8")
            assert str(td_path) not in raw, "polluted real bootstrap settings"

        print("SMOKE_SYSTEM_EVENTS OK")
        print(json.dumps({"runtime": str(runtime), "final_state": coordinator.snapshot()}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001
        print(f"SMOKE_SYSTEM_EVENTS FAIL: {e}", file=sys.stderr)
        raise
