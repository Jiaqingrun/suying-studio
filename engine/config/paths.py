from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from engine.config.settings import AppSettings


# Deliverable folders under output_root (sync-safe). Rendering lives in render_root.
OUTPUT_STATES = ("review", "ready", "failed")


@dataclass
class PathHealth:
    ok: bool
    library_mounted: bool
    output_mounted: bool
    free_disk_gb: float
    warnings: list[str]
    errors: list[str]
    libraries: list[dict]


def ensure_layout(settings: AppSettings) -> None:
    frames = settings.paths.frames_root()
    for p in (
        settings.paths.output_root,
        settings.paths.cache_root,
        settings.paths.data_root,
        settings.paths.render_root,
        settings.paths.music_root,
        frames,
    ):
        p.mkdir(parents=True, exist_ok=True)
    music_readme = settings.paths.music_root / "README.txt"
    if not music_readme.exists():
        music_readme.write_text(
            "速影 · BGM 目录\n"
            "将免版权 mp3 / m4a / wav 放到此文件夹。\n"
            "客户目录若存在「04-音乐」则优先使用客户曲库。\n",
            encoding="utf-8",
        )
    for state in OUTPUT_STATES:
        (settings.paths.output_root / state).mkdir(parents=True, exist_ok=True)
    (settings.paths.cache_root / "proxies").mkdir(parents=True, exist_ok=True)
    (settings.paths.cache_root / "temp").mkdir(parents=True, exist_ok=True)
    (settings.paths.cache_root / "library").mkdir(parents=True, exist_ok=True)
    readme = settings.paths.cache_root / "README.txt"
    if not readme.exists():
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
    # Do not auto-create external/NAS library roots — only verify they exist.


def check_paths(settings: AppSettings) -> PathHealth:
    warnings: list[str] = []
    errors: list[str] = []
    libraries: list[dict] = []

    for root in settings.paths.all_library_roots():
        mounted = root.exists()
        libraries.append({"path": str(root), "mounted": mounted})
        if not mounted:
            msg = f"片库未挂载: {root}"
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

    usage = shutil.disk_usage(settings.paths.cache_root)
    free_gb = usage.free / (1024**3)
    if free_gb < settings.min_free_disk_gb:
        warnings.append(f"内置盘剩余空间不足 {settings.min_free_disk_gb}GB (当前 {free_gb:.1f}GB)")

    ok = len(errors) == 0
    return PathHealth(
        ok=ok,
        library_mounted=library_mounted,
        output_mounted=output_mounted,
        free_disk_gb=round(free_gb, 2),
        warnings=warnings,
        errors=errors,
        libraries=libraries,
    )
