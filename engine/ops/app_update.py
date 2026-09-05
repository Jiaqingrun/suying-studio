"""App update from T2S carrier mirror (latest.json + package)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from engine.ops.carrier import discover_carrier_roots
from engine.security.license import verify_license_file
from engine.security.update_manifest import (
    DEFAULT_UPDATE_STATE,
    ReleaseManifest,
    UpdateState,
    record_installed_release,
    sha256_file,
    verify_latest,
    verify_release,
)

DEFAULT_LICENSE_PATH = (
    Path.home() / "Suying" / "runtime" / "security" / "license.suying-license"
)


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
        Path.home() / "QR" / "dev" / "速影" / "apps" / "desktop" / "package.json",
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


def _verify_app_bundle(app: Path, *, trusted_release: bool = False) -> None:
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
        if not trusted_release:
            raise ValueError("ad-hoc App 必须来自已验证的速影签名 release")
        return
    if f"TeamIdentifier={expected_team}" not in details:
        raise ValueError("更新 App 签名团队与当前安装包不一致")


def _install_app_atomically(
    source: Path,
    destination: Path,
    *,
    trusted_release: bool = False,
    after_swap: Callable[[], None] | None = None,
) -> None:
    """Stage, verify, then swap; restore the previous App if replacement fails."""
    parent = destination.parent
    staged = parent / f".{destination.name}.staged-{os.getpid()}"
    backup = parent / f".{destination.name}.backup-{os.getpid()}"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    moved_old = False
    try:
        shutil.copytree(source, staged, symlinks=True)
        _verify_app_bundle(staged, trusted_release=trusted_release)
        if destination.exists():
            destination.rename(backup)
            moved_old = True
        staged.rename(destination)
        if after_swap:
            after_swap()
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        if moved_old and backup.exists():
            shutil.rmtree(destination, ignore_errors=True)
            backup.rename(destination)
        raise


def _verified_update(
    *,
    state_path: Path | None = DEFAULT_UPDATE_STATE,
    license_path: Path | None = DEFAULT_LICENSE_PATH,
) -> tuple[dict[str, Any] | None, str | None]:
    errors: list[str] = []
    expected_delivery_id: str | None = None
    if license_path and license_path.is_file():
        expected_delivery_id = verify_license_file(license_path).delivery_id
    for root in discover_carrier_roots():
        app_root = (Path(root) / "app").resolve()
        latest_path = app_root / "latest.json"
        latest_sig = app_root / "latest.json.sig"
        if not latest_path.is_file():
            continue
        if not latest_sig.is_file():
            errors.append(f"{latest_path}: 缺少 latest.json.sig")
            continue
        try:
            pointer, _ = verify_latest(latest_path, latest_sig)
            release_path = (app_root / pointer.release_path).resolve()
            if not release_path.is_relative_to(app_root):
                raise ValueError("release_path 越出载体 app 目录")
            release_sig = release_path.with_name(f"{release_path.name}.sig")
            if sha256_file(release_path) != pointer.release_sha256:
                raise ValueError("release.json 摘要与 latest 指针不匹配")
            manifest, release_digest = verify_release(
                release_path,
                release_sig,
                expected_arch=platform.machine(),
                expected_delivery_id=expected_delivery_id,
                state_path=state_path,
            )
            if manifest.release_seq != pointer.release_seq:
                raise ValueError("latest 与 release 的序号不一致")
            package_path = (release_path.parent / manifest.artifact.path).resolve()
            if not package_path.is_relative_to(app_root):
                raise ValueError("artifact 越出载体 app 目录")
            return (
                {
                    "manifest": manifest,
                    "release_digest": release_digest,
                    "carrier_root": Path(root),
                    "latest_path": latest_path,
                    "release_path": release_path,
                    "package_path": package_path,
                },
                None,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{latest_path}: {exc}")
    return None, ("；".join(errors) if errors else None)


def check_update() -> dict[str, Any]:
    verified, verify_error = _verified_update()
    cur = current_app_version()
    if not verified:
        return {
            "ok": verify_error is None,
            "update_available": False,
            "current_version": cur,
            "reason": "manifest_untrusted" if verify_error else "carrier_manifest_missing",
            "error": verify_error,
            "carrier_roots": [str(r) for r in discover_carrier_roots()],
        }
    manifest: ReleaseManifest = verified["manifest"]
    remote = manifest.version
    newer = _parse_ver(remote) > _parse_ver(cur) if remote else False
    installed_seq = 0
    if DEFAULT_UPDATE_STATE.is_file():
        try:
            installed_seq = UpdateState.model_validate_json(
                DEFAULT_UPDATE_STATE.read_bytes()
            ).highest_release_seq
        except Exception:
            installed_seq = 0
    same_version_new_release = remote == cur and manifest.release_seq > installed_seq
    return {
        "ok": True,
        "update_available": newer or same_version_new_release,
        "force": False,
        "current_version": cur,
        "remote_version": remote,
        "release_seq": manifest.release_seq,
        "release_digest": verified["release_digest"],
        "key_id": manifest.key_id,
        "delivery_id": manifest.delivery_id,
        "notes": manifest.notes,
        "package": manifest.artifact.path,
        "sha256": manifest.artifact.sha256,
        "size": manifest.artifact.size,
        "package_path": str(verified["package_path"]),
        "carrier_root": str(verified["carrier_root"]),
        "manifest_path": str(verified["release_path"]),
    }


def install_update(*, apply: bool = False) -> dict[str, Any]:
    """Verify package sha256; if apply, open/install DMG or copy .app.

    Requires explicit apply=True (click to install). Never silent.
    """
    info = check_update()
    if not info.get("update_available"):
        return {**info, "installed": False, "error": "没有可用更新"}
    verified, verify_error = _verified_update()
    if not verified:
        return {
            **info,
            "installed": False,
            "verified": False,
            "error": verify_error or "签名更新清单不可用",
        }
    manifest: ReleaseManifest = verified["manifest"]
    pkg = Path(verified["package_path"])
    if not pkg.is_file():
        return {**info, "installed": False, "error": f"安装包不存在: {pkg}"}
    expect = manifest.artifact.sha256
    if pkg.stat().st_size != manifest.artifact.size:
        return {**info, "installed": False, "error": "更新包字节数与签名清单不匹配"}
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
    applications = Path("/Applications") / "速影 Studio.app"
    legacy = Path("/Applications") / "速影.app"
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
                    src = next(
                        (
                            p
                            for p in (
                                mount / "速影 Studio.app",
                                mount / "速影.app",
                            )
                            if p.is_dir()
                        ),
                        None,
                    )
                    if src is None:
                        return {**info, "installed": False, "error": "dmg 内未找到 速影 Studio.app"}
                    _install_app_atomically(
                        src,
                        applications,
                        trusted_release=True,
                        after_swap=lambda: record_installed_release(
                            manifest,
                            str(verified["release_digest"]),
                            state_path=DEFAULT_UPDATE_STATE,
                        ),
                    )
                    if legacy.exists() or legacy.is_symlink():
                        try:
                            if legacy.is_symlink() or legacy.is_file():
                                legacy.unlink()
                            else:
                                import shutil

                                shutil.rmtree(legacy)
                        except OSError:
                            pass
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
                found = next(
                    (
                        p
                        for p in Path(td).rglob("*.app")
                        if p.name in ("速影 Studio.app", "速影.app")
                    ),
                    None,
                )
                if not found:
                    return {**info, "installed": False, "error": "zip 内未找到 速影 Studio.app"}
                _install_app_atomically(
                    found,
                    applications,
                    trusted_release=True,
                    after_swap=lambda: record_installed_release(
                        manifest,
                        str(verified["release_digest"]),
                        state_path=DEFAULT_UPDATE_STATE,
                    ),
                )
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
