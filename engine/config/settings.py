from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_root() -> Path:
    """Prefer ~/Suying/data; fall back to legacy ~/MontageStudio/data if present."""
    for key in ("SUYING_DATA_ROOT", "MONTAGE_DATA_ROOT"):
        if os.environ.get(key):
            return Path(os.environ[key])
    suying = Path.home() / "Suying" / "data"
    legacy = Path.home() / "MontageStudio" / "data"
    if suying.exists():
        return suying
    if legacy.exists():
        return legacy
    return suying


def bootstrap_data_root() -> Path:
    """Local pointer directory (always under ~/Suying when possible)."""
    for key in ("SUYING_DATA_ROOT", "MONTAGE_DATA_ROOT"):
        if os.environ.get(key):
            return Path(os.environ[key])
    return Path.home() / "Suying" / "data"


def ensure_suying_layout(data_root: Path | None = None) -> Path:
    """Create ~/Suying tree; optionally symlink/copy settings from MontageStudio once."""
    root = Path.home() / "Suying"
    data = data_root or (root / "data")
    cache = root / "cache"
    data.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    legacy_data = Path.home() / "MontageStudio" / "data"
    legacy_settings = legacy_data / "settings.json"
    new_settings = data / "settings.json"
    # One-time: if new install points at Suying but settings only exist in legacy, copy
    if data == root / "data" and not new_settings.exists() and legacy_settings.exists():
        try:
            shutil.copy2(legacy_settings, new_settings)
            legacy_db = legacy_data / "montage.db"
            new_db = data / "montage.db"
            if legacy_db.exists() and not new_db.exists():
                # Prefer hard link / copy of DB so history is preserved
                try:
                    os.link(legacy_db, new_db)
                except OSError:
                    shutil.copy2(legacy_db, new_db)
        except OSError:
            pass
    return data


class PathConfig(BaseModel):
    library_root: Path = Field(default_factory=lambda: Path.home() / "Suying" / "library")
    library_roots: list[Path] = Field(default_factory=list)
    output_root: Path = Field(default_factory=lambda: Path.home() / "Suying" / "output")
    cache_root: Path = Field(default_factory=lambda: Path.home() / "Suying" / "cache")
    data_root: Path = Field(default_factory=_default_data_root)
    # Intermediate renders (not deliverables). Keep outside sync tree.
    render_root: Path = Field(default_factory=lambda: Path.home() / "Suying" / "render")
    # Royalty-free BGM drops (mp3/m4a/wav). Customer 04-音乐 takes priority when present.
    music_root: Path = Field(default_factory=lambda: Path.home() / "Suying" / "music")
    # Deprecated: frames live under cache_root/frames. Kept for settings.json compat only.
    cliplet_root: Path | None = None
    external_required: bool = False

    def all_library_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for p in [self.library_root, *self.library_roots]:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            roots.append(p)
        return roots

    def frames_root(self) -> Path:
        """抽帧缓存：统一在 cache_root/frames（不再使用独立 cliplet_root）。"""
        return Path(self.cache_root) / "frames"

    def resolved_cliplet_root(self) -> Path:
        """Backward-compatible alias → frames parent (cache_root)."""
        return Path(self.cache_root)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONTAGE_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8766
    paths: PathConfig = Field(default_factory=PathConfig)
    max_render_concurrency: int = 1
    circuit_breaker_threshold: int = 5
    # Sprint A: consecutive failed+review → circuit_open (quality melt)
    quality_circuit_threshold: int = 5
    min_free_disk_gb: float = 10.0
    default_seed: int | None = None
    auto_daily_enabled: bool = False
    auto_daily_hour: int = 9
    active_customer: str = ""
    # Audio mix: default strip source audio — BGM only (ambient_gain=0)
    require_audio: bool = True
    ambient_gain: float = 0.0
    bgm_gain: float = 0.45
    audio_target_lufs: float = -14.0
    keep_source_audio: bool = False  # True 时才混入原片环境音
    preserve_source_resolution: bool = True  # canvas ≥ max source size in plan
    # G4 TTS: edge (晓晓) | say | mock | auto (prefer edge)
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "-8%"
    tts_pitch: str = "+20Hz"
    tts_chars_per_sec_zh: float = 3.8
    tts_chars_per_sec_en: float = 13.0
    # G4 mix: narration leads; BGM bed ducks under voice
    narration_gain: float = 1.0
    bgm_bed_gain: float = 0.2
    # G5 Reach limits (per customer / calendar day)
    reach_daily_quota: int = 5
    # Per-platform caps; empty = only total applies. When set, sum(values) must be <= reach_daily_quota.
    reach_platform_quotas: dict[str, int] = Field(default_factory=dict)
    reach_fail_threshold: int = 3
    # Vectorization is OFF by default; customer must enable manually in App
    vectorization_enabled: bool = False
    vectorization_enabled_at: str | None = None
    require_manual_vectorization: bool = True
    # Always incremental on enable: only fill gaps; never wipe existing vectors
    vectorization_mode: str = "incremental"
    # First-run wizard completed
    onboarded: bool = False
    product_name: str = "速影"
    # Cursor Agent SDK / Cloud Agents — stored in workspace settings (never sync tree)
    cursor_api_key: str = ""


