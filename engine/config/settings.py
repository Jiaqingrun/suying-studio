from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from engine.runtime.pause_coordinator import SystemEventControl, default_system_event_control


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


def _default_media_workspace() -> Path:
    """Client media workspace (library/cache/render); not the bootstrap data_root."""
    return Path.home() / "Movies" / "速影工作区"


class PathConfig(BaseModel):
    library_root: Path = Field(
        default_factory=lambda: _default_media_workspace() / "library"
    )
    library_roots: list[Path] = Field(default_factory=list)
    output_root: Path = Field(
        default_factory=lambda: _default_media_workspace() / "output"
    )
    cache_root: Path = Field(
        default_factory=lambda: _default_media_workspace() / "cache"
    )
    data_root: Path = Field(default_factory=_default_data_root)
    # Intermediate renders (not deliverables). Same tree as media workspace.
    render_root: Path = Field(
        default_factory=lambda: _default_media_workspace() / "render"
    )
    # Royalty-free BGM drops (mp3/m4a/wav). Customer 04-音乐 takes priority when present.
    music_root: Path = Field(
        default_factory=lambda: _default_media_workspace() / "music"
    )
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
    # Consecutive Ollama/infra item skips before pausing the job (stop empty render loop).
    ollama_infra_pause_threshold: int = 3
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
    # G4 TTS: edge (晓晓) | clone (F5 本地声色包) | say | mock | auto (prefer edge)
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "-8%"
    tts_pitch: str = "+35Hz"
    tts_chars_per_sec_zh: float = 3.8
    tts_chars_per_sec_en: float = 13.0
    # Optional local clone pack (configs/voice_packs/<id> or 05-品牌/voice)
    tts_clone_pack: str = "aunt_slow"
    tts_clone_speed: float = 1.0
    tts_clone_ref_wav: str = ""
    tts_clone_ref_text: str = ""
    # When VO is longer than planned picture, atempo-fit speech before freeze-pad (L15).
    narration_fit_to_picture: bool = True
    narration_fit_max_speed: float = 1.35
    # G4 mix: narration leads; BGM bed ducks under voice
    narration_gain: float = 1.0
    bgm_bed_gain: float = 0.48
    # G5 Reach safety: daily quotas were removed; consecutive failures still stop work.
    reach_fail_threshold: int = 3
    # Vectorization is OFF by default; customer must enable manually in App
    vectorization_enabled: bool = False
    vectorization_enabled_at: str | None = None
    require_manual_vectorization: bool = True
    # Always incremental on enable: only fill gaps; never wipe existing vectors
    vectorization_mode: str = "incremental"
    # Daily local window (Asia/Shanghai): edge-trigger enable/disable
    vectorization_schedule_enabled: bool = False
    vectorization_schedule_start: str = "22:00"
    vectorization_schedule_end: str = "07:00"
    # When both orientations have zero gaps, auto-disable (inside window stays off until next edge)
    vectorization_auto_stop_when_done: bool = True
    # Edge detector: last known in-window state (None until first policy tick)
    vectorization_schedule_last_in_window: bool | None = None
    # First-run wizard completed
    onboarded: bool = False
    product_name: str = "速影 Studio"
    # Local AI models (Ollama). Empty → host-tier recommendation at runtime.
    ollama_embed_model: str = "nomic-embed-text"
    ollama_vision_model: str = ""
    # Cascade: primary (fast screen) → escalate on gate/timeout/schema. Empty escalate → tier default.
    ollama_vision_escalate_model: str = ""
    ollama_vision_cascade: bool = False
    # Low-resource default: never sweep the full library with a VLM.
    # New cliplets get coarse metadata embeddings; 9B verifies selected candidates.
    # Tier gate_profile may set "off" (lite) or "on_demand" (standard+).
    semantic_analysis_mode: str = "on_demand"
    semantic_full_backfill_enabled: bool = False
    semantic_toggle_enabled: bool = True
    strict_semantic_allowed: bool = True
    clone_tts_allowed: bool = True
    custom_voice_import_allowed: bool = True
    cliplet_quality_floor: float = 0.35
    blur_reject_floor: float = 0.35
    vision_timeout_sec: float = 180.0
    vector_batch_size: int = 8
    gate_profile_version: str = ""
    # Ops toggle: Ollama rewrites visual hints → spoken copy + theme emoji stickers before burn
    ollama_narration_enabled: bool = False
    ollama_narration_model: str = ""  # empty → vision model or qwen3.5:9b
    ollama_narration_burn_emoji: bool = True
    # GVideoRules: natural-language → structured production rules (empty → qwen text)
    ollama_rule_model: str = ""
    # GSystemPause: machine-level macOS sleep/screen pause preferences
    system_event_control: SystemEventControl = Field(default_factory=default_system_event_control)
    # GStab.WORKSPACE: bound external workspace identity (never auto-overwrite DB)
    workspace_id: str = ""
    workspace_volume_uuid: str = ""

    @model_validator(mode="after")
    def enforce_machine_hard_locks(self) -> "AppSettings":
        """Fail closed on dangerous relaxations; tier capacity gates stay writable."""
        # Cap render slots: 1 default, 2 only for max-tier install plans.
        try:
            conc = int(self.max_render_concurrency)
        except (TypeError, ValueError):
            conc = 1
        self.max_render_concurrency = 2 if conc >= 2 else 1
        mode = str(self.semantic_analysis_mode or "on_demand").strip().lower()
        self.semantic_analysis_mode = mode if mode in {"off", "on_demand"} else "on_demand"
        # Fleet default: never persist/enable full-library VLM sweep.
        # Lab escape: process env SUYING_ALLOW_SEMANTIC_FULL_BACKFILL=1 (dev/migration only).
        if not allow_semantic_full_backfill_env():
            self.semantic_full_backfill_enabled = False
        # Fleet default: no 9B→27B cascade. Lab escape: SUYING_ALLOW_VISION_CASCADE=1.
        if not allow_vision_cascade_env():
            self.ollama_vision_cascade = False
        return self


