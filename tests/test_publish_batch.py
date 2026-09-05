"""G5.BATCH publish runner / schedule / verify tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from engine.api.reach_publish_routes import (
    ScheduleTaskCreateRequest,
    _validate_schedule_request,
    create_schedule_task,
)
from engine.reach.cdp_publish import wait_upload_ready
from engine.reach.publish_runner import (
    ACTIVE_RUN_STATUSES,
    confirm_item_outcome,
    get_active_run,
    login_wall_grace_sec,
    preflight_items,
    reconcile_stale_runs,
    resume_run,
    run_to_dict,
    skip_current_item,
    _await_login_wall_resolution,
    _defer_item_pre_submit,
    _defer_true_login_wall_fail_forward,
    _halt_for_true_login_wall,
    _record_run_finish_summary,
)
from engine.reach.publish_verify import is_outcome_unknown, is_verified_success, merge_verify


class PublishVerifyTests(unittest.TestCase):
    def test_verified_requires_inline_ok(self) -> None:
        pub = {"ok": True, "verify": {"ok": True, "reason": "success_text"}, "pub_clicked": True}
        self.assertTrue(is_verified_success(pub))

    def test_clicked_not_verified(self) -> None:
        pub = {"ok": False, "pub_clicked": True, "verify": {"ok": False, "reason": "unknown"}}
        self.assertFalse(is_verified_success(pub))
        self.assertTrue(is_outcome_unknown(pub))

    def test_merge_verify_from_publish(self) -> None:
        pub = {"publish": {"verify": {"ok": True}}}
        self.assertTrue(merge_verify(pub).get("ok"))


class UploadReadyRegressionTests(unittest.TestCase):
    def test_douyin_static_uploading_help_is_not_a_progress_signal(self) -> None:
        session = MagicMock()
        expressions: list[str] = []

        def evaluate(expression: str) -> dict[str, object]:
            expressions.append(expression)
            return {"state": "form", "reason": "generic_preview"}

        session.evaluate.side_effect = evaluate
        with patch(
            "engine.reach.vision_reach.snapshot_page",
            return_value={"url": "https://creator.douyin.com/creator-micro/content/post/video"},
        ):
            result = wait_upload_ready(session, timeout=0.1)

        self.assertTrue(result["ok"])
        self.assertTrue(expressions)
        self.assertNotIn("取消上传|上传中|转码中", expressions[0])
        self.assertIn("取消上传|转码中|正在上传", expressions[0])


class PublishRunnerPreflightTests(unittest.TestCase):
    def test_platform_mismatch_rejected(self) -> None:
        session = MagicMock()
        with patch("engine.reach.publish_runner.resolve_profile_platform", return_value="channels"):
            result = preflight_items(
                    session,
                    customer_id=1,
                    items=[
                        {
                            "platform": "douyin",
                            "chrome_profile": "抖音-01",
                            "pack_dir": "/tmp/fake.publish_pack",
                        }
                    ],
                )
        self.assertFalse(result["ok"])
        self.assertTrue(result["problems"])

    def test_create_run_sets_legacy_cursor_handoff_false(self) -> None:
        """Customer DBs still have NOT NULL cursor_handoff_enabled; inserts must set it."""
        import tempfile
        from pathlib import Path

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from engine.catalog.db import Base, Customer
        from engine.reach.publish_runner import create_run

        db_path = Path(tempfile.mkdtemp()) / "batch.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        customer = Customer(
            name="批次客户",
            library_root="/tmp/lib",
            output_root="/tmp/out",
            profile_json={},
        )
        session.add(customer)
        session.commit()
        session.refresh(customer)
        item = {
            "platform": "douyin",
            "chrome_profile": "抖音-01",
            "output_id": 1,
            "title": "t",
            "video_path": "/tmp/a.mp4",
            "pack_dir": "/tmp/a.pack",
        }
        with (
            patch(
                "engine.reach.publish_runner.preflight_items",
                return_value={"ok": True, "items": [item]},
            ),
            patch("engine.reach.publish_runner._audit_publish"),
            patch("engine.reach.publish_runner.reconcile_stale_runs", return_value=0),
            patch("engine.reach.publish_runner.get_active_run", return_value=None),
        ):
            run = create_run(
                session,
                customer_id=customer.id,
                items=[item],
                accept_risk=True,
                source="immediate",
            )
        self.assertFalse(run.cursor_handoff_enabled)
        session.close()


class PublishScheduleValidationTests(unittest.TestCase):
    def test_simple_schedule_accepts_explicit_matching_account(self) -> None:
        with patch(
            "engine.reach.browser.list_chrome_profiles",
            return_value={
                "profiles": [
                    {
                        "name": "抖音-01",
                        "platform": "douyin",
                        "provisioning_status": "explicit",
                        "login_status": "verified_logged_in",
                    }
                ]
            },
        ):
            _validate_schedule_request(
                customer_id=1,
                timezone_name="Asia/Shanghai",
                chrome_profile="抖音-01",
                platform="douyin",
                content_source="eligible_ready_random",
                source_config={},
                times=[],
                windows=[
                    {
                        "start": "09:00",
                        "end": "11:00",
                        "weekdays": [0, 1, 2],
                        "count": 2,
                        "min_gap_minutes": 20,
                    }
                ],
                items_per_trigger=1,
            )

    def test_schedule_accepts_second_precision_window(self) -> None:
        with patch(
            "engine.reach.browser.list_chrome_profiles",
            return_value={
                "profiles": [
                    {
                        "name": "抖音-01",
                        "platform": "douyin",
                        "provisioning_status": "explicit",
                        "login_status": "verified_logged_in",
                    }
                ]
            },
        ):
            _validate_schedule_request(
                customer_id=1,
                timezone_name="Asia/Shanghai",
                chrome_profile="抖音-01",
                platform="douyin",
                content_source="eligible_ready_random",
                source_config={},
                times=[],
                windows=[
                    {
                        "start": "09:00:01",
                        "end": "11:00:59",
                        "weekdays": [0],
                        "count": 1,
                        "min_gap_minutes": 0,
                    }
                ],
                items_per_trigger=1,
            )

    def test_schedule_rejects_missing_account_and_empty_weekdays(self) -> None:
        with (
            patch(
                "engine.reach.browser.list_chrome_profiles",
                return_value={"profiles": []},
            ),
            self.assertRaisesRegex(ValueError, "发布账号不存在"),
        ):
            _validate_schedule_request(
                customer_id=1,
                timezone_name="Asia/Shanghai",
                chrome_profile="不存在",
                platform="douyin",
                content_source="eligible_ready_random",
                source_config={},
                times=[],
                windows=[
                    {
                        "start": "09:00",
                        "end": "11:00",
                        "weekdays": [],
                        "count": 1,
                    }
                ],
                items_per_trigger=1,
            )


class PublishTaskCreateTests(unittest.TestCase):
    def test_multi_account_task_creates_one_second_random_schedule_per_account(self) -> None:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import StaticPool

        from engine.catalog.db import Base, Customer, ReachPublishSchedule

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session = Session(engine)
        customer = Customer(name="多账号任务", profile_json={})
        session.add(customer)
        session.commit()
        body = ScheduleTaskCreateRequest(
            name="早间任务",
            targets=[
                {"platform": "douyin", "chrome_profile": "抖音-01", "publish_count": 2},
                {"platform": "xhs", "chrome_profile": "小红书-01", "publish_count": 3},
            ],
            windows=[{"start": "09:00:01", "end": "11:00:59"}],
            weekdays=[0, 1, 2],
            auto_production=True,
            accept_risk=True,
        )
        with (
            patch(
                "engine.api.reach_publish_routes.get_session",
                return_value=session,
            ),
            patch(
                "engine.api.reach_publish_routes._active_customer",
                return_value=customer,
            ),
            patch("engine.api.reach_publish_routes._validate_schedule_request"),
            patch("engine.api.reach_publish_routes.ensure_window_triggers"),
        ):
            result = create_schedule_task(body)

        verify = Session(engine)
        rows = verify.scalars(
            select(ReachPublishSchedule).order_by(ReachPublishSchedule.id)
        ).all()
        self.assertTrue(result["ok"])
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.repeat_count for row in rows], [2, 3])
        self.assertEqual(
            [row.windows_json[0]["count"] for row in rows],
            [2, 3],
        )
        self.assertTrue(
            all(row.random_algorithm == "window_seconds_v2" for row in rows)
        )
        self.assertTrue(
            all(row.content_source == "generate_then_publish" for row in rows)
        )
        group_ids = {
            row.source_config_json.get("task_group_id")
            for row in rows
        }
        self.assertEqual(len(group_ids), 1)
        verify.close()


class PublishRunDictTests(unittest.TestCase):
    def test_run_to_dict_shape(self) -> None:
        run = MagicMock()
        run.run_id = "abc"
        run.customer_id = 1
        run.status = "queued"
        run.source = "manual"
        run.schedule_id = None
        run.trigger_id = None
        run.accept_risk = True
        run.stop_after_current = False
        run.consecutive_failures = 0
        run.circuit_break = False
        run.chrome_port = None
        run.chrome_profile = None
        run.error = ""
        run.log_json = []
        run.created_at = None
        run.updated_at = None
        run.started_at = None
        run.finished_at = None
        d = run_to_dict(run, items=[])
        self.assertEqual(d["run_id"], "abc")
        self.assertEqual(d["status"], "queued")


class PublishRunResumeTests(unittest.TestCase):
    def test_paused_item_returns_to_resumable_checkpoint(self) -> None:
        session = MagicMock()
        run = MagicMock(status="paused_human", error="")
        item = MagicMock(id=7, phase="paused_human", error="uploading")
        item.evidence_json = {}
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
            patch(
                "engine.reach.publish_runner.start_run",
                return_value={"ok": True, "run_id": "run-1"},
            ) as start,
        ):
            result = resume_run("run-1")

        self.assertTrue(result["ok"])
        self.assertEqual(item.phase, "queued")
        self.assertEqual(item.error, "")
        self.assertEqual(run.status, "running")
        session.commit.assert_called_once()
        start.assert_called_once_with("run-1")

    def test_cover_pause_resumes_from_existing_form(self) -> None:
        session = MagicMock()
        run = MagicMock(status="paused_human", error="")
        item = MagicMock(id=8, phase="paused_human", error="cover_failed", note="")
        item.evidence_json = {
            "publish_result": {"phase": "cover_failed", "pub_clicked": False}
        }
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
            patch(
                "engine.reach.publish_runner.start_run",
                return_value={"ok": True, "run_id": "run-2"},
            ),
        ):
            resume_run("run-2")

        self.assertEqual(item.phase, "queued")
        self.assertEqual(item.note, "resume_existing_form")

    def test_upload_timeout_resumes_from_completed_form_without_reupload(self) -> None:
        session = MagicMock()
        run = MagicMock(status="paused_human", error="")
        item = MagicMock(id=9, phase="paused_human", error="uploading", note="")
        item.evidence_json = {
            "publish_result": {
                "phase": "uploading",
                "pub_clicked": False,
                "upload": {"ok": True},
            }
        }
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
            patch(
                "engine.reach.publish_runner.start_run",
                return_value={"ok": True, "run_id": "run-3"},
            ),
        ):
            resume_run("run-3")

        self.assertEqual(item.phase, "queued")
        self.assertEqual(item.note, "resume_existing_form")

    def test_unknown_outcome_cannot_be_automatically_resumed(self) -> None:
        session = MagicMock()
        run = MagicMock(status="outcome_unknown")
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            self.assertRaisesRegex(ValueError, "必须先人工确认"),
        ):
            resume_run("run-1")


class PublishRunControlTests(unittest.TestCase):
    def test_paused_item_can_be_deferred_and_run_continues(self) -> None:
        session = MagicMock()
        run = MagicMock(id=1, run_id="run-1", status="paused_human", error="")
        item = MagicMock(
            id=7,
            run_id=1,
            phase="paused_human",
            evidence_json={},
            error="需要登录",
            retry_mode="",
        )
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner._worker_alive_for", return_value=False),
            patch(
                "engine.reach.publish_runner.start_run",
                return_value={"ok": True, "run_id": "run-1"},
            ) as start,
        ):
            session.get.return_value = item
            result = skip_current_item("run-1", 7, retry_mode="manual")

        self.assertTrue(result["ok"])
        self.assertEqual(item.phase, "deferred")
        self.assertEqual(item.retry_mode, "manual")
        self.assertEqual(run.status, "running")
        start.assert_called_once_with("run-1")

    def test_clicked_item_requires_confirmation_before_retry(self) -> None:
        session = MagicMock()
        run = MagicMock(id=1, run_id="run-1", status="outcome_unknown", error="")
        item = MagicMock(
            id=8,
            run_id=1,
            phase="outcome_unknown",
            evidence_json={"publish_result": {"pub_clicked": True}},
            error="",
        )
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner.start_run", return_value={"ok": True}),
        ):
            session.get.return_value = item
            skip_current_item("run-1", 8, retry_mode="auto")

        self.assertEqual(item.phase, "awaiting_confirmation")
        self.assertEqual(item.retry_mode, "")
        self.assertIn("禁止补发", item.error)

    def test_stale_run_is_released_to_deferred(self) -> None:
        session = MagicMock()
        run = MagicMock(run_id="stale", status="running", requested_action="")
        item = MagicMock(
            phase="uploading",
            evidence_json={},
            retry_mode="",
            error="",
        )
        session.scalars.return_value.all.return_value = [run]
        with (
            patch("engine.reach.publish_runner._worker_alive_for", return_value=False),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
        ):
            changed = reconcile_stale_runs(session)

        self.assertEqual(changed, 1)
        self.assertEqual(run.status, "interrupted_system")
        self.assertEqual(item.phase, "deferred")
        self.assertEqual(item.retry_mode, "manual")
        session.commit.assert_called_once()

    def test_confirm_not_published_enters_auto_retry(self) -> None:
        session = MagicMock()
        run = MagicMock(id=1, run_id="run-1", status="outcome_unknown")
        item = MagicMock(
            id=9,
            run_id=1,
            phase="outcome_unknown",
            note="",
            retry_mode="",
            publication_group_id=3,
            publication_target_id=4,
            evidence_json={},
        )
        with (
            patch("engine.reach.publish_runner.get_session", return_value=session),
            patch("engine.reach.publish_runner.get_run", return_value=run),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
            patch(
                "engine.reach.publication_lifecycle.record_target_outcome",
                return_value={"ok": True, "retired": False},
            ),
        ):
            session.get.return_value = item
            confirm_item_outcome(
                "run-1",
                9,
                outcome="not_published",
                retry_mode="auto",
            )

        self.assertEqual(item.phase, "deferred")
        self.assertEqual(item.retry_mode, "auto")
        self.assertIsNotNone(item.retry_after)


class PublishFailForwardTests(unittest.TestCase):
    def test_login_wall_grace_default_bounded(self) -> None:
        self.assertEqual(login_wall_grace_sec(), 90.0)
        self.assertIn("waiting_login", ACTIVE_RUN_STATUSES)

    def test_true_login_wall_timeout_defers_and_leaves_active_status(self) -> None:
        session = MagicMock()
        run = MagicMock(
            id=1,
            run_id="wall-run",
            status="waiting_login",
            customer_id=1,
            config_json={},
            error="",
        )
        item = MagicMock(
            id=11,
            run_id=1,
            phase="waiting_login",
            chrome_profile="视频号-A",
            evidence_json={},
            publication_group_id=None,
            publication_target_id=None,
            error="scan needed",
            retry_mode="",
        )
        sibling = MagicMock(
            id=12,
            run_id=1,
            phase="soft_skipped",
            chrome_profile="视频号-A",
            evidence_json={"soft_skip": {"kind": "true_login_wall"}},
            publication_group_id=None,
            publication_target_id=None,
            error="blocked",
            retry_mode="",
        )
        other = MagicMock(
            id=13,
            run_id=1,
            phase="queued",
            chrome_profile="抖音-B",
            evidence_json={},
            publication_group_id=None,
            publication_target_id=None,
            error="",
            retry_mode="",
        )

        def fake_list(_session, _run):
            return [item, sibling, other]

        with (
            patch(
                "engine.reach.publish_runner.list_run_items",
                side_effect=fake_list,
            ),
            patch("engine.reach.publish_runner.create_alert", create=True),
        ):
            with patch("engine.ops.human_alerts.create_alert"), patch(
                "engine.ops.human_alerts.dispatch_due_alerts"
            ):
                n = _defer_true_login_wall_fail_forward(
                    session, run, item, reason="true_login_wall"
                )

        self.assertGreaterEqual(n, 1)
        self.assertEqual(item.phase, "deferred")
        self.assertEqual(item.retry_mode, "auto")
        self.assertEqual(sibling.phase, "deferred")
        self.assertEqual(other.phase, "queued")
        self.assertEqual(run.status, "running")
        self.assertNotIn(run.status, RUN_TERMINAL_IF_ANY)

    def test_await_login_wall_timeout_defers(self) -> None:
        session = MagicMock()
        run = MagicMock(
            id=1,
            run_id="grace-run",
            status="waiting_login",
            customer_id=1,
            config_json={},
            error="wall",
        )
        item = MagicMock(
            id=2,
            run_id=1,
            phase="waiting_login",
            chrome_profile="视频号-X",
            evidence_json={},
            publication_group_id=None,
            publication_target_id=None,
            error="wall",
            retry_mode="",
        )
        with (
            patch("engine.reach.publish_runner._cancelled", return_value=False),
            patch(
                "engine.reach.publish_runner.list_run_items",
                return_value=[item],
            ),
            patch("engine.ops.human_alerts.create_alert"),
            patch("engine.ops.human_alerts.dispatch_due_alerts"),
            patch("engine.reach.publish_runner.time.sleep"),
            patch(
                "engine.reach.publish_runner.time.monotonic",
                side_effect=[0.0, 0.0, 100.0, 100.0],
            ),
        ):
            result = _await_login_wall_resolution(session, run, item, grace_sec=1.0)
        self.assertEqual(result, "deferred")
        self.assertEqual(item.phase, "deferred")
        self.assertEqual(run.status, "running")

    def test_pub_clicked_cannot_auto_defer_fail_forward(self) -> None:
        session = MagicMock()
        run = MagicMock(run_id="r1", config_json={})
        item = MagicMock(
            phase="verifying",
            evidence_json={"publish_result": {"pub_clicked": True}},
            publication_group_id=None,
            publication_target_id=None,
        )
        with self.assertRaises(ValueError):
            _defer_item_pre_submit(session, run, item, reason="nope")

    def test_stale_waiting_login_reconcile_without_worker(self) -> None:
        from datetime import datetime, timedelta, timezone

        session = MagicMock()
        old = datetime.now(timezone.utc) - timedelta(seconds=200)
        run = MagicMock(
            run_id="zomb",
            status="waiting_login",
            requested_action="",
            heartbeat_at=old,
            updated_at=old,
            started_at=old,
            created_at=old,
            error="",
            config_json={},
        )
        item = MagicMock(
            phase="waiting_login",
            evidence_json={},
            retry_mode="",
            error="",
            publication_group_id=None,
            publication_target_id=None,
        )
        session.scalars.return_value.all.return_value = [run]
        with (
            patch("engine.reach.publish_runner._worker_alive_for", return_value=False),
            patch("engine.reach.publish_runner.list_run_items", return_value=[item]),
        ):
            changed = reconcile_stale_runs(session)
        self.assertEqual(changed, 1)
        self.assertEqual(run.status, "interrupted_system")
        self.assertEqual(item.phase, "deferred")
        self.assertEqual(item.retry_mode, "auto")

    def test_get_active_run_includes_waiting_login(self) -> None:
        session = MagicMock()
        run = MagicMock(status="waiting_login", run_id="a1")
        session.scalar.return_value = run
        found = get_active_run(session)
        self.assertIs(found, run)

    def test_window_missed_creates_alert(self) -> None:
        from engine.reach.publish_schedule import _alert_window_missed

        session = MagicMock()
        trigger = MagicMock(
            id=9,
            customer_id=3,
            local_date="2026-08-07",
            planned_at=None,
        )
        schedule = MagicMock(chrome_profile="视频号-1", name="上午班")
        with (
            patch("engine.ops.human_alerts.create_alert") as create_alert,
            patch("engine.ops.human_alerts.dispatch_due_alerts") as dispatch,
        ):
            _alert_window_missed(session, trigger, schedule)
        create_alert.assert_called_once()
        kwargs = create_alert.call_args.kwargs
        self.assertEqual(kwargs.get("kind"), "publish_window_missed")
        dispatch.assert_called_once()

    def test_deferred_yields_only_when_due(self) -> None:
        from datetime import datetime, timedelta, timezone

        from engine.reach.publish_schedule import (
            _has_due_or_imminent_triggers,
            _has_due_triggers,
        )

        session = MagicMock()
        now = datetime.now(timezone.utc)
        sched = MagicMock(id=1, enabled=True)
        # Only future-planned should not block when horizon=0 / due-only.
        future = MagicMock(
            planned_at=now + timedelta(seconds=60),
            window_end=now + timedelta(hours=1),
            status="pending",
        )
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[sched])),
            MagicMock(all=MagicMock(return_value=[future])),
            MagicMock(all=MagicMock(return_value=[sched])),
            MagicMock(all=MagicMock(return_value=[future])),
        ]
        self.assertFalse(
            _has_due_triggers(session, customer_id=1, now=now)
        )
        self.assertFalse(
            _has_due_or_imminent_triggers(
                session, customer_id=1, now=now, horizon_sec=0.0
            )
        )

    def test_deferred_blocks_when_already_due(self) -> None:
        from datetime import datetime, timedelta, timezone

        from engine.reach.publish_schedule import _has_due_triggers

        session = MagicMock()
        now = datetime.now(timezone.utc)
        sched = MagicMock(id=1, enabled=True)
        due = MagicMock(
            planned_at=now - timedelta(seconds=5),
            window_end=now + timedelta(hours=1),
            status="pending",
        )
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[sched])),
            MagicMock(all=MagicMock(return_value=[due])),
        ]
        self.assertTrue(_has_due_triggers(session, customer_id=1, now=now))

    def test_heal_completes_published_preparing_trigger(self) -> None:
        from engine.reach.publish_schedule import heal_stuck_triggers

        session = MagicMock()
        sched = MagicMock(id=10, chrome_profile="小红书-1")
        trig = MagicMock(
            id=1,
            schedule_id=10,
            status="preparing",
            occurrence_key="k1",
            run_id=None,
            planned_at=None,
            window_end=None,
            claimed_at=None,
            note="",
            updated_at=None,
            created_at=None,
        )
        occ = MagicMock(
            status="published",
            publish_run_id="run-ok",
            production_job_id=1,
        )
        session.scalars.return_value.all.return_value = [trig]
        session.get.side_effect = lambda model, key: (
            sched if key == 10 else MagicMock(status="completed")
        )
        session.scalar.side_effect = [occ, None]

        actions = heal_stuck_triggers(session, customer_id=1)
        self.assertTrue(any(a.get("action") == "completed" for a in actions))
        self.assertEqual(trig.status, "completed")
        self.assertEqual(trig.run_id, "run-ok")

    def test_heal_reopens_failed_occ_within_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        from engine.reach.publish_schedule import heal_stuck_triggers

        session = MagicMock()
        now = datetime.now(timezone.utc)
        sched = MagicMock(id=10, chrome_profile="抖音-1")
        trig = MagicMock(
            id=2,
            schedule_id=10,
            status="preparing",
            occurrence_key="k2",
            run_id=None,
            planned_at=now - timedelta(minutes=5),
            window_end=now + timedelta(hours=1),
            claimed_at=now - timedelta(minutes=10),
            note="",
            updated_at=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=10),
        )
        occ = MagicMock(status="failed", publish_run_id=None, production_job_id=9)
        session.scalars.return_value.all.return_value = [trig]
        session.get.side_effect = lambda model, key: (
            sched if key == 10 else MagicMock(status="failed")
        )
        session.scalar.side_effect = [occ, None]
        actions = heal_stuck_triggers(session, customer_id=1)
        self.assertTrue(any(a.get("action") == "reopen_pending" for a in actions))
        self.assertEqual(trig.status, "pending")

    def test_asset_error_no_longer_hard_blocks_auto(self) -> None:
        from engine.reach.publish_asset_heal import (
            is_asset_incomplete_error,
            is_hard_no_auto_error,
        )
        from engine.reach.publish_runner import _is_no_auto_makeup_error

        self.assertTrue(is_asset_incomplete_error("缺文案：平台 douyin 正文/描述为空，禁止发布"))
        self.assertFalse(_is_no_auto_makeup_error("缺文案：平台 douyin 正文/描述为空，禁止发布"))
        self.assertTrue(is_hard_no_auto_error("Chrome 配置尚未完成登录或不完整: 抖音-7237"))
        self.assertTrue(_is_no_auto_makeup_error("Chrome 配置尚未完成登录或不完整: 抖音-7237"))

    def test_repair_promotes_safe_deferred_to_auto(self) -> None:
        from engine.reach.publish_runner import repair_deferred_auto_eligibility

        session = MagicMock()
        good = MagicMock(
            phase="deferred",
            retry_mode="manual",
            retry_after=None,
            error="no_file_input；已跳过",
            evidence_json={},
            updated_at=None,
            output_id=None,
        )
        # Missing copy is no longer permanently blocked from auto (healed/reused).
        soft = MagicMock(
            phase="deferred",
            retry_mode="manual",
            retry_after=None,
            error="缺文案：平台 douyin 正文/描述为空，禁止发布",
            evidence_json={},
            updated_at=None,
            output_id=None,
        )
        hard = MagicMock(
            phase="deferred",
            retry_mode="manual",
            retry_after=None,
            error="Chrome 配置尚未完成登录或不完整: 抖音-x",
            evidence_json={},
            updated_at=None,
            output_id=None,
        )
        with patch(
            "engine.reach.publish_runner.list_deferred_items",
            return_value=[good, soft, hard],
        ), patch(
            "engine.reach.publish_asset_heal.heal_deferred_assets",
            return_value={"healed": 0, "promoted_auto": 0},
        ):
            n = repair_deferred_auto_eligibility(session, customer_id=1)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(good.retry_mode, "auto")
        self.assertEqual(soft.retry_mode, "auto")
        self.assertEqual(hard.retry_mode, "none")

    def test_finish_summary_records_deferred(self) -> None:
        session = MagicMock()
        run = MagicMock(
            status="completed",
            config_json={},
            run_id="fin",
        )
        items = [
            MagicMock(phase="published", evidence_json={}),
            MagicMock(
                phase="deferred",
                evidence_json={"fail_forward": {"kind": "true_login_wall"}},
            ),
        ]
        with patch("engine.reach.publish_runner.list_run_items", return_value=items):
            _record_run_finish_summary(session, run)
        summary = (run.config_json or {}).get("finish_summary") or {}
        self.assertEqual(summary.get("published"), 1)
        self.assertEqual(summary.get("deferred"), 1)


# Helper avoids importing RUN_TERMINAL name confusion in fail-forward assert.
RUN_TERMINAL_IF_ANY = frozenset(
    {"completed", "failed", "cancelled", "interrupted_system"}
)


if __name__ == "__main__":
    unittest.main()
