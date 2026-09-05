#!/usr/bin/env python3
"""Smoke test for Montage Studio MVP engine.

E2.6: must run in a temp workspace and never require 始峰 / QR-Volume paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FORBIDDEN_PATH_MARKERS = ("始峰", "QR-Volume", "徐玲飞", "车凯盛")


def _assert_no_customer_disk(payload: object) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for m in FORBIDDEN_PATH_MARKERS:
        assert m not in blob, f"smoke leaked customer-disk marker {m!r}: {blob[:400]}"


def _bootstrap_active_production_rule(client, customer_name: str) -> int:
    from engine.catalog.db import get_session
    from engine.config.settings import load_settings, save_settings
    from sqlalchemy import select

    from engine.catalog.db import Customer

    settings = load_settings()
    settings.active_customer = customer_name
    save_settings(settings)
    session = get_session()
    try:
        row = session.scalar(select(Customer).where(Customer.name == customer_name))
        if row is None:
            raise RuntimeError(f"smoke: customer not found: {customer_name}")
    finally:
        session.close()
    created = client.post(
        "/production-rules",
        json={
            "name": "冒烟启用规则",
            "content_category": "default",
            "orientation": "portrait",
            "rules": {"pace": "normal"},
        },
    )
    assert created.status_code == 200, created.text
    rid = int(created.json()["rule"]["id"])
    assert client.post(
        f"/production-rules/{rid}/approve", json={"approved_by": "smoke"}
    ).status_code == 200
    assert client.post(f"/production-rules/{rid}/activate").status_code == 200
    return rid


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        boot = base / "bootstrap"
        runtime = base / "runtime"
        install_root = base / "install"
        for p in (home, boot, runtime, install_root):
            p.mkdir(parents=True, exist_ok=True)

        # Isolation must be established before importing any engine module:
        # settings imports the process-wide pause coordinator, which otherwise
        # reads the real machine runtime and may leak a persisted paused state.
        os.environ["HOME"] = str(home)
        os.environ["SUYING_DATA_ROOT"] = str(boot)
        os.environ["MONTAGE_DATA_ROOT"] = str(boot)
        os.environ["SUYING_APP_RUNTIME_DIR"] = str(runtime)
        os.environ["SUYING_INSTALL_ROOT"] = str(install_root)
        os.environ["SUYING_SYSTEM_TOKEN_OPTIONAL"] = "1"

        from engine.api.app import app
        from engine.catalog.db import init_db, reset_engine
        from engine.config.settings import AppSettings, PathConfig, save_settings
        from fastapi.testclient import TestClient

        settings = AppSettings(
            paths=PathConfig(
                library_root=base / "library",
                output_root=base / "output",
                cache_root=base / "cache",
                data_root=base / "data",
                external_required=False,
            ),
            active_customer="演示客户",
            onboarded=False,
        )
        for p in (
            settings.paths.library_root,
            settings.paths.output_root,
            settings.paths.cache_root,
            settings.paths.data_root,
        ):
            p.mkdir(parents=True, exist_ok=True)

        os.environ["MONTAGE_DATA_ROOT"] = str(settings.paths.data_root)
        # Drop any prior engine binding so we don't touch the volume DB
        reset_engine()
        save_settings(settings)
        init_db(settings)
        from sqlalchemy import text
        from engine.catalog.db import get_engine

        with get_engine(settings).connect() as conn:
            versions = {
                str(row[0])
                for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
            }
            assert "20260726_closeout" in versions, versions
            triggers = {
                str(row[0])
                for row in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' AND name LIKE 'trg_%usage%'"
                    )
                ).fetchall()
            }
            assert "trg_daily_usage_cap_insert" not in triggers
            assert "trg_daily_usage_cap_update" not in triggers
            assert "trg_weekly_usage_cap_insert" not in triggers
            assert {
                "trg_daily_usage_nonneg_insert",
                "trg_daily_usage_nonneg_update",
                "trg_weekly_usage_nonneg_insert",
                "trg_weekly_usage_nonneg_update",
            }.issubset(triggers), triggers

        client = TestClient(app)
        _bootstrap_active_production_rule(client, "演示客户")
        hbody = None
        for _ in range(60):
            health = client.get("/health")
            assert health.status_code == 200, health.text
            hbody = health.json()
            if hbody.get("status") != "starting":
                break
            time.sleep(0.1)
        assert hbody is not None
        assert hbody["status"] in ("ok", "degraded", "blocked"), hbody
        if hbody["status"] == "blocked":
            # control-plane-only env is acceptable for some probes; full smoke needs ready
            raise AssertionError(f"workspace blocked in smoke temp dir: {hbody.get('workspace_state')}")
        assert hbody["status"] in ("ok", "degraded")
        assert hbody["onboarded"] is False, "GET /health must not complete onboarding"
        _assert_no_customer_disk(hbody.get("paths") or {})
        assert str(hbody.get("paths", {}).get("data_root", "")).startswith(str(base))
        install_plan = client.get("/setup/install-plan")
        assert install_plan.status_code == 200, install_plan.text
        install_body = install_plan.json()
        assert install_body["schema_version"] == "suying.install.plan.v1"
        assert install_body["profile"]["full_library_vision"] is False
        assert all("27b" not in model.lower() for model in install_body["selected_models"])
        stale_plan = client.post(
            "/setup/install-plan",
            json={"plan_id": "stale", "include_narration": False},
        )
        assert stale_plan.status_code == 409, stale_plan.text
        approved_plan = client.post(
            "/setup/install-plan",
            json={
                "plan_id": install_body["plan_id"],
                "include_narration": False,
            },
        )
        assert approved_plan.status_code == 200, approved_plan.text
        assert approved_plan.json()["plan_id"] == install_body["plan_id"]
        assert approved_plan.json()["approved_at"]

        # Industry packs must load from repo samples (no customer disk)
        from engine.catalog.industry_pack import load_industry_pack, pack_id_for_customer

        blank = load_industry_pack("_blank")
        assert isinstance(blank, dict) and blank
        building = load_industry_pack("building-supply")
        assert isinstance(building, dict) and building
        assert pack_id_for_customer("unknown-customer", None) == "_blank"
        assert pack_id_for_customer("演示客户", {"industry_pack": "_blank"}) == "_blank"

        sample = ROOT / "configs" / "samples" / "sample-customer.json"
        if sample.exists():
            sample_root = base / "customers" / "sample"
            for subdir in ("01-片库", "02-成片", "03-词池"):
                (sample_root / subdir).mkdir(parents=True, exist_ok=True)
            created = client.post(
                "/customers",
                json={
                    "name": "sample",
                    "library_root": str(settings.paths.library_root),
                    "output_root": str(settings.paths.output_root),
                    "keyword_pack_path": str(sample_root / "03-词池" / "keyword-pack.json"),
                },
            )
            assert created.status_code == 200, created.text
            imp = client.post(
                "/keywords/import",
                params={"customer_name": "sample", "path": str(sample)},
            )
            assert imp.status_code == 200, imp.text
            _bootstrap_active_production_rule(client, "sample")

        dry = client.post(
            "/dry-run",
            json={
                "customer_name": "sample",
                "theme": "default",
                "category": "default",
            },
        )
        assert dry.status_code == 200, dry.text
        body = dry.json()
        assert "title" in body
        _assert_no_customer_disk(body)

        dry_blank = client.post(
            "/dry-run",
            json={
                "customer_name": "演示客户",
                "theme": "default",
                "category": "default",
            },
        )
        assert dry_blank.status_code == 200, dry_blank.text

        job = client.post(
            "/jobs",
            json={
                "mode": "count",
                "target_count": 1,
                "customer_name": "sample",
                "theme": "default",
                "category": "default",
                "use_active_rule": True,
            },
        )
        assert job.status_code == 200, job.text
        job_id = int(job.json()["id"])

        # E1.C2: reject deletes deliverable media and does NOT auto-queue re-render
        from engine.catalog.db import RenderOutput, get_session

        session = get_session()
        try:
            fake_dir = base / "failed" / "reject-smoke"
            fake_dir.mkdir(parents=True, exist_ok=True)
            fake_mp4 = fake_dir / "smoke_reject.mp4"
            fake_mp4.write_bytes(b"\x00\x00fake")
            out = RenderOutput(
                job_id=job_id,
                output_path=str(fake_mp4),
                state="ready",
                seed=1,
                sidecar_path=None,
                qc_json={},
            )
            session.add(out)
            session.commit()
            session.refresh(out)
            output_id = out.id
        finally:
            session.close()

        # Customer-scoped review: make active_customer match the job/output customer.
        settings.active_customer = "sample"
        save_settings(settings)

        rejected = client.post(
            f"/review/{output_id}",
            params={
                "status": "rejected",
                "reason": "dark_blur",
                "note": "画面糊",
                "rerender": "true",
            },
        )
        assert rejected.status_code == 200, rejected.text
        rej = rejected.json()
        assert rej.get("reason") == "dark_blur"
        assert not rej.get("rerender_job"), rej
        assert rej.get("state") == "failed", rej
        purge = rej.get("purge") or {}
        assert purge.get("ok") is True, purge
        assert not fake_mp4.exists(), "rejected output media must be deleted"
        # Explicit ops re-render endpoint still works when asked
        again = client.post(f"/review/{output_id}/rerender", params={"reason": "reuse"})
        assert again.status_code == 200, again.text
        rr_job_id = int(again.json()["job"]["id"])

        session = get_session()
        try:
            from engine.catalog.db import Job

            rr = session.get(Job, rr_job_id)
            assert rr is not None
            assert rr.target_count == 1
            snap = rr.config_snapshot_json or {}
            assert snap.get("rerender_of_output_id") == output_id
            assert snap.get("reject_reason") == "reuse"
        finally:
            session.close()

        # E1.C1: logo resolve is optional; missing file must not crash
        from engine.render.logo import (
            LOGO_POSITIONS,
            normalize_logo_position,
            overlay_xy_expr,
            resolve_logo_path,
        )

        assert resolve_logo_path(settings, profile={"brand": {"logo_enabled": False}}) is None
        assert normalize_logo_position("tr") == "top_right"
        assert overlay_xy_expr("top_left", 36).startswith("36:")
        assert "W-w-" in overlay_xy_expr("bottom_right", 36)
        for pos in LOGO_POSITIONS:
            assert ":" in overlay_xy_expr(pos, 24)
        brand_dir = settings.paths.output_root.parent / "05-品牌"
        brand_dir.mkdir(parents=True, exist_ok=True)
        logo_file = brand_dir / "logo.png"
        logo_file.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
            )
        )
        found = resolve_logo_path(settings, profile={"brand": {"logo_enabled": True}})
        assert found == logo_file, found

        # G3: publish_pack export in temp dir (no customer disk)
        from engine.pack.publish import export_publish_pack

        # Pack source stays inside the active customer's output root: API must
        # reject arbitrary filesystem paths even in isolated smoke mode.
        fake_mp4 = settings.paths.output_root / "ready" / "demo.mp4"
        fake_mp4.parent.mkdir(parents=True, exist_ok=True)
        # Real upload-contract fixture: MP4/H.264/AAC/yuv420p/1080x1920.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1080x1920:r=25:d=0.2",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(fake_mp4),
            ],
            check=True,
            capture_output=True,
        )
        side = {
            "title": "仓配一体｜工地一站配齐",
            "theme": "仓配",
            "copywriting": {
                "description": "测试描述",
                "hashtags": ["#仓配", "#本地发货"],
                "music_credit": "Music: test",
            },
            "covers": [],
            "meta": {},
        }
        (fake_mp4.with_suffix(".json")).write_text(
            json.dumps(side, ensure_ascii=False), encoding="utf-8"
        )
        man = export_publish_pack(
            output_path=fake_mp4,
            profile={"compliance": {"banned_terms": ["绝对第一"]}, "brand": {"display_name": "演示"}},
            pack_data={"compliance": {"blocked_terms": []}},
        )
        pack_dir = Path(man["pack_dir"])
        assert (pack_dir / "video.mp4").is_file()
        assert (pack_dir / "copy.zh.json").is_file()
        assert (pack_dir / "subtitle.zh.srt").is_file()
        assert (pack_dir / "compliance.json").is_file()
        assert (pack_dir / "manifest.json").is_file()
        assert man.get("compliance_passed") is True
        assert set(man.get("platforms") or []) == {"douyin", "channels", "xhs", "kuaishou"}
        assert all(
            (pack_dir / "platforms" / platform / "manifest.json").is_file()
            and (pack_dir / "platforms" / platform / "compliance.json").is_file()
            for platform in man["platforms"]
        )
        zh_copy = json.loads((pack_dir / "copy.zh.json").read_text(encoding="utf-8"))
        kuaishou_copy = (zh_copy.get("platforms") or {}).get("kuaishou") or {}
        assert len(kuaishou_copy.get("hashtags") or []) <= 4, kuaishou_copy
        assert str(kuaishou_copy.get("body") or "").count("#") <= 4, kuaishou_copy
        # G4.33 English variant: subtitle + copy + voiceover
        assert (pack_dir / "subtitle.en.srt").is_file()
        assert (pack_dir / "copy.en.json").is_file()
        assert (pack_dir / "voiceover.en.wav").is_file()
        assert "en" in (man.get("variants") or {})
        en_body = (pack_dir / "subtitle.en.srt").read_text(encoding="utf-8")
        assert any("a" <= c.lower() <= "z" for c in en_body), en_body[:200]

        # Publication isolation requires an explicit output_id bound to the
        # current pack. Use separate outputs for the raw queue and multi-target
        # from-pack contracts so their frozen publication groups cannot merge.
        second_pack_dir = pack_dir.parent / f"{pack_dir.name}-second"
        shutil.copytree(pack_dir, second_pack_dir)
        from engine.catalog.db import ReviewItem

        session = get_session()
        try:
            publish_output_ids: list[int] = []
            for current_pack in (pack_dir, second_pack_dir):
                publish_out = RenderOutput(
                    job_id=job_id,
                    output_path=str(fake_mp4),
                    state="ready",
                    seed=2 + len(publish_output_ids),
                    sidecar_path=str(fake_mp4.with_suffix(".json")),
                    qc_json={"ready_gate": {"ok": True}},
                    pack_status="ready",
                    pack_dir=str(current_pack),
                    pack_error="",
                    pack_version="smoke",
                    platform_asset_status={},
                )
                session.add(publish_out)
                session.flush()
                session.add(
                    ReviewItem(
                        render_output_id=publish_out.id,
                        status="approved",
                        is_current=True,
                        decision_source="smoke",
                        evidence_json={"ready_gate": True},
                    )
                )
                publish_output_ids.append(publish_out.id)
            session.commit()
        finally:
            session.close()

        # G4.34: narration preview API (mock TTS)
        prev = client.post(
            "/pack/narration/preview",
            json={
                "script": "仓配一体，本地发货更快。",
                "lang": "zh",
                "provider": "mock",
            },
        )
        assert prev.status_code == 200, prev.text
        pj = prev.json()
        assert pj.get("ok") is True
        assert pj.get("provider") == "mock"
        assert float(pj.get("total_duration_sec") or 0) > 0.5
        assert len(pj.get("duration_budget") or []) >= 1
        assert (pj.get("template") or {}).get("duration_from_tts") is True
        _assert_no_customer_disk(pj)

        # G5.40: reach queue skeleton (enqueue / list / status) — no auto-publish
        enq = client.post(
            "/reach/queue",
            json={
                "platform": "douyin",
                "title": "仓配一体",
                "body": "本地发货",
                "video_path": str(pack_dir / "video.mp4"),
                "pack_dir": str(pack_dir),
                "output_id": publish_output_ids[0],
                "note": "human_in_loop",
            },
        )
        assert enq.status_code == 200, enq.text
        ej = enq.json()
        assert ej.get("ok") is True and ej.get("auto_publish") is False
        rid = int(ej["item"]["id"])
        listed = client.get("/reach/queue")
        assert listed.status_code == 200, listed.text
        assert any(i["id"] == rid for i in listed.json().get("items") or [])
        st = client.post(
            f"/reach/queue/{rid}/status",
            json={"status": "awaiting_human"},
        )
        assert st.status_code == 200, st.text
        assert st.json()["item"]["status"] == "awaiting_human"
        bad = client.post(f"/reach/queue/{rid}/status", json={"status": "queued"})
        assert bad.status_code == 400
        _assert_no_customer_disk(ej)

        # G5.41: prefill from publish_pack
        from_pack = client.post(
            "/reach/queue/from-pack",
            json={
                "pack_dir": str(second_pack_dir),
                "platforms": ["douyin", "xhs"],
                "output_id": publish_output_ids[1],
            },
        )
        assert from_pack.status_code == 200, from_pack.text
        fp = from_pack.json()
        assert fp.get("count") == 2
        assert fp.get("auto_publish") is False
        assert all(i["status"] == "awaiting_human" for i in fp["items"])
        assert all(i.get("video_path") for i in fp["items"])
        assert all(i.get("title") for i in fp["items"])

        # G5.42: consecutive-failure circuit (daily quotas intentionally removed)
        from engine.catalog.db import get_session as _gs
        from engine.reach.circuit import assert_circuit_closed, record_failure
        from engine.reach.queue import get_item as _gi

        sess = _gs()
        try:
            cid = int(fp["items"][0]["customer_id"])
            for it in fp["items"]:
                row = _gi(sess, int(it["id"]), customer_id=cid)
                if row:
                    record_failure(sess, row, error="smoke_fail")
            extra = _gi(sess, rid, customer_id=cid)
            assert extra is not None
            record_failure(sess, extra, error="smoke_fail")
            blocked = False
            try:
                assert_circuit_closed(sess, customer_id=cid, fail_threshold=3)
            except ValueError as e:
                blocked = "暂停" in str(e) or "失败" in str(e)
            assert blocked, "circuit should block enqueue"
        finally:
            sess.close()

        # G5.43: open official entry (dry_run — no real browser in smoke)
        # circuit may block new enqueue; use an existing id or create via direct DB bypass for open test
        plats = client.get("/reach/platforms")
        assert plats.status_code == 200, plats.text
        pj = plats.json()
        plat_list = pj.get("platforms") or []
        plat_ids = {p["id"] for p in plat_list}
        assert plat_ids == {"douyin", "channels", "xhs", "kuaishou"}
        assert all(p.get("short") for p in plat_list)
        specs = pj.get("slot_specs") or {}
        assert len(specs.get("douyin") or []) == 1
        assert (specs.get("douyin") or [])[0].get("aspect") == "9:16"
        assert len(specs.get("channels") or []) == 1
        assert (specs.get("channels") or [])[0].get("aspect") == "6:7"
        assert len(specs.get("xhs") or []) == 1
        assert (specs.get("xhs") or [])[0].get("aspect") == "3:4"
        assert set(specs) == {"douyin", "channels", "xhs", "kuaishou"}
        # circuit may block new enqueue; use an existing id or create via direct DB bypass for open test
        # reset one failed → queued then open dry_run
        from engine.reach.queue import set_status as _ss

        sess2 = _gs()
        try:
            row = _gi(sess2, int(fp["items"][0]["id"]), customer_id=int(fp["items"][0]["customer_id"]))
            assert row is not None
            _ss(sess2, row, "queued")  # failed → queued allowed
            oid = row.id
        finally:
            sess2.close()
        opened = client.post(f"/reach/queue/{oid}/open", json={"dry_run": True})
        assert opened.status_code == 200, opened.text
        oj = opened.json()
        assert oj.get("ok") is True
        assert oj.get("auto_publish") is False
        assert oj.get("open", {}).get("url", "").startswith("http")
        assert Path(oj.get("paste_card") or "").is_file()

        # G5.43b: Chrome local profiles (list/create/open; no real browser launch)
        # Layout: <root>/customer-<id>/video/<profile> (video scope only)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            from engine.catalog.db import Customer, get_session as _gs2
            from sqlalchemy import select as _sel

            _sess = _gs2()
            try:
                _cust = _sess.scalar(_sel(Customer).where(Customer.name == settings.active_customer))
                assert _cust is not None
                cid = int(_cust.id)
            finally:
                _sess.close()
            scoped = root / f"customer-{cid}" / "video"
            scoped.mkdir(parents=True)
            (scoped / "smoke01").mkdir()
            (scoped / "accounts.txt").write_text("smoke01\n", encoding="utf-8")
            (scoped / "accounts.json").write_text(
                '{"smoke01": {"platform": "douyin", "label": "抖音创作者中心"}}\n',
                encoding="utf-8",
            )
            old_env = __import__("os").environ.get("SUYING_CHROME_PROFILES")
            __import__("os").environ["SUYING_CHROME_PROFILES"] = str(root)
            try:
                cps = client.get("/reach/chrome-profiles")
                assert cps.status_code == 200, cps.text
                cpj = cps.json()
                assert cpj.get("ok") is True
                assert cpj.get("auto_publish") is False
                assert cpj.get("business_scope") == "video"
                assert any(p["name"] == "smoke01" for p in (cpj.get("profiles") or []))
                plat_ids = {p["id"] for p in (cpj.get("platforms") or [])}
                assert "kuaishou" in plat_ids and "channels" in plat_ids and "xhs" in plat_ids
                assert "douyin" in plat_ids
                # Soft-article platforms must not appear in video chrome catalog
                assert "toutiao" not in plat_ids and "baijiahao" not in plat_ids
                assert "zhihu" not in plat_ids and "wechat_mp" not in plat_ids

                created = client.post(
                    "/reach/chrome-profiles/create",
                    json={"platform": "xhs", "count": 2},
                )
                assert created.status_code == 200, created.text
                cj = created.json()
                assert cj.get("count") == 2
                assert cj.get("selected")
                assert all(c["platform"] == "xhs" for c in (cj.get("created") or []))
                assert (scoped / "accounts.json").is_file()
                # create must not require launching Chrome
                assert Path(cj["created"][0]["path"]).is_dir()
                assert f"customer-{cid}/video" in str(cj["created"][0]["path"]).replace("\\", "/")

                cps2 = client.get("/reach/chrome-profiles")
                names = {p["name"] for p in (cps2.json().get("profiles") or [])}
                assert cj["created"][0]["name"] in names
                assert any(
                    p.get("platform") == "xhs" for p in (cps2.json().get("profiles") or [])
                )

                cop = client.post(
                    "/reach/chrome-profiles/open",
                    json={"name": cj["created"][0]["name"], "dry_run": True},
                )
                assert cop.status_code == 200, cop.text
                assert cop.json().get("platform") == "xhs"
                assert "xiaohongshu" in (cop.json().get("open") or {}).get("url", "")

                channels_created = client.post(
                    "/reach/chrome-profiles/create",
                    json={"platform": "channels", "count": 1},
                )
                assert channels_created.status_code == 200, channels_created.text
                channels_name = channels_created.json()["selected"]
                ch = client.post(
                    "/reach/chrome-profiles/open",
                    json={"name": channels_name, "platform": "channels", "dry_run": True},
                )
                assert ch.status_code == 200, ch.text
                assert ch.json().get("platform") == "channels"
                assert "channels.weixin" in (ch.json().get("open") or {}).get("url", "")

                sel = client.post(
                    "/reach/chrome-profiles/select",
                    json={"name": "smoke01", "platform": "douyin"},
                )
                assert sel.status_code == 200, sel.text
                assert sel.json().get("selected") == "smoke01"
                opened2 = client.post(
                    f"/reach/queue/{oid}/open",
                    json={"dry_run": True, "chrome_profile": "smoke01"},
                )
                assert opened2.status_code == 200, opened2.text
                o2 = opened2.json().get("open") or {}
                assert o2.get("chrome_profile") == "smoke01" or "user_data_dir" in o2

                # G5.V auto-upload dry_run while chrome root still valid
                bad_au = client.post(
                    "/reach/auto-upload/start",
                    json={"chrome_profile": "smoke01", "queue_id": oid, "accept_risk": False},
                )
                assert bad_au.status_code == 400
                au = client.post(
                    "/reach/auto-upload/start",
                    json={
                        "chrome_profile": "smoke01",
                        "queue_id": oid,
                        "platform": "douyin",
                        "accept_risk": True,
                        "dry_run": True,
                    },
                )
                assert au.status_code == 200, au.text
                assert au.json().get("ok") is True
                import time as _time

                _time.sleep(0.5)
                aus = client.get("/reach/auto-upload/status")
                assert aus.status_code == 200, aus.text
                assert aus.json().get("ok") is True
                assert aus.json().get("phase") in ("done", "starting", "waiting_login", "idle")
                auc = client.post("/reach/auto-upload/cancel")
                assert auc.status_code == 200
            finally:
                if old_env is None:
                    __import__("os").environ.pop("SUYING_CHROME_PROFILES", None)
                else:
                    __import__("os").environ["SUYING_CHROME_PROFILES"] = old_env

        # G5.44: local message hub
        inbox = client.get("/reach/inbox")
        assert inbox.status_code == 200, inbox.text
        ib = inbox.json()
        assert ib.get("ok") is True
        assert ib.get("auto_reply") is False
        assert ib.get("scrapes_platform_inbox") is False
        assert isinstance(ib.get("notices"), list)
        assert "unread_count" in ib

        # GContent: SEO/GEO sources → generate → approve → dry-run publish
        from sqlalchemy import select as sa_select

        from engine.catalog.db import Customer, get_session as _gs

        _sess = _gs()
        try:
            crow = _sess.scalar(sa_select(Customer).where(Customer.name == settings.active_customer))
            if crow is None:
                crow = _sess.scalars(sa_select(Customer)).first()
            assert crow is not None
            prof = dict(crow.profile_json or {})
            brand = dict(prof.get("brand") or {})
            brand["display_name"] = brand.get("display_name") or crow.name or "演示品牌"
            prof["brand"] = brand
            crow.profile_json = prof
            _sess.commit()
        finally:
            _sess.close()

        for i in range(1, 4):
            src = client.post(
                "/content/sources",
                json={
                    "title": f"smoke-fact-{i}",
                    "body": f"冒烟已确认事实{i}：本地可核验服务说明条目。",
                    "source_url": f"https://example.com/f{i}",
                    "verified": True,
                    "verified_by": "smoke",
                },
            )
            assert src.status_code == 200, src.text
            client.post(f"/content/sources/{src.json()['source']['id']}/verify")
        site = client.post(
            "/content/sites",
            json={
                "domain": "smoke.example.com",
                "sitemap_url": "https://smoke.example.com/sitemap.xml",
            },
        )
        assert site.status_code == 200, site.text
        comp = client.get("/content/completeness")
        assert comp.status_code == 200, comp.text
        cj = comp.json()
        assert cj.get("can_publish") is True, cj
        plats = client.get("/content/platforms")
        assert plats.status_code == 200, plats.text
        assert any(p.get("id") == "baijiahao" for p in plats.json().get("platforms") or [])
        gen = client.post(
            "/content/articles/generate",
            json={"topic": "冒烟配送说明", "platforms": ["website", "baijiahao"]},
        )
        assert gen.status_code == 200, gen.text
        article_id = gen.json()["article"]["id"]
        variant_id = gen.json()["variants"][0]["id"]
        appr = client.post(
            f"/content/articles/{article_id}/approve",
            json={"approved_by": "smoke"},
        )
        assert appr.status_code == 200, appr.text
        job = client.post("/content/jobs", json={"variant_id": variant_id})
        assert job.status_code == 200, job.text
        jid = job.json()["job"]["id"]
        run = client.post(f"/content/jobs/{jid}/run", json={"dry_run": True})
        assert run.status_code == 200, run.text
        assert run.json().get("ok") is True
        assert run.json().get("validated") is True
        assert run.json()["job"]["status"] == "validated"
        # idempotent re-enqueue returns same key job
        job2 = client.post("/content/jobs", json={"variant_id": variant_id})
        assert job2.status_code == 200, job2.text
        assert job2.json()["job"]["id"] == jid

        # GVideoRules · schema / draft / approve / activate / dry-run freeze
        schema = client.get("/production-rules/schema")
        assert schema.status_code == 200, schema.text
        assert schema.json().get("ok") is True
        assert schema.json().get("hard_locks")
        rec_cats = schema.json().get("recommended_content_categories") or []
        assert "scene_tour" in rec_cats, "schema must recommend scene_tour (跟镜精品)"

        # 跟镜精品 · labels / hot-inbox / coverage / dry-run（无规则时中文拒片）
        st_labels = client.get("/scene-tour/labels")
        assert st_labels.status_code == 200, st_labels.text
        assert st_labels.json()["categories"]["scene_tour"] == "跟镜精品"
        assert "生成跟镜精品" in str(st_labels.json().get("ui") or {})
        hot = client.get("/scene-tour/hot-inbox")
        assert hot.status_code == 200, hot.text
        assert hot.json().get("enabled") is False
        assert "第二版" in str(hot.json().get("message") or "")
        cov = client.get("/scene-tour/coverage")
        assert cov.status_code == 200, cov.text
        dry_st = client.post("/scene-tour/dry-run")
        assert dry_st.status_code == 200, dry_st.text
        dry_body = dry_st.json()
        assert dry_body.get("blocked") is True or dry_body.get("ok") is False
        reasons_blob = " ".join(str(x) for x in (dry_body.get("reasons") or []))
        assert reasons_blob, "跟镜试规划拒片须有中文原因"
        assert "scene_tour" not in reasons_blob
        assert "fail-closed" not in reasons_blob.lower()

        created_rule = client.post(
            "/production-rules",
            json={
                "name": "smoke-rule",
                "source_text": "节奏快一点，多拍产品细节，旁白朴实",
                "rules": {
                    "pace": "fast",
                    "narration_tone": "plain",
                    "prefer_semantic_labels": ["product_closeup"],
                    "skip_ready_gate": True,
                    "min_cliplet_quality": 0.1,
                },
            },
        )
        assert created_rule.status_code == 200, created_rule.text
        rule = created_rule.json()["rule"]
        assert rule["status"] == "draft"
        assert any("skip_ready_gate" in str(x) for x in rule.get("rejected") or [])
        assert any("min_cliplet_quality" in str(x) for x in rule.get("rejected") or [])
        rid = rule["id"]
        assert client.post(f"/production-rules/{rid}/approve", json={"approved_by": "smoke"}).status_code == 200
        assert client.post(f"/production-rules/{rid}/activate").status_code == 200
        active_rule = client.get("/production-rules/active")
        assert active_rule.status_code == 200
        assert active_rule.json()["rule"]["id"] == rid
        dry_rule = client.post(
            "/dry-run",
            json={
                "theme": "default",
                "category": "default",
                "use_active_rule": True,
                "rule_rotation": False,
            },
        )
        assert dry_rule.status_code == 200, dry_rule.text
        assert dry_rule.json().get("production_rules", {}).get("active", {}).get("rule_profile_id") == rid

        # G5.V cover templates + publish hard gate (copy + cover slots)
        from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

        data_root = settings.paths.data_root
        # missing body → gate fails
        try:
            require_publish_assets(
                platform="douyin",
                pack_dir=pack_dir,
                data_root=data_root,
                title="t",
                body="",
            )
            raise AssertionError("expected PublishAssetsError for empty body")
        except PublishAssetsError as e:
            assert "文案" in str(e)

        # Publishing never falls back to pack covers when App has no selected template.
        one_cover = pack_dir
        try:
            require_publish_assets(
                platform="channels",
                pack_dir=one_cover,
                data_root=data_root,
            )
            raise AssertionError("expected PublishAssetsError for missing cover slot")
        except PublishAssetsError as e:
            assert "封面" in str(e) or "槽" in str(e)

        # API: create / select / set slot / resolve
        ct = client.post("/reach/cover-templates", json={"name": "smoke-cover"})
        assert ct.status_code == 200, ct.text
        tid = ct.json()["template"]["id"]
        ctl = client.get("/reach/cover-templates")
        assert ctl.status_code == 200, ctl.text
        ctlj = ctl.json()
        assert ctlj.get("slot_counts", {}).get("douyin") == 1
        assert ctlj.get("slot_counts", {}).get("kuaishou") == 1
        assert len((ctlj.get("slot_specs") or {}).get("douyin") or []) == 1
        assert (ctlj.get("slot_specs") or {}).get("channels", [{}])[0].get("aspect") == "6:7"
        sel = client.post("/reach/cover-templates/select", json={"template_id": tid})
        assert sel.status_code == 200, sel.text
        assert sel.json().get("selected_id") == tid
        from PIL import Image

        img = base / "slot.jpg"
        Image.new("RGB", (90, 160), color=(20, 40, 80)).save(img)
        slot = client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "douyin", "slot_index": 0, "source_path": str(img)},
        )
        assert slot.status_code == 200, slot.text
        assert slot.json()["template"]["complete"]["douyin"] is True
        img_h = base / "slot_h.jpg"
        Image.new("RGB", (160, 90), color=(80, 40, 20)).save(img_h)
        rejected_horizontal = client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "douyin", "slot_index": 0, "source_path": str(img_h)},
        )
        assert rejected_horizontal.status_code == 400, rejected_horizontal.text
        dy0 = (slot.json()["template"]["slots_detail"]["douyin"] or [])[0]
        assert dy0.get("label") == "竖封面" and dy0.get("aspect") == "9:16"
        # Channels also has exactly one vertical App cover.
        client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "channels", "slot_index": 0, "source_path": str(img)},
        )
        # now channels gate passes with template
        ok_assets = require_publish_assets(
            platform="channels",
            pack_dir=one_cover,
            data_root=data_root,
            template_id=tid,
        )
        assert ok_assets["ok"] is True
        assert len(ok_assets["covers"]) == 1
        # An explicit template that differs from App selection is rejected.
        try:
            require_publish_assets(
                platform="douyin",
                pack_dir=one_cover,
                data_root=data_root,
                template_id="__no_such_template__",
            )
            raise AssertionError("expected PublishAssetsError for mismatched App template")
        except PublishAssetsError as e:
            assert "封面" in str(e) or "槽" in str(e)
        resolved = client.post(
            "/reach/cover-templates/resolve",
            json={"platform": "douyin", "pack_dir": str(one_cover), "template_id": tid},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolve"]["ok"] is True
        assert resolved.json()["gate"]["ok"] is True
        assert len(resolved.json()["resolve"]["covers"]) == 1

        # G4.30: TTS adapter — script → segment WAVs + real durations
        from engine.pack.tts import estimate_duration_sec, split_script, synthesize_script

        script = "仓配一体，工地一站配齐。本地发货更快。Welcome to the warehouse."
        parts = split_script(script)
        assert len(parts) >= 2, parts
        assert estimate_duration_sec(parts[0], lang="zh") >= 0.5
        tts_dir = base / "tts_out"
        narr = synthesize_script(script, tts_dir, lang="zh", provider="mock")
        assert narr.total_duration_sec > 1.0, narr.total_duration_sec
        assert len(narr.segments) >= 2
        for seg in narr.segments:
            assert Path(seg.audio_path).is_file()
            assert seg.duration_sec > 0.3
        assert Path(narr.manifest_path).is_file()

        # G4.31: TTS duration budget → template slots
        from engine.pack.narration_plan import duration_budget_from_narration, template_from_narration
        from engine.template.engine import DEFAULT_TEMPLATE, template_with_duration_budget

        budget = duration_budget_from_narration(narr)
        assert len(budget) == len(narr.segments)
        timed = template_from_narration(DEFAULT_TEMPLATE, narr)
        assert timed.duration_from_tts is True
        assert len(timed.slots) == len(budget)
        assert abs(timed.target_duration - sum(budget)) < 0.05
        for slot, dur in zip(timed.slots, budget):
            assert slot.min_duration <= dur + 0.2
            assert slot.max_duration >= dur - 0.05
        # empty budget falls back to 3 default-ish slots via helper
        empty = template_with_duration_budget(DEFAULT_TEMPLATE, [])
        assert len(empty.slots) >= 3

        print(
            json.dumps(
                {
                    "ok": True,
                    "e2_6_isolated": True,
                    "g3_publish_pack": True,
                    "g4_tts_adapter": True,
                    "g4_duration_budget": True,
                    "g4_en_variant": True,
                    "g4_narration_api": True,
                    "g5_reach_queue": True,
                    "g5_reach_complete": True,
                    "tts_segments": len(narr.segments),
                    "tts_total_sec": narr.total_duration_sec,
                    "budget_slots": len(timed.slots),
                    "health_status": hbody.get("status"),
                    "dry_run_blocked": body.get("blocked"),
                    "rerender_job_id": rr_job_id,
                    "data_root": str(settings.paths.data_root),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
