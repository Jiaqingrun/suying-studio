#!/usr/bin/env python3
"""PL-05 · FCP工作盘 → Movies 工作区路径审计 / 显式 remap.

默认 **dry-run**：只报告，不改 DB、不拷文件、不改 sidecar。
显式 ``--apply`` 才执行：必要时拷贝到 Movies，再改写权威路径字段。

类别：
  - ``both_exist`` / ``movies_only``：可 remap（改路径）
  - ``old_exists_only``：需先拷贝再 remap（``--apply`` 含拷贝）
  - ``both_missing``：不改路径；报告为归档候选（缺两端文件）

Examples::

    python3 scripts/pl05_fcp_movies_remap.py
    python3 scripts/pl05_fcp_movies_remap.py --json -o /tmp/fcp-remap-dryrun.json
    python3 scripts/pl05_fcp_movies_remap.py --apply --limit 3   # 显式写入
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

FCP_MARKERS = ("/Volumes/FCP工作盘",)
CUSTOMER_ANCHOR = "速影客户"
DEFAULT_MOVIES = Path.home() / "Movies" / "速影工作区"
DEFAULT_DB = Path.home() / "Suying" / "data" / "montage.db"

# Sidecar / sibling suffixes next to the main mp4 stem.
SIBLING_SUFFIXES = (
    ".mp4",
    ".json",
    ".zh.srt",
    ".voice.wav",
    ".publish_pack",
    "_covers",
)


class RowPlan:
    __slots__ = (
        "id",
        "job_id",
        "state",
        "pack_status",
        "old_path",
        "new_path",
        "category",
        "old_exists",
        "new_exists",
        "action",
        "notes",
    )

    def __init__(
        self,
        id: int,
        job_id: int | None,
        state: str,
        pack_status: str,
        old_path: str,
        new_path: str,
        category: str,
        old_exists: bool,
        new_exists: bool,
        action: str,
        notes: str = "",
    ) -> None:
        self.id = id
        self.job_id = job_id
        self.state = state
        self.pack_status = pack_status
        self.old_path = old_path
        self.new_path = new_path
        self.category = category
        self.old_exists = old_exists
        self.new_exists = new_exists
        self.action = action
        self.notes = notes

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


def movies_root(explicit: str | None = None) -> Path:
    return Path(explicit).expanduser() if explicit else DEFAULT_MOVIES


def is_fcp_path(path: str | None) -> bool:
    if not path:
        return False
    return any(marker in path for marker in FCP_MARKERS)


def remap_path(old: str, *, movies: Path) -> str | None:
    """Map FCP absolute path → Movies workspace by anchoring on ``速影客户``."""
    if not is_fcp_path(old):
        return None
    idx = old.find(CUSTOMER_ANCHOR)
    if idx < 0:
        # Fallback: keep basename under Movies root (rare; flagged in notes).
        return str(movies / Path(old).name)
    return str(movies / old[idx:])


def rewrite_text(value: str, *, movies: Path) -> str:
    out = value
    for marker in FCP_MARKERS:
        # Prefer Media-stripped rewrite via remap_path when possible.
        if marker in out:
            mapped = remap_path(out, movies=movies)
            if mapped:
                return mapped
            out = out.replace(marker + "/Media", str(movies))
            out = out.replace(marker, str(movies))
    return out


def classify(old_exists: bool, new_exists: bool) -> str:
    if old_exists and new_exists:
        return "both_exist"
    if old_exists and not new_exists:
        return "old_exists_only"
    if not old_exists and new_exists:
        return "movies_only"
    return "both_missing"


def planned_action(category: str) -> str:
    if category == "both_missing":
        return "skip_archive_candidate"
    if category == "old_exists_only":
        return "copy_then_remap"
    return "remap_paths"


def _path_exists(path: str) -> bool:
    p = Path(path)
    return p.is_file() or p.is_dir()


def collect_plans(
    conn: sqlite3.Connection,
    *,
    movies: Path,
    limit: int | None = None,
) -> list[RowPlan]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, job_id, state, pack_status, output_path
        FROM render_outputs
        WHERE output_path LIKE '%FCP工作盘%'
        ORDER BY id
        """
    ).fetchall()
    plans: list[RowPlan] = []
    for row in rows:
        old = str(row["output_path"] or "")
        new = remap_path(old, movies=movies) or ""
        old_ex = Path(old).is_file()
        new_ex = Path(new).is_file() if new else False
        cat = classify(old_ex, new_ex)
        note = ""
        if CUSTOMER_ANCHOR not in old:
            note = "no_customer_anchor; basename fallback"
        plans.append(
            RowPlan(
                id=int(row["id"]),
                job_id=row["job_id"],
                state=str(row["state"] or ""),
                pack_status=str(row["pack_status"] or ""),
                old_path=old,
                new_path=new,
                category=cat,
                old_exists=old_ex,
                new_exists=new_ex,
                action=planned_action(cat),
                notes=note,
            )
        )
        if limit is not None and len(plans) >= limit:
            break
    return plans


