#!/usr/bin/env python3
"""Export / restore 速影运维机 Keychain 密钥（明文 JSON，仅供个人 NAS 主仓备份）。

Never commit the export file. Never publish to 速影/更新包 / 团队空间.
Logs and stdout must not print secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICES = (
    "com.qr.suying.release-signing",
    "com.qr.suying-ops",
    "com.qr.suying",
    "com.qr.suying.license",
)

# release-signing is the trust root for updates + licenses.
CRITICAL_ITEMS = {
    ("com.qr.suying.release-signing", "release-v1"),
    ("com.qr.suying-ops", "vault-master-key-v1"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _security(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/security", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def list_service_accounts(services: tuple[str, ...] = SERVICES) -> list[tuple[str, str]]:
    raw = _security("dump-keychain", check=False)
    text = (raw.stdout or "") + "\n" + (raw.stderr or "")
    if raw.returncode != 0 and "keychain" not in text.lower():
        # dump-keychain often writes to stdout even with warnings
        pass
    found: set[tuple[str, str]] = set()
    for block in re.split(r"\nkeychain: ", text):
        if "com.qr.suying" not in block:
            continue
        svce = re.search(r'"svce"<blob>="([^"]+)"', block)
        acct = re.search(r'"acct"<blob>="([^"]+)"', block)
        if not svce or not acct:
            continue
        service, account = svce.group(1), acct.group(1)
        if service in services:
            found.add((service, account))
    # Always probe known critical accounts even if dump parse missed them.
    for service, account in CRITICAL_ITEMS:
        probe = _security(
            "find-generic-password",
            "-s",
            service,
            "-a",
            account,
            "-w",
            check=False,
        )
        if probe.returncode == 0:
            found.add((service, account))
    return sorted(found)


def read_secret(service: str, account: str) -> str:
    result = _security(
        "find-generic-password",
        "-s",
        service,
        "-a",
        account,
        "-w",
    )
    secret = result.stdout.strip()
    if not secret:
        raise RuntimeError(f"空密钥: {service}/{account}")
    return secret


def export_keychain(*, host: str | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for service, account in list_service_accounts():
        try:
            secret = read_secret(service, account)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{service}/{account}: {exc}")
            continue
        items.append(
            {
                "service": service,
                "account": account,
                "secret": secret,
                "critical": (service, account) in CRITICAL_ITEMS,
            }
        )
    for service, account in CRITICAL_ITEMS:
        if not any(i["service"] == service and i["account"] == account for i in items):
            missing.append(f"CRITICAL missing: {service}/{account}")
    if any(m.startswith("CRITICAL") for m in missing):
        raise RuntimeError("; ".join(missing))

    payload = {
        "schema": 1,
        "kind": "suying-ops-keychain",
        "plaintext": True,
        "warning": (
            "明文密钥备份。仅允许存放在个人 T2S「速影/主仓备份/keys/」。"
            "禁止进入 Git、速影/更新包、团队空间、客户机。"
        ),
        "exported_at": _now_iso(),
        "host": host or os.uname().nodename,
        "items": items,
        "item_count": len(items),
        "missing": missing,
    }
    return payload


def fingerprint_payload(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in sorted(
        payload.get("items") or [],
        key=lambda x: (str(x.get("service")), str(x.get("account"))),
    ):
        lines.append(
            f"{item.get('service')}|{item.get('account')}|{item.get('secret')}"
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def write_export(path: Path, payload: dict[str, Any]) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(data, encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def restore_payload(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    if int(payload.get("schema") or 0) != 1:
        raise ValueError("不支持的 schema")
    if payload.get("kind") != "suying-ops-keychain":
        raise ValueError("不是 suying-ops-keychain 导出")
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("导出无 items")

    restored: list[str] = []
    skipped: list[str] = []
    for item in items:
        service = str(item.get("service") or "").strip()
        account = str(item.get("account") or "").strip()
        secret = str(item.get("secret") or "")
        if not service or not account or not secret:
            skipped.append(f"{service}/{account}: incomplete")
            continue
        label = f"{service}/{account}"
        if dry_run:
            restored.append(label)
            continue
        result = _security(
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            secret,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"写入失败 {label}: {err}")
        restored.append(label)
    return {
        "ok": True,
        "dry_run": dry_run,
        "restored": restored,
        "skipped": skipped,
        "count": len(restored),
    }


def download_keys_from_t2s(dest: Path) -> Path:
    """Download plaintext ops-keychain.json from personal 速影主仓备份."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from engine.ops.suying_sync import load_zspace_session
    from scripts.backup_repo_to_zspace import REMOTE_ROOT, TARGET_NAS_ID
    from scripts.publish_update_repo import _client_class

    session = load_zspace_session()
    if str(session.get("nas_id") or "") != TARGET_NAS_ID:
        raise RuntimeError(
            f"当前极空间不是目标 T2S（{TARGET_NAS_ID}），"
            f"实际={session.get('nas_id')}"
        )
    client = _client_class()(
        f"http://127.0.0.1:{session['local_port']}",
        session,
    )
    remote = f"{REMOTE_ROOT}/keys/ops-keychain.json"
    backend_keys = client.ensure_remote_dir(f"{REMOTE_ROOT}/keys")
    listing = client.list_dir(backend_keys)
    hit = next((item for item in listing if item.get("name") == "ops-keychain.json"), None)
    if not hit:
        raise RuntimeError(f"远端没有 {remote}")
    size = int(hit.get("size") or hit.get("file_size") or 0)
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    client.download(remote, dest, expected_size=size if size > 0 else None)
    os.chmod(dest, 0o600)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description="速影运维 Keychain 明文导出/恢复")
    sub = parser.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="导出到本地 JSON（600）")
    exp.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "Suying" / "ops" / "repo-backup" / "keys" / "ops-keychain.json",
    )

    imp = sub.add_parser("restore", help="从 JSON 写回 Keychain")
    imp.add_argument("--from", dest="source", type=Path, default=None)
    imp.add_argument(
        "--from-t2s",
        action="store_true",
        help="从 T2S 速影/主仓备份/keys/ops-keychain.json 下载后恢复",
    )
    imp.add_argument("--dry-run", action="store_true")

    lst = sub.add_parser("list", help="列出将导出的 service/account（不含密钥）")

    args = parser.parse_args()
    if args.cmd == "list":
        for service, account in list_service_accounts():
            crit = "CRITICAL" if (service, account) in CRITICAL_ITEMS else ""
            print(f"{service}\t{account}\t{crit}".rstrip())
        return 0
    if args.cmd == "export":
        payload = export_keychain()
        path = write_export(args.out, payload)
        # Never print secrets.
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(path),
                    "item_count": payload["item_count"],
                    "fingerprint": fingerprint_payload(payload),
                    "items": [
                        {
                            "service": i["service"],
                            "account": i["account"],
                            "critical": i["critical"],
                        }
                        for i in payload["items"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.cmd == "restore":
        if args.from_t2s:
            source = download_keys_from_t2s(
                Path.home() / "Suying" / "ops" / "repo-backup" / "keys" / "ops-keychain.from-t2s.json"
            )
        elif args.source:
            source = args.source.expanduser().resolve()
        else:
            parser.error("restore 需要 --from <文件> 或 --from-t2s")
            return 2
        payload = json.loads(source.read_text(encoding="utf-8"))
        result = restore_payload(payload, dry_run=bool(args.dry_run))
        result["source"] = str(source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
