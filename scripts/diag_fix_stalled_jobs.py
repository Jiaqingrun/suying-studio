#!/usr/bin/env python3
"""Diagnose stalled produce jobs on a customer Mac and repair common blockers.

Typical failure after 0.3.0 redeploy without clone TTS:
  circuit_open / 旁白未使用锁死阿姨慢速旁白 / f5-tts not installed

Usage (ops Mac, repo root):
  python3 scripts/diag_fix_stalled_jobs.py --host xlf-remote
  python3 scripts/diag_fix_stalled_jobs.py --host xlf-remote --fix
  python3 scripts/diag_fix_stalled_jobs.py --host xlf-remote --fix --resume-jobs

Local (on the customer Mac itself):
  python3 scripts/diag_fix_stalled_jobs.py --local
  python3 scripts/diag_fix_stalled_jobs.py --local --fix --resume-jobs
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API_DEFAULT = "http://127.0.0.1:8766"
APP_REL = "Applications/速影 Studio.app"
BACKUP_GLOB = "Suying/backups/apps"


def _http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 20) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Origin": "http://tauri.localhost"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def _ssh(host: str, script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", host, "bash", "-s"],
        input=script,
        text=True,
        capture_output=True,
        check=check,
    )


def _remote_json(host: str, script: str) -> Any:
    result = _ssh(host, script)
    text = result.stdout.strip()
    if not text:
        raise RuntimeError(f"empty remote output\nstderr={result.stderr}")
    # Last JSON object/array in stdout
    lines = [ln for ln in text.splitlines() if ln.strip().startswith(("{", "["))]
    if not lines:
        raise RuntimeError(f"no JSON in remote output:\n{text[-2000:]}\nstderr={result.stderr}")
    return json.loads(lines[-1])


def discover_paths_local() -> dict[str, Any]:
    home = Path.home()
    # Prefer the install actually shipping the live engine when possible.
    candidates_app = [
        Path("/Applications/速影 Studio.app"),
        home / APP_REL,
    ]
    app = next((p for p in candidates_app if p.is_dir()), Path("/Applications/速影 Studio.app"))
    # If two installs exist, prefer the one whose python currently holds :8766.
    try:
        listeners = subprocess.check_output(
            ["lsof", "-t", "-iTCP:8766", "-sTCP:LISTEN"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        for pid in listeners.split():
            try:
                cmd = subprocess.check_output(["ps", "-p", pid, "-o", "command="], text=True).strip()
            except subprocess.CalledProcessError:
                continue
            for cand in candidates_app:
                marker = str(cand / "Contents/Resources/runtime/python")
                if marker in cmd and cand.is_dir():
                    app = cand
                    break
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    runtime = app / "Contents/Resources/runtime"
    python = runtime / "python/bin/python3"
    candidates: list[Path] = []
    backup_root = home / BACKUP_GLOB
    if backup_root.is_dir():
        candidates.extend(p for p in backup_root.iterdir() if p.is_dir())
    # Sidecar backups next to the installed App (e.g. *.preclose-*).
    for parent in {app.parent, Path("/Applications")}:
        if not parent.is_dir():
            continue
        for p in parent.iterdir():
            if not p.is_dir():
                continue
            name = p.name
            if name == app.name:
                continue
            if "速影" in name and (".app" in name or name.endswith(".app")):
                candidates.append(p)
    backups = sorted(
        [
            p
            for p in candidates
            if (p / "Contents/Resources/runtime/python/bin/python3").exists()
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    f5_backup = None
    for backup in backups:
        py = backup / "Contents/Resources/runtime/python/bin/python3"
        if not py.is_file():
            continue
        probe = subprocess.run(
            [str(py), "-c", "import importlib.util; print(int(bool(importlib.util.find_spec('f5_tts'))))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.stdout.strip() == "1":
            f5_backup = backup
            break
    return {
        "home": str(home),
        "app": str(app),
        "runtime": str(runtime),
        "python": str(python),
        "f5_backup": str(f5_backup) if f5_backup else None,
    }


REMOTE_DISCOVER = r'''
set +e
HOME_DIR="$HOME"
APP="$HOME_DIR/Applications/速影 Studio.app"
[ -d "$APP" ] || APP="/Applications/速影 Studio.app"
RT="$APP/Contents/Resources/runtime"
PY="$RT/python/bin/python3"
F5_BK=""
# Backups are named like 速影-Studio.app.<UTC> (not always ending in .app)
while IFS= read -r cand; do
  [ -n "$cand" ] || continue
  CPY="$cand/Contents/Resources/runtime/python/bin/python3"
  [ -x "$CPY" ] || continue
  if "$CPY" -c 'import importlib.util as u; import sys; sys.exit(0 if u.find_spec("f5_tts") else 1)' 2>/dev/null; then
    F5_BK="$cand"
    break
  fi
done < <(ls -1dt "$HOME_DIR/Suying/backups/apps"/* 2>/dev/null)
HAS_F5=0
if [ -x "$PY" ]; then
  "$PY" -c 'import importlib.util as u; import sys; sys.exit(0 if u.find_spec("f5_tts") else 1)' 2>/dev/null && HAS_F5=1
fi
export HOME_DIR APP RT PY F5_BK HAS_F5
python3 - <<'PY'
import json, os
print(json.dumps({
  "home": os.environ["HOME_DIR"],
  "app": os.environ["APP"],
  "runtime": os.environ["RT"],
  "python": os.environ["PY"],
  "f5_backup": os.environ.get("F5_BK") or None,
  "has_f5": os.environ.get("HAS_F5") == "1",
}, ensure_ascii=False))
PY
'''


def diagnose_via_api(api: str) -> dict[str, Any]:
    health = _http_json(f"{api}/health")
    jobs = _http_json(f"{api}/jobs?limit=30")
    voice = None
    try:
        voice = _http_json(f"{api}/voice/tts")
    except Exception as exc:  # noqa: BLE001
        voice = {"error": str(exc)}
    stalled = [
        j
        for j in (jobs or [])
        if str(j.get("status") or "") in {"circuit_open", "paused", "failed", "paused_system"}
        and int(j.get("produced_count") or 0) < int(j.get("target_count") or 1)
    ]
    return {
        "health": {
            "status": health.get("status"),
            "runtime_state": health.get("runtime_state"),
            "worker_running": health.get("worker_running"),
            "active_customer": health.get("active_customer"),
            "path_health_ok": (health.get("path_health") or {}).get("ok"),
            "tts_provider_settings": (health.get("paths") and None),
        },
        "voice": voice,
        "stalled_jobs": [
            {
                "id": j.get("id"),
                "status": j.get("status"),
                "produced": j.get("produced_count"),
                "target": j.get("target_count"),
                "fails": j.get("consecutive_failures"),
                "theme": j.get("theme"),
                "last_error": j.get("last_error"),
            }
            for j in stalled[:20]
        ],
        "recent_jobs": [
            {
                "id": j.get("id"),
                "status": j.get("status"),
                "produced": j.get("produced_count"),
                "target": j.get("target_count"),
                "fails": j.get("consecutive_failures"),
                "theme": j.get("theme"),
            }
            for j in (jobs or [])[:10]
        ],
    }


REMOTE_EVENTS = r'''
JOB_IDS="%JOB_IDS%"
sqlite3 "$HOME/Suying/data/montage.db" <<SQL
.mode json
SELECT job_id, level, message, substr(payload_json,1,240) AS payload, created_at
FROM job_events
WHERE job_id IN (%JOB_IDS%)
ORDER BY id DESC
LIMIT 30;
SQL
'''


def collect_events(host: str | None, job_ids: list[int]) -> list[dict[str, Any]]:
    if not job_ids:
        return []
    ids = ",".join(str(i) for i in job_ids)
    if host:
        script = REMOTE_EVENTS.replace("%JOB_IDS%", ids)
        result = _ssh(host, script, check=False)
        text = result.stdout.strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []
    db = Path.home() / "Suying/data/montage.db"
    if not db.is_file():
        return []
    out = subprocess.check_output(
        [
            "sqlite3",
            str(db),
            "-json",
            f"SELECT job_id, level, message, substr(payload_json,1,240) AS payload, created_at "
            f"FROM job_events WHERE job_id IN ({ids}) ORDER BY id DESC LIMIT 30;",
        ],
        text=True,
    )
    return json.loads(out) if out.strip() else []


def classify(diag: dict[str, Any], events: list[dict[str, Any]], paths: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    blob = json.dumps(events, ensure_ascii=False)
    voice = diag.get("voice") or {}
    if "f5-tts not installed" in blob or "Clone TTS required" in blob:
        reasons.append("missing_f5_tts")
    if voice.get("clone_available") is False or (
        voice.get("effective_provider") == "clone" and not paths.get("has_f5")
    ):
        if "missing_f5_tts" not in reasons:
            reasons.append("missing_f5_tts")
    if not paths.get("f5_backup") and "missing_f5_tts" in reasons:
        reasons.append("no_f5_backup_app")
    if not diag.get("stalled_jobs"):
        reasons.append("no_stalled_jobs")
    return {
        "reasons": reasons,
        "can_restore_f5_from_backup": bool(paths.get("f5_backup")) and "missing_f5_tts" in reasons,
        "summary": (
            "VIDEO_LOCK/settings 要求 clone（阿姨慢速旁白），但当前一体包 Python 未内嵌 f5-tts；"
            "任务连续失败后熔断。"
            if "missing_f5_tts" in reasons
            else "未识别到 f5-tts 缺失；请查看 job_events。"
        ),
    }


def pause_jobs(api: str, job_ids: list[int]) -> None:
    for job_id in job_ids:
        try:
            _http_json(f"{api}/jobs/{job_id}/pause", method="POST", body={})
            print(f"  paused job #{job_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"  pause job #{job_id} skipped: {exc}")


def resume_jobs(api: str, job_ids: list[int]) -> None:
    for job_id in job_ids:
        try:
            row = _http_json(f"{api}/jobs/{job_id}/resume", method="POST", body={})
            print(f"  resumed job #{job_id} -> {row}")
        except Exception as exc:  # noqa: BLE001
            print(f"  resume job #{job_id} failed: {exc}")


def stop_app_remote(host: str) -> None:
    _ssh(
        host,
        "killall appsdesktop 2>/dev/null || true; "
        "pkill -f 'python3 -m engine.main' 2>/dev/null || true; "
        "sleep 2; "
        "if lsof -iTCP:8766 -sTCP:LISTEN >/dev/null 2>&1; then "
        "kill $(lsof -t -iTCP:8766 -sTCP:LISTEN) 2>/dev/null || true; fi",
        check=False,
    )


def stop_app_local() -> None:
    subprocess.run(["killall", "appsdesktop"], check=False, capture_output=True)
    subprocess.run(["pkill", "-f", "python3 -m engine.main"], check=False, capture_output=True)


def sync_hf_model_caches(*, host: str) -> None:
    """Copy F5-TTS / Vocos weights from ops Mac HF cache to the customer."""
    hub = Path.home() / ".cache/huggingface/hub"
    needed = [
        hub / "models--SWivid--F5-TTS",
        hub / "models--charactr--vocos-mel-24khz",
    ]
    missing = [str(p) for p in needed if not p.is_dir()]
    if missing:
        raise RuntimeError(
            "运维机缺少 F5/Vocos 模型缓存，无法离线同步: " + ", ".join(missing)
        )
    _ssh(host, "mkdir -p ~/.cache/huggingface/hub ~/Library/Caches/huggingface/hub")
    for target in ("~/.cache/huggingface/hub/", "~/Library/Caches/huggingface/hub/"):
        cmd = [
            "rsync",
            "-a",
            *[str(p) for p in needed],
            f"{host}:{target}",
        ]
        print("==> sync HF caches ->", target)
        subprocess.run(cmd, check=True)
    # Prefer offline after caches land.
    _ssh(
        host,
        r"""
