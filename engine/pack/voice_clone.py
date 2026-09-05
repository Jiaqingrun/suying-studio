"""Local zero-shot voice clone packs (F5-TTS) for optional TTS provider=clone.

Customer-agnostic: packs live under ``configs/voice_packs/<id>/`` or
``<customer>/05-品牌/voice/``. No customer-name branches.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Default bundled pack (slow literary reference ≈ 3.3 cps).
DEFAULT_CLONE_PACK_ID = "aunt_slow"
CLONE_PROVIDER_ALIASES = frozenset({"clone", "f5", "f5tts", "aunt", "voice_clone"})


@dataclass(frozen=True)
class VoicePack:
    id: str
    label: str
    ref_wav: Path
    ref_text: str
    speed: float = 1.0
    chars_per_sec_zh: float = 3.3
    engine: str = "f5"
    root: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "ref_wav": str(self.ref_wav),
            "ref_text": self.ref_text,
            "speed": self.speed,
            "chars_per_sec_zh": self.chars_per_sec_zh,
            "engine": self.engine,
            "root": str(self.root) if self.root else None,
        }


def is_clone_provider(name: str | None) -> bool:
    return str(name or "").strip().lower() in CLONE_PROVIDER_ALIASES


def bundled_voice_packs_root() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "voice_packs"


def customer_brand_voice_dir(
    settings: Any | None,
    *,
    output_root: str | Path | None = None,
) -> Path | None:
    """``<customer_root>/05-品牌/voice`` when output_root is ``…/02-成片``.

    Prefer an explicit ``output_root`` (active customer row) over bare
    ``settings.paths`` so packs never resolve under a previous tenant.
    """
    try:
        out = output_root or getattr(getattr(settings, "paths", None), "output_root", None)
        if not out:
            return None
        brand = Path(out).expanduser().resolve().parent / "05-品牌" / "voice"
        return brand
    except Exception:  # noqa: BLE001
        return None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _load_pack_dir(root: Path, *, pack_id: str | None = None) -> VoicePack | None:
    root = Path(root)
    if not root.is_dir():
        return None
    meta: dict[str, Any] = {}
    pack_json = root / "pack.json"
    if pack_json.is_file():
        try:
            raw = json.loads(pack_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                meta = raw
        except Exception:  # noqa: BLE001
            meta = {}
    ref_wav_name = str(meta.get("ref_wav") or "ref.wav")
    ref_text_name = str(meta.get("ref_text") or "ref.txt")
    wav = root / ref_wav_name
    txt = root / ref_text_name
    if not wav.is_file():
        wav = root / "ref.wav"
    if not wav.is_file():
        return None
    if txt.is_file():
        ref_text = _read_text(txt)
    else:
        ref_text = str(meta.get("ref_text_inline") or "").strip()
    if not ref_text:
        return None
    pid = str(pack_id or meta.get("id") or root.name).strip() or root.name
    return VoicePack(
        id=pid,
        label=str(meta.get("label") or pid),
        ref_wav=wav.resolve(),
        ref_text=ref_text,
        speed=float(meta.get("speed") or 1.0),
        chars_per_sec_zh=float(meta.get("chars_per_sec_zh") or 3.3),
        engine=str(meta.get("engine") or "f5"),
        root=root.resolve(),
    )


def resolve_voice_pack(
    *,
    pack_id: str | None = None,
    voice_lock: dict[str, Any] | None = None,
    settings: Any | None = None,
    ref_wav: str | Path | None = None,
    ref_text: str | None = None,
) -> VoicePack:
    """Resolve clone pack. Explicit paths > lock > settings > customer brand > bundled."""
    lock = voice_lock if isinstance(voice_lock, dict) else {}
    explicit_wav = ref_wav or lock.get("ref_wav") or getattr(settings, "tts_clone_ref_wav", None)
    explicit_text = ref_text
    if explicit_text is None:
        explicit_text = lock.get("ref_text") or getattr(settings, "tts_clone_ref_text", None)
    if explicit_wav:
        wav_path = Path(str(explicit_wav)).expanduser()
        if not wav_path.is_file():
            raise FileNotFoundError(f"clone ref_wav missing: {wav_path}")
        text = str(explicit_text or "").strip()
        if not text:
            sibling = wav_path.with_suffix(".txt")
            alt = wav_path.parent / "ref.txt"
            if sibling.is_file():
                text = _read_text(sibling)
            elif alt.is_file():
                text = _read_text(alt)
        if not text:
            raise ValueError("clone ref_text required with ref_wav")
        return VoicePack(
            id=str(pack_id or lock.get("voice_pack") or "custom"),
            label=str(lock.get("label") or "custom"),
            ref_wav=wav_path.resolve(),
            ref_text=text,
            speed=float(lock.get("clone_speed") or getattr(settings, "tts_clone_speed", 1.0) or 1.0),
            chars_per_sec_zh=float(
                lock.get("chars_per_sec_zh")
                or getattr(settings, "tts_chars_per_sec_zh", 3.3)
                or 3.3
            ),
            engine="f5",
            root=wav_path.parent.resolve(),
        )

    wanted = (
        str(pack_id or "").strip()
        or str(lock.get("voice_pack") or lock.get("clone_pack") or "").strip()
        or str(getattr(settings, "tts_clone_pack", "") or "").strip()
        or DEFAULT_CLONE_PACK_ID
    )

    brand = customer_brand_voice_dir(settings)
    candidates: list[Path] = []
    if brand:
        candidates.append(brand / wanted)
        candidates.append(brand)
    candidates.append(bundled_voice_packs_root() / wanted)

    for root in candidates:
        pack = _load_pack_dir(root, pack_id=wanted)
        if pack is not None:
            speed = float(
                lock.get("clone_speed")
                or getattr(settings, "tts_clone_speed", None)
                or pack.speed
                or 1.0
            )
            cps = float(
                lock.get("chars_per_sec_zh")
                or getattr(settings, "tts_chars_per_sec_zh", None)
                or pack.chars_per_sec_zh
                or 3.3
            )
            return VoicePack(
                id=pack.id,
                label=str(lock.get("label") or pack.label),
                ref_wav=pack.ref_wav,
                ref_text=pack.ref_text,
                speed=speed,
                chars_per_sec_zh=cps,
                engine=pack.engine,
                root=pack.root,
            )

    raise FileNotFoundError(
        f"voice clone pack not found: {wanted!r} (tried brand voice/ and configs/voice_packs/)"
    )


_f5_lock = threading.Lock()
_f5_tts: Any | None = None
_f5_device: str | None = None


def _slug_pack_id(label: str) -> str:
    import re

    raw = (label or "").strip().lower()
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw).strip("-")
    if not slug:
        slug = "custom"
    # Prefer ASCII-ish id for filesystem; hash Chinese-only labels.
    if not re.search(r"[a-z0-9]", slug):
        import hashlib

        slug = "v" + hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    return slug[:48]


def import_customer_voice_pack(
    settings: Any,
    *,
    source_audio: str | Path,
    label: str,
    ref_text: str,
    pack_id: str | None = None,
    speed: float = 1.0,
    chars_per_sec_zh: float = 3.3,
) -> VoicePack:
    """Drop-in audio → customer ``05-品牌/voice/packs/<id>/{ref.wav,ref.txt,pack.json}``."""
    import shutil
    import subprocess

    brand = customer_brand_voice_dir(settings)
    if brand is None:
        raise ValueError("未配置成片根目录，无法保存客户音色（需 output_root → 05-品牌/voice）")
    src = Path(source_audio).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"参考音频不存在: {src}")
    text = (ref_text or "").strip()
    if len(text) < 4:
        raise ValueError("请填写参考文本（与音频内容一致，至少 4 字）")
    display = (label or "").strip() or src.stem
    pid = _slug_pack_id(pack_id or display)
    if pid == DEFAULT_CLONE_PACK_ID:
        raise ValueError("不能覆盖内置声色包 id")
    dest_root = brand / "packs" / pid
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_wav = dest_root / "ref.wav"
    # Normalize to 16-bit mono wav via ffmpeg when available.
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                str(dest_wav),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        shutil.copy2(src, dest_wav)
    (dest_root / "ref.txt").write_text(text + "\n", encoding="utf-8")
    meta = {
        "id": pid,
        "label": display,
        "engine": "f5",
        "speed": float(speed or 1.0),
        "chars_per_sec_zh": float(chars_per_sec_zh or 3.3),
        "ref_wav": "ref.wav",
        "ref_text": "ref.txt",
        "custom": True,
        "confirmed": False,
    }
    (dest_root / "pack.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pack = _load_pack_dir(dest_root, pack_id=pid)
    if pack is None:
        raise RuntimeError("音色包已写入但无法加载，请检查参考音与文本")
    return pack


def _read_pack_meta(root: Path) -> dict[str, Any]:
    pack_json = root / "pack.json"
    if not pack_json.is_file():
        return {}
    try:
        raw = json.loads(pack_json.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def pack_confirmed(root: Path | None, *, custom: bool) -> bool:
    if not custom:
        return True
    if root is None:
        return False
    meta = _read_pack_meta(Path(root))
    if "confirmed" not in meta:
        # Legacy custom packs imported before confirm flow → treat as confirmed.
        return True
    return bool(meta.get("confirmed"))


def confirm_customer_voice_pack(settings: Any, pack_id: str) -> VoicePack:
    brand = customer_brand_voice_dir(settings)
    if brand is None:
        raise ValueError("未配置成片根目录")
    root = brand / "packs" / str(pack_id).strip()
    if not root.is_dir():
        raise FileNotFoundError(f"自定义音色不存在: {pack_id}")
    meta = _read_pack_meta(root)
    meta["id"] = str(pack_id).strip()
    meta["confirmed"] = True
    meta.setdefault("custom", True)
    (root / "pack.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pack = _load_pack_dir(root, pack_id=str(pack_id).strip())
    if pack is None:
        raise RuntimeError("确认后无法加载音色包")
    return pack


def patch_customer_voice_pack(
    settings: Any,
    pack_id: str,
    *,
    label: str | None = None,
    ref_text: str | None = None,
    speed: float | None = None,
) -> VoicePack:
    brand = customer_brand_voice_dir(settings)
    if brand is None:
        raise ValueError("未配置成片根目录")
    root = brand / "packs" / str(pack_id).strip()
    if not root.is_dir():
        raise FileNotFoundError(f"自定义音色不存在: {pack_id}")
    pack_json = root / "pack.json"
    meta: dict[str, Any] = {}
    if pack_json.is_file():
        try:
            raw = json.loads(pack_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                meta = raw
        except Exception:  # noqa: BLE001
            meta = {}
    if label is not None:
        meta["label"] = str(label).strip() or meta.get("label") or pack_id
    if speed is not None:
        meta["speed"] = float(speed)
    if ref_text is not None:
        text = str(ref_text).strip()
        if len(text) < 4:
            raise ValueError("参考文本至少 4 字")
        (root / "ref.txt").write_text(text + "\n", encoding="utf-8")
        meta["ref_text"] = "ref.txt"
    meta["id"] = str(pack_id).strip()
    pack_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pack = _load_pack_dir(root, pack_id=str(pack_id).strip())
    if pack is None:
        raise RuntimeError("改名保存后无法加载音色包")
    return pack


def delete_customer_voice_pack(settings: Any, pack_id: str) -> None:
    import shutil

    if str(pack_id).strip() == DEFAULT_CLONE_PACK_ID:
        raise ValueError("内置声色包不可删除")
    brand = customer_brand_voice_dir(settings)
    if brand is None:
        raise ValueError("未配置成片根目录")
    root = brand / "packs" / str(pack_id).strip()
    if not root.is_dir():
        raise FileNotFoundError(f"自定义音色不存在: {pack_id}")
    shutil.rmtree(root)


def _pick_device() -> str:
    try:
        import torch

        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def apply_bundled_clone_hf_env() -> None:
    """If the App bundle ships huggingface/hub weights, point HF_HOME there (offline)."""
    import os

    # voice_clone.py → pack → engine → studio → runtime (packaged layout)
    runtime_root = Path(__file__).resolve().parents[3]
    hub = runtime_root / "huggingface" / "hub"
    if not hub.is_dir():
        return
    os.environ.setdefault("HF_HOME", str(runtime_root / "huggingface"))
    # Prefer offline when weights are baked; local.env may still override earlier.
    if any(hub.glob("models--SWivid--F5-TTS*")) and any(
        hub.glob("models--charactr--vocos*")
    ):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# ---------------------------------------------------------------------------
# F5 overlay (outside App) — survives core package reinstall / overwrite
# ---------------------------------------------------------------------------
# Bundled .app runtime integrity is file-set locked; post-install pip into the
# App tree needs resign and is wiped on the next core upgrade. Keep a machine
# local site-packages under ~/Suying/runtime so clone TTS can survive upgrades.
# Contract: engine.pack.f5_kit + docs/VOICE_CLONE.md (core App + F5 Runtime Kit).
_F5_OVERLAY_APPLIED: Path | None = None


def default_f5_overlay_root() -> Path:
    from engine.pack.f5_kit import default_f5_overlay_root as _root

    return _root()


def _overlay_path_allowed(path: Path) -> bool:
    from engine.pack.f5_kit import overlay_path_allowed

    return overlay_path_allowed(path)


def apply_f5_site_overlay(*, force: bool = False) -> Path | None:
    """Prepend machine-local F5 site-packages to ``sys.path`` if present.

    Returns the overlay path when applied, otherwise None.
    Safe no-op when overlay missing, untrusted, or built for another Python ABI
    (e.g. App kit is cpython-312 while lab engine uses anaconda 3.13).
    """
    global _F5_OVERLAY_APPLIED
    import sys

    from engine.pack.f5_kit import overlay_has_f5_marker

    if _F5_OVERLAY_APPLIED is not None and not force:
        return _F5_OVERLAY_APPLIED

    root = default_f5_overlay_root()
    if not _overlay_path_allowed(root) or not root.is_dir():
        return None
    # Require a real package marker so empty dirs never shadow imports.
    if not overlay_has_f5_marker(root):
        return None
    if not _f5_overlay_matches_python_abi(root):
        # Never prepend py312 wheels into py313 lab — breaks numpy/Pillow mid-render.
        return None
    entry = str(root)
    # Prepend once (or re-front if force).
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    _F5_OVERLAY_APPLIED = root
    return root


def _f5_overlay_matches_python_abi(root: Path) -> bool:
    """True when overlay compiled extensions match current CPython tag."""
    import sys

    want = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    # numpy is the first import that fails loudly when ABI mismatches
    markers = list((root / "numpy").rglob("_multiarray_umath*.so"))
    if not markers:
        markers = list((root / "torch").rglob(f"*{want}*.so"))[:3]
        if not markers:
            # pure-python overlay or incomplete kit — allow
            return True
        return True
    return any(want in p.name for p in markers)


def f5_available() -> bool:
    apply_f5_site_overlay()
    try:
        import f5_tts  # noqa: F401

        return True
    except ImportError:
        return False


def clone_runtime_status() -> dict[str, Any]:
    """Whether local clone TTS can run without empty-spin / online HF fetch."""
    import os

    from engine.pack.f5_kit import (
        find_app_python,
        kit_compat_status,
        overlay_has_f5_marker,
        python_tag_from_executable,
    )

    overlay = apply_f5_site_overlay()
    available = f5_available()
    hf_home = Path(
        os.environ.get("HF_HOME")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or (Path.home() / ".cache" / "huggingface")
    ).expanduser()
    hub = hf_home / "hub" if (hf_home / "hub").is_dir() else hf_home
    f5_cached = False
    vocos_cached = False
    try:
        if hub.is_dir():
            f5_cached = any(hub.glob("models--SWivid--F5-TTS*"))
            vocos_cached = any(hub.glob("models--charactr--vocos*"))
    except OSError:
        pass
    offline = os.environ.get("HF_HUB_OFFLINE") == "1" or os.environ.get(
        "TRANSFORMERS_OFFLINE"
    ) == "1"
    weights_ready = bool(f5_cached and vocos_cached)
    # In-app import without overlay still counts; report source for ops.
    via = "bundle"
    if available and overlay is not None:
        via = "overlay"
        try:
            import f5_tts

            f = getattr(f5_tts, "__file__", None) or (
                list(getattr(f5_tts, "__path__", []) or [])[:1] or [None]
            )[0]
            if f and str(overlay) not in str(f):
                via = "bundle+overlay"
        except Exception:  # noqa: BLE001
            pass
    app_py = find_app_python()
    app_tag = None
    if app_py is not None:
        try:
            app_tag = python_tag_from_executable(app_py)
        except Exception:  # noqa: BLE001
            app_tag = None
    kit_extra = kit_compat_status(
        app_python_tag=app_tag,
        overlay=overlay or default_f5_overlay_root(),
    )
    # ABI mismatch: import may still work until first C-extension crash; surface
    # f5_kit_compat_ok for ops. Clone lock still uses import + weights (L19).
    return {
        "clone_available": bool(available),
        "f5_tts_importable": bool(available),
        "f5_source": via if available else "missing",
        "f5_overlay_path": str(overlay) if overlay else str(default_f5_overlay_root()),
        "f5_overlay_ready": bool(overlay is not None and overlay_has_f5_marker(overlay)),
        "hf_hub_offline": offline,
        "hf_home": str(hf_home),
        "f5_weights_cached": f5_cached,
        "vocos_weights_cached": vocos_cached,
        "weights_ready": weights_ready,
        "ok_for_clone_lock": bool(available) and (weights_ready or not offline),
        # Ops-only extras (do not replace L19 readiness keys above).
        "f5_kit_rev": kit_extra.get("f5_kit_rev"),
        "f5_kit_path": kit_extra.get("f5_kit_path"),
        "f5_kit_compat_ok": kit_extra.get("f5_kit_compat_ok"),
        "f5_kit_python_tag": kit_extra.get("f5_kit_python_tag"),
        "app_python_tag": kit_extra.get("app_python_tag"),
        "f5_kit_compat_reasons": kit_extra.get("f5_kit_compat_reasons") or [],
    }


def assert_clone_runtime_ready() -> None:
    """Raise ValueError when VIDEO_LOCK clone cannot run (fail closed, no melt)."""
    status = clone_runtime_status()
    if not status["f5_tts_importable"]:
        raise ValueError(
            "音色锁要求本地克隆旁白，但运行时未安装 f5-tts；"
            "正式路径：core App + F5 Runtime Kit（scripts/install-f5-runtime-kit.sh；"
            "物化 scripts/materialize_f5_overlay.py / package-f5-runtime-kit.sh）；"
            "clone 一体包仅作应急"
        )
    if status.get("f5_kit_compat_ok") is False and status.get("f5_source") == "overlay":
        reasons = status.get("f5_kit_compat_reasons") or []
        raise ValueError(
            "F5 Runtime Kit 与 App Python ABI 不匹配或 overlay 无效："
            + ("; ".join(str(r) for r in reasons) if reasons else "compat_fail")
            + "；请重装匹配 python_tag 的 Kit"
        )
    if status["hf_hub_offline"] and not status["weights_ready"]:
        raise ValueError(
            "HF_HUB_OFFLINE=1 但本机缺少 F5-TTS/Vocos 权重缓存；请先 materialize_clone_tts_cache 或同步 ~/.cache/huggingface/hub"
        )


def active_customer_requires_clone(settings: Any | None = None) -> bool:
    """True when active customer's VIDEO_LOCK pins provider=clone."""
    from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
    from engine.catalog.db import get_session
    from engine.config.settings import load_settings
    from engine.pack.video_lock import load_video_lock

    settings = settings or load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        lock = load_video_lock(
            customer.name,
            output_root=getattr(customer, "output_root", None) or scoped.paths.output_root,
            profile=profile,
        )
    finally:
        session.close()
    voice = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    return is_clone_provider(str(voice.get("provider") or ""))


