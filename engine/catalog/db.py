from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
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
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
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
    seed: Mapped[int] = mapped_column(Integer)
    sidecar_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    copy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    log_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
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
    score: Mapped[float] = mapped_column(Float, default=1.0)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    render_output_id: Mapped[int] = mapped_column(ForeignKey("render_outputs.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/approved/rejected
    note: Mapped[str] = mapped_column(Text, default="")
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


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def db_path(settings: AppSettings | None = None) -> Path:
    settings = settings or load_settings()
    settings.paths.data_root.mkdir(parents=True, exist_ok=True)
    return settings.paths.data_root / "montage.db"


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
            cursor.execute("PRAGMA busy_timeout=60000")
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


def _run_sqlite_migrations(settings: AppSettings | None = None) -> None:
    from sqlalchemy import text

    from engine.catalog.customer_scope import DEMO_CUSTOMER, get_or_create_customer

    settings = settings or load_settings()
    engine = get_engine(settings)

    with engine.begin() as conn:
        _add_column_if_missing(conn, "assets", "proxy_path", "TEXT")
        _add_column_if_missing(conn, "customers", "library_root", "TEXT")
        _add_column_if_missing(conn, "customers", "library_roots", "JSON")
        _add_column_if_missing(conn, "customers", "output_root", "TEXT")
        _add_column_if_missing(conn, "customers", "keyword_pack_path", "TEXT")
        _add_column_if_missing(conn, "customers", "active", "BOOLEAN DEFAULT 1")
        _add_column_if_missing(conn, "assets", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "jobs", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "calendar_entries", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "keyword_usage", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "cliplet_usage", "customer_id", "INTEGER")
        _add_column_if_missing(conn, "cliplets", "theme", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "theme_score", "FLOAT")
        _add_column_if_missing(conn, "cliplets", "scene", "VARCHAR(64)")
        _add_column_if_missing(conn, "cliplets", "objects_json", "JSON")
        _add_column_if_missing(conn, "cliplets", "actions_json", "JSON")
        _migrate_calendar_unique(conn)

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
    session.add(
        JobEvent(
            job_id=job_id,
            level=level,
            message=message,
            payload_json=payload or {},
        )
    )


def dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