set -e
mkdir -p "$HOME/Suying/runtime"
ENVF="$HOME/Suying/runtime/local.env"
touch "$ENVF"
grep -q '^HF_HUB_OFFLINE=' "$ENVF" 2>/dev/null || echo 'HF_HUB_OFFLINE=1' >> "$ENVF"
grep -q '^TRANSFORMERS_OFFLINE=' "$ENVF" 2>/dev/null || echo 'TRANSFORMERS_OFFLINE=1' >> "$ENVF"
grep -q '^HF_HOME=' "$ENVF" 2>/dev/null || echo "HF_HOME=$HOME/.cache/huggingface" >> "$ENVF"
""",
        check=False,
    )


def restore_f5_python(*, host: str | None, app: str, backup: str, merge: bool = True) -> None:
    """LEGACY emergency only: Restore F5-TTS into App runtime python.

    **Not the delivery path.** Merging into a signed App is wiped on the next core
    overwrite and requires RUNTIME_MANIFEST resign. Prefer App-external F5 Runtime Kit:

      scripts/package-f5-runtime-kit.sh
      scripts/install-f5-runtime-kit.sh
      docs/VOICE_CLONE.md (core App + overlay)

    Default *merge* copies only missing site-packages from a clone-capable backup into
    the current core runtime (keeps 0.6+/0.7 core deps). Full ``--delete`` replace is
    available when both trees are the same clone flavor.
    """
    print(
        "WARN: LEGACY App-tree F5 merge — next core install will lose it; "
        "prefer install-f5-runtime-kit.sh",
        file=sys.stderr,
    )
    if merge:
        src_sp = f"{backup}/Contents/Resources/runtime/python/lib/python3.12/site-packages"
        dst_sp = f"{app}/Contents/Resources/runtime/python/lib/python3.12/site-packages"
        print(f"==> merge F5 site-packages from backup\n    {src_sp}\n -> {dst_sp}")
        py_merge = r"""
