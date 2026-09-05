from __future__ import annotations

import errno
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.config.settings import AppSettings


# Deliverable folders under output_root (sync-safe). Rendering lives in render_root.
OUTPUT_STATES = ("review", "ready", "failed")

# Process-local write probes (GET /health is hot — avoid touch every poll).
_WRITE_PROBE_TTL_SEC = 10.0
_write_probe_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

_WRITE_ROLE_LABELS = {
    "render": "渲染目录",
    "cache": "缓存目录",
    "output": "成片目录",
}


@dataclass
class PathHealth:
    ok: bool
    library_mounted: bool
    output_mounted: bool
    free_disk_gb: float
    warnings: list[str]
    errors: list[str]
    libraries: list[dict]
    writable: bool = True
    write_checks: list[dict] = field(default_factory=list)


def clear_write_probe_cache() -> None:
    """Test / ops helper: drop in-process write-probe cache."""
    _write_probe_cache.clear()


def is_readonly_fs_error(exc: BaseException) -> bool:
    """True when failure is a permanent filesystem read-only (not transient network flake)."""
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) == errno.EROFS:
            return True
        msg = str(exc).lower()
        if "read-only file system" in msg or "readonly file system" in msg:
            return True
    # Worker wraps: "OSError: [Errno 30] Read-only file system: ..."
    text = str(exc).lower()
    return (
        "read-only file system" in text
        or "readonly file system" in text
        or "errno 30" in text
        or "[errno 30]" in text
    )


