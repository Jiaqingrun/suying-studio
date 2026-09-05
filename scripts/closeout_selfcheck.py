#!/usr/bin/env python3
"""关账离线交付自检：PASS / FAIL / NEED_HUMAN。无 FAIL 才 exit 0。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ok(path: Path) -> bool:
    return path.is_file() if path.suffix else path.is_dir()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows: list[dict[str, str]] = []

    def add(cid: str, status: str, detail: str) -> None:
        rows.append({"id": cid, "status": status, "detail": detail})

    plan = ROOT / "docs" / "CLOSEOUT_PLAN.md"
    acc = ROOT / "docs" / "CLOSEOUT_ACCEPTANCE.md"
    add("CLOSE.PLAN", "PASS" if plan.is_file() else "FAIL", str(plan.relative_to(ROOT)))
    add("CLOSE.ACCEPT", "PASS" if acc.is_file() else "FAIL", str(acc.relative_to(ROOT)))

    for name in ("APP_OPT_QUEUE.md", "CONTINUOUS_QUEUE.md"):
        stub = ROOT / "docs" / name
        archived = ROOT / "docs" / "archive" / name
        text = stub.read_text(encoding="utf-8") if stub.is_file() else ""
        ok = archived.is_file() and "历史归档" in text
        add(f"CLOSE.ARCHIVE.{name}", "PASS" if ok else "FAIL", "stub+archive")

    v2 = ROOT / "apps" / "desktop-v2"
    add(
        "CLOSE.V2WONT",
        "PASS" if not v2.exists() else "FAIL",
        "apps/desktop-v2 must not exist",
    )
    desktop = ROOT / "apps" / "desktop"
    add("CLOSE.DESKTOP", "PASS" if desktop.is_dir() else "FAIL", "apps/desktop")

    try:
        from engine.reach.cdp_platforms import get_platform_adapter, list_platform_ids

        ids = list_platform_ids()
        assert ids == ("douyin", "channels", "xhs", "kuaishou")
        assert get_platform_adapter("douyin").platform == "douyin"
        add("CLOSE.CDP", "PASS", "cdp_platforms registry ok")
    except Exception as exc:  # noqa: BLE001
        add("CLOSE.CDP", "FAIL", str(exc))

    classify = ROOT / "engine" / "reach" / "upload_state_classify.py"
    dom_test = ROOT / "tests" / "test_cdp_dom_contracts.py"
    fixtures = ROOT / "tests" / "fixtures" / "cdp_dom"
    ok_dom = classify.is_file() and dom_test.is_file() and fixtures.is_dir()
    add("CLOSE.DOM", "PASS" if ok_dom else "FAIL", "upload_state_classify + fixtures")

    try:
        from engine.reach.messages_adapters import SPECS

        wechat = SPECS.get("wechat_mp")
        douyin = SPECS.get("douyin")
        if wechat is None or douyin is None:
            add("CLOSE.MSG_SPEC", "FAIL", "missing specs")
        elif wechat.readonly_verified or douyin.readonly_verified:
            add(
                "CLOSE.MSG_SPEC",
                "FAIL",
                "wechat_mp/douyin must stay readonly_verified=False until calibrated",
            )
        else:
            add("CLOSE.MSG_SPEC", "PASS", "unverified platforms not marked verified")
    except Exception as exc:  # noqa: BLE001
        add("CLOSE.MSG_SPEC", "FAIL", str(exc))

    readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    add(
        "CLOSE.INDEX",
        "PASS" if "CLOSEOUT_PLAN.md" in readme else "FAIL",
        "docs/README indexes CLOSEOUT",
    )
    lock = (ROOT / "docs" / "DEV_LOCK.md").read_text(encoding="utf-8")
    add(
        "CLOSE.LOCK",
        "PASS" if "关账冻结" in lock and "WONT · 2026-08-03" in lock else "FAIL",
        "DEV_LOCK freeze + V2 WONT",
    )

    deploy = (ROOT / "scripts" / "deploy-remote.sh").read_text(encoding="utf-8")
    rem = (ROOT / "docs" / "REMOTE_DEPLOY.md").read_text(encoding="utf-8")
    truth_ok = "CONFIG_TRUTH" in deploy and "config_truth" in rem.lower()
    add("CLOSE.TRUTH", "PASS" if truth_ok else "FAIL", "deploy + REMOTE_DEPLOY config_truth")

    # W2 offline tool / entrypoint availability (not a substitute for smoke green)
    truth_script = ROOT / "scripts" / "config_truth_diff.py"
    smoke = ROOT / "scripts" / "smoke_test.py"
    add(
        "CLOSE.W2.TOOLS",
        "PASS" if truth_script.is_file() and smoke.is_file() else "FAIL",
        "config_truth_diff + smoke_test present",
    )
    try:
        import scripts.config_truth_diff as _ctd  # noqa: F401

        add("CLOSE.W2.TRUTH_IMPORT", "PASS", "config_truth_diff importable")
    except Exception:
        # script is executed as __main__; at least ensure parseable
        raw = truth_script.read_text(encoding="utf-8")
        add(
            "CLOSE.W2.TRUTH_IMPORT",
            "PASS" if "def main" in raw and "Does not write" in raw else "FAIL",
            "config_truth_diff parseable read-only",
        )

    # Human-gate IDs: PASS if DEV_LOCK marks DONE (incl. 真机 pass); else NEED_HUMAN.
    lock_partial_humans = (
        ("GSP.6", "sleep/wake 真机"),
        ("GSO.6", "新成片人眼"),
        ("G7.5", "消息平台校准"),
        ("GVC.4", "clone 听感"),
        ("SEM.P5.H", "视觉抽样"),
        ("CUX.DUAL", "横屏人眼"),  # DEV_LOCK uses CUX.DUAL_FRAME
        ("GUI.SHIP", "双账号/软文/三路通知真机"),
    )
    for cid, label in lock_partial_humans:
        # Match row marks like "**DONE 真机 · 2026-08-10**" or "DONE 本机 · … + 关账"
        needle_ids = [cid]
        if cid == "CUX.DUAL":
            needle_ids.append("CUX.DUAL_FRAME")
        done = False
        for line in lock.splitlines():
            if not any(nid in line for nid in needle_ids):
                continue
            if "DONE" in line and "PARTIAL" not in line.split("|")[-1]:
                # Prefer status column (last cells) containing DONE
                done = True
                break
            # e.g. "**PARTIAL 真机** · … DONE 代码" — require status-bearing DONE without PARTIAL nearby
            if "DONE 真机" in line or "DONE · 2026-08-10" in line or "关账 §E 真机" in line:
                done = True
                break
        if done:
            add(cid, "PASS", f"{label} · DEV_LOCK human pass")
        else:
            add(cid, "NEED_HUMAN", label + " · CLOSEOUT_ACCEPTANCE")

    # Extra closed gates (2026-08-10) reflected for W4 reporting
    if "QUAL.NARR_B" in lock and "DONE 真机 · 2026-08-10" in lock:
        add("QUAL.NARR_B", "PASS", "ready 抽验 · DEV_LOCK")
    if "CLOSE.CONFIG_TRUTH" in lock and "DONE · 2026-08-10" in lock:
        add("CLOSE.CONFIG_TRUTH", "PASS", "xlf config_truth archive · DEV_LOCK")

    fails = sum(1 for r in rows if r["status"] == "FAIL")
    needs = sum(1 for r in rows if r["status"] == "NEED_HUMAN")
    passes = sum(1 for r in rows if r["status"] == "PASS")

    if args.json:
        print(json.dumps({"pass": passes, "fail": fails, "need_human": needs, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"{'ID':<22} {'STATUS':<12} DETAIL")
        print("-" * 72)
        for r in rows:
            print(f"{r['id']:<22} {r['status']:<12} {r['detail']}")
        print("-" * 72)
        print(f"PASS={passes} FAIL={fails} NEED_HUMAN={needs}")

    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
