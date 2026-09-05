#!/usr/bin/env python3
"""Materialize a machine-local F5 site-packages overlay (survives core App upgrades).

Why: core flavor 一体包不含 f5-tts；对 App 内 site-packages 热补会在下次覆盖安装时丢失，
且改 App 树须重签 RUNTIME_MANIFEST。overlay 在包体外：
  ~/Suying/runtime/f5_site_packages
引擎启动时会把它插到 sys.path（见 engine.pack.voice_clone.apply_f5_site_overlay）。

Usage (ops / 本机):
  # 从含 f5 的 App 或 runtime stage 导出
  python3 scripts/materialize_f5_overlay.py --from-app "/Applications/速影 Studio.app"
  python3 scripts/materialize_f5_overlay.py --from-runtime-stage /path/to/runtime --clean

  # 状态（含 kit manifest）
  python3 scripts/materialize_f5_overlay.py --check

  # 仅从已有 overlay 写 / 升级 f5_kit.manifest.json（不重新拷包）
  python3 scripts/materialize_f5_overlay.py --write-manifest --kit-rev 1
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pack.f5_kit import (  # noqa: E402
    build_kit_manifest,
    default_f5_overlay_root,
    default_kit_manifest_path,
    load_kit_manifest,
    next_kit_rev,
    overlay_has_f5_marker,
    python_tag_from_executable,
    write_kit_manifest,
)
from engine.pack.voice_clone import (  # noqa: E402
    apply_f5_site_overlay,
    clone_runtime_status,
    f5_available,
)


def _site_packages(python_bin: Path) -> Path:
    out = subprocess.check_output(
        [
            str(python_bin),
            "-c",
            "import site; print(next(p for p in site.getsitepackages() if p.endswith('site-packages')))",
        ],
        text=True,
    ).strip()
    path = Path(out)
    if not path.is_dir():
        raise SystemExit(f"找不到 site-packages: {path}")
    return path


def _has_f5(python_bin: Path) -> bool:
    probe = subprocess.run(
        [
            str(python_bin),
            "-c",
            "import importlib.util as u; print(int(bool(u.find_spec('f5_tts'))))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.stdout.strip() == "1"


def _top_level_used_by_f5(python_bin: Path, site_sp: Path) -> list[str]:
    """Import F5TTS and collect top-level names under site-packages that were touched."""
    code = r"""
import sys
from pathlib import Path
sp = Path(sys.argv[1]).resolve()
from f5_tts.api import F5TTS  # noqa: F401
names = set()
for mod_name, mod in list(sys.modules.items()):
    if mod is None:
        continue
    # avoid lazy getattr on incomplete modules (transformers subpackages)
    d = getattr(mod, "__dict__", None) or {}
    f = d.get("__file__")
    raw_path = d.get("__path__")
    roots = []
    if raw_path is not None:
        try:
            roots = list(raw_path)
        except TypeError:
            roots = []
    candidates = ([f] if f else []) + [str(r) for r in roots]
    for c in candidates:
        try:
            p = Path(c).resolve()
        except Exception:
            continue
        try:
            rel = p.relative_to(sp)
        except ValueError:
            continue
        if rel.parts:
            names.add(rel.parts[0])
extra = set()
for n in list(names):
    for p in sp.glob(n + "-*.dist-info"):
        extra.add(p.name)
    if n in ("numpy", "numba", "torch", "torchaudio", "torchvision", "f5_tts", "transformers", "vocos"):
        for p in sp.glob(n + "*"):
            extra.add(p.name)
