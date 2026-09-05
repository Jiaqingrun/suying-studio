from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from engine.catalog.db import (
    Base,
    Customer,
    Job,
    PublicationGroup,
    PublicationTarget,
    ReachPublishReservation,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachPublishSchedule,
    ReachPublishTrigger,
    ReachQueueItem,
    RenderOutput,
)
from engine.reach.publication_lifecycle import (
    claim_target_for_submission,
    freeze_publication_group,
    output_has_active_group,
    record_target_outcome,
    release_unsubmitted_target,
    resolve_target,
    retry_pending_archives,
)


def _fixture(tmp_path: Path) -> tuple[sessionmaker[Session], Session, Customer, RenderOutput]:
    engine = create_engine(f"sqlite:///{tmp_path / 'publication.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    output_root = tmp_path / "customer-output"
    ready = output_root / "ready"
    ready.mkdir(parents=True)
    video = ready / "clip.mp4"
    sidecar = ready / "clip.json"
    subtitle = ready / "clip.zh.srt"
    voice = ready / "clip.voice.wav"
    covers = ready / "clip_covers"
    pack = output_root / "packs" / "clip.publish_pack"
    covers.mkdir()
    pack.mkdir(parents=True)
    video.write_bytes(b"video")
    subtitle.write_text("subtitle", encoding="utf-8")
    voice.write_bytes(b"voice")
    (covers / "douyin.jpg").write_bytes(b"cover")
    (pack / "video.mp4").write_bytes(b"pack-video")
    (pack / "subtitle.zh.srt").write_text("pack subtitle", encoding="utf-8")
    (pack / "voiceover.zh.wav").write_bytes(b"pack voice")
    (pack / "manifest.json").write_text("{}", encoding="utf-8")
    customer = Customer(
        name="客户A",
        library_root=str(tmp_path / "library"),
        output_root=str(output_root),
        profile_json={},
    )
    session.add(customer)
    session.flush()
    job = Job(
        customer_id=customer.id,
        status="completed",
        mode="count",
        target_count=1,
        template_name="default",
        config_snapshot_json={},
    )
    session.add(job)
    session.flush()
    sidecar.write_text(
        json.dumps({"meta": {}, "output_path": str(video), "pack_dir": str(pack)}),
        encoding="utf-8",
    )
    output = RenderOutput(
        job_id=job.id,
        output_path=str(video),
        sidecar_path=str(sidecar),
        state="ready",
        seed=1,
        qc_json={"ready_gate": {"ok": True}},
        pack_status="ready",
        pack_dir=str(pack),
        platform_asset_status={},
    )
    session.add(output)
    session.commit()
    session.refresh(customer)
    session.refresh(output)
    return factory, session, customer, output


def _group(session: Session, customer: Customer, output: RenderOutput):
    group, _ = freeze_publication_group(
        session,
        customer_id=customer.id,
        output_id=output.id,
        targets=[
            {"platform": "douyin", "account_key": "account-a"},
            {"platform": "xhs", "account_key": "account-b"},
        ],
        source="test",
    )
    session.commit()
    return group


def test_partial_success_unknown_and_original_group_resume(tmp_path: Path) -> None:
    _, session, customer, output = _fixture(tmp_path)
    group = _group(session, customer, output)
    douyin = resolve_target(
        session, group_id=group.id, platform="douyin", account_key="account-a"
    )
    xhs = resolve_target(
        session, group_id=group.id, platform="xhs", account_key="account-b"
    )

    claim_target_for_submission(session, group_id=group.id, target_id=douyin.id)
    result = record_target_outcome(
        session, group_id=group.id, target_id=douyin.id, outcome="published"
    )
    assert result["retired"] is False
    assert output_has_active_group(
        session, customer_id=customer.id, output_id=output.id
    )
    with pytest.raises(ValueError, match="冻结"):
        freeze_publication_group(
            session,
            customer_id=customer.id,
            output_id=output.id,
            targets=[{"platform": "channels", "account_key": "account-c"}],
            source="other_plan",
        )

    claim_target_for_submission(session, group_id=group.id, target_id=xhs.id)
    record_target_outcome(
        session, group_id=group.id, target_id=xhs.id, outcome="outcome_unknown"
    )
    with pytest.raises(ValueError, match="结果不明"):
        claim_target_for_submission(session, group_id=group.id, target_id=xhs.id)
    record_target_outcome(
        session,
        group_id=group.id,
        target_id=xhs.id,
        outcome="not_published",
        note="人工查作品列表确认未发布",
    )
    claim_target_for_submission(session, group_id=group.id, target_id=xhs.id)
    session.close()


def test_all_targets_retire_invalidate_and_archive_every_asset(tmp_path: Path) -> None:
    _, session, customer, output = _fixture(tmp_path)
    group = _group(session, customer, output)
    targets = session.query(PublicationTarget).filter_by(group_id=group.id).all()
    queue = ReachQueueItem(
        customer_id=customer.id,
        platform="xhs",
        status="queued",
        output_id=output.id,
        publication_group_id=group.id,
        publication_target_id=targets[1].id,
        title="t",
        body="b",
        video_path=output.output_path,
        pack_dir=output.pack_dir,
        copy_json={},
        log_json=[],
    )
    schedule = ReachPublishSchedule(
        customer_id=customer.id,
        chrome_profile="p",
        platform="xhs",
        content_source="eligible_ready_random",
    )
    session.add_all([queue, schedule])
    session.flush()
    trigger = ReachPublishTrigger(
        schedule_id=schedule.id,
        customer_id=customer.id,
        planned_at=group.created_at,
    )
    session.add(trigger)
    session.flush()
    reservation = ReachPublishReservation(
        customer_id=customer.id,
        schedule_id=schedule.id,
        trigger_id=trigger.id,
        ordinal=0,
        output_id=output.id,
        platform="xhs",
        chrome_profile="p",
        status="reserved",
        assignment_json={},
    )
    run = ReachPublishRun(
        customer_id=customer.id,
        run_id="retire-run",
        status="running",
        accept_risk=True,
    )
    session.add_all([reservation, run])
    session.flush()
    deferred = ReachPublishRunItem(
        run_id=run.id,
        customer_id=customer.id,
        ordinal=0,
        output_id=output.id,
        publication_group_id=group.id,
        publication_target_id=targets[1].id,
        platform="xhs",
        chrome_profile="p",
        phase="deferred",
        title="t",
        video_path=output.output_path,
        pack_dir=output.pack_dir,
    )
    session.add(deferred)
    session.commit()

    first, second = targets
    claim_target_for_submission(session, group_id=group.id, target_id=first.id)
    record_target_outcome(
        session, group_id=group.id, target_id=first.id, outcome="published"
    )
    claim_target_for_submission(session, group_id=group.id, target_id=second.id)
    result = record_target_outcome(
        session, group_id=group.id, target_id=second.id, outcome="published"
    )
    assert result["retired"] is True
    session.refresh(group)
    session.refresh(output)
    session.refresh(queue)
    session.refresh(reservation)
    session.refresh(deferred)
    assert group.status == "retired_published"
    assert output.state == "retired_published"
    assert queue.status == "cancelled"
    assert reservation.status == "released"
    assert deferred.phase == "cancelled"
    archive = Path(output.output_path).parent
    assert archive.parent.parent.name == "published"
    assert archive.name == f"output-{output.id}"
    assert (archive / "output.mp4").is_file()
    assert (archive / "sidecar.json").is_file()
    assert (archive / "covers" / "clip_covers" / "douyin.jpg").is_file()
    assert (archive / "companions" / "clip.zh.srt").is_file()
    assert (archive / "companions" / "clip.voice.wav").is_file()
    assert (archive / "publish_pack" / "manifest.json").is_file()
    manifest = json.loads((archive / "SHA256.json").read_text(encoding="utf-8"))
    assert manifest["output_id"] == output.id
    assert all(row["sha256"] for row in manifest["files"])


def test_archive_failure_stays_isolated_and_retries_archive_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, session, customer, output = _fixture(tmp_path)
    group, _ = freeze_publication_group(
        session,
        customer_id=customer.id,
        output_id=output.id,
        targets=[{"platform": "douyin", "account_key": "a"}],
        source="test",
    )
    target = resolve_target(
        session, group_id=group.id, platform="douyin", account_key="a"
    )
    claim_target_for_submission(session, group_id=group.id, target_id=target.id)
    monkeypatch.setattr(
        "engine.ops.published_archive.archive_published_output",
        lambda *args, **kwargs: {"ok": False, "error": "disk full"},
    )
    result = record_target_outcome(
        session, group_id=group.id, target_id=target.id, outcome="published"
    )
    assert result["archive_only_retry"] is True
    session.refresh(group)
    assert group.status == "archive_failed"
    with pytest.raises(ValueError, match="只允许归档恢复"):
        claim_target_for_submission(session, group_id=group.id, target_id=target.id)
    monkeypatch.undo()
    recovered = retry_pending_archives(session, customer_id=customer.id)
    assert recovered[0]["retired"] is True
    session.refresh(group)
    assert group.status == "retired_published"


def test_cas_and_crash_recovery_and_customer_isolation(tmp_path: Path) -> None:
    factory, session, customer, output = _fixture(tmp_path)
    group = _group(session, customer, output)
    target = resolve_target(
        session, group_id=group.id, platform="douyin", account_key="account-a"
    )
    other = factory()
    claim_target_for_submission(session, group_id=group.id, target_id=target.id)
    session.commit()
    with pytest.raises(ValueError, match="另一发布路径"):
        claim_target_for_submission(other, group_id=group.id, target_id=target.id)
    other.rollback()
    release_unsubmitted_target(
        session, group_id=group.id, target_id=target.id
    )
    session.commit()
    claim_target_for_submission(session, group_id=group.id, target_id=target.id)

    customer_b = Customer(
        name="客户B",
        library_root=str(tmp_path / "b-lib"),
        output_root=str(tmp_path / "b-out"),
        profile_json={},
    )
    session.add(customer_b)
    session.commit()
    with pytest.raises(ValueError, match="不属于当前客户"):
        freeze_publication_group(
            session,
            customer_id=customer_b.id,
            output_id=output.id,
            targets=[{"platform": "channels", "account_key": "account-b"}],
            source="cross_customer_attempt",
        )
    assert not output_has_active_group(
        session, customer_id=customer_b.id, output_id=output.id
    )
    other.close()
    session.close()
