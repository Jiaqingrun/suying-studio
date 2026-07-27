#!/usr/bin/env python3
"""Smoke: sync service is per-user, nas_id-bound and carrier-only by default."""

from __future__ import annotations

import json
import os
import plistlib
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "packaging" / "zspace-sync" / "zspace-team-sync.py"
INSTALLER = ROOT / "scripts" / "install-sync-service.sh"


def _fake_diskutil_proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["diskutil"],
        returncode=0,
        stdout=plistlib.dumps({"MountPoint": "/Volumes/FakeSync"}),
        stderr=b"",
    )


def _load_sync_module(home: Path) -> dict:
    os.environ["HOME"] = str(home)
    return runpy.run_path(str(SYNC))


def main() -> None:
    for path in (
        SYNC,
        INSTALLER,
        ROOT / "packaging" / "zspace-sync" / "zspace-team-sync.sh",
        ROOT / "packaging" / "zspace-sync" / "设置说明.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "/Users/qr" not in text, f"发现固定 macOS 用户路径: {path}"
        assert "EF259876-00CC-4DB0-928C-15CC218A007D" not in text, f"发现固定卷 UUID: {path}"
        assert "北京始峰伟业" not in text, f"发现固定客户配置: {path}"
    assert not (ROOT / "packaging" / "zspace-sync" / "suying-sync.default.json").exists()

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "other-user"
        config = home / ".qr" / "suying-sync.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "version": 2,
                    "sync_mode": "carrier_only",
                    "media_sync_enabled": False,
                    "carrier_relpath": "速影载体",
                    "carrier_mirror": str(home / "Suying" / "carrier"),
                    "zspace": {"username": "demo", "nas_id": "nas-model-independent"},
                    "customers": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mod = _load_sync_module(home)
        r = mod["load_runtime_maps"]()
        assert r["mode"] == "carrier_only"
        assert r["remote_root"] == "/public/速影载体"
        assert str(r["local_root"]).endswith("/Suying/carrier")

        fail_home = base / "media-fail-user"
        fail_qr = fail_home / ".qr"
        fail_qr.mkdir(parents=True)
        (fail_qr / "suying-sync.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "sync_mode": "media",
                    "media_sync_enabled": True,
                    "volume_uuid": "00000000-0000-0000-0000-000000000099",
                    "volume_relpath": "team-sync",
                    "media_sources": [],
                    "customers": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mod_fail = _load_sync_module(fail_home)
        with patch("subprocess.run", return_value=_fake_diskutil_proc()):
            try:
                mod_fail["load_runtime_maps"]()
                raise AssertionError("expected fail-closed when media_sources empty")
            except RuntimeError as e:
                assert "媒体" in str(e)

        iso_home = base / "media-iso-user"
        iso_qr = iso_home / ".qr"
        iso_qr.mkdir(parents=True)
        (iso_qr / "suying-sync.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "sync_mode": "media",
                    "media_sync_enabled": True,
                    "volume_uuid": "00000000-0000-0000-0000-000000000099",
                    "volume_relpath": "team-sync",
                    "media_sources": [
                        {
                            "customer_key": "demo-a",
                            "remote_root": "album-backup/person-a",
                            "local_target": "customers/demo-a/01-lib/person-a",
                            "pull_only": True,
                        }
                    ],
                    "customers": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mod_iso = _load_sync_module(iso_home)
        with patch("subprocess.run", return_value=_fake_diskutil_proc()):
            r2 = mod_iso["load_runtime_maps"]()
            assert r2["pull_remote_roots"] == ["/public/album-backup/person-a"]

    print(
        json.dumps(
            {"ok": True, "portable_user": True, "carrier_only": True, "media_fail_closed": True},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
