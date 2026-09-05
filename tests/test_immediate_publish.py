from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Base, Customer, Job, RenderOutput
from engine.catalog.review_auto import ensure_output_decision, record_review_decision
from engine.ops.audit_log import log_to_dict, write_log
from engine.ops.automation_loop import _eligible_output

from engine.reach.publish_sources import (
    allocate_occurrences,
    assign_content_to_occurrences,
    has_current_approved_review,
    has_verified_ready_gate,
    keep_eligible_account_occurrences,
    materialize_from_schedule,
    release_all_content_reservations,
    reserve_trigger_content,
)


ACCOUNTS = [
    {"platform": "douyin", "chrome_profile": "账号A"},
    {"platform": "xhs", "chrome_profile": "账号B"},
    {"platform": "kuaishou", "chrome_profile": "账号C"},
]
CANDIDATES = [
    {"output_id": value, "video_path": f"/tmp/{value}.mp4", "pack_dir": f"/tmp/{value}.pack"}
    for value in range(1, 8)
]


def test_auto_even_and_small_total_are_stable() -> None:
    allocated = allocate_occurrences(
        ACCOUNTS, total_count=2, allocation_mode="auto_even"
    )
    assert [row["chrome_profile"] for row in allocated] == ["账号A", "账号B"]

    allocated = allocate_occurrences(
        ACCOUNTS, total_count=8, allocation_mode="auto_even"
    )
    assert [sum(row["chrome_profile"] == name for row in allocated) for name in ("账号A", "账号B", "账号C")] == [3, 3, 2]


def test_missing_platform_drops_its_slots_without_reassigning() -> None:
    allocated = allocate_occurrences(
        ACCOUNTS, total_count=3, allocation_mode="auto_even"
    )
    usable, skipped = keep_eligible_account_occurrences(
        allocated,
        eligible_platforms={"douyin", "xhs"},
    )

    assert [row["chrome_profile"] for row in usable] == ["账号A", "账号B"]
    assert [row["chrome_profile"] for row in skipped] == ["账号C"]
    assert skipped[0]["skip_reason"] == "missing_publish_assets"


def test_manual_total_must_match() -> None:
    with pytest.raises(ValueError, match="精确等于"):
        allocate_occurrences(
            ACCOUNTS,
            total_count=3,
            allocation_mode="manual",
            manual_counts={"douyin:账号A": 1},
        )


def test_three_content_modes_and_seed_replay() -> None:
    occurrences = allocate_occurrences(
        ACCOUNTS[:2], total_count=4, allocation_mode="auto_even"
    )
    first = assign_content_to_occurrences(
        occurrences,
        content_mode="random_unique",
        candidate_outputs=CANDIDATES,
        seed=42,
    )
    replay = assign_content_to_occurrences(
        occurrences,
        content_mode="random_unique",
        candidate_outputs=CANDIDATES,
        seed=42,
    )
    assert [row["output"]["output_id"] for row in first] == [
        row["output"]["output_id"] for row in replay
    ]
    assert len({row["output"]["output_id"] for row in first}) == 4

    cycle = assign_content_to_occurrences(
        occurrences,
        content_mode="selected_cycle",
        candidate_outputs=CANDIDATES,
        selected_output_ids=[2, 5],
        seed=1,
    )
    assert [row["output"]["output_id"] for row in cycle] == [2, 5, 2, 5]

    with pytest.raises(ValueError, match="跨 occurrence 重复"):
        assign_content_to_occurrences(
            occurrences,
            content_mode="single",
            candidate_outputs=CANDIDATES,
            selected_output_ids=[3],
            seed=1,
        )


def test_random_unique_fails_closed_when_insufficient() -> None:
    occurrences = allocate_occurrences(
        ACCOUNTS, total_count=5, allocation_mode="auto_even"
    )
    with pytest.raises(ValueError, match="当前仅 2 条"):
        assign_content_to_occurrences(
            occurrences,
            content_mode="random_unique",
            candidate_outputs=CANDIDATES[:2],
            seed=7,
        )