def summarize(plans: list[RowPlan]) -> dict[str, Any]:
    cats: dict[str, int] = {}
    actions: dict[str, int] = {}
    for p in plans:
        cats[p.category] = cats.get(p.category, 0) + 1
        actions[p.action] = actions.get(p.action, 0) + 1
    return {
        "total": len(plans),
        "by_category": cats,
        "by_action": actions,
        "remap_candidates": sum(
            1 for p in plans if p.action in {"remap_paths", "copy_then_remap"}
        ),
        "archive_candidates": sum(
            1 for p in plans if p.action == "skip_archive_candidate"
        ),
    }


def _copy_tree_or_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            return
        shutil.copytree(src, dst, dirs_exist_ok=False)
    else:
        if dst.exists():
            return
        shutil.copy2(src, dst)


def copy_output_bundle(old_mp4: Path, new_mp4: Path) -> list[str]:
    """Copy mp4 + known siblings from FCP to Movies. Returns copied relative labels."""
    copied: list[str] = []
    stem = old_mp4.stem
    parent = old_mp4.parent
    new_parent = new_mp4.parent
    new_parent.mkdir(parents=True, exist_ok=True)
    for suffix in SIBLING_SUFFIXES:
        src = parent / f"{stem}{suffix}"
        if not src.exists():
            continue
        dst = new_parent / f"{stem}{suffix}"
        _copy_tree_or_file(src, dst)
        copied.append(suffix)
    return copied


def _rewrite_json_paths(obj: Any, *, movies: Path) -> Any:
    if isinstance(obj, str):
        return rewrite_text(obj, movies=movies) if is_fcp_path(obj) else obj
    if isinstance(obj, list):
        return [_rewrite_json_paths(x, movies=movies) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_json_paths(v, movies=movies) for k, v in obj.items()}
    return obj


