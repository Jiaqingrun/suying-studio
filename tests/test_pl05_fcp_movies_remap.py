"""Unit tests for PL-05 FCP→Movies remap helpers (no live DB writes)."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pl05_fcp_movies_remap",
    ROOT / "scripts" / "pl05_fcp_movies_remap.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def test_remap_path_anchors_on_customer():
    movies = Path("/Users/qr/Movies/速影工作区")
    old = (
        "/Volumes/FCP工作盘/Media/速影客户/北京始峰伟业/02-成片/ready/"
        "2026-08-16/montage_411_1.mp4"
    )
    new = mod.remap_path(old, movies=movies)
    assert new == (
        "/Users/qr/Movies/速影工作区/速影客户/北京始峰伟业/02-成片/ready/"
        "2026-08-16/montage_411_1.mp4"
    )


def test_classify_and_actions():
    assert mod.classify(True, False) == "old_exists_only"
    assert mod.planned_action("old_exists_only") == "copy_then_remap"
    assert mod.planned_action("both_missing") == "skip_archive_candidate"
    assert mod.planned_action("movies_only") == "remap_paths"


def test_summarize_and_report_policy(tmp_path: Path):
    movies = tmp_path / "Movies" / "速影工作区"
    db = tmp_path / "t.db"
    db.write_text("")
    plans = [
        mod.RowPlan(
            id=1,
            job_id=1,
            state="ready",
            pack_status="ready",
            old_path="/Volumes/FCP工作盘/Media/速影客户/A/y.mp4",
            new_path=str(movies / "速影客户/A/y.mp4"),
            category="old_exists_only",
            old_exists=True,
            new_exists=False,
            action="copy_then_remap",
        ),
        mod.RowPlan(
            id=2,
            job_id=2,
            state="failed",
            pack_status="pending",
            old_path="/Volumes/FCP工作盘/Media/速影客户/A/z.mp4",
            new_path=str(movies / "速影客户/A/z.mp4"),
            category="both_missing",
            old_exists=False,
            new_exists=False,
            action="skip_archive_candidate",
        ),
    ]
    summary = mod.summarize(plans)
    assert summary["total"] == 2
    assert summary["archive_candidates"] == 1
    assert summary["remap_candidates"] == 1

    report = mod.build_report(
        plans, dry_run=True, movies=movies, db_path=db
    )
    assert report["dry_run"] is True
    assert report["policy"]["apply_flag"].startswith("--apply")


def test_rewrite_json_paths_nested():
    movies = Path("/Users/x/Movies/速影工作区")
    payload = {
        "covers": [
            "/Volumes/FCP工作盘/Media/速影客户/A/_covers/cover_1.jpg",
            {"path": "/Volumes/FCP工作盘/Media/速影客户/A/_covers/cover_2.jpg"},
        ]
    }
    out = mod._rewrite_json_paths(payload, movies=movies)
    assert out["covers"][0].startswith(str(movies))
    assert "FCP" not in out["covers"][0]
    assert "FCP" not in out["covers"][1]["path"]


def test_cli_dry_run_no_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "montage.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE render_outputs (
          id INTEGER PRIMARY KEY,
          job_id INTEGER,
          state TEXT,
          pack_status TEXT,
          output_path TEXT,
          sidecar_path TEXT,
          pack_dir TEXT,
          qc_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO render_outputs VALUES (9,1,'ready','ready',?,?,?,?)",
        (
            "/Volumes/FCP工作盘/Media/速影客户/A/02-成片/ready/a.mp4",
            "/Volumes/FCP工作盘/Media/速影客户/A/02-成片/ready/a.json",
            None,
            "{}",
        ),
    )
    conn.commit()
    conn.close()
    out = tmp_path / "report.json"
    rc = mod.main(
        [
            "--db",
            str(db),
            "--movies",
            str(tmp_path / "Movies" / "速影工作区"),
            "--json",
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    data = __import__("json").loads(out.read_text())
    assert data["dry_run"] is True
    assert data["summary"]["total"] == 1
    # Ensure DB path unchanged
    conn = sqlite3.connect(str(db))
    path = conn.execute("SELECT output_path FROM render_outputs WHERE id=9").fetchone()[0]
    conn.close()
    assert "FCP工作盘" in path
