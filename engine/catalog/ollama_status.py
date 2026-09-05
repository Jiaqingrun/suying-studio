"""Local Ollama readiness for embedding + vision captioning."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from engine.catalog.host_profile import (
    EMBED_MODEL,
    VISION_BY_TIER,
    VISION_ESCALATE,
    VISION_FAST,
    host_dict,
    probe_host,
    recommend_models,
    resolve_vision_policy,
    vision_spec_for_tier,
)
from engine.catalog.ollama_runtime import OLLAMA_URL

INSTALL_URL = "https://ollama.com/download"
# Back-compat exports (primary fast-screen; escalate is separate)
VISION_MODEL = VISION_FAST
VISION_MODEL_ALIASES = VISION_BY_TIER["standard"]["aliases"]

_pull_lock = threading.Lock()
_pull_cancel = threading.Event()
_pull_process: subprocess.Popen[str] | None = None
_pull_state: dict[str, Any] = {
    "running": False,
    "model": None,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "message": "",
    "log_tail": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute_pull(model: str) -> subprocess.CompletedProcess[str]:
    global _pull_process
    proc = subprocess.Popen(
        ["ollama", "pull", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with _pull_lock:
        _pull_process = proc
    try:
        stdout, stderr = proc.communicate(timeout=7200)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise
    finally:
        with _pull_lock:
            if _pull_process is proc:
                _pull_process = None
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def cancel_pull() -> bool:
    """Terminate a running model pull; interrupted pulls are never auto-resumed."""
    _pull_cancel.set()
    with _pull_lock:
        proc = _pull_process
        was_running = bool(_pull_state.get("running"))
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    if was_running:
        with _pull_lock:
            _pull_state.update(
                {
                    "running": False,
                    "ok": False,
                    "finished_at": _now_iso(),
                    "message": "系统暂停已中断模型拉取，需人工重新确认",
                }
            )
    return was_running


def _model_names(tags: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for row in tags:
        name = str(row.get("name") or row.get("model") or "").strip()
        if name:
            names.append(name)
    return names


def _has_model(names: list[str], wanted: str | tuple[str, ...] | list[str]) -> bool:
    """Exact tag match only (e.g. ``qwen3.5:9b`` must not match ``qwen3.5:14b``).

    ``wanted`` may list explicit aliases (e.g. a bare name without ``:latest``);
    each entry is still matched exactly — no base-name/prefix wildcarding.
    """
    wanted_list = [wanted] if isinstance(wanted, str) else list(wanted)
    names_l = {n.lower() for n in names}
    for wanted_one in wanted_list:
        wanted_l = wanted_one.lower()
        if wanted_l in names_l:
            return True
        # A bare tag (no ":") is only satisfied by an explicit ":latest" entry —
        # never by any other tag sharing the same base name.
        if ":" not in wanted_l and f"{wanted_l}:latest" in names_l:
            return True
    return False


def allowed_pull_models() -> set[str]:
    allowed = {
        EMBED_MODEL,
        "nomic-embed-text",
        VISION_FAST,
        VISION_ESCALATE,
        "qwen2.5:3b",
        "qwen2.5:7b",
        "qwen2.5:14b",
        "qwen2.5:32b",
    }
    for spec in VISION_BY_TIER.values():
        allowed.add(str(spec["model"]))
        for a in spec.get("aliases") or ():
            allowed.add(str(a))
            allowed.add(str(a).split(":")[0])
        esc = spec.get("escalate_model")
        if esc:
            allowed.add(str(esc))
        for a in spec.get("escalate_aliases") or ():
            allowed.add(str(a))
            allowed.add(str(a).split(":")[0])
    return allowed


def active_models_from_settings() -> tuple[str, str]:
    """Return (embed, primary vision) preferring persisted settings / policy."""
    try:
        policy = resolve_vision_policy()
        return policy.embed_model, policy.primary_model
    except Exception:
        rec = recommend_models()
        return EMBED_MODEL, str(rec["vision_model"])


def check_ollama(*, timeout: float = 3.0) -> dict[str, Any]:
    """Probe Ollama daemon, host profile, and recommended / active models."""
    host = probe_host()
    rec = recommend_models(host)
    policy = resolve_vision_policy(host=host)
    embed_model = policy.embed_model
    vision_model = policy.primary_model
    escalate_model = policy.escalate_model
    # Aliases for the active vision model (tier match or name itself)
    vision_aliases: list[str] = [vision_model, vision_model.split(":")[0]]
    for spec in VISION_BY_TIER.values():
        if vision_model == spec["model"] or vision_model in (spec.get("aliases") or ()):
            vision_aliases = list(spec["aliases"])
            break

    pull_cmds = [f"ollama pull {embed_model}", f"ollama pull {vision_model}"]
    if escalate_model and escalate_model not in (embed_model, vision_model):
        pull_cmds.append(f"ollama pull {escalate_model}")

    out: dict[str, Any] = {
        "reachable": False,
        "install_url": INSTALL_URL,
        "embed_model": embed_model,
        "vision_model": vision_model,
        "escalate_model": escalate_model,
        "cascade": policy.cascade,
        "vision_timeout_sec": policy.timeout_sec,
        "escalate_timeout_sec": policy.escalate_timeout_sec,
        "vision_policy": {
            "tier": policy.tier,
            "primary_model": policy.primary_model,
            "escalate_model": policy.escalate_model,
            "cascade": policy.cascade,
            "timeout_sec": policy.timeout_sec,
            "escalate_timeout_sec": policy.escalate_timeout_sec,
            "allow_27b_default": policy.allow_27b_default,
        },
        "recommended": rec,
        "host": host_dict(host),
        "embed_ready": False,
        "vision_ready": False,
        "escalate_ready": False if escalate_model else None,
        "models": [],
        "ready": False,
        "message": "",
        "pull_commands": pull_cmds,
        "pull": dict(_pull_state),
        "setup_steps": _setup_steps(host),
    }
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code != 200:
                out["message"] = f"Ollama 响应异常 HTTP {resp.status_code}"
                return out
            data = resp.json()
            names = _model_names(list(data.get("models") or []))
            out["reachable"] = True
            out["models"] = names
            out["embed_ready"] = _has_model(names, embed_model)
            out["vision_ready"] = _has_model(names, vision_aliases)
            if escalate_model:
                out["escalate_ready"] = _has_model(names, escalate_model)
            out["ready"] = out["embed_ready"]
            if out["ready"] and out["vision_ready"]:
                if policy.cascade and escalate_model and not out["escalate_ready"]:
                    out["message"] = (
                        f"主视觉 {vision_model} 就绪；升级模型 {escalate_model} 未安装"
                    )
                else:
                    msg = f"Ollama 就绪：快筛 {vision_model}"
                    if policy.cascade and escalate_model:
                        msg += f" → 升级 {escalate_model}"
                    out["message"] = msg
            elif out["ready"]:
                out["message"] = f"向量已就绪；建议安装视觉模型 {vision_model}"
            elif out["reachable"]:
                out["message"] = f"请安装向量模型：ollama pull {embed_model}"
            return out
    except Exception:
        if host.ollama_cli:
            out["message"] = "已安装 Ollama CLI，但服务未启动。请打开 Ollama 应用后再拉取模型。"
        else:
            out["message"] = "未检测到 Ollama。请先安装并启动，再拉取适合本机的模型。"
        return out


def _setup_steps(host: Any) -> list[dict[str, Any]]:
    steps = [
        {
            "id": "python",
            "title": "Python 3.11+",
            "ok": bool(host.python_ok),
            "detail": (
                f"{host.python_path} ({host.python_version})"
                if host.python_ok
                else "未找到可用 Python，请安装 python.org 或 brew install python@3.12"
            ),
        },
        {
            "id": "ffmpeg",
            "title": "FFmpeg",
            "ok": bool(host.ffmpeg_ok),
            "detail": host.ffmpeg_path or "未找到 ffmpeg，建议 brew install ffmpeg",
        },
        {
            "id": "ollama",
            "title": "Ollama",
            "ok": bool(host.ollama_cli),
            "detail": host.ollama_path or f"未安装，请打开 {INSTALL_URL}",
        },
    ]
    return steps


def apply_recommended_to_settings() -> dict[str, Any]:
    """Persist recommended embed/vision (+ cascade escalate) into settings.json."""
    from engine.config.settings import load_settings, save_settings

    host = probe_host()
    rec = recommend_models(host)
    settings = load_settings()
    settings.ollama_embed_model = str(rec["embed_model"])
    settings.ollama_vision_model = str(rec["vision_model"])
    settings.ollama_vision_escalate_model = str(rec.get("escalate_model") or "")
    settings.ollama_vision_cascade = bool(rec.get("cascade"))
    save_settings(settings)
    return {
        "ok": True,
        "embed_model": settings.ollama_embed_model,
        "vision_model": settings.ollama_vision_model,
        "escalate_model": settings.ollama_vision_escalate_model,
        "cascade": settings.ollama_vision_cascade,
        "tier": rec["tier"],
        "reason": rec["reason"],
    }


def start_pull(model: str) -> dict[str, Any]:
    """Start `ollama pull <model>` in background (one at a time)."""
    model = (model or "").strip()
    if not model:
        return {"ok": False, "message": "缺少 model"}
    allowed = allowed_pull_models()
    # Allow exact or base name
    if model not in allowed and model.split(":")[0] not in {a.split(":")[0] for a in allowed}:
        return {
            "ok": False,
            "message": f"不允许拉取的模型: {model}",
            "allowed": sorted(allowed),
        }
    with _pull_lock:
        if _pull_state["running"]:
            return {
                "ok": False,
                "message": f"已有拉取任务进行中：{_pull_state.get('model')}",
                "pull": dict(_pull_state),
            }
        _pull_cancel.clear()
        _pull_state.update(
            {
                "running": True,
                "model": model,
                "started_at": _now_iso(),
                "finished_at": None,
                "ok": None,
                "message": f"正在拉取 {model}…",
                "log_tail": "",
            }
        )

    def _run() -> None:
        try:
            proc = _execute_pull(model)
            tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-2000:]
            interrupted = _pull_cancel.is_set()
            ok = proc.returncode == 0 and not interrupted
            with _pull_lock:
                _pull_state.update(
                    {
                        "running": False,
                        "ok": ok,
                        "finished_at": _now_iso(),
                        "message": (
                            "系统暂停已中断模型拉取，需人工重新确认"
                            if interrupted
                            else ("拉取完成" if ok else f"拉取失败（exit {proc.returncode}）")
                        ),
                        "log_tail": tail,
                    }
                )
        except FileNotFoundError:
            with _pull_lock:
                _pull_state.update(
                    {
                        "running": False,
                        "ok": False,
                        "finished_at": _now_iso(),
                        "message": "未找到 ollama 命令，请先安装 Ollama",
                        "log_tail": "",
                    }
                )
        except Exception as exc:
            with _pull_lock:
                _pull_state.update(
                    {
                        "running": False,
                        "ok": False,
                        "finished_at": _now_iso(),
                        "message": f"拉取异常：{exc}",
                        "log_tail": "",
                    }
                )

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": f"已开始拉取 {model}", "pull": dict(_pull_state)}


def start_pull_recommended() -> dict[str, Any]:
    """Apply recommended models to settings, then pull embed → vision (+ escalate)."""
    applied = apply_recommended_to_settings()
    models = [applied["embed_model"], applied["vision_model"]]
    esc = (applied.get("escalate_model") or "").strip()
    if esc and esc not in models:
        models.append(esc)
    result = start_pull_models(models)
    result.update({"tier": applied["tier"], "reason": applied["reason"]})
    return result


def start_pull_models(models: list[str]) -> dict[str, Any]:
    """Pull an ordered, allowlisted model set in one resumable process task."""
    allowed = allowed_pull_models()
    normalized: list[str] = []
    allowed_bases = {a.split(":")[0] for a in allowed}
    for raw in models:
        model = str(raw or "").strip()
        if not model or model in normalized:
            continue
        if model not in allowed and model.split(":")[0] not in allowed_bases:
            return {"ok": False, "message": f"不允许拉取的模型: {model}"}
        normalized.append(model)
    if not normalized:
        return {"ok": False, "message": "安装计划没有选中模型"}
    with _pull_lock:
        if _pull_state["running"]:
            return {
                "ok": False,
                "message": f"已有拉取任务进行中：{_pull_state.get('model')}",
                "pull": dict(_pull_state),
            }
        _pull_cancel.clear()
        _pull_state.update(
            {
                "running": True,
                "model": normalized[0],
                "started_at": _now_iso(),
                "finished_at": None,
                "ok": None,
                "message": f"按安装计划执行：{' → '.join(normalized)}",
                "log_tail": "",
            }
        )

    def _run() -> None:
        ok_all = True
        tails: list[str] = []
        try:
            for m in normalized:
                if _pull_cancel.is_set():
                    ok_all = False
                    break
                with _pull_lock:
                    _pull_state.update({"model": m, "message": f"正在拉取 {m}…"})
                try:
                    proc = _execute_pull(m)
                    tails.append(((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1000:])
                    if proc.returncode != 0:
                        ok_all = False
                        with _pull_lock:
                            _pull_state.update(
                                {
                                    "running": False,
                                    "ok": False,
                                    "finished_at": _now_iso(),
                                    "message": f"拉取失败：{m}（exit {proc.returncode}）",
                                    "log_tail": "\n".join(tails)[-2000:],
                                }
                            )
                        return
                except FileNotFoundError:
                    ok_all = False
                    with _pull_lock:
                        _pull_state.update(
                            {
                                "running": False,
                                "ok": False,
                                "finished_at": _now_iso(),
                                "message": "未找到 ollama 命令，请先安装 Ollama",
                                "log_tail": "",
                            }
                        )
                    return
            with _pull_lock:
                interrupted = _pull_cancel.is_set()
                _pull_state.update(
                    {
                        "running": False,
                        "ok": ok_all and not interrupted,
                        "finished_at": _now_iso(),
                        "message": (
                            "系统暂停已中断模型拉取，需人工重新确认"
                            if interrupted
                            else ("推荐模型已全部安装" if ok_all else "部分模型安装失败")
                        ),
                        "log_tail": "\n".join(tails)[-2000:],
                    }
                )
        except Exception as exc:
            with _pull_lock:
                _pull_state.update(
                    {
                        "running": False,
                        "ok": False,
                        "finished_at": _now_iso(),
                        "message": f"拉取异常：{exc}",
                        "log_tail": "\n".join(tails)[-2000:],
                    }
                )

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "message": f"已按安装计划开始安装：{' → '.join(normalized)}",
        "models": normalized,
        "pull": dict(_pull_state),
    }


# re-export for callers that imported vision_spec
__all__ = [
    "EMBED_MODEL",
    "VISION_MODEL",
    "OLLAMA_URL",
    "INSTALL_URL",
    "check_ollama",
    "start_pull",
    "start_pull_models",
    "start_pull_recommended",
    "apply_recommended_to_settings",
    "active_models_from_settings",
    "allowed_pull_models",
    "vision_spec_for_tier",
]
