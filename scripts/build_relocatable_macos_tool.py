#!/usr/bin/env python3
"""Create a relocatable macOS FFmpeg/ffprobe bundle with dylib closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_PREFIXES = ("/System/", "/usr/lib/")
FORBIDDEN_PREFIXES = ("/opt/homebrew/", "/usr/local/")
COPYRIGHT_NAMES = ("LICENSE", "COPYING", "NOTICE")
REPOSITORY_SOURCES = {
    ("ffmpeg", "8.1.2_1"): ("ffmpeg-8.1.2.tar.xz", "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"),
    ("lame", "3.101"): ("lame-3.101.tar.gz", "7578af6eebd578b2bd64e468fac4ae1f03670a7e028166e67f855674b9b6aeac"),
    ("mpg123", "1.33.6"): ("mpg123-1.33.6.tar.bz2", "929a7c18ba662b8927aed4de229ad9ae8ab2b4806dd0f30b90113eb1b4e2195a"),
    ("x264", "r3222"): ("x264-r3222-b35605ace3dd.tar.gz", "cd71a7515b0e9a012e1ac9b1f8415bebcaf6fc97d4db32286642ac4c0fbe24f9"),
    ("x265", "4.2"): ("x265_4.2.tar.gz", "40b1ea0453e0309f0eba934e0ddf533f8f6295966679e8894e8f1c1c8d5e1210"),
}


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def dependencies(path: Path) -> list[str]:
    lines = _run("/usr/bin/otool", "-L", str(path)).splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in lines if " (" in line]


def rpaths(path: Path) -> list[str]:
    lines = _run("/usr/bin/otool", "-l", str(path)).splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_RPATH":
            continue
        for candidate in lines[index + 1 : index + 5]:
            stripped = candidate.strip()
            if stripped.startswith("path ") and " (offset " in stripped:
                values.append(stripped[5:].split(" (offset ", 1)[0])
                break
    return values


def _expand_special_path(value: str, owner: Path, executable_dir: Path) -> Path | None:
    replacements = {
        "@loader_path": owner.parent,
        "@executable_path": executable_dir,
    }
    for prefix, root in replacements.items():
        if value == prefix:
            return root.resolve()
        if value.startswith(f"{prefix}/"):
            return (root / value[len(prefix) + 1 :]).resolve()
    if value.startswith("/"):
        return Path(value).resolve()
    return None


def resolve_dependency(dependency: str, owner: Path, executable_dir: Path) -> Path | None:
    direct = _expand_special_path(dependency, owner, executable_dir)
    if direct is not None:
        return direct
    if not dependency.startswith("@rpath/"):
        return None
    suffix = dependency[len("@rpath/") :]
    for raw_rpath in rpaths(owner):
        root = _expand_special_path(raw_rpath, owner, executable_dir)
        if root is None:
            continue
        candidate = (root / suffix).resolve()
        if candidate.is_file():
            return candidate
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _formula_identity(path: Path) -> tuple[str, str, Path]:
    parts = path.resolve().parts
    try:
        index = parts.index("Cellar")
        name, version = parts[index + 1 : index + 3]
    except (ValueError, IndexError) as error:
        raise ValueError(f"无法识别 Homebrew Cellar 来源: {path}") from error
    cellar = Path(*parts[: index + 3])
    formula = cellar / ".brew" / f"{name}.rb"
    if not formula.is_file():
        raise FileNotFoundError(f"缺少 Homebrew formula 元数据: {formula}")
    return name, version, formula


def _formula_metadata(formula: Path) -> tuple[str, str, str | None]:
    text = formula.read_text(encoding="utf-8")
    url_match = re.search(r'^\s*url\s+"([^"]+)"', text, re.MULTILINE)
    license_match = re.search(r'^\s*license\s+"([^"]+)"', text, re.MULTILINE)
    sha_match = re.search(r'^\s*sha256\s+"([0-9a-f]{64})"', text, re.MULTILINE)
    if not url_match or not license_match:
        raise ValueError(f"formula 缺少来源 URL 或许可证: {formula}")
    return (
        url_match.group(1),
        license_match.group(1),
        sha_match.group(1) if sha_match else None,
    )


def _cached_source(url: str, expected_sha256: str | None) -> Path | None:
    if not expected_sha256:
        return None
    cache = Path.home() / "Library" / "Caches" / "Homebrew" / "downloads"
    if not cache.is_dir():
        return None
    basename = url.rstrip("/").rsplit("/", 1)[-1]
    for candidate in cache.iterdir():
        if candidate.is_file() and candidate.name.endswith(f"--{basename}"):
            if sha256(candidate) == expected_sha256:
                return candidate
    return None


def _repository_source(name: str, version: str) -> tuple[Path, str] | None:
    item = REPOSITORY_SOURCES.get((name, version))
    if item is None:
        return None
    filename, expected = item
    candidate = Path(__file__).resolve().parents[1] / "docs" / "legal" / "source-offer" / filename
    if not candidate.is_file() or sha256(candidate) != expected:
        raise ValueError(f"仓库对应源码缺失或摘要不符: {name} {version}")
    return candidate, expected


def collect_legal_materials(
    staging: Path,
    source_paths: set[Path],
) -> dict[str, object]:
    legal_root = staging / "legal"
    packages: list[dict[str, object]] = []
    gaps: list[dict[str, str]] = []
    by_formula: dict[tuple[str, str], Path] = {}
    for source in sorted(source_paths):
        name, version, formula = _formula_identity(source)
        by_formula.setdefault((name, version), formula)
    for (name, version), formula in sorted(by_formula.items()):
        source_url, license_expression, source_sha = _formula_metadata(formula)
        cellar = formula.parent.parent
        package_root = legal_root / "licenses" / f"{name}-{version}"
        license_paths: list[str] = []
        for candidate in sorted(cellar.iterdir()):
            if candidate.is_file() and candidate.name.upper().startswith(COPYRIGHT_NAMES):
                target = package_root / candidate.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target)
                license_paths.append(target.relative_to(staging).as_posix())
        if not license_paths:
            gaps.append(
                {
                    "package": name,
                    "material": "LICENSE/COPYING/NOTICE",
                    "reason": f"Cellar {cellar} 未找到许可证正文",
                }
            )
        source_required = "GPL" in license_expression.upper()
        source_paths_out: list[str] = []
        if source_required:
            cached = _cached_source(source_url, source_sha)
            repository = _repository_source(name, version)
            if cached is None and repository is not None:
                cached, source_sha = repository
            if cached is None:
                gaps.append(
                    {
                        "package": name,
                        "material": "corresponding source",
                        "reason": "Homebrew 缓存中没有与 formula SHA256 对应的源码包",
                    }
                )
            else:
                target = legal_root / "source-offer" / cached.name.split("--", 1)[-1]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, target)
                source_paths_out.append(target.relative_to(staging).as_posix())
        packages.append(
            {
                "name": name,
                "version": version,
                "license_expression": license_expression,
                "source_url": source_url,
                "source_sha256": source_sha,
                "license_paths": license_paths,
                "source_required": source_required,
                "corresponding_source_paths": source_paths_out,
            }
        )
    document = {
        "schema_version": "suying.ffmpeg-bundle-legal.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "distribution_status": "ready" if not gaps else "blocked",
        "packages": packages,
        "gaps": gaps,
    }
    (legal_root / "FFMPEG_BUNDLE_MANIFEST.json").parent.mkdir(parents=True, exist_ok=True)
    (legal_root / "FFMPEG_BUNDLE_MANIFEST.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return document


def build(output: Path, binaries: list[Path]) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    bin_dir = staging / "bin"
    lib_dir = staging / "lib"
    bin_dir.mkdir()
    lib_dir.mkdir()
    source_to_target: dict[Path, Path] = {}
    queue = [path.expanduser().resolve() for path in binaries]
    executable_dir = queue[0].parent
    try:
        for binary in queue:
            if not binary.is_file():
                raise FileNotFoundError(binary)
            target = bin_dir / binary.name
            shutil.copy2(binary, target)
            source_to_target[binary] = target

        index = 0
        while index < len(queue):
            source = queue[index]
            index += 1
            for dep in dependencies(source):
                if dep.startswith(SYSTEM_PREFIXES):
                    continue
                dep_path = resolve_dependency(dep, source, executable_dir)
                if dep_path is None:
                    raise ValueError(f"无法解析 Mach-O 依赖: owner={source}, dependency={dep}")
                if not dep_path.is_file():
                    raise FileNotFoundError(f"Mach-O 依赖不存在: {dep}")
                target = lib_dir / dep_path.name
                if target.exists():
                    if sha256(target) != sha256(dep_path):
                        raise ValueError(f"dylib 文件名冲突: {dep_path.name}")
                else:
                    shutil.copy2(dep_path, target)
                if dep_path not in source_to_target:
                    queue.append(dep_path)
                    source_to_target[dep_path] = target

        for original, target in source_to_target.items():
            is_library = target.parent == lib_dir
            for dep in dependencies(target):
                resolved = resolve_dependency(dep, original, executable_dir)
                replacement_target = source_to_target.get(resolved) if resolved else None
                if replacement_target:
                    replacement = (
                        f"@loader_path/{replacement_target.name}"
                        if is_library
                        else f"@loader_path/../lib/{replacement_target.name}"
                    )
                    subprocess.run(
                        ["/usr/bin/install_name_tool", "-change", dep, replacement, str(target)],
                        check=True,
                    )
            if is_library:
                subprocess.run(
                    [
                        "/usr/bin/install_name_tool",
                        "-id",
                        f"@rpath/{target.name}",
                        str(target),
                    ],
                    check=True,
                )
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", str(target)],
                check=True,
                capture_output=True,
            )
            target.chmod(0o755)

        unresolved: list[str] = []
        for target in [*bin_dir.iterdir(), *lib_dir.iterdir()]:
            unresolved.extend(
                dep
                for dep in dependencies(target)
                if dep.startswith(FORBIDDEN_PREFIXES)
            )
        if unresolved:
            raise RuntimeError(f"relocatable bundle 仍引用 Homebrew 路径: {unresolved[:3]}")
        (staging / "THIRD_PARTY_DEPENDENCIES.txt").write_text(
            "\n".join(sorted(str(path) for path in source_to_target)) + "\n",
            encoding="utf-8",
        )
        collect_legal_materials(staging, set(source_to_target))
        previous = output.with_name(f".{output.name}.previous-{os.getpid()}")
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(staging, output)
        except Exception:
            if previous.exists():
                os.replace(previous, output)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return output
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = build(args.output, [args.ffmpeg, args.ffprobe])
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