print("\n".join(sorted(names | extra)))
"""
    proc = subprocess.run(
        [str(python_bin), "-c", code, str(site_sp)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"导入 F5 以收集包失败:\n{proc.stderr[-800:] or proc.stdout[-800:]}"
        )
    rows = []
    for ln in proc.stdout.splitlines():
        name = ln.strip()
        if not name or " " in name or "/" in name or name.startswith("Download"):
            continue
        rows.append(name)
    if "f5_tts" not in rows and not any(r.startswith("f5_tts") for r in rows):
        raise SystemExit("导入 F5 后未解析到 f5_tts 包路径")
    return rows


def _resolve_python(from_app: Path | None, from_stage: Path | None) -> tuple[Path, Path]:
    """Return (python_bin, source_label_path)."""
    if from_stage is not None:
        stage = from_stage.expanduser().resolve()
        # stage may be …/runtime or …/runtime/python
        candidates = [
            stage / "python/bin/python3",
            stage / "bin/python3",
            stage / "Contents/Resources/runtime/python/bin/python3",
        ]
        for py in candidates:
            if py.is_file():
                return py, stage
        raise SystemExit(f"runtime stage 无 python3: {stage}")
    app = (from_app or Path("/Applications/速影 Studio.app")).expanduser().resolve()
    py = app / "Contents/Resources/runtime/python/bin/python3"
    if not py.is_file():
        raise SystemExit(f"App 内无 python: {py}")
    return py, app


def materialize(
    *,
    python_bin: Path,
    source_label: Path,
    overlay: Path,
    clean: bool,
    kit_rev: int | None,
    source_build: str,
) -> dict:
    if not _has_f5(python_bin):
        raise SystemExit(f"源 Python 不含 f5_tts，无法导出 overlay: {python_bin}")
    sp = _site_packages(python_bin)
    names = _top_level_used_by_f5(python_bin, sp)
    # Always force numpy/numba pins from source (numba needs NumPy≤2.4).
    for force in list(sp.glob("numpy*")) + list(sp.glob("numba*")):
        if force.name not in names:
            names.append(force.name)
    names = sorted(set(names))
    overlay.mkdir(parents=True, exist_ok=True)
    if clean:
        for child in overlay.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    copied = 0
    for name in names:
        src = sp / name
        if not src.exists():
            continue
        dst = overlay / name
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        copied += 1

    py_tag = python_tag_from_executable(python_bin)
    rev = kit_rev if kit_rev is not None else next_kit_rev()
    numpy_ver = ""
    try:
        numpy_ver = subprocess.check_output(
            [str(python_bin), "-c", "import numpy; print(numpy.__version__)"],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        pass

    meta = {
        "source": str(source_label),
        "source_app": str(source_label),
        "source_site_packages": str(sp),
        "packages": names,
        "count": copied,
        "python_tag": py_tag,
        "numpy_version": numpy_ver,
        "kit_rev": rev,
        "source_build": source_build,
    }
    (overlay / ".suying_f5_overlay.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = build_kit_manifest(
        overlay=overlay,
        kit_rev=rev,
        python_tag=py_tag,
        source_build=source_build,
    )
    write_kit_manifest(manifest)
    meta["kit_manifest"] = str(default_kit_manifest_path())
    meta["package_sha256"] = manifest["package_sha256"]
    return meta


def write_manifest_only(*, overlay: Path, kit_rev: int | None, source_build: str) -> dict:
    if not overlay_has_f5_marker(overlay):
        raise SystemExit(f"overlay 缺少 f5_tts 标记: {overlay}")
    py = None
    for candidate in (
        Path("/Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3"),
        Path.home() / "Applications/速影 Studio.app/Contents/Resources/runtime/python/bin/python3",
    ):
        if candidate.is_file():
            py = candidate
            break
    py_tag = python_tag_from_executable(py) if py else python_tag_from_executable()
    om = {}
    mp = overlay / ".suying_f5_overlay.json"
    if mp.is_file():
        try:
            om = json.loads(mp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            om = {}
    rev = kit_rev
    if rev is None:
        if isinstance(om.get("kit_rev"), int):
            rev = int(om["kit_rev"])
        else:
            existing = load_kit_manifest()
            rev = int(existing["kit_rev"]) if existing and existing.get("kit_rev") else next_kit_rev()
    if om.get("python_tag"):
        py_tag = str(om["python_tag"])
    manifest = build_kit_manifest(
        overlay=overlay,
        kit_rev=int(rev),
        python_tag=py_tag,
        source_build=source_build or str(om.get("source_build") or ""),
    )
    path = write_kit_manifest(manifest)
    om.update(
        {
            "python_tag": py_tag,
            "kit_rev": int(rev),
            "source_build": manifest["source_build"],
        }
    )
    mp.write_text(json.dumps(om, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"kit_manifest": str(path), **manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-app",
        type=Path,
        default=None,
        help="含 f5-tts 的一体包 App（默认 /Applications/速影 Studio.app）",
    )
    parser.add_argument(
        "--from-runtime-stage",
        type=Path,
        default=None,
        help="embed 用的 runtime 目录（或含 python/bin/python3 的 stage）",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=None,
        help="默认 ~/Suying/runtime/f5_site_packages（SUYING_F5_SITE_PACKAGES 可覆盖）",
    )
    parser.add_argument("--clean", action="store_true", help="先清空 overlay 再写入")
    parser.add_argument("--check", action="store_true", help="只检查 overlay / kit / 可用性")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="不拷包，仅为现有 overlay 写 f5_kit.manifest.json",
    )
    parser.add_argument("--kit-rev", type=int, default=None, help="固定 kit_rev（默认自动 +1）")
    parser.add_argument(
        "--source-build",
        default="",
        help="写入 manifest 的 source_build（如 BUNDLE_VERSION 或 git sha）",
    )
    args = parser.parse_args()
    overlay = (args.overlay or default_f5_overlay_root()).expanduser().resolve()

    if args.check:
        applied = apply_f5_site_overlay(force=True)
        man = load_kit_manifest()
        status = clone_runtime_status()
        print(
            json.dumps(
                {
                    "overlay": str(overlay),
                    "exists": overlay.is_dir(),
                    "marker": overlay_has_f5_marker(overlay),
                    "applied": str(applied) if applied else None,
                    "f5_available": f5_available(),
                    "kit_manifest": str(default_kit_manifest_path()),
                    "kit_manifest_present": default_kit_manifest_path().is_file(),
                    "kit_rev": (man or {}).get("kit_rev"),
                    "python_tag": (man or {}).get("python_tag"),
                    "clone_runtime": status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if f5_available() else 2

    if args.write_manifest:
        meta = write_manifest_only(
            overlay=overlay,
            kit_rev=args.kit_rev,
            source_build=args.source_build,
        )
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0

    py, label = _resolve_python(args.from_app, args.from_runtime_stage)
    meta = materialize(
        python_bin=py,
        source_label=label,
        overlay=overlay,
        clean=args.clean,
        kit_rev=args.kit_rev,
        source_build=args.source_build,
    )
    apply_f5_site_overlay(force=True)
    ok = f5_available()
    meta["verify_f5_available"] = ok
    meta["overlay"] = str(overlay)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if not ok:
        return 3
    print(
        "\nOK · 已写入 overlay + f5_kit.manifest.json。"
        f" core 覆盖后仍可 import f5-tts（引擎加载 {overlay}）。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