def test_random_unique_respects_platform_eligibility() -> None:
    occurrences = [
        {"platform": "xhs", "chrome_profile": "小红书账号", "occurrence_index": 0},
        {"platform": "douyin", "chrome_profile": "抖音账号", "occurrence_index": 1},
    ]
    candidates = [
        {**CANDIDATES[0], "eligible_platforms": ["douyin"]},
        {**CANDIDATES[1], "eligible_platforms": ["xhs"]},
    ]

    assigned = assign_content_to_occurrences(
        occurrences,
        content_mode="random_unique",
        candidate_outputs=candidates,
        seed=3,
    )

    assert assigned[0]["output"]["eligible_platforms"] == ["xhs"]
    assert assigned[1]["output"]["eligible_platforms"] == ["douyin"]


def test_ready_gate_evidence_is_required() -> None:
    assert has_verified_ready_gate({"ready_gate": {"ok": True}})
    assert not has_verified_ready_gate({})
    assert not has_verified_ready_gate({"ready_gate": {"ok": False}})


def test_publish_candidate_requires_current_approved_decision(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="发布门禁客户", profile_json={})
        session.add(customer)
        session.flush()
        job = Job(
            customer_id=customer.id,
            mode="count",
            target_count=1,
            status="completed",
            theme="default",
            category="default",
            template_name="default-vertical",
        )
        session.add(job)
        session.flush()
        output = RenderOutput(
            job_id=job.id,
            output_path="/tmp/publish-gate.mp4",
            state="ready",
            seed=1,
            sidecar_path="",
            qc_json={"ready_gate": {"ok": True}},
        )
        session.add(output)
        session.flush()
        assert not has_current_approved_review(session, output.id)
        assert _eligible_output(session, job.id) is None

        ensure_output_decision(session, customer, output, source="test")
        assert has_current_approved_review(session, output.id)
        pack_dir = tmp_path / "publish-gate.publish_pack"
        pack_dir.mkdir()
        output.pack_status = "ready"
        output.pack_dir = str(pack_dir)
        output.platform_asset_status = {
            platform: {"status": "ready"}
            for platform in ("douyin", "channels", "xhs", "kuaishou")
        }
        assert _eligible_output(session, job.id).id == output.id

        output.state = "review"
        record_review_decision(
            session,
            output,
            status="uncertain",
            note="证据冲突",
            source="manual",
        )
        assert not has_current_approved_review(session, output.id)
        assert _eligible_output(session, job.id) is None


