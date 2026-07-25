#!/usr/bin/env python3
"""P4 zero-fork smoke: two customers, activate switch, list/write isolation.

Runs in a temp workspace — never touches QR-Volume or real customer disks.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.api.app import app  # noqa: E402
from engine.catalog.db import Job, get_session, init_db, reset_engine  # noqa: E402
from engine.config.settings import AppSettings, PathConfig, load_settings, save_settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402


FORBIDDEN = ("始峰", "QR-Volume", "徐玲飞", "车凯盛")


def _no_leak(payload: object) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for m in FORBIDDEN:
        assert m not in blob, f"leaked {m!r}"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="suying-zerofork-") as tmp:
        base = Path(tmp)
        lib_a, out_a = base / "a_lib", base / "a_out"
        lib_b, out_b = base / "b_lib", base / "b_out"
        for p in (lib_a, out_a, lib_b, out_b, base / "cache", base / "data"):
            p.mkdir(parents=True, exist_ok=True)

        settings = AppSettings(
            paths=PathConfig(
                library_root=lib_a,
                output_root=out_a,
                cache_root=base / "cache",
                data_root=base / "data",
                external_required=False,
            ),
            active_customer="",
            onboarded=True,
            vectorization_enabled=False,
        )
        os.environ["MONTAGE_DATA_ROOT"] = str(settings.paths.data_root)
        os.environ["SUYING_DATA_ROOT"] = str(settings.paths.data_root)
        reset_engine()
        save_settings(settings)
        init_db(settings)

        client = TestClient(app)

        # Z1 create A and B
        ca = client.post(
            "/customers",
            json={"name": "零分叉客户A", "library_root": str(lib_a), "output_root": str(out_a)},
        )
        assert ca.status_code == 200, ca.text
        cb = client.post(
            "/customers",
            json={"name": "零分叉客户B", "library_root": str(lib_b), "output_root": str(out_b)},
        )
        assert cb.status_code == 200, cb.text
        a = ca.json()
        b = cb.json()
        assert a["id"] != b["id"]
        _no_leak(a)
        _no_leak(b)

        # Z2 activate A, create a job under A
        act = client.post("/customers/activate", json={"name": "零分叉客户A"})
        assert act.status_code == 200, act.text
        h = client.get("/health").json()
        assert h.get("active_customer") == "零分叉客户A"
        assert h.get("active_customer_id") == a["id"]

        job_a = client.post(
            "/jobs",
            json={
                "mode": "count",
                "target_count": 1,
                "customer_name": "零分叉客户A",
                "theme": "default",
                "category": "default",
                "template_name": "default-vertical",
            },
        )
        assert job_a.status_code == 200, job_a.text
        ja = job_a.json()
        assert ja.get("customer_id") == a["id"]

        jobs_a = client.get("/jobs").json()
        assert any(j.get("id") == ja["id"] for j in jobs_a), jobs_a
        _no_leak(jobs_a)

        # Seed one asset per customer for list isolation
        from datetime import datetime, timezone
        from uuid import uuid4

        from engine.catalog.db import Asset

        sess = get_session()
        try:
            sess.add(
                Asset(
                    customer_id=a["id"],
                    uuid=str(uuid4()),
                    source_path=str(lib_a / "a.mp4"),
                    storage_path=str(lib_a / "a.mp4"),
                    status="ready",
                    duration_sec=1.0,
                    width=1080,
                    height=1920,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            sess.add(
                Asset(
                    customer_id=b["id"],
                    uuid=str(uuid4()),
                    source_path=str(lib_b / "b.mp4"),
                    storage_path=str(lib_b / "b.mp4"),
                    status="ready",
                    duration_sec=1.0,
                    width=1080,
                    height=1920,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            sess.commit()
        finally:
            sess.close()

        assets_a = client.get("/assets").json()
        assert len(assets_a) == 1 and assets_a[0].get("customer_id") == a["id"]

        # Z3/Z4 switch to B — A's job/asset must not appear
        act_b = client.post("/customers/activate", json={"name": "零分叉客户B"})
        assert act_b.status_code == 200, act_b.text
        h2 = client.get("/health").json()
        assert h2.get("active_customer") == "零分叉客户B"
        assert h2.get("active_customer_id") == b["id"]

        jobs_b = client.get("/jobs").json()
        assert not any(j.get("id") == ja["id"] for j in jobs_b), jobs_b
        assert all(j.get("customer_id") == b["id"] for j in jobs_b)

        assets_b = client.get("/assets").json()
        assert len(assets_b) == 1 and assets_b[0].get("customer_id") == b["id"]

        job_b = client.post(
            "/jobs",
            json={
                "mode": "count",
                "target_count": 1,
                "customer_name": "零分叉客户B",
                "theme": "default",
                "category": "default",
                "template_name": "default-vertical",
            },
        )
        assert job_b.status_code == 200, job_b.text
        jb = job_b.json()
        assert jb.get("customer_id") == b["id"]

        # assets / outputs / reach quota scoped (empty ok)
        for path in ("/assets", "/outputs", "/reach/quota"):
            r = client.get(path)
            assert r.status_code == 200, (path, r.text)
            _no_leak(r.json())

        # switch back to A — see A's job, not B's
        client.post("/customers/activate", json={"name": "零分叉客户A"})
        jobs_a2 = client.get("/jobs").json()
        ids = {j.get("id") for j in jobs_a2}
        assert ja["id"] in ids
        assert jb["id"] not in ids

        # Z6 industry pack resolution independent of customer display name
        from engine.catalog.industry_pack import pack_id_for_customer

        assert pack_id_for_customer("零分叉客户B", None) == "_blank"
        assert pack_id_for_customer("零分叉客户B", {"industry_pack": "building-supply"}) == "building-supply"

        # Z7 settings still point at temp data_root
        s = load_settings()
        assert str(s.paths.data_root).startswith(str(base))

        # DB rows tagged correctly
        sess = get_session()
        try:
            rows = list(sess.scalars(select(Job)).all())
            by_c = {}
            for row in rows:
                by_c.setdefault(row.customer_id, []).append(row.id)
            assert ja["id"] in by_c.get(a["id"], [])
            assert jb["id"] in by_c.get(b["id"], [])
        finally:
            sess.close()

        print("ZERO_FORK_OK Z1-Z4 Z6-Z7")


if __name__ == "__main__":
    main()
