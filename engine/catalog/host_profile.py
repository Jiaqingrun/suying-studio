"""Host capability probe + Ollama model recommendations by RAM."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EMBED_MODEL = "nomic-embed-text"

# Fast-screen multimodal (official Ollama tag, Text+Image, ~6.6GB).
VISION_FAST = "qwen3.5:9b"
VISION_FAST_ALIASES = ("qwen3.5:9b", "qwen3.5:9b-q4_K_M")
# Escalate / high-RAM quality model (~17GB). Never the default on ≤16GB hosts.
VISION_ESCALATE = "qwen3.5:27b-q4_K_M"
VISION_ESCALATE_ALIASES = (
    "qwen3.5:27b-q4_K_M",
    "qwen3.5:27b",
)

# All tiers use 9B only and only for selected candidates. Vector always nomic.
VISION_BY_TIER: dict[str, dict[str, Any]] = {
    "lite": {
        "model": VISION_FAST,
        "aliases": VISION_FAST_ALIASES,
        "approx_gb": 6.6,
        "label": "轻量/入门视觉 Qwen3.5 9B（约 6.6GB）",
        "min_ram_gb": 8,
        "timeout_sec": 180.0,
        "escalate_model": None,
        "escalate_timeout_sec": 180.0,
        "cascade": False,
        "allow_27b_default": False,
    },
    "standard": {
        "model": VISION_FAST,
        "aliases": VISION_FAST_ALIASES,
        "approx_gb": 6.6,
        "label": "16GB 标准视觉 Qwen3.5 9B（约 6.6GB；禁止默认 27B）",
        "min_ram_gb": 16,
        "timeout_sec": 180.0,
        "escalate_model": None,
        "escalate_timeout_sec": 180.0,
        "cascade": False,
        "allow_27b_default": False,
    },
    "pro": {
        "model": VISION_FAST,
        "aliases": VISION_FAST_ALIASES,
        "approx_gb": 6.6,
        "label": "中档视觉 9B 按需验证（不扫全库）",
        "min_ram_gb": 32,
        "timeout_sec": 240.0,
        "escalate_model": None,
        "escalate_timeout_sec": 240.0,
        "cascade": False,
        "allow_27b_default": False,
    },
    "max": {
        "model": VISION_FAST,
        "aliases": VISION_FAST_ALIASES,
        "approx_gb": 6.6,
        "label": "高配视觉 9B 按需验证（不扫全库）",
        "min_ram_gb": 96,
        "timeout_sec": 300.0,
        "escalate_model": None,
        "escalate_timeout_sec": 300.0,
        "cascade": False,
        "allow_27b_default": False,
    },
    "compat": {
        "model": "gemma4",
        "aliases": ("gemma4", "gemma4:latest"),
        "approx_gb": 9.0,
        "label": "兼容可选 · gemma4（约 9GB）",
        "min_ram_gb": 64,
        "timeout_sec": 180.0,
        "escalate_model": None,
        "escalate_timeout_sec": 180.0,
        "cascade": False,
        "allow_27b_default": False,
        "optional": True,
    },
}


@dataclass(frozen=True)
class HostProfile:
    ram_gb: float
    arch: str
    chip: str
    platform: str
    tier: str
    python_path: str | None
    python_version: str | None
    python_ok: bool
    ffmpeg_ok: bool
    ffmpeg_path: str | None
    ollama_cli: bool
    ollama_path: str | None


@dataclass(frozen=True)
class VisionPolicy:
    """Resolved primary/escalate models + timeouts for one host/settings pair."""

    tier: str
    primary_model: str
    escalate_model: str | None
    cascade: bool
    timeout_sec: float
    escalate_timeout_sec: float
    embed_model: str
    allow_27b_default: bool

    def model_for_stage(self, stage: str) -> str:
        if stage == "escalate" and self.escalate_model:
            return self.escalate_model
        return self.primary_model

    def timeout_for_stage(self, stage: str) -> float:
        if stage == "escalate" and self.escalate_model:
            return float(self.escalate_timeout_sec)
        return float(self.timeout_sec)


def _mem_bytes() -> int:
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out)
        except Exception:
            pass
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return int(page) * int(pages)
    except Exception:
        return 0


def _chip_name() -> str:
    if platform.system() != "Darwin":
        return platform.processor() or platform.machine()
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        if out:
            return out
    except Exception:
        pass
    try:
        sp = subprocess.check_output(
            ["system_profiler", "SPHardwareDataType"], text=True, timeout=8
        )
        for line in sp.splitlines():
            if "Chip:" in line or "Processor Name:" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.machine()


def recommend_tier(ram_gb: float) -> str:
    """Map RAM → tier. ≥96 max; 32–95 pro; 16–31 standard; else lite."""
    if ram_gb >= 96:
        return "max"
    if ram_gb >= 32:
        return "pro"
    if ram_gb >= 16:
        return "standard"
    return "lite"


def install_vision_by_default(tier: str) -> bool:
    """Low-memory hosts keep vision optional; all other tiers install 9B on demand."""
    return tier != "lite"


def is_27b_model(name: str | None) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return "27b" in n or n in {a.lower() for a in VISION_ESCALATE_ALIASES}


# GUI/.app 常只有 /usr/bin:/bin；Homebrew 工具装在这些目录。
_HOST_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/homebrew/sbin",
    "/usr/local/sbin",
)


def ensure_host_bin_path() -> str:
    """Prepend common host tool dirs so shutil.which finds ffmpeg/ollama under .app."""
    cur = os.environ.get("PATH") or ""
    parts = [p for p in cur.split(":") if p]
    seen = set(parts)
    prepend: list[str] = []
    local_tools = (
        str(Path.home() / "Suying" / "runtime" / "tools" / "bin"),
        str(Path.home() / "Suying" / "runtime" / "tools" / "ffmpeg" / "bin"),
    )
    for d in (*local_tools, *_HOST_BIN_DIRS):
        if d not in seen and os.path.isdir(d):
            prepend.append(d)
            seen.add(d)
    if prepend:
        new_path = ":".join(prepend + parts) if parts else ":".join(prepend)
        os.environ["PATH"] = new_path
        return new_path
    return cur


def _which_tool(name: str) -> str | None:
    ensure_host_bin_path()
    found = shutil.which(name)
    if found:
        return found
    for d in _HOST_BIN_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _python_info() -> tuple[str | None, str | None, bool]:
    ensure_host_bin_path()
    candidates: list[str] = []
    for key in ("SUYING_PYTHON", "MONTAGE_PYTHON"):
        env = (os.environ.get(key) or "").strip()
        if env:
            candidates.append(env)
    for name in ("python3.12", "python3.11", "python3"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    seen: set[str] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            ver = subprocess.check_output(
                [path, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                text=True,
                timeout=5,
            ).strip()
            major, minor, *_ = ver.split(".")
            ok = int(major) == 3 and int(minor) >= 11
            return path, ver, ok
        except Exception:
            continue
    return None, None, False


def probe_host() -> HostProfile:
    ensure_host_bin_path()
    ram_b = _mem_bytes()
    ram_gb = round(ram_b / (1024**3), 1) if ram_b else 0.0
    arch = platform.machine()
    py_path, py_ver, py_ok = _python_info()
    ff = _which_tool("ffmpeg")
    ol = _which_tool("ollama")
    return HostProfile(
        ram_gb=ram_gb,
        arch=arch,
        chip=_chip_name(),
        platform=platform.system(),
        tier=recommend_tier(ram_gb if ram_gb > 0 else 16),
        python_path=py_path,
        python_version=py_ver,
        python_ok=py_ok,
        ffmpeg_ok=bool(ff),
        ffmpeg_path=ff,
        ollama_cli=bool(ol),
        ollama_path=ol,
    )


def vision_spec_for_tier(tier: str) -> dict[str, Any]:
    return dict(VISION_BY_TIER.get(tier) or VISION_BY_TIER["standard"])


def clamp_primary_for_tier(tier: str, model: str) -> str:
    """Refuse 27B as the default primary on tiers that forbid it (≤16GB / standard)."""
    spec = vision_spec_for_tier(tier)
    if not spec.get("allow_27b_default") and is_27b_model(model):
        return str(spec["model"])
    return model


def resolve_vision_policy(
    *,
    host: HostProfile | None = None,
    settings: Any | None = None,
) -> VisionPolicy:
    """Combine host tier + persisted settings into a cascade policy."""
    host = host or probe_host()
    spec = vision_spec_for_tier(host.tier)
    if settings is None:
        try:
            from engine.config.settings import load_settings

            settings = load_settings()
        except Exception:
            settings = None

    embed = EMBED_MODEL
    primary = str(spec["model"])
    escalate = spec.get("escalate_model")
    cascade = bool(spec.get("cascade"))
    timeout = float(spec.get("timeout_sec") or 180.0)
    esc_timeout = float(spec.get("escalate_timeout_sec") or timeout)

    # Lab-only: pro/max may escalate 9B→27B when SUYING_ALLOW_VISION_CASCADE=1.
    # Fleet VISION_BY_TIER stays 9B-only; recommend_models never advertises 27B.
    lab_cascade = False
    try:
        from engine.config.settings import allow_vision_cascade_env

        lab_cascade = bool(allow_vision_cascade_env()) and host.tier in {"pro", "max"}
    except Exception:
        lab_cascade = False
    if lab_cascade and not escalate:
        escalate = VISION_ESCALATE
        esc_timeout = max(esc_timeout, 300.0)

    if settings is not None:
        embed = (getattr(settings, "ollama_embed_model", None) or "").strip() or EMBED_MODEL
        configured = (getattr(settings, "ollama_vision_model", None) or "").strip()
        if configured:
            primary = configured
        primary = clamp_primary_for_tier(host.tier, primary)
        # Explicit cascade toggle. Fleet tiers have no escalate_model; lab injects it above.
        want_cascade = bool(getattr(settings, "ollama_vision_cascade", False))
        if hasattr(settings, "ollama_vision_cascade"):
            cascade = want_cascade and bool(escalate)
        else:
            cascade = bool(spec.get("cascade")) and bool(escalate)
        if lab_cascade and want_cascade:
            cascade = True
        esc_cfg = (getattr(settings, "ollama_vision_escalate_model", None) or "").strip()
        if cascade:
            escalate = esc_cfg or escalate or (VISION_ESCALATE if lab_cascade else None)
        else:
            escalate = None
        if escalate and escalate == primary:
            escalate = None
            cascade = False
        # gate_profile / settings_patch may raise vision timeout by tier.
        try:
            cfg_timeout = float(getattr(settings, "vision_timeout_sec", 0) or 0)
            if cfg_timeout > 0:
                timeout = max(timeout, cfg_timeout)
                esc_timeout = max(esc_timeout, cfg_timeout)
        except (TypeError, ValueError):
            pass

    return VisionPolicy(
        tier=host.tier,
        primary_model=primary,
        escalate_model=str(escalate) if escalate else None,
        cascade=bool(cascade and escalate),
        timeout_sec=timeout,
        escalate_timeout_sec=esc_timeout,
        embed_model=embed,
        allow_27b_default=bool(spec.get("allow_27b_default")),
    )


def recommend_models(host: HostProfile | None = None) -> dict[str, Any]:
    host = host or probe_host()
    vision = vision_spec_for_tier(host.tier)
    escalate = vision.get("escalate_model")
    pull_order = [EMBED_MODEL, vision["model"]]
    if escalate:
        pull_order.append(str(escalate))
    return {
        "tier": host.tier,
        "embed_model": EMBED_MODEL,
        "vision_model": vision["model"],
        "vision_aliases": list(vision["aliases"]),
        "vision_label": vision["label"],
        "vision_approx_gb": vision["approx_gb"],
        "vision_timeout_sec": vision.get("timeout_sec"),
        "escalate_model": escalate,
        "escalate_timeout_sec": vision.get("escalate_timeout_sec"),
        "cascade": bool(vision.get("cascade") and escalate),
        "allow_27b_default": bool(vision.get("allow_27b_default")),
        "pull_order": pull_order,
        "reason": (
            f"检测到约 {host.ram_gb}GB 内存（{host.chip or host.arch}），"
            f"推荐档位 {host.tier}：向量 {EMBED_MODEL} + 视觉 {vision['model']}"
            + (f"，失败升级 {escalate}" if escalate else "（不默认 27B）")
        ),
        "all_tiers": {
            k: {
                "model": v["model"],
                "label": v["label"],
                "min_ram_gb": v["min_ram_gb"],
                "timeout_sec": v.get("timeout_sec"),
                "escalate_model": v.get("escalate_model"),
                "cascade": bool(v.get("cascade")),
                "allow_27b_default": bool(v.get("allow_27b_default")),
                "optional": bool(v.get("optional")),
            }
            for k, v in VISION_BY_TIER.items()
        },
    }


def host_dict(host: HostProfile | None = None) -> dict[str, Any]:
    host = host or probe_host()
    return {
        "ram_gb": host.ram_gb,
        "arch": host.arch,
        "chip": host.chip,
        "platform": host.platform,
        "tier": host.tier,
        "python_path": host.python_path,
        "python_version": host.python_version,
        "python_ok": host.python_ok,
        "ffmpeg_ok": host.ffmpeg_ok,
        "ffmpeg_path": host.ffmpeg_path,
        "ollama_cli": host.ollama_cli,
        "ollama_path": host.ollama_path,
    }
