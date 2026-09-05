"""Machine-level install plan for repeatable Suying customer deployments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.catalog.host_profile import (
    HostProfile,
    host_dict,
    install_vision_by_default,
    probe_host,
    recommend_models,
)
from engine.ops.app_update import current_app_version


INSTALL_PLAN_SCHEMA = "suying.install.plan.v1"
INSTALL_RECEIPT_SCHEMA = "suying.install.receipt.v1"

# 旁白默认走 qwen3.5:9b（think:false + keep_alive）；勿默认 14b/32b 冷启拖垮生产。
NARRATION_MODEL_BY_TIER = {
    "lite": "qwen2.5:3b",
    "standard": "qwen3.5:9b",
    "pro": "qwen3.5:9b",
    "max": "qwen3.5:9b",
}

PROFILE_LABELS = {
    "lite": "轻量",
    "standard": "标准",
    "pro": "质量",
    "max": "工作站",
}

MODEL_APPROX_GB = {
    "nomic-embed-text": 0.3,
    "qwen3.5:9b": 6.6,
    "qwen2.5:3b": 2.0,
    "qwen2.5:7b": 4.7,
    "qwen2.5:14b": 9.0,
    "qwen2.5:32b": 20.0,
}


GATE_PROFILE_VERSION = "gate_profile.v1"


def render_concurrency_for_tier(tier: str) -> int:
    """Install Profile render slot cap. TTS remains single-slot in ResourceGate.

    - lite / standard / pro: 1 (publish stays 1; no multi-F5)
    - max (≥96GB): 2 ffmpeg-capable render slots; TTS still 1
    """
    name = str(tier or "standard").strip().lower()
    if name == "max":
        return 2
    return 1


def gate_profile_for_tier(tier: str) -> dict[str, Any]:
    """Capacity / soft gates by host tier (absolute READY locks stay global)."""
    name = str(tier or "standard").strip().lower()
    if name not in {"lite", "standard", "pro", "max"}:
        name = "standard"
    base = {
        "gate_profile_version": GATE_PROFILE_VERSION,
        "tier": name,
        "max_render_concurrency": render_concurrency_for_tier(name),
        "semantic_full_backfill_enabled": False,
        "publish_slots": 1,
        "tts_slots": 1,
    }
    if name == "lite":
        return {
            **base,
            "semantic_analysis_mode": "off",
            "semantic_toggle_enabled": False,
            "strict_semantic_allowed": False,
            "clone_tts_allowed": False,
            "custom_voice_import_allowed": False,
            # QUALITY_LOCK hard floor is 0.35 — B-layer may only tighten, never relax.
            "cliplet_quality_floor": 0.35,
            "blur_reject_floor": 0.35,
            "vision_timeout_sec": 180.0,
            "vector_batch_size": 4,
        }
    if name == "standard":
        return {
            **base,
            "semantic_analysis_mode": "on_demand",
            "semantic_toggle_enabled": True,
            "strict_semantic_allowed": True,
            "clone_tts_allowed": True,
            "custom_voice_import_allowed": True,
            "cliplet_quality_floor": 0.35,
            "blur_reject_floor": 0.35,
            "vision_timeout_sec": 180.0,
            "vector_batch_size": 8,
        }
    if name == "pro":
        return {
            **base,
            "semantic_analysis_mode": "on_demand",
            "semantic_toggle_enabled": True,
            "strict_semantic_allowed": True,
            "clone_tts_allowed": True,
            "custom_voice_import_allowed": True,
            "cliplet_quality_floor": 0.35,
            "blur_reject_floor": 0.35,
            "vision_timeout_sec": 240.0,
            "vector_batch_size": 12,
        }
    return {
        **base,
        "semantic_analysis_mode": "on_demand",
        "semantic_toggle_enabled": True,
        "strict_semantic_allowed": True,
        "clone_tts_allowed": True,
        "custom_voice_import_allowed": True,
        "cliplet_quality_floor": 0.35,
        "blur_reject_floor": 0.35,
        "vision_timeout_sec": 300.0,
        "vector_batch_size": 16,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def install_runtime_root() -> Path:
    override = (os.environ.get("SUYING_INSTALL_ROOT") or "").strip()
    return Path(override) if override else Path.home() / "Suying" / "runtime" / "install"


def _disk_free_gb(path: Path | None = None) -> float:
    target = path or Path.home()
    try:
        return round(shutil.disk_usage(target).free / (1024**3), 1)
    except OSError:
        return 0.0


def _host_fingerprint(host: HostProfile) -> str:
    stable = {
        "platform": host.platform,
        "arch": host.arch,
        "chip": host.chip,
        "ram_gb": int(round(host.ram_gb)),
    }
    raw = json.dumps(stable, ensure_ascii=True, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _plan_identity(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": plan.get("schema_version"),
        "app_version": plan.get("app_version"),
        "host_fingerprint": (plan.get("host") or {}).get("fingerprint"),
        "profile_id": (plan.get("profile") or {}).get("id"),
        "selected_models": plan.get("selected_models"),
        "settings_patch": plan.get("settings_patch"),
    }


def _plan_id(plan: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_plan_identity(plan), ensure_ascii=True, sort_keys=True).encode()
    ).hexdigest()[:20]


def validate_install_plan(plan: dict[str, Any], *, require_approved: bool = False) -> None:
    if plan.get("schema_version") != INSTALL_PLAN_SCHEMA:
        raise ValueError("安装计划 schema 不兼容")
    if str(plan.get("plan_id") or "") != _plan_id(plan):
        raise ValueError("安装计划内容与 plan_id 不匹配")
    if require_approved and not plan.get("approved_at"):
        raise ValueError("安装计划尚未批准")


def _component(
    component_id: str,
    *,
    kind: str,
    name: str,
    required: bool,
    selected: bool,
    approx_gb: float,
) -> dict[str, Any]:
    return {
        "id": component_id,
        "kind": kind,
        "name": name,
        "required": required,
        "selected": selected,
        "approx_gb": approx_gb,
    }


def build_install_plan(
    *,
    host: HostProfile | None = None,
    include_vision: bool | None = None,
    include_narration: bool = False,
) -> dict[str, Any]:
    """Build a deterministic preview; no settings or files are changed."""
    host = host or probe_host()
    recommended = recommend_models(host)
    tier = host.tier if host.tier in PROFILE_LABELS else "standard"
    vision_selected = (
        install_vision_by_default(tier) if include_vision is None else bool(include_vision)
    )
    narration_model = NARRATION_MODEL_BY_TIER[tier]
    embed_model = str(recommended["embed_model"])
    vision_model = str(recommended["vision_model"])

    components = [
        _component(
            "embed",
            kind="ollama_model",
            name=embed_model,
            required=True,
            selected=True,
            approx_gb=MODEL_APPROX_GB.get(embed_model, 0.5),
        ),
        _component(
            "vision",
            kind="ollama_model",
            name=vision_model,
            required=False,
            selected=vision_selected,
            approx_gb=float(recommended.get("vision_approx_gb") or 6.6),
        ),
        _component(
            "narration",
            kind="ollama_model",
            name=narration_model,
            required=False,
            selected=bool(include_narration),
            approx_gb=MODEL_APPROX_GB[narration_model],
        ),
    ]
    selected_models = [
        str(component["name"])
        for component in components
        if component["kind"] == "ollama_model" and component["selected"]
    ]
    download_gb = round(
        sum(float(component["approx_gb"]) for component in components if component["selected"]),
        1,
    )
    disk_free_gb = _disk_free_gb()
    minimum_free_gb = round(download_gb + 10.0, 1)
    warnings: list[str] = []
    if host.platform != "Darwin" or host.arch not in {"arm64", "aarch64"}:
        warnings.append("正式客户包当前只支持 Apple Silicon macOS")
    if disk_free_gb < minimum_free_gb:
        warnings.append(f"磁盘不足：至少需要保留 {minimum_free_gb}GB")
    if tier == "lite" and not vision_selected:
        warnings.append("轻量档默认不安装视觉模型；可正常粗索引，候选视觉验证需稍后安装")

    gate = gate_profile_for_tier(tier)
    settings_patch = {
        # Tier caps: lite/standard/pro stay 1; max may overlap ffmpeg (TTS still 1 via ResourceGate).
        "max_render_concurrency": gate["max_render_concurrency"],
        "ollama_embed_model": embed_model,
        "ollama_vision_model": vision_model if vision_selected else "",
        "ollama_vision_escalate_model": "",
        "ollama_vision_cascade": False,
        "ollama_narration_model": narration_model,
        "semantic_analysis_mode": gate["semantic_analysis_mode"],
        "semantic_full_backfill_enabled": False,
        "semantic_toggle_enabled": gate["semantic_toggle_enabled"],
        "strict_semantic_allowed": gate["strict_semantic_allowed"],
        "clone_tts_allowed": gate["clone_tts_allowed"],
        "custom_voice_import_allowed": gate["custom_voice_import_allowed"],
        "cliplet_quality_floor": gate["cliplet_quality_floor"],
        "blur_reject_floor": gate["blur_reject_floor"],
        "vision_timeout_sec": gate["vision_timeout_sec"],
        "vector_batch_size": gate["vector_batch_size"],
        "gate_profile_version": GATE_PROFILE_VERSION,
    }
    app_version = current_app_version()
    host_fp = _host_fingerprint(host)
    plan: dict[str, Any] = {
        "schema_version": INSTALL_PLAN_SCHEMA,
        "plan_id": "",
        "created_at": _now_iso(),
        "app_version": app_version,
        "host": {
            **host_dict(host),
            "fingerprint": host_fp,
            "disk_free_gb": disk_free_gb,
        },
        "profile": {
            "id": tier,
            "label": PROFILE_LABELS[tier],
            "reason": recommended["reason"],
            "max_render_concurrency": gate["max_render_concurrency"],
            "semantic_mode": gate["semantic_analysis_mode"],
            "full_library_vision": False,
            "vision_candidate_limit": 10,
            "gate_profile": gate,
        },
        "components": components,
        "selected_models": selected_models,
        "estimated_download_gb": download_gb,
        "minimum_free_disk_gb": minimum_free_gb,
        "settings_patch": settings_patch,
        "gates": [
            {"id": "platform", "ok": host.platform == "Darwin"},
            {"id": "architecture", "ok": host.arch in {"arm64", "aarch64"}},
            {"id": "python", "ok": host.python_ok},
            {"id": "ffmpeg", "ok": host.ffmpeg_ok},
            {"id": "ollama", "ok": host.ollama_cli},
            {"id": "disk", "ok": disk_free_gb >= minimum_free_gb},
        ],
        "human_steps": [
            "若缺 Homebrew，需人工确认并安装",
            "首次启动 Ollama 可能需要 macOS 图形确认",
            "极空间登录、验证码和 T2S 绑定必须由用户完成",
        ],
        "warnings": warnings,
        "approved_at": None,
        "configured_at": None,
        "models_started_at": None,
    }
    plan["plan_id"] = _plan_id(plan)
    return plan


def persist_install_plan(plan: dict[str, Any], *, root: Path | None = None) -> Path:
    validate_install_plan(plan)
    runtime_root = root or install_runtime_root()
    runtime_root.mkdir(parents=True, exist_ok=True)
    target = runtime_root / "install-plan.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    os.chmod(target, 0o600)
    return target


def load_approved_install_plan(plan_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Load an immutable approved plan by ID; callers must not rebuild a replacement."""
    if not plan_id:
        raise ValueError("缺少 plan_id")
    runtime_root = root or install_runtime_root()
    target = runtime_root / "install-plan.json"
    if not target.is_file():
        raise ValueError("批准的安装计划不存在")
    try:
        plan = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("批准的安装计划无法读取") from exc
    if not isinstance(plan, dict):
        raise ValueError("批准的安装计划格式无效")
    validate_install_plan(plan, require_approved=True)
    if plan.get("plan_id") != plan_id:
        raise ValueError("请求的 plan_id 与已批准计划不匹配")
    return plan