def test_schedule_random_materialization_loads_platform_copy(tmp_path) -> None:
    pack = tmp_path / "sample.publish_pack"
    pack.mkdir()
    (pack / "video.mp4").write_bytes(b"video")
    (pack / "copy.zh.json").write_text(
        json.dumps(
            {
                "platforms": {
                    "douyin": {"title": "客户可见标题", "body": "客户可见正文"}
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    schedule = SimpleNamespace(
        platform="douyin",
        content_source="eligible_ready_random",
        source_config_json={},
        items_per_trigger=1,
        customer_id=1,
    )
    queued = SimpleNamespace(id=1)
    session = MagicMock()
    with (
        patch(
            "engine.reach.publish_sources.pick_eligible_ready_random",
            return_value={
                "ok": True,
                "seed": 7,
                "eligible_count": 1,
                "outputs": [
                    {
                        "output_id": 9,
                        "pack_dir": str(pack),
                        "video_path": str(pack / "video.mp4"),
                        "title": "fallback",
                    }
                ],
            },
        ),
        patch(
            "engine.reach.publish_assets.require_publish_assets",
            return_value={"ok": True},
        ),
        patch("engine.reach.publish_sources.enqueue", return_value=queued) as enqueue,
    ):
        result = materialize_from_schedule(session, schedule)

    assert result["queue_items"] == [queued]
    assert enqueue.call_args.kwargs["title"] == "客户可见标题"
    assert enqueue.call_args.kwargs["body"] == "客户可见正文"


def test_reserve_trigger_content_is_noop() -> None:
    assert reserve_trigger_content(MagicMock(), MagicMock(), MagicMock()) == []


def test_eligible_ready_shortfall_triggers_gap_production() -> None:
    schedule = SimpleNamespace(
        platform="douyin",
        content_source="eligible_ready_random",
        source_config_json={"template_name": "default-vertical"},
        items_per_trigger=2,
        customer_id=1,
        id=9,
    )
    job = SimpleNamespace(id=42)
    session = MagicMock()
    session.get.return_value = None
    with (
        patch(
            "engine.reach.publish_sources.pick_eligible_ready_random",
            return_value={"ok": False, "outputs": [], "eligible_count": 0, "seed": 1},
        ),
        patch(
            "engine.jobs.queue.create_job",
            return_value=job,
        ) as create_job,
    ):
        result = materialize_from_schedule(session, schedule)

    assert result["pending_generation"] is True
    assert result["evidence"]["job_id"] == 42
    assert result["evidence"]["gap_production"] is True
    create_job.assert_called_once()


def test_release_all_content_reservations() -> None:
    from engine.catalog.db import ReachPublishReservation, ReachPublishSchedule, ReachPublishTrigger

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="释放预留测试")
        session.add(customer)
        session.flush()
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            chrome_profile="抖音-01",
            platform="douyin",
            content_source="eligible_ready_random",
        )
        session.add(schedule)
        session.flush()
        trigger = ReachPublishTrigger(
            schedule_id=schedule.id,
            customer_id=customer.id,
            planned_at=__import__("datetime").datetime(2026, 8, 3, 1, 0, 0),
            status="pending",
        )
        session.add(trigger)
        session.flush()
        row = ReachPublishReservation(
            customer_id=customer.id,
            schedule_id=schedule.id,
            trigger_id=trigger.id,
            ordinal=0,
            platform="douyin",
            chrome_profile="抖音-01",
            status="reserved",
            assignment_json={},
        )
        session.add(row)
        session.commit()
        assert release_all_content_reservations(session) == 1
        session.commit()
        assert row.status == "released"
        assert row.assignment_json.get("release_reason") == "policy_no_reserve"
        assert release_all_content_reservations(session) == 0


def test_operation_log_redacts_sensitive_details() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        row = write_log(
            session,
            customer_id=None,
            scope="machine",
            category="publish",
            event="test",
            message="验证码: 123456",
            details={"token": "secret", "nested": {"cookie": "abc"}},
            commit=True,
        )
        assert "123456" not in row.message
        assert row.details_json["token"] == "[REDACTED]"
        assert row.details_json["nested"]["cookie"] == "[REDACTED]"


def test_operation_log_resolves_customer_display_id_and_path() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="日志展示测试")
        session.add(customer)
        session.flush()
        job = Job(
            customer_id=customer.id,
            status="completed",
            target_count=1,
            template_name="default-vertical",
        )
        session.add(job)
        session.flush()
        output = RenderOutput(
            job_id=job.id,
            output_path="/tmp/customer-visible.mp4",
            state="ready",
            seed=1,
            display_no=7,
        )
        session.add(output)
        session.flush()
        row = write_log(
            session,
            customer_id=customer.id,
            category="quality",
            event="review_approved",
            message="审片通过",
            source_type="render_output",
            source_id=output.id,
            details={"output_id": output.id, "job_id": job.id},
        )
        public = log_to_dict(row, session=session)
        assert public["display_no"] == 7
        assert public["display_label"] == "#007"
        assert public["output_path"] == "/tmp/customer-visible.mp4"
        assert public["job_id"] == job.id
        assert public["output_id"] == output.id
