"""F5 Runtime Kit contract (App-external overlay).

Kit lives under ~/Suying/runtime and survives core App overwrite installs.
See docs/VOICE_CLONE.md · HARD_LOCKS L18 path allowlist · L19 readiness semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

F5_KIT_SCHEMA_VERSION = 1
KIT_ID = "suying-f5-runtime"


def runtime_root() -> Path:
    return (Path.home() / "Suying" / "runtime").resolve()


def default_f5_overlay_root() -> Path:
    raw = (os.environ.get("SUYING_F5_SITE_PACKAGES") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (runtime_root() / "f5_site_packages").resolve()


def default_kit_manifest_path() -> Path:
    return (runtime_root() / "f5_kit.manifest.json").resolve()


def overlay_path_allowed(path: Path) -> bool:
    """Fail closed: only under ~/Suying/runtime."""
    try:
        resolved = path.expanduser().resolve()
        allowed = runtime_root()
        return resolved == allowed or resolved.is_relative_to(allowed)
    except (OSError, ValueError, RuntimeError):
        return False


def python_tag_from_executable(python_bin: Path | str | None = None) -> str:
    """Stable tag matching App standalone CPython, e.g. cpython-3.12-darwin-arm64."""
    if python_bin is None:
        return (
            f"cpython-{sys.version_info.major}.{sys.version_info.minor}"
            f"-{sys.platform}-{platform.machine()}"
        )
    py = Path(python_bin)
    code = (
        "import platform,sys;"
        "print(f'cpython-{sys.version_info.major}.{sys.version_info.minor}"
        "-{sys.platform}-{platform.machine()}')"
    )
    out = subprocess.check_output([str(py), "-c", code], text=True).strip()
    if not out.startswith("cpython-"):
        raise RuntimeError(f"invalid python_tag from {py}: {out!r}")
    return out


def python_tag_from_app(app: Path) -> str:
    py = app / "Contents/Resources/runtime/python/bin/python3"
    if not py.is_file():
        raise FileNotFoundError(f"App 内无 python: {py}")
    return python_tag_from_executable(py)


def overlay_has_f5_marker(overlay: Path) -> bool:
    if not overlay.is_dir():
        return False
    return (overlay / "f5_tts").is_dir() or any(overlay.glob("f5_tts-*.dist-info"))


def directory_byte_size(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    return total


def directory_tree_sha256(root: Path, *, max_files: int = 200_000) -> str:
    """Content hash of relative paths + file bytes (stable for install verify)."""
    digest = hashlib.sha256()
    count = 0
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith(".") and name not in {".suying_f5_overlay.json"}:
                # Skip macOS noise; include kit meta marker if present.
                if name != ".suying_f5_overlay.json":
                    continue
            p = Path(dirpath) / name
            if not p.is_file():
                continue
            if p.name == ".DS_Store":
                continue
            files.append(p)
            count += 1
            if count > max_files:
                raise RuntimeError(f"overlay has too many files (>{max_files})")
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_kit_manifest(path: Path | None = None) -> dict[str, Any] | None:
    man = path or default_kit_manifest_path()
    if not man.is_file():
        return None
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_overlay_meta(overlay: Path | None = None) -> dict[str, Any] | None:
    root = overlay or default_f5_overlay_root()
    meta_path = root / ".suying_f5_overlay.json"
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_kit_manifest(
    *,
    overlay: Path,
    kit_rev: int,
    python_tag: str,
    source_build: str = "",
    package_sha256: str = "",
    package_bytes: int = 0,
    requires_app_python: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if kit_rev < 1:
        raise ValueError("kit_rev must be >= 1")
    bytes_ = package_bytes or directory_byte_size(overlay)
    sha = package_sha256 or directory_tree_sha256(overlay)
    return {
        "schema_version": F5_KIT_SCHEMA_VERSION,
        "kit_id": KIT_ID,
        "kit_rev": int(kit_rev),
        "python_tag": python_tag,
        "package_sha256": sha,
        "bytes": int(bytes_),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_build": source_build or "",
        "requires_app_python": requires_app_python or python_tag,
        "compat": {
            "min_engine_api": "1",
            "numpy_max": "2.4",
            "note": "numba requires NumPy<=2.4; materialize pins from source",
        },
        "weights_policy": {
            "kind": "external_hf_hub",
            "models": [
                "models--SWivid--F5-TTS",
                "models--charactr--vocos-mel-24khz",
            ],
        },
        "overlay_relpath": "f5_site_packages",
        "notes": notes or "",
    }


def write_kit_manifest(manifest: dict[str, Any], path: Path | None = None) -> Path:
    dest = path or default_kit_manifest_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def kit_compat_status(
    *,
    app: Path | None = None,
    app_python_tag: str | None = None,
    overlay: Path | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return readiness fields for clone_runtime_status (ops-only extras)."""
    overlay = overlay or default_f5_overlay_root()
    man = manifest if manifest is not None else load_kit_manifest()
    if man is None:
        om = load_overlay_meta(overlay)
        if om and isinstance(om.get("kit_rev"), int):
            man = {
                "kit_rev": om["kit_rev"],
                "python_tag": om.get("python_tag") or "",
                "requires_app_python": om.get("python_tag") or "",
            }

    overlay_ready = overlay_has_f5_marker(overlay)
    kit_path = str(default_kit_manifest_path())
    kit_rev = int(man["kit_rev"]) if man and man.get("kit_rev") is not None else None
    required = ""
    if man:
        required = str(man.get("requires_app_python") or man.get("python_tag") or "")
    app_tag = app_python_tag
    if app_tag is None and app is not None:
        try:
            app_tag = python_tag_from_app(app)
        except (OSError, RuntimeError, FileNotFoundError, subprocess.CalledProcessError):
            app_tag = None
    if app_tag is None:
        # Best-effort: running process tag (dev engine) vs bundled App.
        app_tag = python_tag_from_executable()

    compat_ok = True
    reasons: list[str] = []
    if not overlay_ready:
        compat_ok = False
        reasons.append("overlay_missing_f5_marker")
    if required and app_tag and required != app_tag:
        compat_ok = False
        reasons.append(f"python_tag_mismatch need={required} have={app_tag}")
    if not overlay_path_allowed(overlay):
        compat_ok = False
        reasons.append("overlay_path_not_allowed")

    return {
        "f5_kit_rev": kit_rev,
        "f5_kit_path": kit_path if (default_kit_manifest_path().is_file()) else None,
        "f5_kit_compat_ok": bool(compat_ok and overlay_ready),
        "f5_kit_python_tag": required or (man or {}).get("python_tag"),
        "app_python_tag": app_tag,
        "f5_kit_compat_reasons": reasons,
    }


