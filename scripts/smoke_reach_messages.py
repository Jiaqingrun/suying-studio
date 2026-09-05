#!/usr/bin/env python3
"""Offline G7 smoke: tenant isolation, idempotency, claim and Chrome safety."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from engine.api.app import app  # noqa: E402
from engine.catalog.customer_scope import get_or_create_customer  # noqa: E402
from engine.catalog.db import (  # noqa: E402
    ReachMessage,
    ReachMessageAccount,
    get_session,
    init_db,
    reset_engine,
)
from engine.config.settings import AppSettings, PathConfig, save_settings  # noqa: E402
from engine.reach.browser import chrome_launch_args  # noqa: E402
from engine.reach.chrome_runtime import ManagedChrome  # noqa: E402
from engine.reach.message_sync import MESSAGE_SCAN_INTERVAL_SEC, MessageSync  # noqa: E402
from engine.reach.messages_adapters import SPECS, validate_official_url  # noqa: E402
from engine.reach.notifications import redact_summary, validate_ntfy_server  # noqa: E402


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
            active_customer="客户A",
            onboarded=True,
        )
        for path in (
            settings.paths.library_root,
            settings.paths.output_root,
            settings.paths.cache_root,
            settings.paths.data_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        os.environ["MONTAGE_DATA_ROOT"] = str(settings.paths.data_root)
        os.environ["SUYING_CHROME_PROFILES"] = str(base / "chrome-profiles")
        reset_engine()
        save_settings(settings)
        init_db(settings)

        assert len(SPECS) == 8, sorted(SPECS)
        assert MESSAGE_SCAN_INTERVAL_SEC == 1800
        assert all(spec.message_url.startswith("https://") for spec in SPECS.values())
        assert "--headless=new" in chrome_launch_args(
            base / "profile", SPECS["douyin"].message_url, cdp_port=9222, headless=True
        )
        assert redact_summary("电话 13800138000 邮箱 a@example.com") == "电话 [已脱敏] 邮箱 [已脱敏]"
        try:
            validate_ntfy_server("https://127.0.0.1")
        except ValueError:
            pass
        else:
            raise AssertionError("ntfy SSRF guard accepted loopback")
        for platform, spec in SPECS.items():
            assert validate_official_url(platform, spec.message_url) == spec.message_url
            try:
                validate_official_url(platform, "https://evil.example/messages")
            except ValueError:
                pass
            else:
                raise AssertionError(f"{platform} accepted non-official host")

        # dry_run must not create a process or profile directory.
        dry_profile = base / "chrome-profiles" / "dry-profile"
        dry = ManagedChrome(
            "dry-profile",
            SPECS["douyin"].message_url,
            customer_id=1,
            business_scope="video",
            dry_run=True,
        ).start()
        assert dry["started"] is False and dry["pid"] is None
        assert not dry_profile.exists()

        with TestClient(app) as client:
            # Ensure active customer exists; message API creates its own empty profile.
            session0 = get_session()
            try:
                cust_a = get_or_create_customer(session0, "客户A")
                session0.commit()
                assert int(cust_a.id) > 0
            finally:
                session0.close()
            created = client.post(
                "/reach/message-accounts",
                json={"platform": "douyin", "profile_name": "g7-a", "display_name": "本人账号"},
            )
            assert created.status_code == 200, created.text
            account_id = int(created.json()["account"]["id"])
            assert created.json()["account"]["cooldown_sec"] == 1800
            unlocked = client.post(
                "/reach/message-accounts",
                json={
                    "platform": "xhs",
                    "profile_name": "g7-unlocked",
                    "cooldown_sec": 300,
                },
            )
            # 未存在 profile / 或冷却不可改 → 400/422
            assert unlocked.status_code in (400, 422), unlocked.text

            ntfy = client.put(
                "/reach/notifications/ntfy",
                json={
                    "enabled": False,
                    "server_url": "",
                    "topic": "",
                    "auth_mode": "none",
                    "token": "must-not-echo",
                },
            )
            assert ntfy.status_code == 200, ntfy.text
            assert "must-not-echo" not in ntfy.text
            assert ntfy.json()["config"]["token_configured"] is True

            scan = client.post(
                "/reach/messages/scan", json={"account_id": account_id, "dry_run": True}
            )
            assert scan.status_code == 200, scan.text
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = client.get("/reach/messages/status").json()
                if status["scans"] and status["scans"][0]["status"] == "completed":
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("dry-run scan did not complete")

            session = get_session()
            try:
                account = session.get(ReachMessageAccount, account_id)
                assert account is not None
                item = {
                    "external_key": "same-message",
                    "sender": "张三",
                    "summary": "请问今天可以发货吗？",
                    "reply_url": "https://creator.douyin.com/creator-micro/message/1",
                    "source": "smoke_fixture",
                    "confidence": 1.0,
                }
                assert len(MessageSync._upsert_messages(session, account, [item])) == 1
                session.commit()
                assert MessageSync._upsert_messages(session, account, [item]) == []
                session.commit()
                count = len(
                    session.scalars(
                        select(ReachMessage).where(ReachMessage.account_id == account_id)
                    ).all()
                )
                assert count == 1, count
                saved = session.scalar(
                    select(ReachMessage).where(ReachMessage.account_id == account_id)
                )
                assert saved is not None
                saved.unread = False
                session.commit()
                MessageSync._upsert_messages(session, account, [item])
                session.commit()
                assert saved.unread is False, "重复扫描不得把本地已读重新标成未读"
                saved.unread = True
                session.commit()
                get_or_create_customer(session, "客户B")
            finally:
                session.close()

            listed = client.get("/reach/messages", params={"unread": True})
            assert listed.status_code == 200 and len(listed.json()["messages"]) == 1
            first_claim = client.post("/reach/notifications/claim")
            second_claim = client.post("/reach/notifications/claim")
            assert len(first_claim.json()["messages"]) == 1
            assert second_claim.json()["messages"] == []
            message_id = int(first_claim.json()["messages"][0]["id"])
            for channel in ("app", "macos"):
                report = client.post(
                    "/reach/notifications/report",
                    json={"message_id": message_id, "channel": channel, "status": "sent"},
                )
                assert report.status_code == 200, report.text
            with patch(
                "engine.reach.notifications.send_ntfy",
                return_value={"sent": True, "status": "sent", "test": True},
            ):
                tested = client.post("/reach/notifications/ntfy/test")
            assert tested.status_code == 200 and tested.json()["test"] is True
            events = client.get("/reach/notifications/events").json()["events"]
            assert {"app", "macos", "ntfy"}.issubset({event["channel"] for event in events})
            assert any(event["channel"] == "ntfy" and event["is_test"] for event in events)

            bad = client.patch(
                f"/reach/message-accounts/{account_id}",
                json={"message_url": "https://evil.example/messages"},
            )
            assert bad.status_code == 400

            # Active-customer switch must hide A's account and message from B.
            settings.active_customer = "客户B"
            save_settings(settings)
            assert client.get("/reach/message-accounts").json()["accounts"] == []
            assert client.get("/reach/messages").json()["messages"] == []
            cross_tenant_profile = client.post(
                "/reach/message-accounts",
                json={"platform": "douyin", "profile_name": "g7-a"},
            )
            # Same display name is valid in another customer's isolated tree.
            assert cross_tenant_profile.status_code == 200, cross_tenant_profile.text
            assert (
                cross_tenant_profile.json()["account"]["customer_id"]
                != created.json()["account"]["customer_id"]
            )
            settings.active_customer = "客户A"
            save_settings(settings)

            opened = client.post(
                f"/reach/messages/{message_id}/open", json={"dry_run": True}
            )
            assert opened.status_code == 200, opened.text
            assert opened.json()["opened"]["dry_run"] is True
            assert opened.json()["auto_reply"] is False

        guarded_sources = [
            ROOT / "engine" / "reach" / "chrome_runtime.py",
            ROOT / "engine" / "reach" / "auto_upload.py",
        ]
        forbidden = "kill" + "all"
        assert all(forbidden not in path.read_text(encoding="utf-8") for path in guarded_sources)
        print("SMOKE_REACH_MESSAGES OK")


if __name__ == "__main__":
    main()
