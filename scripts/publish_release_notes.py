#!/usr/bin/env python3
"""Backfill ``更新说明.md`` onto T2S historical release dirs.

Does **not** rewrite signed ``release.json`` / ZIP / ``latest.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.release_notes import (  # noqa: E402
    NOTES_FILENAME,
    load_catalog,
    render_changelog_markdown,
    render_notes_markdown,
)
from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from scripts.publish_update_repo import REMOTE_ROOT, _client_class  # noqa: E402


def _semver_key(ver: str) -> tuple[int, ...]:
    return tuple(int(p) for p in ver.split("."))


def publish_notes(*, dry_run: bool = False, version: str | None = None) -> dict:
    catalog = load_catalog()
    session = load_zspace_session()
    if str(session.get("nas_id") or "") != "T0210023G0UWV":
        raise RuntimeError("当前极空间不是目标 T2S（T0210023G0UWV），拒绝上传")
    client = _client_class()(
        f"http://127.0.0.1:{session['local_port']}",
        session,
    )
    releases_root = f"{REMOTE_ROOT}/releases"
    remote_versions = [str(item.get("name")) for item in client.list_dir(releases_root)]
    remote_versions = sorted(remote_versions, key=_semver_key)
    if version:
        if version not in remote_versions:
            raise RuntimeError(f"T2S 无此版本目录: {version}")
        remote_versions = [version]

    uploaded: list[dict[str, str]] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="suying-release-notes-") as td:
        staging = Path(td)
        changelog = staging / NOTES_FILENAME
        changelog.write_text(
            render_changelog_markdown(catalog=catalog, for_t2s=True),
            encoding="utf-8",
        )
        if not dry_run:
            client.upload(changelog, f"{REMOTE_ROOT}/{NOTES_FILENAME}")
        uploaded.append({"path": f"{REMOTE_ROOT}/{NOTES_FILENAME}", "kind": "index"})

        for ver in remote_versions:
            if ver not in (catalog.get("versions") or {}):
                skipped.append(ver)
                continue
            builds = [str(item.get("name")) for item in client.list_dir(f"{releases_root}/{ver}")]
            version_md = staging / f"{ver}-{NOTES_FILENAME}"
            version_md.write_text(
                render_notes_markdown(ver, catalog=catalog, backfilled=True),
                encoding="utf-8",
            )
            if not dry_run:
                client.upload(version_md, f"{releases_root}/{ver}/{NOTES_FILENAME}")
            uploaded.append({"path": f"{releases_root}/{ver}/{NOTES_FILENAME}", "kind": "version"})

            for build in builds:
                files = [
                    str(item.get("name"))
                    for item in client.list_dir(f"{releases_root}/{ver}/{build}")
                ]
                seq = None
                if "release.json" in files:
                    dest = staging / f"{ver}-{build}-release.json"
                    client.download(f"{releases_root}/{ver}/{build}/release.json", dest)
                    payload = json.loads(dest.read_text(encoding="utf-8"))
                    seq = payload.get("release_seq")
                body = render_notes_markdown(
                    ver,
                    catalog=catalog,
                    build_date=build,
                    release_seq=int(seq) if seq is not None else None,
                    backfilled=True,
                )
                dest_md = staging / f"{ver}-{build}-{NOTES_FILENAME}"
                dest_md.write_text(body, encoding="utf-8")
                if not dry_run:
                    client.upload(dest_md, f"{releases_root}/{ver}/{build}/{NOTES_FILENAME}")
                uploaded.append(
                    {
                        "path": f"{releases_root}/{ver}/{build}/{NOTES_FILENAME}",
                        "kind": "build",
                    }
                )

    return {
        "ok": True,
        "dry_run": dry_run,
        "uploaded": len(uploaded),
        "skipped_unregistered": skipped,
        "paths": uploaded,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--version", help="只补某一版本")
    args = parser.parse_args()
    print(
        json.dumps(
            publish_notes(dry_run=args.dry_run, version=args.version),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
