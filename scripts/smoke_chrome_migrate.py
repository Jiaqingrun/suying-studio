#!/usr/bin/env python3
"""Smoke: workspace chrome migrate defers cross-volume / trash / skip env."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _EmptyScalars:
    def all(self):
        return []


class _FakeSession:
    def scalars(self, *_a, **_k):
        return _EmptyScalars()

    def close(self):
        return None


def main() -> int:
    from engine.reach import browser as br

    tmp = Path(tempfile.mkdtemp(prefix="suying-chrome-migrate-"))
    old = {
        "SUYING_CHROME_PROFILES": os.environ.get("SUYING_CHROME_PROFILES"),
        "SUYING_SKIP_WORKSPACE_CHROME_MIGRATE": os.environ.get(
            "SUYING_SKIP_WORKSPACE_CHROME_MIGRATE"
        ),
        "SUYING_FORCE_WORKSPACE_CHROME_MIGRATE": os.environ.get(
            "SUYING_FORCE_WORKSPACE_CHROME_MIGRATE"
        ),
    }
    # Unset override so workspace merge branch runs; point root via monkeypatch.
    os.environ.pop("SUYING_CHROME_PROFILES", None)
    os.environ.pop("SUYING_SKIP_WORKSPACE_CHROME_MIGRATE", None)
    os.environ.pop("SUYING_FORCE_WORKSPACE_CHROME_MIGRATE", None)

    local = tmp / "local-apfs"
    local.mkdir()
    orig_root = br.chrome_profiles_root
    orig_legacy = br._legacy_workspace_chrome_profiles_roots
    real_stat = Path.stat
    br.chrome_profiles_root = lambda: local  # type: ignore[assignment]
    try:
        # 1) skip env
        os.environ["SUYING_SKIP_WORKSPACE_CHROME_MIGRATE"] = "1"
        report_skip = br.migrate_legacy_chrome_profiles(
            session=_FakeSession(), force_workspace_migrate=False
        )
        reasons = {s.get("reason") for s in report_skip.get("skipped") or []}
        assert "SUYING_SKIP_WORKSPACE_CHROME_MIGRATE" in reasons, report_skip
        os.environ.pop("SUYING_SKIP_WORKSPACE_CHROME_MIGRATE", None)

        # 2) trash never copied on same-volume merge
        work2 = tmp / "workspace2"
        work2.mkdir()
        (work2 / "trash").mkdir()
        (work2 / "trash" / "x").write_text("1", encoding="utf-8")
        (work2 / "keep-me").mkdir()
        (work2 / "keep-me" / "Default").mkdir()
        br._legacy_workspace_chrome_profiles_roots = lambda: [work2]  # type: ignore[assignment]
        report2 = br.migrate_legacy_chrome_profiles(
            session=_FakeSession(), force_workspace_migrate=False
        )
        assert not (local / "trash").exists(), report2
        trash_skips = [
            s
            for s in (report2.get("skipped") or [])
            if s.get("reason") == "trash_or_hidden"
        ]
        assert trash_skips, report2

        # 3) cross-volume deferred without force
        work3 = tmp / "workspace3"
        work3.mkdir()
        (work3 / "prof-a").mkdir()
        (work3 / "prof-a" / "Default").mkdir()
        (local / "customer-9").mkdir(exist_ok=True)
        (local / ".scope_layout_v1").write_text("1", encoding="utf-8")
        br._legacy_workspace_chrome_profiles_roots = lambda: [work3]  # type: ignore[assignment]

        def patched_stat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            st = real_stat(self, *args, **kwargs)
            try:
                resolved = self.resolve()
            except OSError:
                return st
            if resolved == work3.resolve() or work3.resolve() in resolved.parents:
                return os.stat_result(
                    (
                        st.st_mode,
                        st.st_ino,
                        st.st_dev + 99999,
                        st.st_nlink,
                        st.st_uid,
                        st.st_gid,
                        st.st_size,
                        st.st_atime,
                        st.st_mtime,
                        st.st_ctime,
                    )
                )
            return st

        Path.stat = patched_stat  # type: ignore[method-assign]
        try:
            report3 = br.migrate_legacy_chrome_profiles(
                session=_FakeSession(), force_workspace_migrate=False
            )
        finally:
            Path.stat = real_stat  # type: ignore[method-assign]
        defer = [
            s
            for s in (report3.get("skipped") or [])
            if "cross_volume" in str(s.get("reason") or "")
        ]
        assert defer, report3
        assert not (local / "prof-a").exists(), report3

        # 4) force merges across fabricated volume boundary
        Path.stat = patched_stat  # type: ignore[method-assign]
        try:
            report4 = br.migrate_legacy_chrome_profiles(
                session=_FakeSession(), force_workspace_migrate=True
            )
        finally:
            Path.stat = real_stat  # type: ignore[method-assign]
        moved = report4.get("workspace_moved") or []
        assert (local / "prof-a").exists() or any(
            "prof-a" in str(m.get("path") or m.get("relative") or "") for m in moved
        ), report4

        print("smoke_chrome_migrate: OK")
        return 0
    finally:
        Path.stat = real_stat  # type: ignore[method-assign]
        br.chrome_profiles_root = orig_root  # type: ignore[assignment]
        br._legacy_workspace_chrome_profiles_roots = orig_legacy  # type: ignore[assignment]
        for key, val in old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
