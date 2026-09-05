#!/usr/bin/env python3
"""速影一体包全量功能单项验收（本地运维机）。

输出 JSON 报告，供写入桌面 Word。
不替代真机发布；Reach 浏览器真机项标为 SKIPPED_MANUAL。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path.home() / "Desktop"
VERSION = "0.3.0"
ARCH = os.uname().machine
APP_BUNDLE = "速影 Studio.app"


@dataclass
class CaseResult:
    id: str
    name: str
    group: str
    status: str  # PASS | FAIL | SKIP | WARN
    detail: str = ""
    duration_sec: float = 0.0
    fix_notes: str = ""


@dataclass
class Report:
    started_at: str
    finished_at: str = ""
    package_paths: dict[str, str] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)

    def add(self, case: CaseResult) -> None:
        self.cases.append(case)
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "○", "WARN": "!"}.get(case.status, "?")
        print(f"[{mark}] {case.group}/{case.id}: {case.name} — {case.status} {case.detail[:120]}")


def run(cmd: list[str] | str, *, cwd: Path | None = None, timeout: int = 180) -> tuple[int, str]:
    if isinstance(cmd, str):
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    else:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def find_app() -> Path | None:
    candidates = [
        DESKTOP / f"速影-{VERSION}-product-macos-{ARCH}" / APP_BUNDLE,
        DESKTOP / f"速影-{VERSION}-macos-{ARCH}" / APP_BUNDLE,
        ROOT / f"apps/desktop/src-tauri/target/release/bundle/macos/{APP_BUNDLE}",
        DESKTOP / APP_BUNDLE,
        # legacy
        DESKTOP / f"速影-{VERSION}-product-macos-{ARCH}" / "速影.app",
        DESKTOP / "速影.app",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return None


def find_kit() -> Path | None:
    for p in (
        DESKTOP / f"速影-{VERSION}-product-macos-{ARCH}",
        DESKTOP / f"速影-{VERSION}-macos-{ARCH}",
    ):
        if p.is_dir():
            return p
    return None


def http_json(method: str, url: str, body: dict | None = None, *, timeout: float = 30) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"detail": str(e)}
        except json.JSONDecodeError:
            payload = {"_raw": raw[:500]}
        return int(e.code), payload
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def case(report: Report, *, id: str, name: str, group: str, fn: Callable[[], tuple[str, str]]) -> None:
    t0 = time.time()
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001
        status, detail = "FAIL", f"exception: {e}"
    report.add(
        CaseResult(
            id=id,
            name=name,
            group=group,
            status=status,
            detail=detail,
            duration_sec=round(time.time() - t0, 3),
        )
    )


def main() -> int:
    report = Report(started_at=datetime.now().isoformat(timespec="seconds"))
    app = find_app()
    kit = find_kit()
    report.package_paths = {
        "app": str(app) if app else "",
        "kit": str(kit) if kit else "",
    }

    # ---------- 0. Package DoD ----------
    def pkg_exists() -> tuple[str, str]:
        if not app:
            return "FAIL", "找不到 速影.app"
        return "PASS", str(app)

    case(report, id="pkg.app", name="一体包 App 存在", group="出包", fn=pkg_exists)

    def pkg_runtime() -> tuple[str, str]:
        if not app:
            return "SKIP", "无 App"
        rt = app / "Contents/Resources/runtime"
        need = [
            rt / "BUNDLE_LAYOUT.txt",
            rt / "RUNTIME_SOURCE_SHA256",
            rt / "RUNTIME_MANIFEST.json",
            rt / "RUNTIME_MANIFEST.json.sig",
            rt / "python/bin/python3",
            rt / "studio/engine/main.py",
            rt / "studio/engine/reach/publish_runner.py",
            rt / "studio/engine/reach/chrome_runtime.py",
            rt / "studio/engine/ops/automation_loop.py",
            rt / "studio/engine/api/workspace_routes.py",
            rt / "studio/engine/catalog/paper_slip.py",
        ]
        missing = [str(p.relative_to(rt)) for p in need if not p.exists()]
        if missing:
            return "FAIL", "缺少: " + ", ".join(missing)
        return "PASS", "runtime 关键文件齐全"

    case(report, id="pkg.runtime", name="runtime 嵌入与关键模块", group="出包", fn=pkg_runtime)

    def pkg_no_customers() -> tuple[str, str]:
        if not app:
            return "SKIP", "无 App"
        studio = app / "Contents/Resources/runtime/studio"
        bad = list(studio.glob("configs/customers/*")) if (studio / "configs/customers").exists() else []
        if bad:
            return "FAIL", f"客户配置误入包: {len(bad)}"
        if (studio / "creative").exists() or (app / "Contents/Resources/runtime/creative").exists():
            return "FAIL", "含 creative/OpenMontage"
        return "PASS", "无客户资料/creative"

    case(report, id="pkg.forbidden", name="禁入内容检查", group="出包", fn=pkg_no_customers)

    def pkg_imports() -> tuple[str, str]:
        if not app:
            return "SKIP", "无 App"
        py = app / "Contents/Resources/runtime/python/bin/python3"
        studio = app / "Contents/Resources/runtime/studio"
        code, out = run(
            [
                str(py),
                "-c",
                "import encodings,fastapi,uvicorn,edge_tts,numpy;"
                "import engine.main,engine.reach.publish_runner,engine.reach.chrome_runtime;"
                "import engine.ops.automation_loop,engine.api.workspace_routes;"
                "from engine.catalog.paper_slip import ROLLING_CLIPLET_WINDOW, assert_paper_slip_integrity;"
                "assert_paper_slip_integrity();"
                "assert ROLLING_CLIPLET_WINDOW==20;"
                "print('OK')",
            ],
            cwd=studio,
            timeout=120,
        )
        env = os.environ.copy()
        # re-run with PYTHONPATH
        proc = subprocess.run(
            [
                str(py),
                "-c",
                "import encodings,fastapi,uvicorn,edge_tts,numpy;"
                "import engine.main,engine.reach.publish_runner,engine.reach.chrome_runtime;"
                "import engine.ops.automation_loop,engine.api.workspace_routes;"
                "from engine.catalog.paper_slip import ROLLING_CLIPLET_WINDOW, assert_paper_slip_integrity;"
                "assert_paper_slip_integrity();"
                "assert ROLLING_CLIPLET_WINDOW==20;"
                "print('OK')",
            ],
            cwd=str(studio),
            capture_output=True,
            text=True,
            timeout=120,
            env={**env, "PYTHONPATH": str(studio), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if proc.returncode != 0:
            return "FAIL", (proc.stdout + proc.stderr)[-800:]
        return "PASS", proc.stdout.strip()

    case(report, id="pkg.imports", name="内嵌 Python 关键 import", group="出包", fn=pkg_imports)

    def pkg_reloc() -> tuple[str, str]:
        if not app:
            return "SKIP", "无 App"
        with tempfile.TemporaryDirectory(prefix="suying-reloc-") as td:
            dest = Path(td) / "速影.app"
            code, out = run(["ditto", str(app), str(dest)], timeout=300)
            if code != 0:
                return "FAIL", out[-400:]
            py = dest / "Contents/Resources/runtime/python/bin/python3"
            studio = dest / "Contents/Resources/runtime/studio"
            proc = subprocess.run(
                [str(py), "-c", "import encodings,fastapi,uvicorn; print('reloc-ok')"],
                cwd=str(studio),
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "PYTHONPATH": str(studio), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if proc.returncode != 0:
                return "FAIL", (proc.stdout + proc.stderr)[-500:]
            return "PASS", "换路径后 import 成功"

    case(report, id="pkg.reloc", name="换绝对路径可迁移", group="出包", fn=pkg_reloc)

    def pkg_kit_files() -> tuple[str, str]:
        if not kit:
            return "WARN", "产品套件目录未找到（可能仅有 macos 套件）"
        need = ["速影.app", "SHA256.txt"]
        missing = [n for n in need if not (kit / n).exists()]
        extra = []
        if not list(kit.glob("速影-*.dmg")) and not (kit / f"速影-{VERSION}.dmg").exists():
            extra.append("dmg")
        if missing:
            return "FAIL", "缺少 " + ",".join(missing)
        if extra:
            return "WARN", "缺少 " + ",".join(extra)
        return "PASS", str(kit)

    case(report, id="pkg.kit", name="套件交付文件", group="出包", fn=pkg_kit_files)

    # ---------- 1. Source smokes (逐项) ----------
    smoke_scripts = [
        ("smoke_test.py", "主冒烟"),
        ("smoke_hard_locks.py", "硬锁地板"),
        ("smoke_paper_slip.py", "纸片滚动避重"),
        ("smoke_ready_gate.py", "READY_GATE"),
        ("smoke_ops.py", "运维接口"),
        ("smoke_system_events.py", "系统暂停事件"),
        ("smoke_sync_portable.py", "同步可移植"),
        ("smoke_carrier.py", "载体更新"),
        ("smoke_remote_deploy.py", "远程部署门禁"),
        ("smoke_subtitle_align.py", "字幕对齐"),
        ("smoke_subtitle_margin.py", "字幕边距"),
        ("smoke_narration_breath.py", "旁白呼吸断句"),
        ("smoke_emoji_stickers.py", "表情贴纸"),
        ("smoke_sprint_a.py", "Sprint A"),
        ("smoke_reach_messages.py", "触达消息"),
        ("smoke_ollama_narration.py", "Ollama 旁白"),
        ("smoke_zero_fork.py", "零分叉"),
    ]
    for script, title in smoke_scripts:
        path = ROOT / "scripts" / script

        def make_fn(p: Path = path, t: str = title) -> Callable[[], tuple[str, str]]:
            def _fn() -> tuple[str, str]:
                if not p.is_file():
                    return "SKIP", "脚本不存在"
                code, out = run([sys.executable, str(p)], timeout=300)
                tail = out.strip().splitlines()[-8:] if out.strip() else []
                detail = " | ".join(tail)[-700:]
                if code != 0:
                    return "FAIL", detail or f"exit={code}"
                return "PASS", detail or "ok"

            return _fn

        case(report, id=f"smoke.{path.stem}", name=title, group="冒烟脚本", fn=make_fn())

    # ---------- 2. Desktop tsc ----------
    def tsc() -> tuple[str, str]:
        code, out = run(["npx", "tsc", "--noEmit"], cwd=ROOT / "apps/desktop", timeout=180)
        if code != 0:
            return "FAIL", out[-800:]
        return "PASS", "tsc clean"

    case(report, id="ui.tsc", name="桌面 TypeScript 无报错", group="桌面端", fn=tsc)

    # ---------- 3. Live engine API features ----------
    port = 8766
    # detect listening
    def port_open() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False

    engine_up = port_open()

    api_cases: list[tuple[str, str, str, str, dict | None]] = [
        ("api.health", "健康检查", "GET", "/health", None),
        ("api.settings", "设置读取", "GET", "/settings", None),
        ("api.jobs", "任务列表", "GET", "/jobs", None),
        ("api.pipeline", "管线状态", "GET", "/jobs/pipeline", None),
        ("api.assets", "素材列表", "GET", "/assets", None),
        ("api.outputs_review", "审片列表", "GET", "/outputs?state=review", None),
        ("api.reviews", "审核队列", "GET", "/reviews?scope=open&limit=20", None),
        ("api.vector", "向量化状态", "GET", "/vectorization/status", None),
        ("api.workspace", "工作区状态", "GET", "/workspace/status", None),
        ("api.ops_services", "运维服务", "GET", "/ops/services", None),
        ("api.reach_queue", "触达队列", "GET", "/reach/queue?status=ready", None),
        ("api.reach_alerts", "人工告警", "GET", "/reach/publish/human-alerts", None),
        ("api.messages", "触达消息", "GET", "/reach/messages", None),
        ("api.content_msgs", "内容消息", "GET", "/content/messages", None),
        ("api.logs", "操作日志", "GET", "/operation-logs?page=1&page_size=20", None),
        ("api.keywords", "词池摘要", "GET", "/keywords/active-summary", None),
        ("api.rules", "生产规则", "GET", "/production-rules?include_archived=false", None),
        ("api.reports", "运营报告", "GET", "/reports/ops", None),
        ("api.ollama", "Ollama 健康", "GET", "/health/ollama", None),
    ]

    for cid, name, method, path, body in api_cases:

        def make_api(
            c: str = cid,
            m: str = method,
            p: str = path,
            b: dict | None = body,
            up: bool = engine_up,
        ) -> Callable[[], tuple[str, str]]:
            def _fn() -> tuple[str, str]:
                if not up:
                    return "SKIP", "引擎未监听 8766"
                status, payload = http_json(m, f"http://127.0.0.1:{port}{p}", b)
                if status and 200 <= status < 300:
                    return "PASS", f"HTTP {status}"
                if status == 404 and p.startswith("/calendar"):
                    return "WARN", f"HTTP {status}（可接受）"
                return "FAIL", f"HTTP {status}: {str(payload)[:300]}"

            return _fn

        case(report, id=cid, name=name, group="引擎API", fn=make_api())

    def dry_run() -> tuple[str, str]:
        if not engine_up:
            return "SKIP", "引擎未监听"
        status, payload = http_json(
            "POST",
            f"http://127.0.0.1:{port}/dry-run",
            {"theme": "default", "customer_name": "北京始峰伟业"},
            timeout=120,
        )
        if status != 200:
            return "FAIL", f"HTTP {status}: {str(payload)[:400]}"
        blocked = payload.get("blocked")
        warns = payload.get("warnings") or []
        if blocked:
            return "FAIL", f"blocked=true reasons={payload.get('block_reasons')}"
        joined = " | ".join(str(w) for w in warns)
        if "周窗口" in joined or "配额已满" in joined or "日/周" in joined:
            return "FAIL", "仍出现满额文案: " + joined[:300]
        if "滚动避重" not in joined:
            return "WARN", "未看到滚动避重提示: " + joined[:300]
        return "PASS", f"blocked=false clips={len(payload.get('clips') or [])}"

    case(report, id="api.dry_run", name="Dry-run 生产规划", group="生产", fn=dry_run)

    def cors_preflight() -> tuple[str, str]:
        if not engine_up:
            return "SKIP", "引擎未监听"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/jobs",
            method="OPTIONS",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                allow = resp.headers.get("access-control-allow-origin", "")
                if resp.status in (200, 204) and allow:
                    return "PASS", f"status={resp.status} allow={allow}"
                return "WARN", f"status={resp.status} allow={allow!r}"
        except Exception as e:  # noqa: BLE001
            return "FAIL", str(e)

    case(report, id="api.cors", name="Tauri CORS 预检", group="引擎API", fn=cors_preflight)

    def paper_slip_consts() -> tuple[str, str]:
        code, out = run(
            [
                sys.executable,
                "-c",
                "from engine.catalog.paper_slip import assert_paper_slip_integrity, ROLLING_CLIPLET_STEPS;"
                "assert_paper_slip_integrity(); print(ROLLING_CLIPLET_STEPS)",
            ],
            timeout=30,
        )
        if code != 0:
            return "FAIL", out[-400:]
        return "PASS", out.strip()

    case(report, id="feat.paper_slip", name="纸片滚动避重常量", group="生产", fn=paper_slip_consts)

    # Manual / blocked by disk or reach policy
    case(
        report,
        id="manual.reach_publish",
        name="浏览器真机发布（七平台）",
        group="触达",
        fn=lambda: ("SKIP", "需人工真机；DEV_LOCK 禁开 Pack/Reach 主开发"),
    )
    case(
        report,
        id="manual.disk_g0",
        name="外置盘片库成片复检 G0",
        group="素材",
        fn=lambda: ("SKIP", "BLOCKED_DISK / 用户未插盘时不做"),
    )

    report.finished_at = datetime.now().isoformat(timespec="seconds")
    out_json = DESKTOP / f"速影-全量验收-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_json.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON report → {out_json}")
    summary = {}
    for c in report.cases:
        summary[c.status] = summary.get(c.status, 0) + 1
    print("SUMMARY", summary)
    # exit non-zero if any FAIL
    return 1 if summary.get("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
