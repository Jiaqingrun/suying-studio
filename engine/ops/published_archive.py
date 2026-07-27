"""Move published renders out of ready/ so they are not re-queued."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.catalog.db import RenderOutput
from engine.ops.publish_trail import read_sidecar, write_sidecar


def _day_stamp(iso: str | None = None) -> str:
    if iso and len(iso) >= 10:
        return iso[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def published_root(output_root: str | Path) -> Path:
    return Path(output_root) / "published"


def archive_published_output(
    out: RenderOutput,
    *,
    output_root: str | Path,
    platform: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Move mp4 + sidecar (+ covers) from ready/ into published/YYYY-MM-DD/.

    Updates RenderOutput.output_path / sidecar_path / state=published.
    Idempotent if already under published/.
    """
    src = Path(out.output_path) if out.output_path else None
    result: dict[str, Any] = {
        "ok": False,
        "moved": False,
        "output_id": out.id,
        "from": str(src) if src else None,
        "to": None,
        "state": out.state,
    }
    if not src or not src.is_file():
        result["error"] = "成片文件不存在"
        return result

    # Already archived
    try:
        if "published" in src.parts:
            out.state = "published"
            result["ok"] = True
            result["to"] = str(src)
            result["state"] = "published"
            return result
    except Exception:
        pass

    day = _day_stamp()
    dest_dir = published_root(output_root) / day
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        dest = dest_dir / f"{src.stem}_{out.id}{src.suffix}"

    shutil.move(str(src), str(dest))
    result["moved"] = True
    result["to"] = str(dest)

    new_side: Path | None = None
    if out.sidecar_path:
        side_src = Path(out.sidecar_path)
        if side_src.is_file():
            new_side = dest.with_suffix(".json")
            shutil.move(str(side_src), str(new_side))
        # move companion covers next to video if present
        for pattern in (f"{src.stem}_covers", f"{src.stem}.covers"):
            covers = src.parent / pattern
            if covers.is_dir():
                dest_covers = dest_dir / covers.name
                if dest_covers.exists():
                    shutil.rmtree(dest_covers, ignore_errors=True)
                shutil.move(str(covers), str(dest_covers))

    # stamp sidecar meta
    side_path = new_side or dest.with_suffix(".json")
    data = read_sidecar(side_path) if side_path.is_file() else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["archived_published"] = True
    meta["archived_at"] = datetime.now(timezone.utc).isoformat()
    meta["archived_platform"] = platform or ""
    if note:
        meta["archive_note"] = note
    data["meta"] = meta
    try:
        write_sidecar(side_path, data)
    except OSError:
        pass

    out.output_path = str(dest)
    if new_side:
        out.sidecar_path = str(new_side)
    elif side_path.is_file():
        out.sidecar_path = str(side_path)
    out.state = "published"

    result["ok"] = True
    result["state"] = "published"
    result["sidecar"] = out.sidecar_path
    return result


def is_archived_path(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return "published" in Path(path).parts
    except Exception:
        return False


def publish_stats(output_root: str | Path) -> dict[str, Any]:
    """Filesystem counts under ready/ and published/."""
    root = Path(output_root)
    ready_dir = root / "ready"
    pub_dir = root / "published"
    ready_n = len(list(ready_dir.rglob("*.mp4"))) if ready_dir.is_dir() else 0
    pub_n = len(list(pub_dir.rglob("*.mp4"))) if pub_dir.is_dir() else 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_pub = 0
    today_ready = 0
    if (pub_dir / today).is_dir():
        today_pub = len(list((pub_dir / today).glob("*.mp4")))
    if (ready_dir / today).is_dir():
        today_ready = len(list((ready_dir / today).glob("*.mp4")))
    return {
        "ready_files": ready_n,
        "published_files": pub_n,
        "today_published_files": today_pub,
        "today_ready_files": today_ready,
        "published_root": str(pub_dir),
    }
