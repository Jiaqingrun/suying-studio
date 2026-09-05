"""Read-only, customer-scoped reporting facts.

Business metrics come only from SQLite.  Filesystem counts are exposed solely
as reconciliation observations and never replace a database value.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Job,
    PublicationGroup,
    PublicationTarget,
    RenderOutput,
    ReviewItem,
)

BUSINESS_TIMEZONE = "Asia/Shanghai"
_SHANGHAI = ZoneInfo(BUSINESS_TIMEZONE)
_ACTIVE_PUBLICATION_STATUSES = {
    "open",
    "isolated",
    "retired_pending_archive",
    "archive_failed",
}
_RETIRED_PUBLICATION_STATUSES = {
    "retired_pending_archive",
    "archive_failed",
    "retired_published",
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _business_day_bounds(now: datetime | None = None) -> tuple[date, datetime, datetime]:
    instant = _as_utc(now or datetime.now(timezone.utc))
    local_day = instant.astimezone(_SHANGHAI).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=_SHANGHAI)
    end_local = datetime.combine(local_day, time.max, tzinfo=_SHANGHAI)
    return local_day, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _is_today(value: datetime | None, start: datetime, end: datetime) -> bool:
    if value is None:
        return False
    instant = _as_utc(value)
    return start <= instant <= end


def _filesystem_observation(output_root: str | Path | None, business_day: date) -> dict[str, Any]:
    root = Path(output_root) if output_root else None
    ready_dir = root / "ready" if root else None
    published_dir = root / "retired" / "published" if root else None

    def count_mp4(path: Path | None, *, recursive: bool = True) -> int | None:
        if path is None or not path.is_dir():
            return None
        iterator = path.rglob("*.mp4") if recursive else path.glob("*.mp4")
        return sum(1 for _ in iterator)

    return {
        "ready_files": count_mp4(ready_dir),
        "published_files": count_mp4(published_dir),
        "ready_today_files": count_mp4(ready_dir / business_day.isoformat(), recursive=False)
        if ready_dir
        else None,
        "published_today_files": count_mp4(
            published_dir / business_day.isoformat(), recursive=True
        )
        if published_dir
        else None,
    }


def build_report_snapshot(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one immutable-in-effect report snapshot without flushing or committing."""
    generated = _as_utc(now or datetime.now(timezone.utc))
    business_day, day_start, day_end = _business_day_bounds(generated)
    outputs = list(
        session.scalars(
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(Job.customer_id == customer_id)
            .order_by(RenderOutput.id)
        ).all()
    )
    output_ids = {row.id for row in outputs}
    reviews = (
        list(
            session.scalars(
                select(ReviewItem).where(
                    ReviewItem.render_output_id.in_(output_ids),
                    ReviewItem.is_current.is_(True),
                )
            ).all()
        )
        if output_ids
        else []
    )
    groups = list(
        session.scalars(
            select(PublicationGroup).where(PublicationGroup.customer_id == customer_id)
        ).all()
    )
    group_ids = {row.id for row in groups}
    targets = (
        list(
            session.scalars(
                select(PublicationTarget).where(
                    PublicationTarget.customer_id == customer_id,
                    PublicationTarget.group_id.in_(group_ids),
                )
            ).all()
        )
        if group_ids
        else []
    )

    by_state: dict[str, int] = {}
    for output in outputs:
        by_state[output.state] = by_state.get(output.state, 0) + 1

    active_output_ids = {
        group.output_id for group in groups if group.status in _ACTIVE_PUBLICATION_STATUSES
    }
    passed_outputs = [
        output
        for output in outputs
        if isinstance(output.qc_json, dict)
        and isinstance(output.qc_json.get("ready_gate"), dict)
        and output.qc_json["ready_gate"].get("ok") is True
    ]
    production_passed = len(passed_outputs)
    passed_ids = {output.id for output in passed_outputs}
    failed = sum(
        1
        for output in outputs
        if output.id not in passed_ids and output.state in {"failed", "rejected"}
    )
    quality_denominator = production_passed + failed
    ready_available = sum(
        1
        for output in outputs
        if output.state == "ready" and output.id not in active_output_ids
    )

    review_by_status = {"approved": 0, "rejected": 0, "uncertain": 0}
    auto_approved = 0
    auto_rejected = 0
    manual_decided = 0
    for review in reviews:
        if review.status in review_by_status:
            review_by_status[review.status] += 1
        automatic = review.decision_source != "manual"
        if automatic and review.status == "approved":
            auto_approved += 1
        elif automatic and review.status == "rejected":
            auto_rejected += 1
        elif not automatic and review.status in {"approved", "rejected"}:
            manual_decided += 1

    published_group_ids = {
        target.group_id for target in targets if target.status == "published"
    }
    published_today_group_ids = {
        target.group_id
        for target in targets
        if target.status == "published"
        and _is_today(target.published_at, day_start, day_end)
    }
    retired_groups = [
        group for group in groups if group.status in _RETIRED_PUBLICATION_STATUSES
    ]
    observation = _filesystem_observation(output_root, business_day)
    # The ready tree also contains burned intermediates and publish_pack/video.mp4;
    # those are not additional business outputs. Reconcile canonical DB output
    # paths only, while retaining the raw recursive count for diagnostics.
    observation["ready_db_paths_existing"] = sum(
        1
        for output in outputs
        if output.state == "ready"
        and output.id not in active_output_ids
        and bool(output.output_path)
        and Path(output.output_path).is_file()
    )
    observation["published_intentionally_deleted"] = sum(
        1
        for output in outputs
        if isinstance(output.qc_json, dict)
        and bool(output.qc_json.get("published_video_intentional"))
    )
    observation["published_db_paths_existing"] = sum(
        1
        for output in outputs
        if output.state == "retired_published"
        and bool(output.output_path)
        and Path(output.output_path).is_file()
    )
    reconciliation = {
        "filesystem": observation,
        "differences": {
            "ready_available_minus_files": (
                ready_available - observation["ready_db_paths_existing"]
                if observation["ready_db_paths_existing"] is not None
                else None
            ),
            "retired_minus_files": (
                len(retired_groups)
                - observation["published_db_paths_existing"]
                - observation["published_intentionally_deleted"]
            ),
        },
    }
    reconciliation["in_sync"] = all(
        value in (None, 0) for value in reconciliation["differences"].values()
    )

    return {
        "generated_at": generated.isoformat(),
        "timezone": BUSINESS_TIMEZONE,
        "business_date": business_day.isoformat(),
        "source": "database",
        "customer_id": customer_id,
        "outputs": by_state,
        "total_outputs": len(outputs),
        "ready_available": ready_available,
        "production_passed": production_passed,
        "failed": failed,
        "quality_pass_rate": (
            round(production_passed / quality_denominator, 4)
            if quality_denominator
            else None
        ),
        "failure_rate": (
            round(failed / quality_denominator, 4) if quality_denominator else None
        ),
        "auto_approved": auto_approved,
        "auto_rejected": auto_rejected,
        "uncertain_open": review_by_status["uncertain"],
        "manual_decided": manual_decided,
        "reviews": {key: value for key, value in review_by_status.items() if value},
        "published": len(published_group_ids),
        "retired": len(retired_groups),
        "production_passed_today": sum(
            1 for output in passed_outputs if _is_today(output.created_at, day_start, day_end)
        ),
        "published_today": len(published_today_group_ids),
        "reconciliation": reconciliation,
    }