def apply_row(
    conn: sqlite3.Connection,
    plan: RowPlan,
    *,
    movies: Path,
    do_copy: bool,
) -> dict[str, Any]:
    if plan.action == "skip_archive_candidate":
        return {
            "id": plan.id,
            "status": "skipped",
            "reason": "both_missing",
        }

    copied: list[str] = []
    if plan.action == "copy_then_remap" and do_copy:
        copied = copy_output_bundle(Path(plan.old_path), Path(plan.new_path))
        if not Path(plan.new_path).is_file():
            return {
                "id": plan.id,
                "status": "error",
                "reason": "copy_failed_missing_new_mp4",
                "copied": copied,
            }

    row = conn.execute(
        "SELECT output_path, sidecar_path, pack_dir, qc_json FROM render_outputs WHERE id=?",
        (plan.id,),
    ).fetchone()
    if row is None:
        return {"id": plan.id, "status": "error", "reason": "row_gone"}

    new_output = rewrite_text(row[0], movies=movies) if row[0] else row[0]
    new_sidecar = rewrite_text(row[1], movies=movies) if row[1] else row[1]
    new_pack = rewrite_text(row[2], movies=movies) if row[2] else row[2]
    qc_raw = row[3]
    try:
        qc_obj = json.loads(qc_raw) if isinstance(qc_raw, str) and qc_raw else (qc_raw or {})
    except json.JSONDecodeError:
        qc_obj = {}
    if not isinstance(qc_obj, dict):
        qc_obj = {}
    new_qc = _rewrite_json_paths(qc_obj, movies=movies)

    conn.execute(
        """
        UPDATE render_outputs
        SET output_path=?, sidecar_path=?, pack_dir=?, qc_json=?
        WHERE id=?
        """,
        (
            new_output,
            new_sidecar,
            new_pack,
            json.dumps(new_qc, ensure_ascii=False),
            plan.id,
        ),
    )

    # Rewrite on-disk sidecar JSON if present at new location (or old, then copy).
    sidecar_targets: list[Path] = []
    if new_sidecar:
        sidecar_targets.append(Path(new_sidecar))
    if row[1]:
        sidecar_targets.append(Path(row[1]))
    seen: set[str] = set()
    for sp in sidecar_targets:
        key = str(sp)
        if key in seen or not sp.is_file():
            continue
        seen.add(key)
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data2 = _rewrite_json_paths(data, movies=movies)
        # Prefer writing to Movies path.
        out_sp = Path(new_sidecar) if new_sidecar else sp
        out_sp.parent.mkdir(parents=True, exist_ok=True)
        out_sp.write_text(
            json.dumps(data2, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "id": plan.id,
        "status": "applied",
        "new_output": new_output,
        "copied": copied,
    }


def build_report(
    plans: list[RowPlan],
    *,
    dry_run: bool,
    movies: Path,
    db_path: Path,
    applied: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "pl05_fcp_movies_remap/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "db": str(db_path),
        "movies_root": str(movies),
        "fcp_volume_mounted": Path("/Volumes/FCP工作盘").exists(),
        "summary": summarize(plans),
        "rows": [p.as_dict() for p in plans],
        "applied": applied or [],
        "policy": {
            "default": "dry-run report only; no silent DB/path mutation",
            "apply_flag": "--apply required for copy + path rewrite",
            "both_missing": "never rewrite; treat as archive candidate",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PL-05 FCP→Movies path audit / explicit remap (default dry-run)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="显式执行拷贝+路径改写（默认只报告）",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="与 --apply 联用：只改路径、不拷文件（危险；仅当 Movies 已有文件）",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--movies", type=str, default=None, help="Movies 工作区根")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 条 FCP 行")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("-o", "--output", type=Path, help="写入报告 JSON 路径")
    args = parser.parse_args(argv)

    db_path: Path = args.db.expanduser()
    if not db_path.is_file():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 2

    movies = movies_root(args.movies)
    dry_run = not args.apply

    # Read plans with a read connection first.
    uri = f"file:{db_path}?mode=ro" if dry_run else f"file:{db_path}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        plans = collect_plans(conn, movies=movies, limit=args.limit)
        applied: list[dict[str, Any]] = []
        if not dry_run:
            # Reopen writable (uri without mode=ro).
            conn.close()
            conn = sqlite3.connect(str(db_path))
            do_copy = not args.no_copy
            for plan in plans:
                if plan.action == "skip_archive_candidate":
                    applied.append(apply_row(conn, plan, movies=movies, do_copy=False))
                    continue
                if plan.action == "copy_then_remap" and args.no_copy and not plan.new_exists:
                    applied.append(
                        {
                            "id": plan.id,
                            "status": "skipped",
                            "reason": "needs_copy_but_--no-copy",
                        }
                    )
                    continue
                applied.append(
                    apply_row(conn, plan, movies=movies, do_copy=do_copy)
                )
            conn.commit()
            # Keep pre-apply plans in the report; annotate post-apply new_exists.
            for plan in plans:
                if plan.new_path:
                    plan.new_exists = Path(plan.new_path).is_file()

        report = build_report(
            plans,
            dry_run=dry_run,
            movies=movies,
            db_path=db_path,
            applied=applied,
        )
    finally:
        conn.close()

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")

    if args.json:
        print(text)
    else:
        mode = "DRY-RUN" if dry_run else "APPLY"
        print(f"==> PL-05 FCP→Movies remap [{mode}]")
        print(f"db={db_path}")
        print(f"movies={movies}")
        print(f"fcp_mounted={report['fcp_volume_mounted']}")
        print("summary:", json.dumps(report["summary"], ensure_ascii=False))
        for p in plans[:20]:
            print(
                f"  #{p.id} job={p.job_id} {p.category}/{p.action} "
                f"old_ex={p.old_exists} new_ex={p.new_exists}"
            )
            print(f"    {p.old_path}")
            print(f" -> {p.new_path}")
        if len(plans) > 20:
            print(f"  … +{len(plans) - 20} more")
        if args.output:
            print(f"report: {args.output}")
        if dry_run:
            print("hint: re-run with --apply after reviewing the report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
