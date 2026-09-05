"""Move published renders out of ready/ so they are not re-queued."""

from __future__ import annotations

import hashlib
import json
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
    return Path(output_root) / "retired" / "published"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _move_file_resume(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        if dest.exists():
            if _sha256(src) != _sha256(dest):
                raise OSError(f"归档目标冲突: {dest}")
            src.unlink()
        else:
            shutil.move(str(src), str(dest))
    elif not dest.is_file():
        raise OSError(f"待归档文件不存在: {src}")


def _move_tree_resume(src: Path, dest: Path) -> None:
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.rglob("*")):
            if child.is_file():
                _move_file_resume(child, dest / child.relative_to(src))
        for child in sorted(src.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        src.rmdir()
    elif not dest.is_dir():
        raise OSError(f"待归档目录不存在: {src}")


def _rewrite_paths(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_paths(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def archive_published_output(
    out: RenderOutput,
    *,
    output_root: str | Path,
    platform: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Move every output asset into retired/published/YYYY-MM-DD/output-<id>/.

    The move is restartable. A SHA256 manifest is written only after all assets
    are present; DB paths are changed only after that complete filesystem step.
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
    day = _day_stamp()
    archive_base = published_root(output_root)
    existing = sorted(archive_base.glob(f"*/output-{out.id}/output.mp4"))
    if existing:
        dest_dir = existing[0].parent
    else:
        dest_dir = archive_base / day / f"output-{out.id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "output.mp4"
    if src is None:
        result["error"] = "成片路径为空"
        return result
    original_video = str(src)
    _move_file_resume(src, dest)
    result["moved"] = src != dest
    result["to"] = str(dest)

    new_side: Path | None = None
    if out.sidecar_path:
        side_src = Path(out.sidecar_path)
        new_side = dest_dir / "sidecar.json"
        _move_file_resume(side_src, new_side)

    original_pack = str(out.pack_dir or "")
    new_pack: Path | None = None
    if out.pack_dir:
        new_pack = dest_dir / "publish_pack"
        _move_tree_resume(Path(out.pack_dir), new_pack)

    # Move output-specific cover folders and standalone subtitle/voice files.
    sibling_root = src.parent
    for name in (f"{src.stem}_covers", f"{src.stem}.covers"):
        candidate = sibling_root / name
        if candidate.is_dir() or (dest_dir / "covers" / name).is_dir():
            _move_tree_resume(candidate, dest_dir / "covers" / name)
    media_suffixes = {".srt", ".vtt", ".wav", ".mp3", ".m4a", ".aac", ".flac"}
    if sibling_root.is_dir():
        for candidate in sibling_root.glob(f"{src.stem}*"):
            if candidate.is_file() and candidate.suffix.lower() in media_suffixes:
                _move_file_resume(candidate, dest_dir / "companions" / candidate.name)

    # stamp sidecar meta
    side_path = new_side or dest_dir / "sidecar.json"
    data = read_sidecar(side_path) if side_path.is_file() else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta["archived_published"] = True
    meta["archived_at"] = datetime.now(timezone.utc).isoformat()
    meta["archived_platform"] = platform or ""
    if note:
        meta["archive_note"] = note
    replacements = {original_video: str(dest)}
    if original_pack and new_pack:
        replacements[original_pack] = str(new_pack)
    if out.sidecar_path:
        replacements[str(out.sidecar_path)] = str(side_path)
    data = _rewrite_paths(data, replacements)
    data["meta"] = meta
    write_sidecar(side_path, data)

    files = sorted(
        path for path in dest_dir.rglob("*") if path.is_file() and path.name != "SHA256.json"
    )
    manifest = {
        "schema": "suying.retired-published.v1",
        "output_id": out.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [
            {
                "path": str(path.relative_to(dest_dir)),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in files
        ],
    }
    manifest_path = dest_dir / "SHA256.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    out.output_path = str(dest)
    if side_path.is_file():
        out.sidecar_path = str(side_path)
    if new_pack:
        out.pack_dir = str(new_pack)
    out.state = "retired_published"

    result["ok"] = True
    result["state"] = "retired_published"
    result["sidecar"] = out.sidecar_path
    result["pack_dir"] = out.pack_dir
    result["manifest"] = str(manifest_path)
    return result


def is_archived_path(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        parts = Path(path).parts
        return "retired" in parts and "published" in parts
    except Exception:
        return False


def publish_stats(output_root: str | Path) -> dict[str, Any]:
    """Filesystem counts under ready/ and published/."""
    root = Path(output_root)
    ready_dir = root / "ready"
    pub_dir = published_root(root)
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
