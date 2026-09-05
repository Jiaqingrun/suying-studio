"""GStab: database integrity / emptiness checks for ops health."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

# Full integrity_check scans every page and blocks writers for a long time on
# large montage.db. Ops polls call this frequently — cache + quick_check.
_CACHE_TTL_SEC = 300.0
_cache: dict[str, Any] = {"path": "", "at": 0.0, "info": None}


def inspect_montage_db(db_path: Path | str, *, force: bool = False) -> dict[str, Any]:
    """Return integrity + row counts. Never raises.

    Uses PRAGMA quick_check by default (and caches for ``_CACHE_TTL_SEC``) so
    frequent /reports/ops polls do not lock the live writer. Pass ``force=True``
    for an explicit full integrity_check (e.g. backup / doctor).
    """
    p = Path(db_path)
    now = time.monotonic()
    cached = _cache.get("info")
    if (
        not force
        and cached is not None
        and _cache.get("path") == str(p)
        and now - float(_cache.get("at") or 0) < _CACHE_TTL_SEC
    ):
        return dict(cached)

    out: dict[str, Any] = {
        "path": str(p),
        "exists": p.is_file(),
        "size_bytes": int(p.stat().st_size) if p.is_file() else 0,
        "integrity": "missing",
        "ok": False,
        "empty": True,
        "counts": {},
        "warnings": [],
        "errors": [],
        "check_mode": "full" if force else "quick",
    }
    if not p.is_file():
        out["errors"].append("montage.db 不存在")
        return out
    if out["size_bytes"] < 8192:
        out["warnings"].append("montage.db 过小，可能为空壳")
    wal = p.with_name(p.name + "-wal")
    if wal.is_file() and wal.stat().st_size > 0 and out["size_bytes"] <= 4096:
        out["errors"].append("疑似空库+残留 WAL，需恢复或重建")
        return out
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            pragma = "PRAGMA integrity_check" if force else "PRAGMA quick_check"
            integrity = str(con.execute(pragma).fetchone()[0])
            out["integrity"] = integrity
            if integrity != "ok":
                out["errors"].append(f"{pragma.split()[-1]}={integrity[:80]}")
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for t in (
                "customers",
                "jobs",
                "assets",
                "cliplets",
                "render_outputs",
                "reach_queue",
            ):
                if t not in tables:
                    out["counts"][t] = None
                    continue
                out["counts"][t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            jobs = out["counts"].get("jobs") or 0
            assets = out["counts"].get("assets") or 0
            outs = out["counts"].get("render_outputs") or 0
            out["empty"] = jobs == 0 and assets == 0 and outs == 0
            if out["empty"]:
                out["warnings"].append("库内无 jobs/assets/成片记录")
            out["ok"] = integrity == "ok" and not out["errors"]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        out["errors"].append(str(e))
        out["ok"] = False
    if out.get("ok"):
        _cache["path"] = str(p)
        _cache["at"] = now
        _cache["info"] = dict(out)
    return out


def db_health_phrase(info: dict[str, Any]) -> str:
    if not info.get("exists"):
        return "数据库缺失"
    if info.get("errors"):
        return str(info["errors"][0])
    if info.get("empty"):
        return "数据库空（无生产记录）"
    if info.get("integrity") != "ok":
        return "数据库完整性异常"
    return "数据库正常"
