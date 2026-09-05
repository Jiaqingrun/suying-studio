#!/usr/bin/env python3
"""Publish F5 Runtime Kit archive to T2S f5-runtime rail (separate from App packages).

Remote layout:
  /nvme11/my/data/速影/更新包/f5-runtime/
    latest-kit.json
    kits/<archive>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from engine.security.update_manifest import (  # noqa: E402
    encode_public_key,
    public_key_id,
)
from scripts.publish_update_repo import REMOTE_ROOT, _client_class  # noqa: E402
from scripts.sign_update_release import load_keychain_private_key  # noqa: E402

F5_REMOTE = f"{REMOTE_ROOT}/f5-runtime"
DEFAULT_ACCOUNT = "release-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_kit(
    package: Path,
    *,
    kit_rev: int | None = None,
    python_tag: str | None = None,
    notes: str = "",
    meta_path: Path | None = None,
    account: str = DEFAULT_ACCOUNT,
) -> dict[str, Any]:
    package = package.expanduser().resolve()
    if not package.is_file() or ".tar" not in package.name:
        raise ValueError(f"Kit 必须是存在的 tar 包: {package}")

    meta: dict[str, Any] = {}
    mp = meta_path or Path(str(package) + ".meta.json")
    if mp.is_file():
        meta = json.loads(mp.read_text(encoding="utf-8"))

    kit_rev = int(kit_rev if kit_rev is not None else meta.get("kit_rev") or 0)
    if kit_rev < 1 and "-r" in package.name:
        try:
            kit_rev = int(package.name.rsplit("-r", 1)[-1].split(".tar")[0])
        except ValueError:
            kit_rev = 0
    if kit_rev < 1:
        raise ValueError("无法确定 kit_rev；传 --kit-rev 或提供 .meta.json")

    python_tag = python_tag or str(meta.get("python_tag") or "")
    if not python_tag:
        rest = package.name.replace("速影-f5-runtime-kit-", "")
        if "-r" in rest:
            python_tag = rest.rsplit("-r", 1)[0]

    digest = _sha256(package)
    size = package.stat().st_size

    session = load_zspace_session()
    if str(session.get("nas_id") or "") != "T0210023G0UWV":
        raise RuntimeError("当前极空间不是目标 T2S（T0210023G0UWV），拒绝上传")
    client = _client_class()(
        f"http://127.0.0.1:{session['local_port']}",
        session,
    )

    backend_kits = client.ensure_remote_dir(f"{F5_REMOTE}/kits")
    backend_root = client.ensure_remote_dir(F5_REMOTE)

    try:
        listing = client.list_dir(backend_kits)
        hit = next((i for i in listing if i.get("name") == package.name), None)
        remote_size = int((hit or {}).get("size") or (hit or {}).get("file_size") or 0)
        if hit and remote_size == size:
            print(f"remote kit exists (size match), skip upload: {package.name}")
        else:
            client.upload(str(package), backend_kits)
    except Exception:
        client.upload(str(package), backend_kits)

    payload = {
        "kit_rev": kit_rev,
        "sha256": digest,
        "package": f"kits/{package.name}",
        "python_tag": python_tag,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    latest: dict[str, Any] = {
        "schema_version": 1,
        "kit_id": meta.get("kit_id") or "suying-f5-runtime",
        "kit_rev": kit_rev,
        "python_tag": python_tag,
        "package": payload["package"],
        "sha256": digest,
        "bytes": size,
        "notes": notes or str(meta.get("notes") or ""),
        "source_build": meta.get("source_build") or "",
        "weights_policy": meta.get("weights_policy")
        or {
            "kind": "external_hf_hub",
            "models": [
                "models--SWivid--F5-TTS",
                "models--charactr--vocos-mel-24khz",
            ],
        },
    }

    try:
        private_key = load_keychain_private_key(account)
        sig = private_key.sign(body)
        latest["signature"] = {
            "alg": "ed25519",
            "key_id": public_key_id(private_key.public_key()),
            "account": account,
            "public_key": encode_public_key(private_key.public_key()),
            "sig_hex": sig.hex(),
            "payload": payload,
        }
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: 无法签名 latest-kit（{exc}）；仍上传未签指针", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="suying-f5-kit-pub-") as temp:
        staging = Path(temp)
        latest_path = staging / "latest-kit.json"
        latest_path.write_text(
            json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        client.upload(str(latest_path), backend_root)

    print(
        json.dumps(
            {
                "ok": True,
                "remote_root": F5_REMOTE,
                "kit_rev": kit_rev,
                "package": package.name,
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="Kit .tar.gz 路径")
    parser.add_argument("--kit-rev", type=int, default=None)
    parser.add_argument("--python-tag", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument("--meta", type=Path, default=None, help=".meta.json 旁车")
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument(
        "--keep-local",
        action="store_true",
        help="推送成功后仍保留本机 kit（默认删除，避免 ~/Suying/releases 堆历史）",
    )
    args = parser.parse_args()
    package = args.package.expanduser().resolve()
    meta_path = args.meta.expanduser().resolve() if args.meta else Path(str(package) + ".meta.json")
    publish_kit(
        package,
        kit_rev=args.kit_rev,
        python_tag=args.python_tag,
        notes=args.notes,
        meta_path=meta_path if meta_path.is_file() else None,
        account=args.account,
    )
    if not args.keep_local:
        for path in (package, Path(str(package) + ".meta.json"), meta_path):
            if path.is_file():
                path.unlink(missing_ok=True)
                print(f"removed local kit artifact: {path}")
        # 若 kits 目录已空则去掉空壳，避免 releases 再堆目录
        kits_dir = package.parent
        if kits_dir.name == "kits" and kits_dir.is_dir() and not any(kits_dir.iterdir()):
            kits_dir.rmdir()
            parent = kits_dir.parent
            if parent.name == "f5-runtime" and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
