#!/usr/bin/env python3
"""Upload a verified CAS depot to T2S and read every object back."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from engine.security.offline_depot import verify_depot  # noqa: E402
from engine.security.update_manifest import sha256_file  # noqa: E402
from scripts.publish_update_repo import (  # noqa: E402
    REMOTE_ROOT,
    _client_class,
    _remote_size,
)

TARGET_NAS_ID = "T0210023G0UWV"


def publish(depot: Path) -> dict[str, object]:
    root = depot.expanduser().resolve()
    manifest = verify_depot(root)
    session = load_zspace_session()
    if str(session.get("nas_id") or "") != TARGET_NAS_ID:
        raise RuntimeError(f"当前极空间不是目标 T2S（{TARGET_NAS_ID}），拒绝上传")
    client = _client_class()(
        f"http://127.0.0.1:{session['local_port']}",
        session,
    )
    remote_depot = f"{REMOTE_ROOT}/depot"
    remote_objects = f"{remote_depot}/objects/sha256"
    objects_backend = client.ensure_remote_dir(remote_objects)
    listing = {str(item.get("name")): item for item in client.list_dir(objects_backend)}
    uploaded = 0
    with tempfile.TemporaryDirectory(prefix="suying-depot-readback-") as temp:
        verify_dir = Path(temp)
        for digest, item in manifest.objects.items():
            source = root / item.object_path
            remote = f"{remote_objects}/{digest}"
            existing = listing.get(digest)
            if not existing or _remote_size(existing) != item.size:
                client.upload(source, remote)
                uploaded += 1
            downloaded = verify_dir / digest
            client.download(remote, downloaded, expected_size=item.size)
            if sha256_file(downloaded) != digest:
                raise RuntimeError(f"远端 CAS 对象回读 SHA256 失败: {digest}")
            downloaded.unlink()
        # Signed metadata is the commit point and is uploaded only after full readback.
        client.upload(root / "depot.json.sig", f"{remote_depot}/depot.json.sig")
        client.upload(root / "depot.json", f"{remote_depot}/depot.json")
    return {
        "ok": True,
        "depot_seq": manifest.depot_seq,
        "objects": len(manifest.objects),
        "uploaded": uploaded,
        "readback_verified": len(manifest.objects),
        "remote_root": client.to_backend_path(remote_depot),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depot", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.depot), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