import os, subprocess
from pathlib import Path
src, dst = Path(os.environ["SRC"]), Path(os.environ["DST"])
only = sorted({p.name for p in src.iterdir()} - {p.name for p in dst.iterdir()})
print(f"packages_to_add={len(only)}")
for name in only:
    subprocess.check_call(["rsync", "-a", str(src / name), str(dst) + "/"])
# F5 stack (numba/librosa) needs NumPy<=2.4; core venv may ship 2.5+.
# Overwrite only the versions that must match the clone pack.
for name in sorted(src.iterdir()):
    n = name.name
    if n == "numpy" or n.startswith("numpy-") or n == "numpy.libs" or n == "numba" or n.startswith("numba-"):
        if (dst / n).exists() or name.is_dir() or name.suffix:
            target = dst / n
            if target.exists():
                subprocess.check_call(["rm", "-rf", str(target)])
            subprocess.check_call(["rsync", "-a", str(name), str(dst) + "/"])
            print(f"pin_from_backup={n}")
"""
        if host:
            _ssh(
                host,
                f"set -euo pipefail\n"
                f"export SRC={shlex.quote(src_sp)}\n"
                f"export DST={shlex.quote(dst_sp)}\n"
                f"python3 - <<'PY'\n{py_merge}PY\n",
            )
        else:
            env = os.environ.copy()
            env["SRC"] = src_sp
            env["DST"] = dst_sp
            subprocess.run(["python3", "-c", py_merge], check=True, env=env)
        flavor_path = f"{app}/Contents/Resources/runtime/BUNDLE_FLAVOR"
        if host:
            _ssh(host, f"printf 'clone\\n' > {shlex.quote(flavor_path)}")
        else:
            Path(flavor_path).write_text("clone\n", encoding="utf-8")
        return

    src = f"{backup}/Contents/Resources/runtime/python/"
    dst = f"{app}/Contents/Resources/runtime/python/"
    cmd = f"rsync -a --delete {shlex.quote(src)} {shlex.quote(dst)}"
    print(f"==> restore python runtime from backup\n    {cmd}")
    if host:
        _ssh(host, cmd)
    else:
        subprocess.run(["bash", "-lc", cmd], check=True)


def resign_runtime(*, host: str | None, runtime: str, bundle_version: str = "0.3.0") -> None:
    from engine.security.update_manifest import (
        RuntimeFile,
        RuntimeManifest,
        public_key_id,
        write_signed_document,
    )
    from scripts.build_runtime_manifest import build_manifest
    from scripts.sign_update_release import load_keychain_private_key

    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    private_key = load_keychain_private_key("release-v1")

    if host:
        hash_script = f"""