def _resolve_approved_plan(
    plan_or_id: dict[str, Any] | str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    plan_id = (
        str(plan_or_id.get("plan_id") or "")
        if isinstance(plan_or_id, dict)
        else str(plan_or_id or "")
    )
    approved = load_approved_install_plan(plan_id, root=root)
    if isinstance(plan_or_id, dict) and _plan_identity(plan_or_id) != _plan_identity(approved):
        raise ValueError("调用方安装计划已被修改，拒绝应用")
    return approved


def mark_install_plan_stage(
    plan: dict[str, Any] | str,
    stage: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    field = {
        "configured": "configured_at",
        "models_started": "models_started_at",
    }.get(stage)
    if field is None:
        raise ValueError(f"未知安装计划阶段: {stage}")
    approved = _resolve_approved_plan(plan, root=root)
    approved[field] = _now_iso()
    persist_install_plan(approved, root=root)
    if isinstance(plan, dict):
        plan.clear()
        plan.update(approved)
    return approved


def approve_install_plan(
    *,
    include_vision: bool | None = None,
    include_narration: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    plan = build_install_plan(
        include_vision=include_vision,
        include_narration=include_narration,
    )
    plan["approved_at"] = _now_iso()
    persist_install_plan(plan, root=root)
    return plan


def apply_install_plan_settings(
    plan: dict[str, Any] | str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Apply only the machine-level settings allowlisted by Install Profile v1."""
    from engine.config.settings import load_settings, save_settings

    plan = _resolve_approved_plan(plan, root=root)
    expected = _host_fingerprint(probe_host())
    actual = str((plan.get("host") or {}).get("fingerprint") or "")
    if actual != expected:
        raise ValueError("安装计划不属于当前机器，请重新检测")
    allowed = {
        "max_render_concurrency",
        "ollama_embed_model",
        "ollama_vision_model",
        "ollama_vision_escalate_model",
        "ollama_vision_cascade",
        "ollama_narration_model",
        "semantic_analysis_mode",
        "semantic_full_backfill_enabled",
        "semantic_toggle_enabled",
        "strict_semantic_allowed",
        "clone_tts_allowed",
        "custom_voice_import_allowed",
        "cliplet_quality_floor",
        "blur_reject_floor",
        "vision_timeout_sec",
        "vector_batch_size",
        "gate_profile_version",
    }
    patch = plan.get("settings_patch") or {}
    if not isinstance(patch, dict):
        raise ValueError("安装计划 settings_patch 无效")
    settings = load_settings()
    applied: dict[str, Any] = {}
    for key in allowed:
        if key in patch:
            value = patch[key]
            setattr(settings, key, value)
            applied[key] = value
    save_settings(settings)
    return {"ok": True, "applied": applied}


def read_install_state(*, root: Path | None = None) -> dict[str, Any]:
    runtime_root = root or install_runtime_root()
    plan_path = runtime_root / "install-plan.json"
    receipt_path = runtime_root / "install-receipt.json"

    def _read(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    return {
        "plan": _read(plan_path),
        "receipt": _read(receipt_path),
        "plan_path": str(plan_path),
        "receipt_path": str(receipt_path),
    }
