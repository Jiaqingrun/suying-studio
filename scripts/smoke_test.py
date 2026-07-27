#!/usr/bin/env python3
"""Smoke test for Montage Studio MVP engine.

E2.6: must run in a temp workspace and never require 始峰 / QR-Volume paths.
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
from engine.catalog.db import init_db, reset_engine  # noqa: E402
from engine.config.settings import AppSettings, PathConfig, save_settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


FORBIDDEN_PATH_MARKERS = ("始峰", "QR-Volume", "徐玲飞", "车凯盛")


def _assert_no_customer_disk(payload: object) -> None:
    blob = json.dumps(payload, ensure_ascii=False)
    for m in FORBIDDEN_PATH_MARKERS:
        assert m not in blob, f"smoke leaked customer-disk marker {m!r}: {blob[:400]}"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        settings = AppSettings(
            paths=PathConfig(
                library_root=base / "library",
                output_root=base / "output",
                cache_root=base / "cache",
                data_root=base / "data",
                external_required=False,
            ),
            active_customer="演示客户",
            onboarded=True,
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
                        "WHERE type='trigger' AND name LIKE 'trg_daily_usage_cap_%'"
                    )
                ).fetchall()
            }
            assert triggers == {
                "trg_daily_usage_cap_insert",
                "trg_daily_usage_cap_update",
            }, triggers

        client = TestClient(app)
        health = client.get("/health")
        assert health.status_code == 200, health.text
        hbody = health.json()
        assert hbody["status"] in ("ok", "degraded")
        _assert_no_customer_disk(hbody.get("paths") or {})
        assert str(hbody.get("paths", {}).get("data_root", "")).startswith(str(base))

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
            imp = client.post(
                "/keywords/import",
                params={"customer_name": "sample", "path": str(sample)},
            )
            assert imp.status_code == 200, imp.text

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
            },
        )
        assert job.status_code == 200, job.text
        job_id = int(job.json()["id"])

        # E1.C2: reject + one-click re-render queues a count=1 job excluding source clips
        from engine.catalog.db import RenderOutput, get_session

        session = get_session()
        try:
            out = RenderOutput(
                job_id=job_id,
                output_path=str(base / "fake.mp4"),
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
        assert rej.get("rerender_job") and rej["rerender_job"].get("id"), rej
        rr_job_id = int(rej["rerender_job"]["id"])

        session = get_session()
        try:
            from engine.catalog.db import Job

            rr = session.get(Job, rr_job_id)
            assert rr is not None
            assert rr.target_count == 1
            snap = rr.config_snapshot_json or {}
            assert snap.get("rerender_of_output_id") == output_id
            assert snap.get("reject_reason") == "dark_blur"
        finally:
            session.close()

        again = client.post(f"/review/{output_id}/rerender", params={"reason": "reuse"})
        assert again.status_code == 200, again.text
        assert again.json()["job"]["id"]

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
        # minimal valid-enough file for copy (not a real mp4 decode)
        fake_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
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
        # G4.33 English variant: subtitle + copy + voiceover
        assert (pack_dir / "subtitle.en.srt").is_file()
        assert (pack_dir / "copy.en.json").is_file()
        assert (pack_dir / "voiceover.en.wav").is_file()
        assert "en" in (man.get("variants") or {})
        en_body = (pack_dir / "subtitle.en.srt").read_text(encoding="utf-8")
        assert any("a" <= c.lower() <= "z" for c in en_body), en_body[:200]

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
                "video_path": str(base / "fake.mp4"),
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
            json={"pack_dir": str(pack_dir), "platforms": ["douyin", "xhs"]},
        )
        assert from_pack.status_code == 200, from_pack.text
        fp = from_pack.json()
        assert fp.get("count") == 2
        assert fp.get("auto_publish") is False
        assert all(i["status"] == "awaiting_human" for i in fp["items"])
        assert all(i.get("video_path") for i in fp["items"])
        assert all(i.get("title") for i in fp["items"])

        # G5.42: quota + circuit
        q = client.get("/reach/quota")
        assert q.status_code == 200, q.text
        qj = q.json()
        assert qj.get("ok") is True
        assert "daily_quota" in qj and "used_today" in qj
        assert qj.get("auto_publish") is False
        from engine.catalog.db import get_session as _gs
        from engine.reach.limits import assert_can_enqueue, record_failure
        from engine.reach.queue import enqueue as _enq
        from engine.reach.queue import get_item as _gi

        sess = _gs()
        try:
            cid = int(fp["items"][0]["customer_id"])
            for it in fp["items"]:
                row = _gi(sess, int(it["id"]), customer_id=cid)
                if row:
                    record_failure(sess, row, error="smoke_fail")
            extra = _enq(sess, customer_id=cid, platform="douyin", title="circuit")
            record_failure(sess, extra, error="smoke_fail")
            blocked = False
            try:
                assert_can_enqueue(sess, customer_id=cid, fail_threshold=3)
            except ValueError as e:
                blocked = "熔断" in str(e) or "失败" in str(e)
            assert blocked, "circuit should block enqueue"
        finally:
            sess.close()

        # G5.43: open official entry (dry_run — no real browser in smoke)
        # circuit may block new enqueue; use an existing id or create via direct DB bypass for open test
        plats = client.get("/reach/platforms")
        assert plats.status_code == 200, plats.text
        pj = plats.json()
        plat_list = pj.get("platforms") or []
        assert len(plat_list) >= 7
        plat_ids = {p["id"] for p in plat_list}
        for need in ("douyin", "channels", "xhs", "kuaishou", "baijiahao", "toutiao", "zhihu"):
            assert need in plat_ids, need
        assert "haokan" not in plat_ids
        assert "wechat_mp" not in plat_ids
        assert "xigua" not in plat_ids
        assert all(p.get("short") for p in plat_list)
        specs = pj.get("slot_specs") or {}
        assert len(specs.get("douyin") or []) == 2
        assert (specs.get("douyin") or [])[0].get("aspect") == "9:16"
        assert (specs.get("douyin") or [])[1].get("aspect") == "16:9"
        assert len(specs.get("channels") or []) == 2
        assert (specs.get("channels") or [])[0].get("aspect") == "6:7"
        assert len(specs.get("xhs") or []) == 1
        assert (specs.get("xhs") or [])[0].get("aspect") == "3:4"
        assert len(specs.get("zhihu") or []) == 2
        assert len(specs.get("toutiao") or []) == 1
        assert len(specs.get("baijiahao") or []) == 1
        assert "haokan" not in specs
        assert "wechat_mp" not in specs
        assert "xigua" not in specs
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
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "smoke01").mkdir()
            (root / "accounts.txt").write_text("smoke01\n", encoding="utf-8")
            old_env = __import__("os").environ.get("SUYING_CHROME_PROFILES")
            __import__("os").environ["SUYING_CHROME_PROFILES"] = str(root)
            try:
                cps = client.get("/reach/chrome-profiles")
                assert cps.status_code == 200, cps.text
                cpj = cps.json()
                assert cpj.get("ok") is True
                assert cpj.get("auto_publish") is False
                assert any(p["name"] == "smoke01" for p in (cpj.get("profiles") or []))
                plat_ids = {p["id"] for p in (cpj.get("platforms") or [])}
                assert "kuaishou" in plat_ids and "channels" in plat_ids and "xhs" in plat_ids
                assert "toutiao" in plat_ids and "baijiahao" in plat_ids and "zhihu" in plat_ids

                created = client.post(
                    "/reach/chrome-profiles/create",
                    json={"platform": "xhs", "count": 2},
                )
                assert created.status_code == 200, created.text
                cj = created.json()
                assert cj.get("count") == 2
                assert cj.get("selected")
                assert all(c["platform"] == "xhs" for c in (cj.get("created") or []))
                assert (root / "accounts.json").is_file()
                # create must not require launching Chrome
                assert Path(cj["created"][0]["path"]).is_dir()

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

                ch = client.post(
                    "/reach/chrome-profiles/open",
                    json={"name": "smoke01", "platform": "channels", "dry_run": True},
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

        # G5.V cover templates + publish hard gate (copy + cover slots)
        from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

        data_root = settings.paths.data_root
        # missing body → gate fails
        empty_pack = base / "empty_pack"
        empty_pack.mkdir()
        (empty_pack / "video.mp4").write_bytes(b"\x00\x00")
        (empty_pack / "cover.jpg").write_bytes(b"fakejpg")
        (empty_pack / "cover_2.jpg").write_bytes(b"fakejpg2")
        try:
            require_publish_assets(
                platform="douyin",
                pack_dir=empty_pack,
                data_root=data_root,
                title="t",
                body="",
            )
            raise AssertionError("expected PublishAssetsError for empty body")
        except PublishAssetsError as e:
            assert "文案" in str(e)

        # channels needs 2 covers by default — only 1 → fail when no template
        one_cover = base / "one_cover_pack"
        one_cover.mkdir()
        (one_cover / "video.mp4").write_bytes(b"\x00")
        (one_cover / "cover.jpg").write_bytes(b"j")
        (one_cover / "copy.zh.json").write_text(
            json.dumps(
                {
                    "platforms": {
                        "channels": {"title": "短标题", "body": "视频号描述正文"},
                        "douyin": {"title": "抖音标题", "body": "抖音正文足够长"},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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
        assert ctlj.get("slot_counts", {}).get("douyin") == 2
        assert ctlj.get("slot_counts", {}).get("kuaishou") == 2
        assert len((ctlj.get("slot_specs") or {}).get("douyin") or []) == 2
        assert (ctlj.get("slot_specs") or {}).get("channels", [{}])[0].get("aspect") == "6:7"
        sel = client.post("/reach/cover-templates/select", json={"template_id": tid})
        assert sel.status_code == 200, sel.text
        assert sel.json().get("selected_id") == tid
        img = base / "slot.jpg"
        img.write_bytes(b"jpgdata")
        slot = client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "douyin", "slot_index": 0, "source_path": str(img)},
        )
        assert slot.status_code == 200, slot.text
        assert slot.json()["template"]["complete"]["douyin"] is False  # needs 2 slots
        img_h = base / "slot_h.jpg"
        img_h.write_bytes(b"jpgdataH")
        slot2 = client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "douyin", "slot_index": 1, "source_path": str(img_h)},
        )
        assert slot2.status_code == 200, slot2.text
        assert slot2.json()["template"]["complete"]["douyin"] is True
        dy0 = (slot2.json()["template"]["slots_detail"]["douyin"] or [])[0]
        assert dy0.get("label") == "竖封面" and dy0.get("aspect") == "9:16"
        # second cover for channels
        img2 = base / "slot2.jpg"
        img2.write_bytes(b"jpgdata2")
        client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "channels", "slot_index": 0, "source_path": str(img)},
        )
        client.post(
            f"/reach/cover-templates/{tid}/slot",
            json={"platform": "channels", "slot_index": 1, "source_path": str(img2)},
        )
        # now channels gate passes with template
        ok_assets = require_publish_assets(
            platform="channels",
            pack_dir=one_cover,
            data_root=data_root,
            template_id=tid,
        )
        assert ok_assets["ok"] is True
        assert len(ok_assets["covers"]) == 2
        # douyin with only pack cover.jpg (1 file) should fail gate needing 2
        try:
            require_publish_assets(
                platform="douyin",
                pack_dir=one_cover,
                data_root=data_root,
                template_id="__no_such_template__",
            )
            raise AssertionError("expected PublishAssetsError for douyin missing second cover")
        except PublishAssetsError as e:
            assert "封面" in str(e) or "槽" in str(e)
        resolved = client.post(
            "/reach/cover-templates/resolve",
            json={"platform": "douyin", "pack_dir": str(one_cover), "template_id": tid},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolve"]["ok"] is True
        assert resolved.json()["gate"]["ok"] is True
        assert len(resolved.json()["resolve"]["covers"]) == 2

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