set -euo pipefail
RT={shlex.quote(runtime)}
python3 - "$RT" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
files = []
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(root).as_posix()
    if rel in ("RUNTIME_MANIFEST.json", "RUNTIME_MANIFEST.json.sig"):
        continue
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    files.append({{"path": rel, "size": path.stat().st_size, "sha256": digest.hexdigest()}})
print(json.dumps({{"files": files}}, ensure_ascii=False))
PY
"""
        print("==> hashing remote runtime (may take several minutes)")
        result = _ssh(host, hash_script)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        manifest = RuntimeManifest(
            bundle_version=bundle_version,
            arch=arch,
            created_at=datetime.now(timezone.utc),
            key_id=public_key_id(private_key.public_key()),
            files=[RuntimeFile(**item) for item in payload["files"]],
        )
        with tempfile.TemporaryDirectory(prefix="suying-runtime-resign-") as tmp:
            tmp_path = Path(tmp)
            man_path, sig_path = write_signed_document(
                manifest,
                private_key=private_key,
                path=tmp_path / "RUNTIME_MANIFEST.json",
            )
            remote_man = f"{runtime}/RUNTIME_MANIFEST.json"
            remote_sig = f"{runtime}/RUNTIME_MANIFEST.json.sig"
            subprocess.run(
                ["scp", "-o", "BatchMode=yes", str(man_path), f"{host}:{remote_man}"],
                check=True,
            )
            subprocess.run(
                ["scp", "-o", "BatchMode=yes", str(sig_path), f"{host}:{remote_sig}"],
                check=True,
            )
            print(f"==> wrote signed manifest ({len(manifest.files)} files)")
        return

    manifest = build_manifest(
        Path(runtime),
        bundle_version=bundle_version,
        arch=arch,
        private_key=private_key,
    )
    path, sig = write_signed_document(
        manifest,
        private_key=private_key,
        path=Path(runtime) / "RUNTIME_MANIFEST.json",
    )
    print(f"==> wrote {path}\n    {sig}")


def open_app(*, host: str | None, app: str) -> None:
    if host:
        _ssh(host, f"open {shlex.quote(app)}")
    else:
        subprocess.run(["open", app], check=False)


def wait_health(api: str, *, attempts: int = 90) -> bool:
    import time

    for i in range(1, attempts + 1):
        try:
            health = _http_json(f"{api}/health", timeout=3)
            if health.get("status") == "ok":
                print(f"==> health ok after {i}s")
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return False


def verify_clone(api: str, *, host: str | None, python: str) -> dict[str, Any]:
    voice = _http_json(f"{api}/voice/tts")
    if host:
        probe = _ssh(
            host,
            f"{shlex.quote(python)} -c 'import importlib.util as u; print(int(bool(u.find_spec(\"f5_tts\"))))'",
        )
        has_f5 = probe.stdout.strip() == "1"
    else:
        probe = subprocess.run(
            [python, "-c", "import importlib.util as u; print(int(bool(u.find_spec('f5_tts'))))"],
            capture_output=True,
            text=True,
            check=False,
        )
        has_f5 = probe.stdout.strip() == "1"
    return {"voice": voice, "has_f5": has_f5}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="SSH alias, e.g. xlf-remote")
    parser.add_argument("--local", action="store_true", help="Run against this Mac")
    parser.add_argument("--api", default=API_DEFAULT)
    parser.add_argument("--fix", action="store_true", help="Restore f5-tts python from backup + resign")
    parser.add_argument("--resume-jobs", action="store_true", help="Resume stalled jobs after fix")
    parser.add_argument("--bundle-version", default="0.3.0")
    args = parser.parse_args()
    if not args.host and not args.local:
        parser.error("specify --host or --local")

    host = None if args.local else args.host
    print("==> discover paths")
    if host:
        paths = _remote_json(host, REMOTE_DISCOVER)
    else:
        paths = discover_paths_local()
        py = paths["python"]
        probe = subprocess.run(
            [py, "-c", "import importlib.util as u; print(int(bool(u.find_spec('f5_tts'))))"],
            capture_output=True,
            text=True,
            check=False,
        )
        paths["has_f5"] = probe.stdout.strip() == "1"
    print(json.dumps(paths, ensure_ascii=False, indent=2))

    print("==> diagnose API")
    if host:
        # Prefer hitting API on the remote host itself.
        diag = _remote_json(
            host,
            f"""