def next_kit_rev(releases_dir: Path | None = None) -> int:
    """Pick next kit_rev from local releases kits + installed manifest."""
    revs: list[int] = []
    man = load_kit_manifest()
    if man and isinstance(man.get("kit_rev"), int):
        revs.append(int(man["kit_rev"]))
    roots = [
        Path.home() / "Suying" / "releases" / "f5-runtime" / "kits",
        Path.home() / "Suying" / "releases",
    ]
    if releases_dir:
        roots.insert(0, releases_dir)
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.glob("**/速影-f5-runtime-kit-*.tar*"):
            # ...-r12.tar.gz / .tar.zst
            name = p.name
            if "-r" not in name:
                continue
            try:
                part = name.rsplit("-r", 1)[-1]
                num = int(part.split(".tar")[0])
                revs.append(num)
            except ValueError:
                continue
        for p in root.glob("**/f5_kit.manifest.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data.get("kit_rev"), int):
                    revs.append(int(data["kit_rev"]))
            except (OSError, json.JSONDecodeError):
                continue
    return (max(revs) + 1) if revs else 1


def find_app_python(app: Path | None = None) -> Path | None:
    if app is None:
        candidates = [
            Path("/Applications/速影 Studio.app"),
            Path.home() / "Applications" / "速影 Studio.app",
        ]
    else:
        candidates = [app]
    for c in candidates:
        py = c / "Contents/Resources/runtime/python/bin/python3"
        if py.is_file():
            return py
    return None
