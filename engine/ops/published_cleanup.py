"""Safe cleanup for binaries of fully published videos."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, PublicationGroup, RenderOutput
from engine.ops.published_archive import published_root

POLICY_PATH = Path.home() / "Suying" / "data" / "published-cleanup-policy.json"
DEFAULT_POLICY = {
    "enabled": False,
    "retention_days": 30,
    "trash_grace_days": 7,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy() -> dict[str, Any]:
    try:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    policy = {**DEFAULT_POLICY, **(raw if isinstance(raw, dict) else {})}
    policy["enabled"] = bool(policy["enabled"])
    policy["retention_days"] = max(1, min(3650, int(policy["retention_days"])))
    policy["trash_grace_days"] = max(1, min(90, int(policy["trash_grace_days"])))
    return policy


def save_policy(patch: dict[str, Any]) -> dict[str, Any]:
    policy = {**load_policy(), **patch}
    policy["enabled"] = bool(policy.get("enabled"))
    policy["retention_days"] = max(1, min(3650, int(policy.get("retention_days", 30))))
    policy["trash_grace_days"] = max(1, min(90, int(policy.get("trash_grace_days", 7))))
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = POLICY_PATH.with_name(f".{POLICY_PATH.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, POLICY_PATH)
    return policy


def _eligible_rows(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
    older_than_days: int | None = None,
    output_ids: list[int] | None = None,
) -> list[tuple[RenderOutput, PublicationGroup]]:
    root = published_root(output_root).expanduser().resolve()
    stmt = (
        select(RenderOutput, PublicationGroup)
        .join(Job, RenderOutput.job_id == Job.id)
        .join(PublicationGroup, PublicationGroup.output_id == RenderOutput.id)
        .where(
            Job.customer_id == customer_id,
            PublicationGroup.customer_id == customer_id,
            PublicationGroup.status == "retired_published",
            RenderOutput.state == "retired_published",
        )
        .order_by(PublicationGroup.retired_at.asc(), RenderOutput.id.asc())
    )
    if output_ids:
        stmt = stmt.where(RenderOutput.id.in_(output_ids))
    if older_than_days is not None:
        cutoff = _now() - timedelta(days=max(1, int(older_than_days)))
        stmt = stmt.where(PublicationGroup.retired_at.is_not(None))
        stmt = stmt.where(PublicationGroup.retired_at <= cutoff)
    rows: list[tuple[RenderOutput, PublicationGroup]] = []
    for output, group in session.execute(stmt).all():
        meta = dict(output.qc_json or {})
        if meta.get("published_video_deleted_at"):
            continue
        path = Path(output.output_path or "").expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if (
            not path.is_file()
            or path.is_symlink()
            or not resolved.is_relative_to(root)
            or path.name != "output.mp4"
        ):
            continue
        rows.append((output, group))
    return rows


def preview(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
    older_than_days: int | None = None,
    output_ids: list[int] | None = None,
) -> dict[str, Any]:
    rows = _eligible_rows(
        session,
        customer_id=customer_id,
        output_root=output_root,
        older_than_days=older_than_days,
        output_ids=output_ids,
    )
    items = []
    for output, group in rows:
        path = Path(output.output_path)
        items.append(
            {
                "output_id": output.id,
                "display_no": output.display_no,
                "path": str(path),
                "size": path.stat().st_size,
                "retired_at": group.retired_at.isoformat() if group.retired_at else None,
            }
        )
    return {
        "ok": True,
        "count": len(items),
        "bytes": sum(int(item["size"]) for item in items),
        "items": items,
    }


def cleanup(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
    older_than_days: int | None = None,
    output_ids: list[int] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    rows = _eligible_rows(
        session,
        customer_id=customer_id,
        output_root=output_root,
        older_than_days=older_than_days,
        output_ids=output_ids,
    )
    root = Path(output_root).expanduser().resolve()
    trash = root / "retired" / ".trash" / "published-videos" / _now().strftime("%Y-%m-%d")
    trash.mkdir(parents=True, exist_ok=True)
    removed: list[dict[str, Any]] = []
    for output, group in rows:
        source = Path(output.output_path).resolve()
        size = source.stat().st_size
        digest = _sha256(source)
        target = trash / f"output-{output.id}-{uuid.uuid4().hex[:8]}.mp4"
        os.replace(source, target)
        qc = dict(output.qc_json or {})
        qc.update(
            {
                "published_video_deleted_at": _now().isoformat(),
                "published_video_delete_actor": actor,
                "published_video_original_path": str(source),
                "published_video_trash_path": str(target),
                "published_video_sha256": digest,
                "published_video_size": size,
                "published_video_intentional": True,
            }
        )
        output.qc_json = qc
        output.output_path = str(source)
        removed.append(
            {
                "output_id": output.id,
                "display_no": output.display_no,
                "size": size,
                "sha256": digest,
                "trash_path": str(target),
                "retired_at": group.retired_at.isoformat() if group.retired_at else None,
            }
        )
        _write_audit(
            session,
            customer_id=customer_id,
            output=output,
            event="published_video_cleaned",
            message=f"已清理发布视频 #{int(output.display_no or 0):03d}",
            details=removed[-1],
        )
    session.commit()
    return {
        "ok": True,
        "count": len(removed),
        "bytes": sum(item["size"] for item in removed),
        "items": removed,
    }


def maybe_run_scheduled_cleanup(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
) -> dict[str, Any] | None:
    policy = load_policy()
    if not policy["enabled"]:
        return None
    today = datetime.now().astimezone().date().isoformat()
    if str(policy.get("last_run_day") or "") == today:
        return None
    result = cleanup(
        session,
        customer_id=customer_id,
        output_root=output_root,
        older_than_days=int(policy["retention_days"]),
        actor="scheduler",
    )
    save_policy({"last_run_day": today})
    purge_trash(output_root, int(policy["trash_grace_days"]))
    return result


def purge_trash(output_root: str | Path, grace_days: int | None = None) -> dict[str, Any]:
    grace = max(1, int(grace_days or load_policy()["trash_grace_days"]))
    trash = Path(output_root).expanduser().resolve() / "retired" / ".trash" / "published-videos"
    cutoff = _now() - timedelta(days=grace)
    removed = 0
    freed = 0
    if not trash.is_dir():
        return {"ok": True, "removed": 0, "freed_bytes": 0}
    for path in trash.rglob("*.mp4"):
        if path.is_symlink() or not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified > cutoff:
            continue
        freed += path.stat().st_size
        path.unlink()
        removed += 1
    for directory in sorted(
        (path for path in trash.rglob("*") if path.is_dir()), reverse=True
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return {"ok": True, "removed": removed, "freed_bytes": freed}


def _write_audit(
    session: Session,
    *,
    customer_id: int,
    output: RenderOutput,
    event: str,
    message: str,
    details: dict[str, Any],
) -> None:
    from engine.ops.audit_log import write_log

    write_log(
        session,
        customer_id=customer_id,
        category="cleanup",
        event=event,
        message=message,
        stage="completed",
        source_type="render_output",
        source_id=output.id,
        correlation_id=f"output:{output.id}",
        details={"output_id": output.id, **details},
    )