def _env_flag_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def allow_semantic_full_backfill_env() -> bool:
    """Process-local override for controlled full-library semantic.v1 migration."""
    return _env_flag_true("SUYING_ALLOW_SEMANTIC_FULL_BACKFILL")


def allow_vision_cascade_env() -> bool:
    """Process-local override for pro/max 9B→27B vision cascade (dev machine only)."""
    return _env_flag_true("SUYING_ALLOW_VISION_CASCADE")


LAB_ENV_SEMANTIC_FULL_BACKFILL = "SUYING_ALLOW_SEMANTIC_FULL_BACKFILL"
LAB_ENV_VISION_CASCADE = "SUYING_ALLOW_VISION_CASCADE"


def effective_semantic_full_backfill_enabled(
    settings: AppSettings | None = None,
) -> bool:
    """True when lab env allows full sweep (settings.json field stays fleet-false)."""
    if allow_semantic_full_backfill_env():
        return True
    return bool(settings and getattr(settings, "semantic_full_backfill_enabled", False))


def local_runtime_env_path() -> Path:
    """Machine-local lab env; never packaged / never synced to other hosts."""
    override = (os.environ.get("SUYING_LOCAL_ENV") or "").strip()
    if override:
        return Path(override)
    return Path.home() / "Suying" / "runtime" / "local.env"


def _upsert_local_env_flags(updates: dict[str, str | None]) -> Path:
    """Merge KEY=VALUE into ~/Suying/runtime/local.env; None removes the key."""
    path = local_runtime_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []
    kept: list[str] = []
    seen: set[str] = set()
    for raw in existing:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            kept.append(raw)
            continue
        body = line[7:].strip() if line.startswith("export ") else line
        key, _, _val = body.partition("=")
        key = key.strip()
        if key in updates:
            seen.add(key)
            continue
        kept.append(raw)
    for key, value in updates.items():
        if value is None:
            continue
        kept.append(f"{key}={value}")
        seen.add(key)
    text = "\n".join(kept).rstrip() + ("\n" if kept else "")
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def set_semantic_full_backfill_lab(enabled: bool) -> dict[str, Any]:
    """Enable/disable full-library semantic.v1 backfill via machine-local lab env.

    settings.json always persists False (fleet-safe). App/Ops toggle writes
    ~/Suying/runtime/local.env and updates process env so /index/captions works.
    Enabling strips vision cascade (cannot share one GPU with full sweep).
    """
    if enabled:
        os.environ[LAB_ENV_SEMANTIC_FULL_BACKFILL] = "1"
        os.environ.pop(LAB_ENV_VISION_CASCADE, None)
        path = _upsert_local_env_flags(
            {
                LAB_ENV_SEMANTIC_FULL_BACKFILL: "1",
                LAB_ENV_VISION_CASCADE: None,
            }
        )
    else:
        os.environ.pop(LAB_ENV_SEMANTIC_FULL_BACKFILL, None)
        path = _upsert_local_env_flags({LAB_ENV_SEMANTIC_FULL_BACKFILL: None})
    return {
        "enabled": allow_semantic_full_backfill_env(),
        "local_env": str(path),
        "cascade_stripped": bool(enabled),
    }


def apply_local_runtime_env(*, overwrite: bool = False) -> Path | None:
    """Load KEY=VALUE from ~/Suying/runtime/local.env into os.environ.

    Does not override existing process env unless overwrite=True.
    Missing file is a no-op (fleet / customer machines).
    """
    path = local_runtime_env_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if overwrite or key not in os.environ:
            os.environ[key] = value
    return path


def settings_file(data_root: Path) -> Path:
    return data_root / "settings.json"