set -e
python3 - <<'PY'
import json, urllib.request
api={args.api!r}
def get(path):
    req=urllib.request.Request(api+path, headers={{"Origin":"http://tauri.localhost"}})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())
health=get("/health")
jobs=get("/jobs?limit=30")
try:
    voice=get("/voice/tts")
except Exception as e:
    voice={{"error": str(e)}}
stalled=[j for j in jobs if j.get("status") in ("circuit_open","paused","failed","paused_system") and int(j.get("produced_count") or 0) < int(j.get("target_count") or 1)]
print(json.dumps({{
  "health": {{"status": health.get("status"), "runtime_state": health.get("runtime_state"), "worker_running": health.get("worker_running"), "active_customer": health.get("active_customer"), "path_health_ok": (health.get("path_health") or {{}}).get("ok")}},
  "voice": voice,
  "stalled_jobs": [{{"id":j.get("id"),"status":j.get("status"),"produced":j.get("produced_count"),"target":j.get("target_count"),"fails":j.get("consecutive_failures"),"theme":j.get("theme"),"last_error":j.get("last_error")}} for j in stalled[:20]],
  "recent_jobs": [{{"id":j.get("id"),"status":j.get("status"),"produced":j.get("produced_count"),"target":j.get("target_count"),"fails":j.get("consecutive_failures"),"theme":j.get("theme")}} for j in jobs[:10]],
}}, ensure_ascii=False))
PY
""",
        )
    else:
        diag = diagnose_via_api(args.api)
    print(json.dumps(diag, ensure_ascii=False, indent=2))

    job_ids = [int(j["id"]) for j in diag.get("stalled_jobs") or [] if j.get("id") is not None]
    events = collect_events(host, job_ids[:5] or [int(j["id"]) for j in (diag.get("recent_jobs") or [])[:3] if j.get("id")])
    print("==> recent job_events")
    print(json.dumps(events[:12], ensure_ascii=False, indent=2))
    verdict = classify(diag, events, paths)
    print("==> verdict")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    if not args.fix:
        print("\n只读诊断完成。若 reasons 含 missing_f5_tts，请加 --fix --resume-jobs")
        return 0 if "missing_f5_tts" not in verdict["reasons"] else 2

    if not verdict.get("can_restore_f5_from_backup"):
        print("ERROR: 无法自动修复（缺少含 f5-tts 的备份 App，或并非 f5 问题）", file=sys.stderr)
        return 3

    print("==> pause stalled jobs")
    if host:
        ids = " ".join(str(i) for i in job_ids)
        _ssh(
            host,
            f"""