def _safe_mkdir(path: Path) -> bool:
    """Create directory; never crash startup on foreign/unwritable roots."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _probe_writable(path: Path, role: str) -> dict[str, Any]:
    """Create/delete a tiny probe file under path. Never raises."""
    label = _WRITE_ROLE_LABELS.get(role, role)
    if not path.exists():
        if not _safe_mkdir(path):
            detail = f"{label}不存在且无法创建: {path}"
            return {"path": str(path), "role": role, "ok": False, "detail": detail}

    probe = path / f".suying_wprobe_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        try:
            probe.unlink(missing_ok=True)
        except TypeError:  # pragma: no cover — old Python
            if probe.exists():
                probe.unlink()
        except OSError:
            # Wrote successfully; leftover probe file is non-fatal.
            pass
        return {"path": str(path), "role": role, "ok": True, "detail": ""}
    except OSError as e:
        if is_readonly_fs_error(e) or getattr(e, "errno", None) == errno.EROFS:
            detail = f"{label}不可写（只读文件系统）: {path}"
        else:
            detail = f"{label}不可写: {path} ({e})"
        return {"path": str(path), "role": role, "ok": False, "detail": detail}


def _write_probe_roots(settings: AppSettings) -> list[tuple[str, Path]]:
    return [
        ("render", Path(settings.paths.render_root)),
        ("cache", Path(settings.paths.cache_root)),
        ("output", Path(settings.paths.output_root)),
    ]


def _cached_write_checks(
    roots: list[tuple[str, Path]], *, force: bool
) -> list[dict[str, Any]]:
    key = "|".join(f"{role}:{path}" for role, path in roots)
    now = time.monotonic()
    if not force:
        hit = _write_probe_cache.get(key)
        if hit is not None:
            at, checks = hit
            if now - at < _WRITE_PROBE_TTL_SEC:
                return checks
    checks = [_probe_writable(path, role) for role, path in roots]
    _write_probe_cache[key] = (now, checks)
    return checks


def ensure_layout(settings: AppSettings) -> None:
    from engine.config.workspace import probe_workspace

    probe = probe_workspace(settings)
    if not probe.can_mkdir:
        # External workspace missing: never create a fake mount tree / empty db parent.
        return

    frames = settings.paths.frames_root()
    for p in (
        settings.paths.output_root,
        settings.paths.cache_root,
        settings.paths.data_root,
        settings.paths.render_root,
        settings.paths.music_root,
        frames,
    ):
        # For external roots that already exist, only mkdir children — never invent parents.
        if p == settings.paths.data_root and probe.is_external and not p.exists():
            continue
        _safe_mkdir(p)
    music_readme = settings.paths.music_root / "README.txt"
    if _safe_mkdir(settings.paths.music_root) and not music_readme.exists():
        try:
            music_readme.write_text(
                "速影 · BGM 目录\n"
                "将免版权 mp3 / m4a / wav 放到此文件夹。\n"
                "客户目录若存在「04-音乐」则优先使用客户曲库。\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    for state in OUTPUT_STATES:
        _safe_mkdir(settings.paths.output_root / state)
    if _safe_mkdir(settings.paths.cache_root):
        _safe_mkdir(settings.paths.cache_root / "proxies")
        _safe_mkdir(settings.paths.cache_root / "temp")
        _safe_mkdir(settings.paths.cache_root / "library")
        readme = settings.paths.cache_root / "README.txt"
        if not readme.exists():
            try:
                readme.write_text(
                    "速影工作区 · cache\n"
                    "────────────────\n"
                    "• frames/   — 视觉描述抽帧缓存\n"
                    "• library/  — 规范化素材\n"
                    "• proxies/  — 代理媒体\n"
                    "• temp/     — ffmpeg 临时文件\n"
                    "片段时间轴 + 向量在 SQLite：\n"
                    f"  {settings.paths.data_root / 'montage.db'}\n"
                    "  表 cliplets.embedding_json\n"
                    "渲染中间文件：render_root（不进同步区）\n"
                    f"  {settings.paths.render_root}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
    # Do not auto-create external/NAS library roots — only verify they exist.


def check_paths(
    settings: AppSettings, *, force_write_probe: bool = False
) -> PathHealth:
    warnings: list[str] = []
    errors: list[str] = []
    libraries: list[dict] = []

    for root in settings.paths.all_library_roots():
        mounted = root.exists()
        readable = bool(mounted and os.access(root, os.R_OK))
        libraries.append(
            {"path": str(root), "mounted": mounted, "readable": readable if mounted else False}
        )
        if not mounted:
            msg = f"片库未挂载: {root}"
            if settings.paths.external_required:
                errors.append(msg)
            else:
                warnings.append(msg)
        elif not readable:
            msg = f"片库不可读: {root}"
            if settings.paths.external_required:
                errors.append(msg)
            else:
                warnings.append(msg)

    library_mounted = any(item["mounted"] for item in libraries) if libraries else False
    output_mounted = settings.paths.output_root.exists()

    if settings.paths.external_required and not library_mounted:
        errors.append("已启用 external_required，但没有任何可用片库")
    if not output_mounted:
        errors.append(f"输出目录不存在: {settings.paths.output_root}")

    free_gb = 0.0
    try:
        usage_root = settings.paths.cache_root if settings.paths.cache_root.exists() else Path.home()
        usage = shutil.disk_usage(usage_root)
        free_gb = usage.free / (1024**3)
        if free_gb < settings.min_free_disk_gb:
            warnings.append(f"内置盘剩余空间不足 {settings.min_free_disk_gb}GB (当前 {free_gb:.1f}GB)")
    except OSError as e:
        warnings.append(f"无法读取磁盘容量: {e}")

    write_checks = _cached_write_checks(
        _write_probe_roots(settings), force=force_write_probe
    )
    writable = True
    for check in write_checks:
        if not check.get("ok"):
            writable = False
            detail = str(check.get("detail") or "").strip()
            if detail and detail not in errors:
                errors.append(detail)

    ok = len(errors) == 0
    return PathHealth(
        ok=ok,
        library_mounted=library_mounted,
        output_mounted=output_mounted,
        free_disk_gb=round(free_gb, 2),
        warnings=warnings,
        errors=errors,
        libraries=libraries,
        writable=writable,
        write_checks=write_checks,
    )