def _get_f5(device: str | None = None) -> Any:
    global _f5_tts, _f5_device
    if not f5_available():
        raise RuntimeError(
            "f5-tts not installed; pip install f5-tts (optional local clone provider)"
        )
    want = device or _pick_device()
    with _f5_lock:
        if _f5_tts is not None and _f5_device == want:
            return _f5_tts
        from f5_tts.api import F5TTS

        _f5_tts = F5TTS(device=want)
        _f5_device = want
        return _f5_tts


def clone_utterance_to_wav(
    text: str,
    path: Path,
    pack: VoicePack,
    *,
    device: str | None = None,
    seed: int | None = None,
    nfe_step: int = 32,
) -> None:
    """Synthesize one utterance with the pack reference voice → WAV."""
    spoken = (text or "").strip()
    if not spoken:
        raise ValueError("empty clone text")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pack.ref_wav.is_file():
        raise FileNotFoundError(f"clone ref missing: {pack.ref_wav}")

    tts = _get_f5(device)
    tts.infer(
        ref_file=str(pack.ref_wav),
        ref_text=pack.ref_text,
        gen_text=spoken,
        file_wave=str(path),
        remove_silence=True,
        nfe_step=int(nfe_step),
        seed=seed,
        speed=float(pack.speed or 1.0),
    )
    if not path.is_file() or path.stat().st_size < 256:
        raise RuntimeError("f5-tts produced empty audio")