for id in {ids}; do
  curl -fsS -X POST {shlex.quote(args.api)}/jobs/$id/pause -H 'Content-Type: application/json' -d '{{}}' || true
done
""",
            check=False,
        )
    else:
        pause_jobs(args.api, job_ids)

    print("==> stop App/engine")
    if host:
        stop_app_remote(host)
    else:
        stop_app_local()

    restore_f5_python(host=host, app=paths["app"], backup=paths["f5_backup"])
    if host:
        try:
            sync_hf_model_caches(host=host)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: HF 模型缓存同步失败（若客户机可访问 HuggingFace 可忽略）: {exc}")
    resign_runtime(host=host, runtime=paths["runtime"], bundle_version=args.bundle_version)

    print("==> reopen App")
    open_app(host=host, app=paths["app"])
    if host:
        wait = _ssh(
            host,
            f"""
set +e
ok=0
for i in $(seq 1 120); do
  if curl -fsS -m 2 {shlex.quote(args.api)}/health >/tmp/suying-health.json 2>/dev/null; then
    if python3 -c 'import json,sys; d=json.load(open("/tmp/suying-health.json")); sys.exit(0 if d.get("status")=="ok" else 1)'; then
      ok=1
      echo "health_ok_$i"
      break
    fi
  fi
  sleep 1
done
exit $((1-ok))
""",
            check=False,
        )
        ok = wait.returncode == 0
        if ok:
            print("==> health ok")
        else:
            print(wait.stdout[-500:], wait.stderr[-500:])
    else:
        ok = wait_health(args.api)
    if not ok:
        print("ERROR: App/engine 未恢复 health", file=sys.stderr)
        return 4

    if host:
        verified = _remote_json(
            host,
            f"""
set -e
PY={shlex.quote(paths["python"])}
API={shlex.quote(args.api)}
HAS=$("$PY" -c 'import importlib.util as u; print(int(bool(u.find_spec("f5_tts"))))')
curl -fsS -H 'Origin: http://tauri.localhost' "$API/voice/tts" > /tmp/suying-voice.json
HAS="$HAS" python3 - <<'PY'
import json, os
voice = json.load(open("/tmp/suying-voice.json"))
print(json.dumps({{"has_f5": bool(int(os.environ["HAS"])), "voice": voice}}, ensure_ascii=False))
PY
""",
        )
    else:
        verified = verify_clone(args.api, host=None, python=paths["python"])
    print("==> verify", json.dumps(verified, ensure_ascii=False))
    if not verified.get("has_f5"):
        print("ERROR: 修复后仍检测不到 f5_tts", file=sys.stderr)
        return 5

    if args.resume_jobs and job_ids:
        print("==> resume stalled jobs")
        if host:
            ids = " ".join(str(i) for i in job_ids)
            out = _ssh(
                host,
                f"""
for id in {ids}; do
  curl -fsS -X POST {shlex.quote(args.api)}/jobs/$id/resume -H 'Content-Type: application/json' -d '{{}}' || true
  echo
done
curl -fsS '{args.api}/jobs?limit=8'
""",
                check=False,
            )
            print(out.stdout[-1500:])
        else:
            resume_jobs(args.api, job_ids)

    print("==> DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