def reconcile_local_first_paths(settings: AppSettings) -> bool:
    """Local-first: main-disk data_root must not keep external_required stuck on.

    Returns True when settings object was mutated (caller may persist).
    Never rewrites user-bound external volume roots.
    """
    from engine.config.workspace import is_external_data_root

    dirty = False
    boot = bootstrap_data_root()
    data_root = Path(settings.paths.data_root)
    external = is_external_data_root(data_root, boot)
    if not external and settings.paths.external_required:
        settings.paths.external_required = False
        dirty = True
    # Stale volume bind on a local bootstrap: clear (re-bind only if user moves data_root out).
    if not external and str(getattr(settings, "workspace_volume_uuid", "") or "").strip():
        settings.workspace_volume_uuid = ""
        dirty = True
    return dirty


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
            settings.paths.external_required = False
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
    # Prefer install-runtime.sh hint when vision model not chosen yet
    if not (settings.ollama_vision_model or "").strip():
        hint_path = settings.paths.data_root / "recommended_models.json"
        if hint_path.exists():
            try:
                hint = json.loads(hint_path.read_text(encoding="utf-8"))
                if hint.get("ollama_embed_model"):
                    settings.ollama_embed_model = str(hint["ollama_embed_model"])
                if hint.get("ollama_vision_model"):
                    settings.ollama_vision_model = str(hint["ollama_vision_model"])
            except Exception:
                pass
    # One-shot self-heal: App formerly forced external_required=true on every savePaths.
    if reconcile_local_first_paths(settings):
        try:
            if not _is_ephemeral_data_root(settings.paths.data_root):
                save_settings(settings)
        except Exception:
            pass
    return settings


def _is_ephemeral_data_root(path: Path) -> bool:
    """Temp/pytest dirs must never overwrite the real ~/Suying bootstrap pointer."""
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    markers = (
        "/pytest-",
        "/pytest_of_",
        "/tmp/",
        "/var/folders/",
        "/private/var/folders/",
        "/T/tmp",
    )
    return any(m in resolved for m in markers)


def _is_real_machine_bootstrap(boot: Path) -> bool:
    """True only for the on-disk ~/Suying/data pointer (not test overrides)."""
    try:
        return boot.resolve() == (Path.home() / "Suying" / "data").resolve()
    except OSError:
        return False


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_settings(settings: AppSettings) -> None:
    """Persist settings to data_root; keep a bootstrap copy under ~/Suying/data.

    Fail-closed: never mkdir a missing external data_root (would fake a mounted volume).
    """
    # Assignments are not revalidated by pydantic, so clamp again before writing.
    # Always persist full_backfill=false so customer install receipts stay fleet-safe;
    # in-process lab override only via SUYING_ALLOW_SEMANTIC_FULL_BACKFILL.
    # Cascade stays False on disk unless SUYING_ALLOW_VISION_CASCADE=1 (local lab).
    # Tier gate_profile may persist concurrency=2 (max) and semantic_analysis_mode=off (lite).
    try:
        conc = int(settings.max_render_concurrency)
    except (TypeError, ValueError):
        conc = 1
    settings.max_render_concurrency = 2 if conc >= 2 else 1
    mode = str(settings.semantic_analysis_mode or "on_demand").strip().lower()
    settings.semantic_analysis_mode = mode if mode in {"off", "on_demand"} else "on_demand"
    settings.semantic_full_backfill_enabled = False
    if not allow_vision_cascade_env():
        settings.ollama_vision_cascade = False

    from engine.config.workspace import is_external_data_root

    data_root = Path(settings.paths.data_root)
    boot = bootstrap_data_root()
    external = is_external_data_root(data_root, boot)
    if external and not data_root.exists():
        # Still update local bootstrap pointer so App can show "waiting for disk".
        payload: dict[str, Any] = settings.model_dump(mode="json")
        payload.setdefault("paths", {})["data_root"] = str(data_root)
        if not _is_ephemeral_data_root(data_root) or not _is_real_machine_bootstrap(boot):
            boot.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(
                settings_file(boot),
                json.dumps(payload, indent=2, ensure_ascii=False),
            )
        return

    if not external:
        data_root.mkdir(parents=True, exist_ok=True)
    elif not data_root.exists():
        raise OSError(f"外接工作区不存在，拒绝创建: {data_root}")

    payload = settings.model_dump(mode="json")
    # Ensure paths.data_root in payload matches the canonical location
    payload.setdefault("paths", {})["data_root"] = str(settings.paths.data_root)

    primary = settings_file(settings.paths.data_root)
    _atomic_write_text(primary, json.dumps(payload, indent=2, ensure_ascii=False))

    # Never let smoke/pytest temp workspaces clobber the machine bootstrap pointer
    # (that made App/engine point at deleted /var/folders/... paths — user data "disappeared").
    skip_bootstrap_mirror = _is_ephemeral_data_root(settings.paths.data_root) and _is_real_machine_bootstrap(
        boot
    )
    if boot.resolve() != settings.paths.data_root.resolve() and not skip_bootstrap_mirror:
        boot.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            settings_file(boot),
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
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
