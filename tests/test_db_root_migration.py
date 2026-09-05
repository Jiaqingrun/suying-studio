from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import migrate_montage_db_root as migration


def _make_db(path: Path, *, jobs: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO customers(id) VALUES (1)")
        conn.executemany("INSERT INTO jobs(id) VALUES (?)", [(index,) for index in range(1, jobs + 1)])
        conn.commit()
    finally:
        conn.close()


def test_migrate_db_root_keeps_source_and_updates_bootstrap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "external" / "db"
    target_root = tmp_path / "home" / "Suying" / "data"
    source_db = source_root / "montage.db"
    target_db = target_root / "montage.db"
    _make_db(source_db, jobs=3)
    _make_db(target_db, jobs=1)
    settings = target_root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "paths": {"data_root": str(source_root)},
                "active_customer": "demo",
                "workspace_volume_uuid": "OLD-EXTERNAL-VOLUME",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "_open_pids", lambda _path: [])

    preview = migration.migrate(
        settings_path=settings,
        target_root=target_root,
        execute=False,
    )
    assert preview["dry_run"] is True
    assert json.loads(settings.read_text(encoding="utf-8"))["paths"]["data_root"] == str(source_root)

    result = migration.migrate(
        settings_path=settings,
        target_root=target_root,
        execute=True,
    )
    assert result["changed"] is True
    assert source_db.is_file()
    assert migration._integrity_and_counts(target_db)["counts"]["jobs"] == 3
    migrated_settings = json.loads(settings.read_text(encoding="utf-8"))
    assert migrated_settings["paths"]["data_root"] == str(target_root.resolve())
    assert migrated_settings["workspace_volume_uuid"] == ""
    assert any("montage.db.pre-migration-" in item for item in result["backups"])


def test_migration_refuses_open_source(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "external" / "db"
    target_root = tmp_path / "local"
    _make_db(source_root / "montage.db", jobs=1)
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"paths": {"data_root": str(source_root)}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(migration, "_open_pids", lambda _path: [1234])

    try:
        migration.migrate(settings_path=settings, target_root=target_root, execute=True)
        raise AssertionError("expected migration to reject an open source database")
    except RuntimeError as exc:
        assert "进程占用" in str(exc)
    assert not (target_root / "montage.db").exists()
