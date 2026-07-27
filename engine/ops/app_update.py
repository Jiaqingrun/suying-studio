"""App update from T2S carrier mirror (latest.json + package)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from engine.ops.carrier import discover_carrier_roots, read_latest_manifest


def current_app_version() -> str:
    # Prefer explicit app process version, then immutable bundled metadata.
    if os.environ.get("SUYING_APP_VERSION"):
        return os.environ["SUYING_APP_VERSION"]
    bundled_version = Path(__file__).resolve().parents[3] / "BUNDLE_VERSION"
    if bundled_version.is_file():
        version = bundled_version.read_text(encoding="utf-8").strip()
        if version:
            return version
    for cand in (
        Path.home() / "QR" / "dev" / "montage-studio" / "apps" / "desktop" / "package.json",
        Path(__file__).resolve().parents[2] / "apps" / "desktop" / "package.json",
    ):
        if cand.is_file():
            try:
                return str(json.loads(cand.read_text(encoding="utf-8")).get("version") or "0.0.0")
            except Exception:
                pass
    return "0.0.0"


def _parse_ver(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in str(v or "0").split("."):
        try:
            parts.append(int("".join(ch for ch in p if ch.isdigit()) or "0"))
        except ValueError:
            parts.append(0)
    return tuple(parts or (0,))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_app_bundle(app: Path) -> None:
    """Require a structurally valid 速影 bundle with the expected identifier."""
    if not app.is_dir():
        raise ValueError(f"更新包内缺少 App: {app}")
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )
    info = subprocess.run(
        ["codesign", "-dv", "--verbose=4", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )
    details = f"{info.stdout}\n{info.stderr}"
    if "Identifier=com.qr.suying" not in details:
        raise ValueError("更新 App bundle identifier 不匹配")
    team_file = Path(__file__).resolve().parents[3] / "BUNDLE_TEAM_ID"
    expected_team = team_file.read_text(encoding="utf-8").strip() if team_file.is_file() else ""
    if not expected_team:
        if os.environ.get("SUYING_ALLOW_ADHOC_UPDATE") != "1":
            raise ValueError("当前安装包未使用 Developer ID 签名，安全起见禁止自动替换 App")
        return
    if f"TeamIdentifier={expected_team}" not in details:
        raise ValueError("更新 App 签名团队与当前安装包不一致")


def _install_app_atomically(source: Path, destination: Path) -> None:
    """Stage, verify, then swap; restore the previous App if replacement fails."""
    parent = destination.parent
    staged = parent / f".{destination.name}.staged-{os.getpid()}"
    backup = parent / f".{destination.name}.backup-{os.getpid()}"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    moved_old = False
    try:
        shutil.copytree(source, staged, symlinks=True)
        _verify_app_bundle(staged)
        if destination.exists():
            destination.rename(backup)
            moved_old = True
        staged.rename(destination)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        if moved_old and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise


def check_update() -> dict[str, Any]:
    man = read_latest_manifest()
    cur = current_app_version()
    if not man:
        return {
            "ok": True,
            "update_available": False,
            "current_version": cur,
            "reason": "carrier_manifest_missing",
            "carrier_roots": [str(r) for r in discover_carrier_roots()],
        }
    remote = str(man.get("version") or "")
    force = bool(man.get("force"))
    newer = _parse_ver(remote) > _parse_ver(cur) if remote else False
    return {
        "ok": True,
        "update_available": newer or (force and remote != cur),
        "force": force and (newer or remote != cur),
        "current_version": cur,
        "remote_version": remote,
        "notes": man.get("notes") or "",
        "package": man.get("package") or man.get("dmg") or "",
        "sha256": man.get("sha256") or "",
        "carrier_root": man.get("_carrier_root"),
        "manifest_path": man.get("_manifest_path"),
    }


def install_update(*, apply: bool = False) -> dict[str, Any]:
    """Verify package sha256; if apply, open/install DMG or copy .app.

    Requires explicit apply=True (click to install). Never silent.
    """
    info = check_update()
    if not info.get("update_available"):
        return {**info, "installed": False, "error": "没有可用更新"}
    root = Path(str(info.get("carrier_root") or ""))
    pkg_name = str(info.get("package") or "")
    if not root.is_dir() or not pkg_name:
        return {**info, "installed": False, "error": "载体或 package 字段缺失"}
    pkg_name_path = Path(pkg_name)
    if pkg_name_path.is_absolute() or ".." in pkg_name_path.parts or pkg_name_path.name != pkg_name:
        return {**info, "installed": False, "error": "package 必须是载体 app 目录内的文件名"}
    pkg = root / "app" / pkg_name
    if not pkg.is_file():
        return {**info, "installed": False, "error": f"安装包不存在: {pkg}"}
    expect = str(info.get("sha256") or "").strip().lower()
    if not expect:
        return {**info, "installed": False, "error": "更新清单缺少 sha256，拒绝安装"}
    got = sha256_file(pkg).lower()
    if got != expect:
        return {
            **info,
            "installed": False,
            "error": f"sha256 不匹配 expect={expect[:12]}… got={got[:12]}…",
        }
    if not apply:
        return {**info, "installed": False, "verified": True, "package_path": str(pkg)}

    # Install
    applications = Path("/Applications") / "速影.app"
    try:
        if pkg.suffix.lower() == ".dmg":
            with tempfile.TemporaryDirectory(prefix="suying-update-mount-") as td:
                mount = Path(td) / "volume"
                mount.mkdir()
                subprocess.run(
                    [
                        "hdiutil",
                        "attach",
                        "-nobrowse",
                        "-quiet",
                        "-mountpoint",
                        str(mount),
                        str(pkg),
                    ],
                    check=True,
                    capture_output=True,
                )
                try:
                    _install_app_atomically(mount / "速影.app", applications)
                finally:
                    subprocess.run(
                        ["hdiutil", "detach", str(mount), "-quiet"],
                        check=False,
                        capture_output=True,
                    )
        elif pkg.suffix.lower() == ".zip":
            import zipfile

            with tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                with zipfile.ZipFile(pkg, "r") as zf:
                    for member in zf.infolist():
                        target = (root / member.filename).resolve()
                        if not target.is_relative_to(root):
                            raise ValueError("zip 包含路径穿越条目")
                    zf.extractall(td)
                found = next(Path(td).rglob("速影.app"), None)
                if not found:
                    return {**info, "installed": False, "error": "zip 内未找到 速影.app"}
                _install_app_atomically(found, applications)
        else:
            return {**info, "installed": False, "error": f"不支持的包类型: {pkg.suffix}"}
    except Exception as e:  # noqa: BLE001
        return {**info, "installed": False, "error": str(e)}

    # Relaunch
    try:
        subprocess.Popen(["open", "-n", str(applications)])
    except Exception:
        pass
    return {
        **info,
        "installed": True,
        "verified": True,
        "package_path": str(pkg),
        "app_path": str(applications),
        "relaunched": True,
    }


def write_latest_manifest(
    carrier_root: Path,
    *,
    version: str,
    package: str,
    sha256: str,
    force: bool = False,
    notes: str = "",
) -> Path:
    app_dir = Path(carrier_root) / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    path = app_dir / "latest.json"
    path.write_text(
        json.dumps(
            {
                "version": version,
                "package": package,
                "sha256": sha256,
                "force": bool(force),
                "notes": notes or "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
