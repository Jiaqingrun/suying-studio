#!/usr/bin/env python3
"""Demote soft marketing terms out of hard_deny on an active customer keyword pack.

Usage:
  python3 scripts/repair_pack_compliance_policy.py --host xlf-remote --customer 北京始峰伟业
  python3 scripts/repair_pack_compliance_policy.py --local --customer 北京始峰伟业 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.migrate_keyword_pack_v2 import (  # noqa: E402
    CAUTION_TERMS,
    HARD_DENY_TERMS,
    SAFE_REWRITES,
)

REMOTE_REPAIR = r'''
set -euo pipefail
CUSTOMER=%CUSTOMER%
DRY=%DRY%
python3 - "$CUSTOMER" "$DRY" <<'PY'
import hashlib, json, sqlite3, sys
from datetime import datetime, timezone

customer = sys.argv[1]
dry = sys.argv[2] == "1"
HARD = set(%HARD%)
CAUTION = set(%CAUTION%)
SAFE = %SAFE%

db = __import__("pathlib").Path.home() / "Suying/data/montage.db"
con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row
row = con.execute(
    """
    SELECT kp.id, kp.revision, kp.data_json, kp.content_sha256, c.name
    FROM keyword_packs kp
    JOIN customers c ON c.id = kp.customer_id
    WHERE c.name = ? AND kp.status = 'active'
    ORDER BY kp.id DESC LIMIT 1
    """,
    (customer,),
).fetchone()
if not row:
    raise SystemExit(f"no active keyword pack for {customer!r}")

data = json.loads(row["data_json"] or "{}")
comp = data.get("compliance") if isinstance(data.get("compliance"), dict) else {}
before = {
    "hard_deny": list(comp.get("hard_deny") or []),
    "blocked_terms": list(comp.get("blocked_terms") or []),
    "caution_terms": list(comp.get("caution_terms") or []),
    "safe_rewrites": dict(comp.get("safe_rewrites") or {}),
}

def scrub(terms):
    out = []
    for term in terms:
        t = str(term).strip()
        if not t:
            continue
        if t in CAUTION and t not in HARD:
            continue
        out.append(t)
    # Keep true hard denies present.
    for term in sorted(HARD):
        if term not in out:
            out.append(term)
    return list(dict.fromkeys(out))

hard = scrub(before["hard_deny"])
# hard_deny should be intersection with HARD (+ leftover true hard not in CAUTION)
hard = [t for t in hard if t in HARD or t not in CAUTION]
blocked = scrub(before["blocked_terms"])
blocked = [t for t in blocked if t in HARD or t not in CAUTION]
caution = sorted(set(before["caution_terms"]) | CAUTION)
rewrites = dict(before["safe_rewrites"])
rewrites.update(SAFE)
# Drop / replace mappings whose replacement re-introduces evidence or hard keys.
_LEAK = ("规格", "型号", "库存", "现货", "送达")
for key, value in list(rewrites.items()):
    if any(bad in str(value) for bad in _LEAK):
        if key in SAFE:
            rewrites[key] = SAFE[key]
        else:
            del rewrites[key]

comp = {
    **comp,
    "policy_version": "2026-08-cn-ads-platform-v2-layered",
    "hard_deny": sorted(set(hard) | HARD),
    "blocked_terms": list(dict.fromkeys(blocked)),
    "caution_terms": caution,
    "safe_rewrites": rewrites,
    "evidence_required": {
        **{
            "现货": "inventory_snapshot",
            "库存": "inventory_snapshot",
            "送达": "delivery_commitment",
            "经营年限": "business_registry",
            "仓库面积": "verified_scale",
            "品牌授权": "authorization_document",
            "规格": "product_specification",
            "型号": "product_specification",
        },
        **(comp.get("evidence_required") if isinstance(comp.get("evidence_required"), dict) else {}),
    },
}
data["compliance"] = comp
meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
new_rev = int(meta.get("revision") or row["revision"] or 1) + 1
meta["revision"] = new_rev
meta["change_summary"] = "repair: demote soft marketing terms out of hard_deny"
meta["repaired_at"] = datetime.now(timezone.utc).isoformat()
data["meta"] = meta
payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()

removed_hard = sorted(set(before["hard_deny"]) - set(comp["hard_deny"]))
report = {
    "pack_id": row["id"],
    "customer": row["name"],
    "old_revision": row["revision"],
    "new_revision": new_rev,
    "removed_from_hard_deny": removed_hard,
    "hard_deny_count": len(comp["hard_deny"]),
    "blocked_count": len(comp["blocked_terms"]),
    "caution_count": len(comp["caution_terms"]),
    "has_放心_hard": "放心" in comp["hard_deny"] or "放心" in comp["blocked_terms"],
    "dry_run": dry,
}
print(json.dumps(report, ensure_ascii=False, indent=2))
if dry:
    raise SystemExit(0)
con.execute(
    """
    UPDATE keyword_packs
    SET data_json = ?, revision = ?, version = ?, content_sha256 = ?
    WHERE id = ?
    """,
    (json.dumps(data, ensure_ascii=False), new_rev, new_rev, sha, row["id"]),
)
con.commit()
print(json.dumps({"ok": True, "pack_id": row["id"], "revision": new_rev}, ensure_ascii=False))
PY
'''


def _ssh(host: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", host, "bash", "-s"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def layer_compliance(data: dict[str, Any]) -> dict[str, Any]:
    comp = data.get("compliance") if isinstance(data.get("compliance"), dict) else {}
    hard_src = [str(x) for x in (comp.get("hard_deny") or [])]
    blocked_src = [str(x) for x in (comp.get("blocked_terms") or [])]

    def scrub(terms: list[str]) -> list[str]:
        out: list[str] = []
        for term in terms:
            t = term.strip()
            if not t:
                continue
            if t in CAUTION_TERMS and t not in HARD_DENY_TERMS:
                continue
            out.append(t)
        for term in sorted(HARD_DENY_TERMS):
            if term not in out:
                out.append(term)
        return list(dict.fromkeys(out))

    hard = [t for t in scrub(hard_src) if t in HARD_DENY_TERMS or t not in CAUTION_TERMS]
    blocked = [t for t in scrub(blocked_src) if t in HARD_DENY_TERMS or t not in CAUTION_TERMS]
    rewrites = dict(comp.get("safe_rewrites") or {})
    rewrites.update(SAFE_REWRITES)
    _leak = ("规格", "型号", "库存", "现货", "送达")
    for key, value in list(rewrites.items()):
        if any(bad in str(value) for bad in _leak):
            if key in SAFE_REWRITES:
                rewrites[key] = SAFE_REWRITES[key]
            else:
                del rewrites[key]
    data = dict(data)
    data["compliance"] = {
        **comp,
        "policy_version": "2026-08-cn-ads-platform-v2-layered",
        "hard_deny": sorted(set(hard) | HARD_DENY_TERMS),
        "blocked_terms": blocked,
        "caution_terms": sorted(set(comp.get("caution_terms") or []) | CAUTION_TERMS),
        "safe_rewrites": rewrites,
        "evidence_required": {
            "现货": "inventory_snapshot",
            "库存": "inventory_snapshot",
            "送达": "delivery_commitment",
            "经营年限": "business_registry",
            "仓库面积": "verified_scale",
            "品牌授权": "authorization_document",
            "规格": "product_specification",
            "型号": "product_specification",
            **(
                comp.get("evidence_required")
                if isinstance(comp.get("evidence_required"), dict)
                else {}
            ),
        },
    }
    return data


def repair_local(customer: str, *, dry_run: bool) -> dict[str, Any]:
    import sqlite3
    from datetime import datetime, timezone

    db = Path.home() / "Suying/data/montage.db"
    if not db.is_file():
        raise SystemExit(f"db not found: {db}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    row = con.execute(
        """
        SELECT kp.id, kp.revision, kp.data_json, c.name
        FROM keyword_packs kp
        JOIN customers c ON c.id = kp.customer_id
        WHERE c.name = ? AND kp.status = 'active'
        ORDER BY kp.id DESC LIMIT 1
        """,
        (customer,),
    ).fetchone()
    if not row:
        raise SystemExit(f"no active keyword pack for {customer!r}")
    before = json.loads(row["data_json"] or "{}")
    before_hard = list((before.get("compliance") or {}).get("hard_deny") or [])
    before_rw = dict((before.get("compliance") or {}).get("safe_rewrites") or {})
    data = layer_compliance(dict(before))
    meta = dict(data.get("meta") or {})
    new_rev = int(meta.get("revision") or row["revision"] or 1) + 1
    meta["revision"] = new_rev
    meta["change_summary"] = "repair: scrub safe_rewrites leaking evidence terms"
    meta["repaired_at"] = datetime.now(timezone.utc).isoformat()
    data["meta"] = meta
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    after_hard = list(data["compliance"]["hard_deny"])
    after_rw = dict(data["compliance"].get("safe_rewrites") or {})
    changed_rw = {
        k: {"before": before_rw.get(k), "after": after_rw.get(k)}
        for k in sorted(set(before_rw) | set(after_rw))
        if before_rw.get(k) != after_rw.get(k)
    }
    report = {
        "pack_id": row["id"],
        "customer": customer,
        "old_revision": row["revision"],
        "new_revision": new_rev,
        "removed_from_hard_deny": sorted(set(before_hard) - set(after_hard)),
        "safe_rewrites_changed": changed_rw,
        "现货充足_after": after_rw.get("现货充足"),
        "has_放心_hard": "放心" in after_hard
        or "放心" in data["compliance"]["blocked_terms"],
        "dry_run": dry_run,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if dry_run:
        return report
    con.execute(
        """
        UPDATE keyword_packs
        SET data_json = ?, revision = ?, version = ?, content_sha256 = ?
        WHERE id = ?
        """,
        (json.dumps(data, ensure_ascii=False), new_rev, new_rev, sha, row["id"]),
    )
    con.commit()
    report["ok"] = True
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="SSH host alias for customer Mac")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.host and not args.local:
        ap.error("require --host or --local")
    if args.local:
        repair_local(args.customer, dry_run=args.dry_run)
        return 0
    import shlex

    script = REMOTE_REPAIR
    script = script.replace("%CUSTOMER%", shlex.quote(args.customer))
    script = script.replace("%DRY%", "1" if args.dry_run else "0")
    script = script.replace("%HARD%", json.dumps(sorted(HARD_DENY_TERMS), ensure_ascii=False))
    script = script.replace("%CAUTION%", json.dumps(sorted(CAUTION_TERMS), ensure_ascii=False))
    script = script.replace("%SAFE%", json.dumps(SAFE_REWRITES, ensure_ascii=False))
    result = _ssh(args.host, script)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
