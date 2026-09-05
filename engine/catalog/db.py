from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from engine.config.settings import AppSettings, load_settings


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    library_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_roots: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_pack_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class KeywordPack(Base):
    __tablename__ = "keyword_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    name: Mapped[str] = mapped_column(String(128))
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[str] = mapped_column(String(128), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class KeywordUsage(Base):
    __tablename__ = "keyword_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    keyword: Mapped[str] = mapped_column(String(256), index=True)
    theme: Mapped[str] = mapped_column(String(128), default="default")
    used_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    source_path: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    proxy_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(128), default="uncategorized")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orientation: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class VectorizationRun(Base):
    """A frozen, customer-scoped vectorization execution."""

    __tablename__ = "vectorization_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('IDLE','RUNNING','PAUSE_REQUESTED','PAUSED','COMPLETED','BLOCKED','FAILED')",
            name="ck_vectorization_run_status",
        ),
        CheckConstraint("mode IN ('count','all')", name="ck_vectorization_run_mode"),
        Index(
            "uq_vectorization_machine_slot",
            text("(1)"),
            unique=True,
            sqlite_where=text(
                "status IN ('IDLE','RUNNING','PAUSE_REQUESTED','PAUSED','BLOCKED')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait", index=True)
    status: Mapped[str] = mapped_column(String(24), default="IDLE", index=True)
    mode: Mapped[str] = mapped_column(String(16), default="count")
    requested_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    frozen_assets: Mapped[int] = mapped_column(Integer, default=0)
    completed_assets: Mapped[int] = mapped_column(Integer, default=0)
    completed_cliplets: Mapped[int] = mapped_column(Integer, default=0)
    current_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pause_owner: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class VectorizationQueue(Base):
    """First-eligible queue age is stable across scans, retries and runs."""

    __tablename__ = "vectorization_queue"
    __table_args__ = (
        UniqueConstraint("customer_id", "asset_id", name="uq_vectorization_queue_customer_asset"),
        CheckConstraint(
            "status IN ('QUEUED','CLAIMED','COMPLETED','FAILED')",
            name="ck_vectorization_queue_status",
        ),
        Index(
            "ix_vectorization_queue_claim",
            "run_id",
            "status",
            "enqueued_at",
            "asset_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("assets.id"), index=True)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait", index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    eligible_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempt_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completed_cliplets: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    mode: Mapped[str] = mapped_column(String(32), default="count")
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    produced_count: Mapped[int] = mapped_column(Integer, default=0)
    template_name: Mapped[str] = mapped_column(String(128))
    theme: Mapped[str] = mapped_column(String(128), default="default")
    category: Mapped[str] = mapped_column(String(128), default="default")
    orientation: Mapped[str] = mapped_column(String(16), default="portrait", index=True)
    config_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    events: Mapped[list["JobEvent"]] = relationship(back_populates="job")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    job: Mapped[Job] = relationship(back_populates="events")


class RenderOutput(Base):
    __tablename__ = "render_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    output_path: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="rendering")
    orientation: Mapped[str] = mapped_column(String(16), default="portrait", index=True)
    seed: Mapped[int] = mapped_column(Integer)
    # Global human-readable id: SY-{customer_key}-{yyyyMMdd}-{seq6}
    serial: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    # Customer-facing sole UID sequence (#001…); independent of DB id / SY serial
    display_no: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    sidecar_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Durable publish-pack contract. ``ready`` alone is never sufficient for
    # publishing; every video platform must be ``ready`` in the status map.
    pack_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    pack_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_error: Mapped[str] = mapped_column(Text, default="")
    pack_version: Mapped[str] = mapped_column(String(64), default="")
    platform_asset_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PublicationGroup(Base):
    """Frozen, customer-scoped publication intent for one render output."""

    __tablename__ = "publication_groups"
    __table_args__ = (
        UniqueConstraint("customer_id", "group_key", name="uq_publication_group_key"),
        Index(
            "uq_publication_active_output",
            "customer_id",
            "output_id",
            unique=True,
            sqlite_where=text(
                "status IN ('open','isolated','retired_pending_archive','archive_failed')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    output_id: Mapped[int] = mapped_column(Integer, ForeignKey("render_outputs.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(96), index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    # open | isolated | retired_pending_archive | archive_failed | retired_published
    source: Mapped[str] = mapped_column(String(32), default="manual")
    archive_attempts: Mapped[int] = mapped_column(Integer, default=0)
    archive_error: Mapped[str] = mapped_column(Text, default="")
    archive_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublicationTarget(Base):
    """One immutable platform/account occurrence inside a publication group."""

    __tablename__ = "publication_targets"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "platform", "account_key", name="uq_publication_target_occurrence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("publication_groups.id"), index=True
    )
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    output_id: Mapped[int] = mapped_column(Integer, ForeignKey("render_outputs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    account_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # pending | submitting | outcome_unknown | published
    fact_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachQueueItem(Base):
    """G5 semi-auto publish queue — human-in-the-loop; never auto-posts."""

    __tablename__ = "reach_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)  # douyin/channels/xhs/kuaishou/…
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # queued | awaiting_human | published | failed | cancelled | blocked
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    video_path: Mapped[str] = mapped_column(Text, default="")
    pack_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("render_outputs.id"), nullable=True)
    publication_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publication_groups.id"), nullable=True, index=True
    )
    publication_target_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publication_targets.id"), nullable=True, index=True
    )
    copy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    log_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachPublishRun(Base):
    """G5.BATCH durable serial publish run — one machine-wide slot."""

    __tablename__ = "reach_publish_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # queued | running | paused_human | outcome_unknown | failed | interrupted_system | cancelled | completed
    source: Mapped[str] = mapped_column(String(32), default="manual")
    launch_mode: Mapped[str] = mapped_column(String(24), default="manual")
    requested_total: Mapped[int] = mapped_column(Integer, default=0)
    allocation_mode: Mapped[str] = mapped_column(String(24), default="")
    content_mode: Mapped[str] = mapped_column(String(32), default="")
    selection_seed: Mapped[str] = mapped_column(String(64), default="")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    trigger_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    accept_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_after_current: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_action: Mapped[str] = mapped_column(String(24), default="")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    circuit_break: Mapped[bool] = mapped_column(Boolean, default=False)
    # Legacy column kept for customer DBs that still have NOT NULL without DEFAULT.
    # Cursor handoff was removed; always persist False so create_run inserts succeed.
    cursor_handoff_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    chrome_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chrome_profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    log_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items: Mapped[list["ReachPublishRunItem"]] = relationship(back_populates="run")


class ReachPublishRunItem(Base):
    """One queue item inside a serial publish run."""

    __tablename__ = "reach_publish_run_items"
    __table_args__ = (UniqueConstraint("run_id", "ordinal", name="uq_reach_publish_run_item_ord"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("reach_publish_runs.id"), index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    queue_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("reach_queue.id"), nullable=True, index=True)
    output_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("render_outputs.id"), nullable=True, index=True
    )
    publication_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publication_groups.id"), nullable=True, index=True
    )
    publication_target_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publication_targets.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    chrome_profile: Mapped[str] = mapped_column(String(128))
    group_label: Mapped[str] = mapped_column(String(64), default="")
    phase: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gate_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assignment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    requested_action: Mapped[str] = mapped_column(String(24), default="")
    retry_mode: Mapped[str] = mapped_column(String(16), default="")
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_of_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    retry_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    video_path: Mapped[str] = mapped_column(Text, default="")
    pack_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run: Mapped["ReachPublishRun"] = relationship(back_populates="items")


class ReachPublishSchedule(Base):
    """Customer-scoped scheduled publish plan with explicit time points."""

    __tablename__ = "reach_publish_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    chrome_profile: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), index=True)
    content_source: Mapped[str] = mapped_column(String(32), default="manual_ready")
    # manual_ready | eligible_ready_random | generate_then_publish
    source_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    times_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    windows_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    random_algorithm: Mapped[str] = mapped_column(String(32), default="window_v1")
    items_per_trigger: Mapped[int] = mapped_column(Integer, default=1)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachPublishTrigger(Base):
    """Materialized or pending schedule trigger."""

    __tablename__ = "reach_publish_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, ForeignKey("reach_publish_schedules.id"), index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    planned_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # pending | materialized | skipped | missed | cancelled
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    occurrence_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    local_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    random_seed: Mapped[str | None] = mapped_column(String(32), nullable=True)
    random_algorithm: Mapped[str] = mapped_column(String(32), default="window_v1")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    conflict_adjustment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bypass_window_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachPublishReservation(Base):
    """Frozen scheduled content/quota excluded from immediate publishing."""

    __tablename__ = "reach_publish_reservations"
    __table_args__ = (
        UniqueConstraint("trigger_id", "ordinal", name="uq_reach_publish_reservation_trigger_ord"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reach_publish_schedules.id"), index=True
    )
    trigger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reach_publish_triggers.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    output_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("render_outputs.id"), nullable=True, index=True
    )
    queue_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_queue.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    chrome_profile: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="reserved", index=True)
    quota_units: Mapped[int] = mapped_column(Integer, default=1)
    assignment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class OperationLog(Base):
    """Append-only, customer-scoped structured operational audit event."""

    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="customer", index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(64), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(40), default="", index=True)
    source_id: Mapped[str] = mapped_column(String(96), default="", index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    account: Mapped[str] = mapped_column(String(128), default="", index=True)
    platform: Mapped[str] = mapped_column(String(32), default="", index=True)
    retry_no: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class AutomationPlan(Base):
    """Customer-scoped production → publish automation definition."""

    __tablename__ = "automation_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    target_ready_count: Mapped[int] = mapped_column(Integer, default=1)
    template_name: Mapped[str] = mapped_column(String(128), default="default-vertical")
    theme: Mapped[str] = mapped_column(String(128), default="default")
    category: Mapped[str] = mapped_column(String(128), default="default")
    publish_schedule_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_publish_schedules.id"), nullable=True, index=True
    )
    review_policy: Mapped[str] = mapped_column(String(32), default="ready_gate")
    max_production_attempts: Mapped[int] = mapped_column(Integer, default=3)
    max_replacement_attempts: Mapped[int] = mapped_column(Integer, default=2)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AutomationOccurrence(Base):
    """Durable checkpoint joining production, pack, publish and monitoring."""

    __tablename__ = "automation_occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("automation_plans.id"), nullable=True, index=True
    )
    local_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(40), default="preflight", index=True)
    production_job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("jobs.id"), nullable=True, index=True
    )
    output_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("render_outputs.id"), nullable=True, index=True
    )
    pack_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    queue_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_queue.id"), nullable=True, index=True
    )
    publish_schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    publish_trigger_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    publish_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    replacement_of_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("automation_occurrences.id"), nullable=True, index=True
    )
    replacement_attempt: Mapped[int] = mapped_column(Integer, default=0)
    bypass_window_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HumanAlert(Base):
    """Persistent login/verification alert with independent channel delivery."""

    __tablename__ = "human_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="")
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    safe_summary: Mapped[str] = mapped_column(String(280), default="")
    deep_link: Mapped[str] = mapped_column(Text, default="")
    channels_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    escalation_step: Mapped[int] = mapped_column(Integer, default=0)
    next_escalation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachMessageAccount(Base):
    """Customer-scoped, locally managed Chrome account used for read-only scans."""

    __tablename__ = "reach_message_accounts"
    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "business_scope",
            "platform",
            "profile_name",
            name="uq_reach_msg_account_profile_scope",
        ),
        CheckConstraint("cooldown_sec = 1800", name="ck_reach_message_cooldown_lock"),
        CheckConstraint(
            "business_scope IN ('video', 'content')",
            name="ck_reach_msg_account_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    business_scope: Mapped[str] = mapped_column(String(16), default="video", index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    profile_name: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(32), default="message")
    # message = dedicated user-data-dir; shared_legacy = an old publishing
    # profile binding kept readable until the operator replaces it; archived
    # rows retain message history but can no longer be scanned.
    profile_role: Mapped[str] = mapped_column(
        String(32), default="message", server_default="message", index=True
    )
    provisioning_status: Mapped[str] = mapped_column(String(32), default="explicit")
    explicit_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    cooldown_sec: Mapped[int] = mapped_column(Integer, default=1800)
    message_url: Mapped[str] = mapped_column(Text, default="")
    adapter_version: Mapped[str] = mapped_column(String(64), default="")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success_cursor: Mapped[str] = mapped_column(Text, default="")
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachMessage(Base):
    """Unread message metadata only; never stores cookies or complete conversations."""

    __tablename__ = "reach_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "external_key", name="uq_reach_message_external"),
        CheckConstraint("length(summary) <= 280", name="ck_reach_message_summary_len"),
        CheckConstraint(
            "business_scope IN ('video', 'content')",
            name="ck_reach_message_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("reach_message_accounts.id"), index=True)
    business_scope: Mapped[str] = mapped_column(String(16), default="video", index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_key: Mapped[str] = mapped_column(String(160))
    # comment | dm | like | other — likes never trigger App/ntfy notifications
    kind: Mapped[str] = mapped_column(String(16), default="other", index=True)
    sender: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(String(280), default="")
    reply_url: Mapped[str] = mapped_column(Text)
    unread: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="dom_heuristic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    platform_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    identity_source: Mapped[str] = mapped_column(String(64), default="")
    platform_unread: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    redaction_version: Mapped[str] = mapped_column(String(32), default="v1")
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notification_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class ReachMessageScan(Base):
    __tablename__ = "reach_message_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_message_accounts.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReachNotificationEvent(Base):
    """Auditable notification attempt without credentials or full conversations."""

    __tablename__ = "reach_notification_events"
    __table_args__ = (
        UniqueConstraint("customer_id", "message_id", "channel", name="uq_reach_notification_delivery"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_messages.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ProductionRuleProfile(Base):
    """Customer-scoped video production rule versions (GVideoRules).

    AI parse → draft → human approve → activate. Jobs freeze id+revision+effective.
    """

    __tablename__ = "production_rule_profiles"
    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "orientation",
            "name",
            "revision",
            name="uq_production_rule_rev",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    content_category: Mapped[str] = mapped_column(String(128), default="default", index=True)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait", index=True)
    name: Mapped[str] = mapped_column(String(128), default="未命名规则")
    source_text: Mapped[str] = mapped_column(Text, default="")
    requested_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    effective_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[str] = mapped_column(String(64), default="suying.production_rules.v2")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # draft | approved | archived. Active is category-scoped via Customer.profile_json.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    model: Mapped[str] = mapped_column(String(128), default="")
    parse_warnings_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rejected_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    clamped_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # GRuleLabOpt: participate in per-job rotation pool (drafts never picked).
    rotation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SemanticHealthRun(Base):
    """Incremental semantic evidence health-check execution."""

    __tablename__ = "semantic_health_runs"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key", name="uq_semantic_health_run_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    cursor_after: Mapped[int] = mapped_column(Integer, default=0)
    cursor_end: Mapped[int] = mapped_column(Integer, default=0)
    checked_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(128), default="operator")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SemanticHealthCandidate(Base):
    """Idempotent finding produced from persisted coarse/strict evidence."""

    __tablename__ = "semantic_health_candidates"
    __table_args__ = (
        UniqueConstraint("customer_id", "candidate_key", name="uq_semantic_health_candidate_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("semantic_health_runs.id"), index=True)
    candidate_key: Mapped[str] = mapped_column(String(160))
    cliplet_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cliplets.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning", index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    evidence_level: Mapped[str] = mapped_column(String(32), default="unknown")
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SemanticHealthDecision(Base):
    """Append-only operator decision for a health candidate."""

    __tablename__ = "semantic_health_decisions"
    __table_args__ = (
        UniqueConstraint("customer_id", "decision_key", name="uq_semantic_health_decision_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("semantic_health_candidates.id"), index=True)
    decision_key: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(24), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="operator")
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SemanticCluster(Base):
    """Stable, suggested semantic grouping; never auto-merges."""

    __tablename__ = "semantic_clusters"
    __table_args__ = (
        UniqueConstraint("customer_id", "cluster_key", name="uq_semantic_cluster_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    cluster_key: Mapped[str] = mapped_column(String(96))
    name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(24), default="suggested", index=True)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(32), default="labels-embed-v1")
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SemanticClusterMember(Base):
    __tablename__ = "semantic_cluster_members"
    __table_args__ = (
        UniqueConstraint("customer_id", "cluster_id", "cliplet_id", name="uq_semantic_cluster_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    cluster_id: Mapped[int] = mapped_column(Integer, ForeignKey("semantic_clusters.id"), index=True)
    cliplet_id: Mapped[int] = mapped_column(Integer, ForeignKey("cliplets.id"), index=True)
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class OfficialObjectEvidence(Base):
    """Cached official-catalog statement, kept separate from visual facts."""

    __tablename__ = "official_object_evidence"
    __table_args__ = (
        UniqueConstraint("customer_id", "url_hash", name="uq_official_object_evidence_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), default="official_catalog")
    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    canonical_name: Mapped[str] = mapped_column(String(256), default="")
    category: Mapped[str] = mapped_column(String(256), default="")
    model: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    visual_fact_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    verified_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PlatformRuleVersion(Base):
    __tablename__ = "platform_rule_versions"
    __table_args__ = (
        UniqueConstraint("customer_id", "platform", "version", name="uq_platform_rule_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_sha256: Mapped[str] = mapped_column(String(64), default="")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class KeywordPackRevisionCandidate(Base):
    """Auditable full-schema draft awaiting safe auto or human promotion."""

    __tablename__ = "keyword_pack_revision_candidates"
    __table_args__ = (
        UniqueConstraint("customer_id", "draft_key", name="uq_keyword_pack_revision_draft_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    draft_key: Mapped[str] = mapped_column(String(160))
    base_pack_id: Mapped[int] = mapped_column(Integer, ForeignKey("keyword_packs.id"), index=True)
    target_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="high", index=True)
    draft_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_pack_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("keyword_packs.id"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentSource(Base):
    """Customer-confirmed facts / files / URLs for SEO/GEO generation (GContent)."""

    __tablename__ = "content_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact", index=True)
    # fact | file | url | product | case | certificate | policy
    title: Mapped[str] = mapped_column(String(256), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    evidence_hash: Mapped[str] = mapped_column(String(64), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verified_by: Mapped[str] = mapped_column(String(128), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ManagedSite(Base):
    """Official site / multi-domain entry owned by the customer."""

    __tablename__ = "managed_sites"
    __table_args__ = (UniqueConstraint("customer_id", "domain", name="uq_managed_site_domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    domain: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="primary")
    # primary | regional | product | mirror_summary
    cms_type: Mapped[str] = mapped_column(String(64), default="generic")
    publish_url: Mapped[str] = mapped_column(Text, default="")
    canonical_group: Mapped[str] = mapped_column(String(64), default="main")
    sitemap_url: Mapped[str] = mapped_column(Text, default="")
    robots_url: Mapped[str] = mapped_column(Text, default="")
    search_verify_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str] = mapped_column(String(256), default="")  # keychain/secrets pointer only
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentCampaign(Base):
    __tablename__ = "content_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    audience: Mapped[str] = mapped_column(Text, default="")
    keywords_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    questions_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    regions_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    goal: Mapped[str] = mapped_column(String(64), default="brand_authority")
    tone: Mapped[str] = mapped_column(String(64), default="professional")
    author_name: Mapped[str] = mapped_column(String(128), default="")
    weekly_quota: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentArticle(Base):
    """Master fact-checked article (human approval required before publish)."""

    __tablename__ = "content_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("content_campaigns.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # draft | generated | needs_review | approved | rejected | archived
    title: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    body_md: Mapped[str] = mapped_column(Text, default="")
    faq_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fact_ids_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    citations_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    ai_labeled: Mapped[bool] = mapped_column(Boolean, default=True)
    compliance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentVariant(Base):
    """Platform / domain rewrite of a master article."""

    __tablename__ = "content_variants"
    __table_args__ = (
        UniqueConstraint("article_id", "platform", "site_id", name="uq_content_variant_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    article_id: Mapped[int] = mapped_column(Integer, ForeignKey("content_articles.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("managed_sites.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    body_md: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    similarity: Mapped[float] = mapped_column(Float, default=1.0)
    assets_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentPublishJob(Base):
    """Durable article publish queue (not in-memory)."""

    __tablename__ = "content_publish_jobs"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key", name="uq_content_publish_idempotency"),
        CheckConstraint(
            "business_scope IN ('content')",
            name="ck_content_publish_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    article_id: Mapped[int] = mapped_column(Integer, ForeignKey("content_articles.id"), index=True)
    variant_id: Mapped[int] = mapped_column(Integer, ForeignKey("content_variants.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    profile_name: Mapped[str] = mapped_column(String(128), default="")
    business_scope: Mapped[str] = mapped_column(String(16), default="content", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # queued | running | need_human | pending_review | published | failed | cancelled | blocked
    idempotency_key: Mapped[str] = mapped_column(String(80))
    published_url: Mapped[str] = mapped_column(Text, default="")
    review_id: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    human_resume_url: Mapped[str] = mapped_column(Text, default="")
    adapter_version: Mapped[str] = mapped_column(String(32), default="1")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    log_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentPublishAttempt(Base):
    __tablename__ = "content_publish_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("content_publish_jobs.id"), index=True)
    phase: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentInteraction(Base):
    """Desensitized comment/message summaries tied to published content."""

    __tablename__ = "content_interactions"
    __table_args__ = (
        UniqueConstraint("account_id", "external_key", name="uq_content_interaction_external"),
        CheckConstraint("length(summary) <= 280", name="ck_content_interaction_summary_len"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_message_accounts.id"), nullable=True, index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("content_articles.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_key: Mapped[str] = mapped_column(String(160))
    sender: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(String(280), default="")
    reply_url: Mapped[str] = mapped_column(Text, default="")
    unread: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReplyDraft(Base):
    """Local reply suggestions — never auto-sent."""

    __tablename__ = "reply_drafts"
    __table_args__ = (
        CheckConstraint(
            "business_scope IN ('video', 'content')",
            name="ck_reply_draft_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    business_scope: Mapped[str] = mapped_column(String(16), default="content", index=True)
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reach_messages.id"), nullable=True, index=True
    )
    interaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("content_interactions.id"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, default="")
    based_on_summary_only: Mapped[bool] = mapped_column(Boolean, default=True)
    context_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    # draft | copied | discarded | used_externally
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Cliplet(Base):
    __tablename__ = "cliplets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    asset_uuid: Mapped[str] = mapped_column(String(36), index=True)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    duration_sec: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(128), default="uncategorized")
    theme: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    theme_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    objects_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    actions_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Nullable additive v1 fields keep upgraded SQLite databases and legacy rows readable.
    semantic_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    semantic_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    semantic_gate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    semantic_attempts: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    # usable | rejected_blur | rejected_semantic — rejected rows are audit-only.
    status: Mapped[str] = mapped_column(String(32), default="usable", index=True)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    # Provenance is nullable for legacy/coarse compatibility. Strict semantic
    # consumers require all three fields to match the locked Ollama contract.
    embedding_backend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Short lease used by the semantic backfill API. It is independent from
    # status so a crash never destroys a legacy vector or makes a row unusable.
    semantic_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    semantic_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class SemanticBackfillState(Base):
    """Durable audit cursor for bounded semantic backfill calls."""

    __tablename__ = "semantic_backfill_state"
    __table_args__ = (
        UniqueConstraint("customer_id", "schema_version", name="uq_semantic_backfill_customer_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    processed: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    last_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index(
            "uq_review_items_current_output",
            "render_output_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "uq_review_items_decision_key",
            "decision_key",
            unique=True,
            sqlite_where=text("decision_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    render_output_id: Mapped[int] = mapped_column(ForeignKey("render_outputs.id"))
    status: Mapped[str] = mapped_column(
        String(32), default="uncertain"
    )  # uncertain/approved/rejected
    note: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    decision_source: Mapped[str] = mapped_column(String(32), default="manual")
    decision_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GoldenSample(Base):
    """L0 signed quality ruler rows — scores must live in DB, not only docs."""

    __tablename__ = "golden_samples"
    __table_args__ = (UniqueConstraint("customer_id", "code", name="uq_golden_customer_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32))  # G1 / G2 / G3
    filename: Mapped[str] = mapped_column(String(255))
    rel_path: Mapped[str] = mapped_column(Text, default="")  # under output_root
    absolute_path: Mapped[str] = mapped_column(Text, default="")
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    title_text: Mapped[str] = mapped_column(Text, default="")
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    look_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 1–5
    shippable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)  # YYYY-MM-DD
    note: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class CalendarEntry(Base):
    """Content calendar: one day → production plan."""

    __tablename__ = "calendar_entries"
    __table_args__ = (UniqueConstraint("customer_id", "day", name="uq_calendar_customer_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    theme: Mapped[str] = mapped_column(String(128), default="default")
    category: Mapped[str] = mapped_column(String(128), default="default")
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    template_name: Mapped[str] = mapped_column(String(128), default="default-vertical")
    quota: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ClipletUsage(Base):
    """Track cliplet appearances for cooldown / anti-repeat."""

    __tablename__ = "cliplet_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    cliplet_id: Mapped[int] = mapped_column(Integer, index=True)
    asset_uuid: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=True)
    render_output_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("render_outputs.id"), nullable=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class DailyUsage(Base):
    """纸片规则：本地日历日用量（仅 ready 成片 +1）。kind=cliplet|phrase。"""

    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint("customer_id", "kind", "key", "day", name="uq_daily_usage_cust_kind_key_day"),
        CheckConstraint("count >= 0", name="ck_daily_usage_count_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(512), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD local
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class WeeklyUsage(Base):
    """纸片规则：上海自然周用量（周键为周一日期，仅 ready 成片 +1）。"""

    __tablename__ = "weekly_usage"
    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "kind",
            "key",
            "week",
            name="uq_weekly_usage_cust_kind_key_week",
        ),
        CheckConstraint("count >= 0", name="ck_weekly_usage_count_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(512), index=True)
    week: Mapped[str] = mapped_column(String(10), index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PaperSlipReservation(Base):
    """并行生产的纸片预留租约；ready 原子提交，失败/取消释放。"""

    __tablename__ = "paper_slip_reservations"
    __table_args__ = (
        UniqueConstraint(
            "reservation_key",
            "kind",
            "key",
            name="uq_paper_slip_reservation_item",
        ),
        CheckConstraint(
            "status IN ('reserved','committed','released','expired')",
            name="ck_paper_slip_reservation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reservation_key: Mapped[str] = mapped_column(String(160), index=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"), index=True)
    customer_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(512), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    week: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def db_path(settings: AppSettings | None = None) -> Path:
    settings = settings or load_settings()
    from engine.config.workspace import is_external_data_root, probe_workspace

    root = Path(settings.paths.data_root)
    if is_external_data_root(root):
        probe = probe_workspace(settings)
        if not probe.can_init_db:
            raise RuntimeError(
                "；".join(probe.reasons) or f"外接工作区不可用（{probe.state}），拒绝创建空库"
            )
        if not root.exists():
            raise RuntimeError(f"外接工作区不存在，拒绝 mkdir: {root}")
    else:
        root.mkdir(parents=True, exist_ok=True)
    return root / "montage.db"


def reset_engine() -> None:
    """Dispose SQLAlchemy engine so the next call rebinds to current db_path()."""
    global _engine, _SessionLocal
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _SessionLocal = None


def get_engine(settings: AppSettings | None = None):
    global _engine, _SessionLocal
    if _engine is None:
        from sqlalchemy import event

        path = db_path(settings)
        _engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 60},
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=120000")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db(settings: AppSettings | None = None) -> None:
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    _run_sqlite_migrations(settings)


def _table_cols(conn, table: str) -> set[str]:
    from sqlalchemy import text

    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _add_column_if_missing(conn, table: str, col: str, ddl: str) -> None:
    from sqlalchemy import text

    if col not in _table_cols(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def _migrate_business_scope_columns(conn) -> None:
    """Add business_scope to message/content tables and backfill by platform."""
    from sqlalchemy import text

    _add_column_if_missing(conn, "reach_message_accounts", "business_scope", "VARCHAR(16)")
    _add_column_if_missing(conn, "reach_messages", "business_scope", "VARCHAR(16)")
    _add_column_if_missing(conn, "reach_messages", "kind", "VARCHAR(16)")
    try:
        conn.execute(text("UPDATE reach_messages SET kind='other' WHERE kind IS NULL OR kind=''"))
    except Exception:
        pass
    _add_column_if_missing(conn, "reply_drafts", "business_scope", "VARCHAR(16)")
    _add_column_if_missing(conn, "content_publish_jobs", "business_scope", "VARCHAR(16)")

    content_plats = (
        "baijiahao",
        "toutiao",
        "zhihu",
        "wechat_mp",
        "sohu",
        "netease",
        "penguin",
        "website",
        "csdn",
        "juejin",
        "cnblogs",
        "baike",
        "baidu_ziyuan",
        "so_360",
    )
    video_plats = ("douyin", "channels", "xhs", "kuaishou")
    content_in = ", ".join(f"'{p}'" for p in content_plats)
    video_in = ", ".join(f"'{p}'" for p in video_plats)

    if "reach_message_accounts" in {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }:
        conn.execute(
            text(
                f"UPDATE reach_message_accounts SET business_scope='content' "
                f"WHERE (business_scope IS NULL OR business_scope='') AND platform IN ({content_in})"
            )
        )
        conn.execute(
            text(
                f"UPDATE reach_message_accounts SET business_scope='video' "
                f"WHERE (business_scope IS NULL OR business_scope='') AND platform IN ({video_in})"
            )
        )
        # Unknown platforms stay video only if still empty — prefer leave empty then force video as legacy default.
        conn.execute(
            text(
                "UPDATE reach_message_accounts SET business_scope='video' "
                "WHERE business_scope IS NULL OR business_scope=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_message_accounts_business_scope "
                "ON reach_message_accounts (business_scope)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_msg_acct_customer_scope "
                "ON reach_message_accounts (customer_id, business_scope)"
            )
        )

    if "reach_messages" in {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }:
        conn.execute(
            text(
                "UPDATE reach_messages SET business_scope=("
                "  SELECT a.business_scope FROM reach_message_accounts a "
                "  WHERE a.id = reach_messages.account_id"
                ") WHERE business_scope IS NULL OR business_scope=''"
            )
        )
        conn.execute(
            text(
                f"UPDATE reach_messages SET business_scope='content' "
                f"WHERE (business_scope IS NULL OR business_scope='') AND platform IN ({content_in})"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_messages SET business_scope='video' "
                "WHERE business_scope IS NULL OR business_scope=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_messages_business_scope "
                "ON reach_messages (business_scope)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_messages_customer_scope_unread "
                "ON reach_messages (customer_id, business_scope, unread)"
            )
        )

    if "reply_drafts" in {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }:
        conn.execute(
            text(
                "UPDATE reply_drafts SET business_scope=("
                "  SELECT m.business_scope FROM reach_messages m "
                "  WHERE m.id = reply_drafts.message_id"
                ") WHERE (business_scope IS NULL OR business_scope='') AND message_id IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE reply_drafts SET business_scope='content' "
                "WHERE business_scope IS NULL OR business_scope=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reply_drafts_business_scope "
                "ON reply_drafts (business_scope)"
            )
        )

    if "content_publish_jobs" in {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }:
        conn.execute(
            text(
                "UPDATE content_publish_jobs SET business_scope='content' "
                "WHERE business_scope IS NULL OR business_scope=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_content_publish_jobs_business_scope "
                "ON content_publish_jobs (business_scope)"
            )
        )

    # Drop and recreate tenant triggers so older DBs pick up business_scope-aware bodies.
    if "reach_message_accounts" in {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }:
        conn.execute(text("DROP TRIGGER IF EXISTS trg_reach_message_profile_tenant_insert"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_reach_message_profile_tenant_update"))
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_profile_tenant_insert
                BEFORE INSERT ON reach_message_accounts
                WHEN EXISTS (
                    SELECT 1 FROM reach_message_accounts
                    WHERE profile_name = NEW.profile_name
                      AND business_scope = NEW.business_scope
                      AND customer_id != NEW.customer_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'message Chrome profile cannot cross customers');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_profile_tenant_update
                BEFORE UPDATE OF profile_name, customer_id, business_scope ON reach_message_accounts
                WHEN EXISTS (
                    SELECT 1 FROM reach_message_accounts
                    WHERE profile_name = NEW.profile_name
                      AND business_scope = NEW.business_scope
                      AND customer_id != NEW.customer_id
                      AND id != NEW.id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'message Chrome profile cannot cross customers');
                END
                """
            )
        )


def _migrate_calendar_unique(conn) -> None:
    """Rebuild calendar_entries so day is unique per customer, not globally."""
    from sqlalchemy import text

    cols = _table_cols(conn, "calendar_entries")
    if "customer_id" not in cols:
        return
    indexes = conn.execute(text("PRAGMA index_list(calendar_entries)")).fetchall()
    for idx in indexes:
        idx_name = idx[1]
        unique = idx[2]
        if not unique:
            continue
        info = conn.execute(text(f"PRAGMA index_info({idx_name})")).fetchall()
        indexed_cols = [row[2] for row in info]
        if indexed_cols == ["day"]:
            conn.execute(text("""
                CREATE TABLE calendar_entries_v6 (
                    id INTEGER PRIMARY KEY,
                    customer_id INTEGER,
                    day VARCHAR(10) NOT NULL,
                    theme VARCHAR(128) DEFAULT 'default',
                    category VARCHAR(128) DEFAULT 'default',
                    customer_name VARCHAR(128) DEFAULT '',
                    template_name VARCHAR(128) DEFAULT 'default-vertical',
                    quota INTEGER DEFAULT 5,
                    note TEXT DEFAULT '',
                    active BOOLEAN DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME,
                    FOREIGN KEY(customer_id) REFERENCES customers(id),
                    UNIQUE(customer_id, day)
                )
            """))
            conn.execute(text("""
                INSERT INTO calendar_entries_v6
                (id, customer_id, day, theme, category, customer_name, template_name, quota, note, active, created_at, updated_at)
                SELECT id, customer_id, day, theme, category, customer_name, template_name, quota, note, active, created_at, updated_at
                FROM calendar_entries
            """))
            conn.execute(text("DROP TABLE calendar_entries"))
            conn.execute(text("ALTER TABLE calendar_entries_v6 RENAME TO calendar_entries"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_calendar_entries_day ON calendar_entries (day)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_calendar_entries_customer_id ON calendar_entries (customer_id)"))
            break


def _migrate_review_decisions(conn) -> None:
    """Add append-only review decisions with one current row per output."""
    _add_column_if_missing(
        conn, "review_items", "is_current", "BOOLEAN NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(
        conn, "review_items", "decision_source", "VARCHAR(32) DEFAULT 'legacy'"
    )
    _add_column_if_missing(conn, "review_items", "decision_key", "VARCHAR(160)")
    _add_column_if_missing(
        conn, "review_items", "evidence_json", "JSON DEFAULT '{}'"
    )
    conn.execute(
        text("UPDATE review_items SET status='uncertain' WHERE status='pending'")
    )
    conn.execute(text("UPDATE review_items SET is_current=0"))
    conn.execute(
        text(
            "UPDATE review_items SET is_current=1 "
            "WHERE id IN (SELECT MAX(id) FROM review_items GROUP BY render_output_id)"
        )
    )
    # A legacy row labelled ready without explicit READY_GATE success is an
    # evidence conflict, not a publishable output.
    conn.execute(
        text(
            "UPDATE render_outputs SET state='review' "
            "WHERE state='ready' "
            "AND COALESCE(json_extract(qc_json, '$.ready_gate.ok'), 0) != 1"
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO review_items
                (render_output_id, status, note, is_current, decision_source,
                 decision_key, evidence_json, created_at, decided_at)
            SELECT
                o.id,
                CASE
                    WHEN o.state='ready' THEN 'approved'
                    WHEN o.state='failed' THEN 'rejected'
                    ELSE 'uncertain'
                END,
                CASE
                    WHEN o.state='ready' THEN '自动通过：成片已通过出片门禁'
                    WHEN o.state='failed' THEN '自动拒绝：硬失败不可发布'
                    ELSE '待人工：审片状态或证据冲突'
                END,
                1,
                'migration',
                'migration-output:' || o.id,
                json_object('output_state', o.state, 'policy', 'mandatory_ready_gate_v2'),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM render_outputs o
            WHERE o.state IN ('ready', 'review', 'failed')
              AND NOT EXISTS (
                  SELECT 1 FROM review_items r WHERE r.render_output_id=o.id
              )
            """
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_items_current_output "
            "ON review_items (render_output_id) WHERE is_current=1"
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_items_decision_key "
            "ON review_items (decision_key) WHERE decision_key IS NOT NULL"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_review_items_current_status "
            "ON review_items (is_current, status)"
        )
    )


def install_paper_slip_guards(conn) -> None:  # noqa: ANN001
    """Install upgrade-safe SQLite guards for paper-slip reservations (no hard caps)."""
    # Drop legacy day/week hard-cap triggers from older installs.
    for name in (
        "trg_daily_usage_cap_insert",
        "trg_daily_usage_cap_update",
        "trg_weekly_usage_cap_insert",
        "trg_weekly_usage_cap_update",
        "trg_paper_slip_reservation_cap_insert",
    ):
        conn.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
    statements = (
        """
        CREATE TRIGGER IF NOT EXISTS trg_daily_usage_nonneg_insert
        BEFORE INSERT ON daily_usage
        WHEN NEW.count < 0
        BEGIN
            SELECT RAISE(ABORT, 'paper slip daily usage count must be >= 0');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_daily_usage_nonneg_update
        BEFORE UPDATE OF count ON daily_usage
        WHEN NEW.count < 0
        BEGIN
            SELECT RAISE(ABORT, 'paper slip daily usage count must be >= 0');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_weekly_usage_nonneg_insert
        BEFORE INSERT ON weekly_usage
        WHEN NEW.count < 0
        BEGIN
            SELECT RAISE(ABORT, 'paper slip weekly usage count must be >= 0');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_weekly_usage_nonneg_update
        BEFORE UPDATE OF count ON weekly_usage
        WHEN NEW.count < 0
        BEGIN
            SELECT RAISE(ABORT, 'paper slip weekly usage count must be >= 0');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_paper_slip_reservation_no_reactivate
        BEFORE UPDATE OF status ON paper_slip_reservations
        WHEN NEW.status='reserved' AND OLD.status!='reserved'
        BEGIN
            SELECT RAISE(ABORT, 'paper slip reservations cannot be reactivated');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_paper_slip_reservation_scope_immutable
        BEFORE UPDATE OF customer_id, kind, key, day, week, reservation_key
        ON paper_slip_reservations
        WHEN OLD.status='reserved' AND (
          NEW.customer_id!=OLD.customer_id OR NEW.kind!=OLD.kind OR NEW.key!=OLD.key
          OR NEW.day!=OLD.day OR NEW.week!=OLD.week
          OR NEW.reservation_key!=OLD.reservation_key
        )
        BEGIN
            SELECT RAISE(ABORT, 'paper slip reservation scope is immutable');
        END
        """,
    )
    for statement in statements:
        conn.execute(text(statement))


def _migrate_paper_slip_rolling_diversity(conn) -> None:  # noqa: ANN001
    """Drop day/week hard-cap CHECK and rebuild usage tables if needed."""
    from sqlalchemy import text

    def _has_legacy_cap(table: str) -> bool:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": table},
        ).scalar()
        blob = str(sql or "")
        return "count <= 2" in blob or f"ck_{table}_count_cap" in blob

    install_paper_slip_guards(conn)
    for table, create_sql in (
        (
            "daily_usage",
            """
            CREATE TABLE daily_usage__rolling (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER NOT NULL DEFAULT 0,
              kind VARCHAR(32) NOT NULL,
              key VARCHAR(512) NOT NULL,
              day VARCHAR(10) NOT NULL,
              count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
              updated_at DATETIME
            )
            """,
        ),
        (
            "weekly_usage",
            """
            CREATE TABLE weekly_usage__rolling (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER NOT NULL DEFAULT 0,
              kind VARCHAR(32) NOT NULL,
              key VARCHAR(512) NOT NULL,
              week VARCHAR(10) NOT NULL,
              count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
              updated_at DATETIME
            )
            """,
        ),
    ):
        if not _has_legacy_cap(table):
            continue
        conn.execute(text(f"DROP TABLE IF EXISTS {table}__rolling"))
        conn.execute(text(create_sql))
        cols = "customer_id, kind, key, day, count, updated_at" if table == "daily_usage" else (
            "customer_id, kind, key, week, count, updated_at"
        )
        key_col = "day" if table == "daily_usage" else "week"
        conn.execute(
            text(
                f"INSERT INTO {table}__rolling (id, {cols}) "
                f"SELECT id, customer_id, kind, key, {key_col}, count, updated_at FROM {table}"
            )
        )
        conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text(f"ALTER TABLE {table}__rolling RENAME TO {table}"))
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_cust_kind_key_{key_col} "
                f"ON {table} (customer_id, kind, key, {key_col})"
            )
        )
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_customer_id ON {table} (customer_id)"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_kind ON {table} (kind)"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_key ON {table} (key)"))
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{key_col} ON {table} ({key_col})"))
    install_paper_slip_guards(conn)


def _run_sqlite_migrations(settings: AppSettings | None = None) -> None:
    from sqlalchemy import text

    from engine.catalog.customer_scope import DEMO_CUSTOMER, get_or_create_customer

    settings = settings or load_settings()
    engine = get_engine(settings)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at DATETIME NOT NULL
            )
        """))
        # 2026-08-01: daily production/upload quotas were removed. These tables
        # contain counters only; publication facts live in publication_targets.
        conn.execute(text("DROP TABLE IF EXISTS license_quota_reservations"))
        conn.execute(text("DROP TABLE IF EXISTS license_daily_usage"))
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS operation_logs_append_only_update "
                "BEFORE UPDATE ON operation_logs BEGIN "
                "SELECT RAISE(ABORT, 'operation_logs are append-only'); END"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER IF NOT EXISTS operation_logs_append_only_delete "
                "BEFORE DELETE ON operation_logs BEGIN "
                "SELECT RAISE(ABORT, 'operation_logs are append-only'); END"
            )
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260801_remove_business_quotas",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        _add_column_if_missing(conn, "assets", "proxy_path", "TEXT")
        _add_column_if_missing(conn, "assets", "orientation", "VARCHAR(16) DEFAULT 'unknown'")
        _add_column_if_missing(conn, "jobs", "orientation", "VARCHAR(16) DEFAULT 'portrait'")
        _add_column_if_missing(
            conn, "render_outputs", "orientation", "VARCHAR(16) DEFAULT 'portrait'"
        )
        _add_column_if_missing(
            conn, "production_rule_profiles", "orientation", "VARCHAR(16) DEFAULT 'portrait'"
        )
        _add_column_if_missing(
            conn, "vectorization_runs", "orientation", "VARCHAR(16) DEFAULT 'portrait'"
        )
        _add_column_if_missing(
            conn, "vectorization_queue", "orientation", "VARCHAR(16) DEFAULT 'portrait'"
        )
        conn.execute(
            text(
                "UPDATE assets SET orientation=CASE "
                "WHEN width IS NULL OR height IS NULL OR width=height THEN 'unknown' "
                "WHEN width > height THEN 'landscape' ELSE 'portrait' END "
                "WHERE orientation IS NULL OR orientation='' OR orientation='unknown'"
            )
        )
        conn.execute(
            text(
                "UPDATE assets SET status='pending' "
                "WHERE status='rejected_landscape'"
            )
        )
        conn.execute(
            text(
                "UPDATE vectorization_queue SET orientation=COALESCE("
                "(SELECT orientation FROM assets WHERE assets.id=vectorization_queue.asset_id),"
                "'portrait')"
            )
        )
        for table in ("jobs", "render_outputs", "production_rule_profiles", "vectorization_runs"):
            conn.execute(
                text(
                    f"UPDATE {table} SET orientation='portrait' "
                    "WHERE orientation IS NULL OR orientation=''"
                )
            )
        _add_column_if_missing(conn, "customers", "library_root", "TEXT")
        _add_column_if_missing(conn, "customers", "library_roots", "JSON")
        _add_column_if_missing(conn, "customers", "output_root", "TEXT")
        _add_column_if_missing(conn, "customers", "keyword_pack_path", "TEXT")
        _add_column_if_missing(conn, "customers", "active", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(conn, "keyword_packs", "schema_version", "VARCHAR(128) DEFAULT ''")
        _add_column_if_missing(conn, "keyword_packs", "revision", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "keyword_packs", "content_sha256", "VARCHAR(64) DEFAULT ''")
        _add_column_if_missing(conn, "keyword_packs", "source_path", "TEXT")
        _add_column_if_missing(conn, "keyword_packs", "source_kind", "VARCHAR(32) DEFAULT 'manual'")
        _add_column_if_missing(conn, "keyword_packs", "status", "VARCHAR(32) DEFAULT 'active'")
        _add_column_if_missing(conn, "keyword_packs", "validation_report", "JSON DEFAULT '{}'")
        _add_column_if_missing(conn, "keyword_packs", "activated_at", "DATETIME")
        _add_column_if_missing(conn, "keyword_packs", "archived_at", "DATETIME")
        _add_column_if_missing(conn, "assets", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "jobs", "customer_id", "INTEGER")
        _add_column_if_missing(
            conn, "production_rule_profiles", "content_category", "VARCHAR(128) DEFAULT 'default'"
        )
        _add_column_if_missing(
            conn, "production_rule_profiles", "rotation_enabled", "BOOLEAN DEFAULT 0"
        )
        conn.execute(
            text(
                "UPDATE production_rule_profiles SET content_category='default' "
                "WHERE content_category IS NULL OR trim(content_category)=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_production_rule_customer_category "
                "ON production_rule_profiles (customer_id, content_category)"
            )
        )
        _add_column_if_missing(conn, "calendar_entries", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "keyword_usage", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "cliplet_usage", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "cliplets", "theme", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "theme_score", "FLOAT")
        _add_column_if_missing(conn, "cliplets", "scene", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "objects_json", "JSON")
        _add_column_if_missing(conn, "cliplets", "actions_json", "JSON")
        _add_column_if_missing(conn, "cliplets", "semantic_schema_version", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "semantic_json", "JSON")
        _add_column_if_missing(conn, "cliplets", "semantic_gate_json", "JSON")
        _add_column_if_missing(conn, "cliplets", "semantic_attempts", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "cliplets", "status", "VARCHAR(32) DEFAULT 'usable'")
        _add_column_if_missing(conn, "cliplets", "embedding_backend", "VARCHAR(32)")
        _add_column_if_missing(conn, "cliplets", "embedding_model", "VARCHAR(128)")
        _add_column_if_missing(conn, "cliplets", "embedding_schema_version", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "semantic_claim_token", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "semantic_claimed_at", "DATETIME")
        _add_column_if_missing(conn, "vectorization_queue", "last_attempt_id", "VARCHAR(64)")
        _add_column_if_missing(
            conn, "vectorization_queue", "last_attempt_status", "VARCHAR(16)"
        )
        _add_column_if_missing(
            conn, "render_outputs", "pack_status", "VARCHAR(32) DEFAULT 'pending'"
        )
        _add_column_if_missing(conn, "render_outputs", "pack_dir", "TEXT")
        _add_column_if_missing(conn, "render_outputs", "pack_error", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "render_outputs", "pack_version", "VARCHAR(64) DEFAULT ''")
        _add_column_if_missing(
            conn, "render_outputs", "platform_asset_status", "JSON DEFAULT '{}'"
        )
        _add_column_if_missing(conn, "render_outputs", "serial", "VARCHAR(96)")
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_render_outputs_serial "
                "ON render_outputs (serial)"
            )
        )
        _add_column_if_missing(conn, "render_outputs", "display_no", "INTEGER")
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_render_outputs_display_no "
                "ON render_outputs (display_no)"
            )
        )
        _add_column_if_missing(conn, "reach_queue", "publication_group_id", "INTEGER")
        _add_column_if_missing(conn, "reach_queue", "publication_target_id", "INTEGER")
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_render_outputs_pack_status "
                "ON render_outputs (pack_status)"
            )
        )
        _migrate_review_decisions(conn)
        _add_column_if_missing(
            conn, "reach_publish_runs", "launch_mode", "VARCHAR(24) DEFAULT 'manual'"
        )
        _add_column_if_missing(conn, "reach_publish_runs", "requested_total", "INTEGER DEFAULT 0")
        _add_column_if_missing(
            conn, "reach_publish_runs", "allocation_mode", "VARCHAR(24) DEFAULT ''"
        )
        _add_column_if_missing(
            conn, "reach_publish_runs", "content_mode", "VARCHAR(32) DEFAULT ''"
        )
        _add_column_if_missing(
            conn, "reach_publish_runs", "selection_seed", "VARCHAR(64) DEFAULT ''"
        )
        _add_column_if_missing(
            conn, "reach_publish_runs", "config_json", "JSON DEFAULT '{}'"
        )
        _add_column_if_missing(
            conn, "reach_publish_runs", "notification_json", "JSON DEFAULT '{}'"
        )
        _add_column_if_missing(
            conn, "reach_publish_runs", "requested_action", "VARCHAR(24) DEFAULT ''"
        )
        _add_column_if_missing(conn, "reach_publish_runs", "heartbeat_at", "DATETIME")
        _add_column_if_missing(
            conn,
            "reach_publish_runs",
            "cursor_handoff_enabled",
            "BOOLEAN DEFAULT 0",
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "output_id", "INTEGER"
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "assignment_json", "JSON DEFAULT '{}'"
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "requested_action", "VARCHAR(24) DEFAULT ''"
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "retry_mode", "VARCHAR(16) DEFAULT ''"
        )
        _add_column_if_missing(conn, "reach_publish_run_items", "retry_after", "DATETIME")
        _add_column_if_missing(conn, "reach_publish_run_items", "retry_of_item_id", "INTEGER")
        _add_column_if_missing(
            conn, "reach_publish_run_items", "retry_run_id", "VARCHAR(32)"
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "publication_group_id", "INTEGER"
        )
        _add_column_if_missing(
            conn, "reach_publish_run_items", "publication_target_id", "INTEGER"
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_queue_publication_group_id "
                "ON reach_queue (publication_group_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_queue_publication_target_id "
                "ON reach_queue (publication_target_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_publish_item_publication_group_id "
                "ON reach_publish_run_items (publication_group_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_publish_item_publication_target_id "
                "ON reach_publish_run_items (publication_target_id)"
            )
        )
        _add_column_if_missing(conn, "reach_publish_schedules", "windows_json", "JSON DEFAULT '[]'")
        _add_column_if_missing(
            conn, "reach_publish_schedules", "random_algorithm", "VARCHAR(32) DEFAULT 'window_v1'"
        )
        _add_column_if_missing(conn, "reach_publish_triggers", "occurrence_key", "VARCHAR(128)")
        _add_column_if_missing(conn, "reach_publish_triggers", "local_date", "VARCHAR(10)")
        _add_column_if_missing(conn, "reach_publish_triggers", "window_start", "DATETIME")
        _add_column_if_missing(conn, "reach_publish_triggers", "window_end", "DATETIME")
        _add_column_if_missing(conn, "reach_publish_triggers", "random_seed", "VARCHAR(32)")
        _add_column_if_missing(
            conn, "reach_publish_triggers", "random_algorithm", "VARCHAR(32) DEFAULT 'window_v1'"
        )
        _add_column_if_missing(conn, "reach_publish_triggers", "ordinal", "INTEGER DEFAULT 0")
        _add_column_if_missing(
            conn, "reach_publish_triggers", "conflict_adjustment_json", "JSON DEFAULT '{}'"
        )
        _add_column_if_missing(conn, "reach_publish_triggers", "claimed_at", "DATETIME")
        _add_column_if_missing(
            conn, "reach_publish_triggers", "bypass_window_reason", "VARCHAR(64)"
        )
        _add_column_if_missing(
            conn, "reach_message_accounts", "purpose", "VARCHAR(32) DEFAULT 'legacy'"
        )
        _add_column_if_missing(
            conn,
            "reach_message_accounts",
            "profile_role",
            "VARCHAR(32) NOT NULL DEFAULT 'shared_legacy'",
        )
        _add_column_if_missing(
            conn,
            "reach_message_accounts",
            "provisioning_status",
            "VARCHAR(32) DEFAULT 'legacy_unverified'",
        )
        _add_column_if_missing(conn, "reach_message_accounts", "explicit_created_at", "DATETIME")
        _add_column_if_missing(
            conn, "reach_message_accounts", "adapter_version", "VARCHAR(64) DEFAULT ''"
        )
        _add_column_if_missing(conn, "reach_message_accounts", "last_attempt_at", "DATETIME")
        _add_column_if_missing(conn, "reach_message_accounts", "last_success_at", "DATETIME")
        _add_column_if_missing(
            conn, "reach_message_accounts", "success_cursor", "TEXT DEFAULT ''"
        )
        _add_column_if_missing(conn, "reach_messages", "platform_event_at", "DATETIME")
        _add_column_if_missing(
            conn, "reach_messages", "identity_source", "VARCHAR(64) DEFAULT ''"
        )
        _add_column_if_missing(conn, "reach_messages", "platform_unread", "BOOLEAN")
        _add_column_if_missing(
            conn,
            "reach_messages",
            "redaction_version",
            "VARCHAR(32) DEFAULT 'v1'",
        )
        _add_column_if_missing(conn, "reach_messages", "purged_at", "DATETIME")
        conn.execute(
            text(
                "UPDATE reach_message_accounts SET profile_role='shared_legacy' "
                "WHERE profile_role IS NULL OR trim(profile_role)=''"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_messages SET identity_source='legacy_external_key' "
                "WHERE identity_source IS NULL OR trim(identity_source)=''"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_messages SET redaction_version='legacy' "
                "WHERE redaction_version IS NULL OR trim(redaction_version)=''"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_message_accounts_profile_role "
                "ON reach_message_accounts (profile_role)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_messages_platform_event_at "
                "ON reach_messages (platform_event_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_reach_messages_purged_at "
                "ON reach_messages (purged_at)"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_message_accounts SET purpose='legacy', "
                "provisioning_status='legacy_unverified', enabled=0 "
                "WHERE business_scope='content' AND explicit_created_at IS NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_message_accounts SET purpose='video', "
                "provisioning_status='explicit' "
                "WHERE business_scope='video' "
                "AND (purpose IS NULL OR purpose='' OR purpose='legacy')"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_reach_publish_trigger_occurrence "
                "ON reach_publish_triggers (occurrence_key) WHERE occurrence_key IS NOT NULL"
            )
        )
        conn.execute(
            text(
                "UPDATE reach_publish_runs SET status='interrupted_system', "
                "error='migration: duplicate machine-wide active slot' "
                "WHERE status IN ('queued','running','paused_human','outcome_unknown','stopping') "
                "AND id NOT IN (SELECT MAX(id) FROM reach_publish_runs "
                "WHERE status IN ('queued','running','paused_human','outcome_unknown','stopping'))"
            )
        )
        conn.execute(text("DROP INDEX IF EXISTS uq_reach_publish_machine_slot"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_reach_publish_machine_slot "
                "ON reach_publish_runs ((1)) "
                "WHERE status IN ('queued','running','paused_human','outcome_unknown','stopping')"
            )
        )
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS semantic_backfill_state (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                schema_version VARCHAR(64) NOT NULL,
                processed INTEGER DEFAULT 0 NOT NULL,
                passed INTEGER DEFAULT 0 NOT NULL,
                rejected INTEGER DEFAULT 0 NOT NULL,
                last_cursor INTEGER,
                updated_at DATETIME,
                FOREIGN KEY(customer_id) REFERENCES customers(id),
                UNIQUE(customer_id, schema_version)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_health_run_customer_status "
            "ON semantic_health_runs (customer_id, status, id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_candidate_customer_status "
            "ON semantic_health_candidates (customer_id, status, kind)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_cluster_customer_status "
            "ON semantic_clusters (customer_id, status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_official_evidence_customer_status "
            "ON official_object_evidence (customer_id, status, expires_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_keyword_revision_customer_status "
            "ON keyword_pack_revision_candidates (customer_id, status, id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cliplets_semantic_claim_token "
            "ON cliplets (semantic_claim_token)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_backfill_state_customer_id "
            "ON semantic_backfill_state (customer_id)"
        ))
        # Repair older ORM-written JSON `null` values and enforce the hard
        # invariant for all final semantic rejects. Passed vectors are untouched.
        conn.execute(text(
            "UPDATE cliplets SET embedding_json=NULL, embedding_backend=NULL, "
            "embedding_model=NULL, embedding_schema_version=NULL, indexed_at=NULL "
            "WHERE status='rejected_semantic'"
        ))
        if "reach_message_accounts" in {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }:
            _migrate_business_scope_columns(conn)
            conn.execute(text("UPDATE reach_message_accounts SET cooldown_sec = 1800"))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_cooldown_insert
                BEFORE INSERT ON reach_message_accounts
                WHEN NEW.cooldown_sec != 1800
                BEGIN
                    SELECT RAISE(ABORT, 'message scan interval is locked to 1800 seconds');
                END
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_cooldown_update
                BEFORE UPDATE OF cooldown_sec ON reach_message_accounts
                WHEN NEW.cooldown_sec != 1800
                BEGIN
                    SELECT RAISE(ABORT, 'message scan interval is locked to 1800 seconds');
                END
            """))
        else:
            _migrate_business_scope_columns(conn)
        # Same profile names are valid in different customer directories. Older
        # tenant triggers incorrectly rejected that safe layout.
        conn.execute(text("DROP TRIGGER IF EXISTS trg_reach_message_profile_tenant_insert"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_reach_message_profile_tenant_update"))
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_profile_scope_insert
                BEFORE INSERT ON reach_message_accounts
                WHEN EXISTS (
                    SELECT 1 FROM reach_message_accounts
                    WHERE customer_id = NEW.customer_id
                      AND business_scope = NEW.business_scope
                      AND profile_name = NEW.profile_name
                )
                BEGIN
                    SELECT RAISE(ABORT, 'message Chrome profile already bound in scope');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_reach_message_profile_scope_update
                BEFORE UPDATE OF customer_id, business_scope, profile_name ON reach_message_accounts
                WHEN EXISTS (
                    SELECT 1 FROM reach_message_accounts
                    WHERE customer_id = NEW.customer_id
                      AND business_scope = NEW.business_scope
                      AND profile_name = NEW.profile_name
                      AND id != NEW.id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'message Chrome profile already bound in scope');
                END
                """
            )
        )
        _migrate_calendar_unique(conn)
        # Keep legacy daily_usage intact. Weekly rows are an additive projection;
        # historical weeks already over the new cap become saturated, never erased.
        conn.execute(
            text(
                """
                INSERT INTO weekly_usage(customer_id, kind, key, week, count, updated_at)
                SELECT customer_id, kind, key,
                       date(day, '-' || ((CAST(strftime('%w', day) AS INTEGER)+6)%7) || ' days'),
                       MIN(2, SUM(count)), CURRENT_TIMESTAMP
                FROM daily_usage
                GROUP BY customer_id, kind, key,
                         date(day, '-' || ((CAST(strftime('%w', day) AS INTEGER)+6)%7) || ' days')
                ON CONFLICT(customer_id, kind, key, week)
                DO UPDATE SET count=excluded.count, updated_at=excluded.updated_at
                """
            )
        )
        conn.execute(
            text(
                "UPDATE paper_slip_reservations SET status='expired', "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE status='reserved' AND julianday(expires_at) <= julianday('now')"
            )
        )
        # Drop legacy hard-cap triggers and rebuild usage tables if needed.
        conn.execute(text("DROP TRIGGER IF EXISTS trg_daily_usage_cap_insert"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_daily_usage_cap_update"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_weekly_usage_cap_insert"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_weekly_usage_cap_update"))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_paper_slip_reservation_cap_insert"))
        _migrate_paper_slip_rolling_diversity(conn)
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260731_paper_slip_rolling_diversity",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260802_message_profiles_history_v1",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260726_closeout",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260730_gsemantic_ops_health_clusters_evidence",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260730_gsemantic_ops_paper_slip_weekly_reservations",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260730_keyword_revision_topic_intents",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260728_strict_embedding_provenance",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260726_g7_notifications_lock",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260726_g7_reach_messages",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260727_gcontent_seo_geo",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260727_business_scope_isolation",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        # 2026-08-03: abolish schedule content reservations; reopen blocked triggers.
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        if "reach_publish_reservations" in tables:
            conn.execute(
                text(
                    "UPDATE reach_publish_reservations SET status='released', "
                    "updated_at=CURRENT_TIMESTAMP WHERE status='reserved'"
                )
            )
        if "reach_publish_triggers" in tables:
            conn.execute(
                text(
                    "UPDATE reach_publish_triggers SET status='pending', "
                    "updated_at=CURRENT_TIMESTAMP WHERE status='blocked_reservation'"
                )
            )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": "20260803_abolish_content_reservations",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # Chrome profile physical migration runs after columns exist (uses ORM).
    try:
        from engine.reach.browser import migrate_legacy_chrome_profiles

        migrate_legacy_chrome_profiles()
    except Exception:
        pass

    session = get_session()
    try:
        seed_name = (settings.active_customer or "").strip() or DEMO_CUSTOMER
        default = get_or_create_customer(
            session,
            seed_name,
            library_root=str(settings.paths.library_root),
            library_roots=[str(p) for p in settings.paths.library_roots],
            output_root=str(settings.paths.output_root),
            profile_json={"industry_pack": "_blank"} if seed_name == DEMO_CUSTOMER else None,
        )
        cid = default.id

        for asset in session.scalars(select(Asset)).all():
            if asset.customer_id is None:
                asset.customer_id = cid
        for job in session.scalars(select(Job)).all():
            if job.customer_id is None:
                snap_name = (job.config_snapshot_json or {}).get("customer_name")
                if snap_name:
                    cust = session.scalar(select(Customer).where(Customer.name == snap_name))
                    job.customer_id = cust.id if cust else cid
                else:
                    job.customer_id = cid
        for row in session.scalars(select(CalendarEntry)).all():
            if row.customer_id is None:
                cust = session.scalar(select(Customer).where(Customer.name == row.customer_name))
                row.customer_id = cust.id if cust else cid
        for row in session.scalars(select(KeywordUsage)).all():
            if row.customer_id is None:
                if row.job_id:
                    job = session.get(Job, row.job_id)
                    row.customer_id = job.customer_id if job and job.customer_id else cid
                else:
                    row.customer_id = cid
        for row in session.scalars(select(ClipletUsage)).all():
            if row.customer_id is None:
                if row.job_id:
                    job = session.get(Job, row.job_id)
                    row.customer_id = job.customer_id if job and job.customer_id else cid
                else:
                    row.customer_id = cid
        session.commit()

        if not (settings.active_customer or "").strip():
            settings.active_customer = seed_name
            from engine.config.settings import save_settings

            save_settings(settings)
    finally:
        session.close()


def log_event(session: Session, job_id: int | None, level: str, message: str, payload: dict | None = None) -> None:
    if job_id is None:
        return
    event_payload = dict(payload or {})
    event_payload["_structured_log"] = True
    session.add(
        JobEvent(
            job_id=job_id,
            level=level,
            message=message,
            payload_json=event_payload,
        )
    )
    if message in {"审片自动通过", "审片一键通过"}:
        return
    try:
        from engine.ops.audit_log import write_log

        job = session.get(Job, job_id)
        details = {
            key: value for key, value in event_payload.items() if key != "_structured_log"
        }
        source_type = "job"
        source_id: str | int = job_id
        if details.get("output_id") not in (None, ""):
            source_type = "render_output"
            source_id = details["output_id"]
        with session.begin_nested():
            write_log(
                session,
                customer_id=job.customer_id if job else None,
                category="production",
                event="job_event",
                message=message,
                level="error" if level == "error" else "warning" if level == "warning" else "info",
                stage=str(event_payload.get("stage") or ""),
                source_type=source_type,
                source_id=source_id,
                correlation_id=f"job:{job_id}",
                account=str(details.get("account") or details.get("chrome_profile") or ""),
                platform=str(details.get("platform") or ""),
                details=details,
            )
    except Exception:
        # JobEvent remains the fallback; logging must never corrupt production.
        pass


def dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