def settings_file(data_root: Path) -> Path:
    return data_root / "settings.json"


def load_settings() -> AppSettings:
    """Load settings, honoring an external data_root (e.g. NAS CLIPLET/data)."""
    ensure_suying_layout()
    bootstrap = bootstrap_data_root()
    bootstrap.mkdir(parents=True, exist_ok=True)
    path = settings_file(bootstrap)

    if not path.exists():
        legacy = Path.home() / "MontageStudio" / "data" / "settings.json"
        if legacy.exists():
            path = legacy
        else:
            settings = AppSettings()
            settings.paths.data_root = bootstrap
            settings.paths.cache_root = Path.home() / "Suying" / "cache"
            from engine.ops.cursor_key import apply_cursor_api_key_env

            apply_cursor_api_key_env(settings.cursor_api_key)
            return settings

    raw = json.loads(path.read_text(encoding="utf-8"))
    configured = (raw.get("paths") or {}).get("data_root")
    data_root = Path(configured) if configured else bootstrap

    # If settings point at an external root that has its own settings.json, prefer that
    if data_root.resolve() != bootstrap.resolve():
        ext_path = settings_file(data_root)
        if ext_path.exists():
            raw = json.loads(ext_path.read_text(encoding="utf-8"))
            data_root = Path((raw.get("paths") or {}).get("data_root") or data_root)

    settings = AppSettings(**raw)
    settings.paths.data_root = data_root
    from engine.ops.cursor_key import apply_cursor_api_key_env

    apply_cursor_api_key_env(settings.cursor_api_key)
    return settings


def save_settings(settings: AppSettings) -> None:
    """Persist settings to data_root; keep a bootstrap copy under ~/Suying/data."""
    settings.paths.data_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = settings.model_dump(mode="json")
    # Ensure paths.data_root in payload matches the canonical location
    payload.setdefault("paths", {})["data_root"] = str(settings.paths.data_root)

    primary = settings_file(settings.paths.data_root)
    primary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    boot = bootstrap_data_root()
    if boot.resolve() != settings.paths.data_root.resolve():
        boot.mkdir(parents=True, exist_ok=True)
        settings_file(boot).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    from engine.ops.cursor_key import apply_cursor_api_key_env

    apply_cursor_api_key_env(settings.cursor_api_key)


def enable_vectorization(settings: AppSettings | None = None) -> AppSettings:
    settings = settings or load_settings()
    settings.vectorization_enabled = True
    settings.vectorization_mode = "incremental"
    if not settings.vectorization_enabled_at:
        settings.vectorization_enabled_at = datetime.now(timezone.utc).isoformat()
    save_settings(settings)
    return settings


def disable_vectorization(settings: AppSettings | None = None) -> AppSettings:
    settings = settings or load_settings()
    settings.vectorization_enabled = False
    save_settings(settings)
    return settings
