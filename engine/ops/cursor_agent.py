"""速影助手 · Cursor Agent 多轮对话循环（含 SSE 流式）。"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.config.settings import load_settings
from engine.ops.cursor_key import apply_cursor_api_key_env

log = logging.getLogger(__name__)

_lock = threading.RLock()
_agents: dict[str, Any] = {}
_busy: dict[str, bool] = {}

BOOTSTRAP = (
    "你是「速影助手」，嵌入在速影（Suying）本机混剪工作室 App 中。"
    "用户通过速影 App 与你对话来完成项目操作。请用简洁中文回复。"
    "不要提及 Cursor、Composer、IDE；对外一律自称速影助手。"
    "可读写当前工作区文件、协助配置、排查与生产相关问题。"
    "\n\n用户消息：\n"
)

TOOL_LABELS = {
    "Shell": "执行命令",
    "Read": "读取文件",
    "Write": "写入文件",
    "StrReplace": "修改文件",
    "Grep": "搜索代码",
    "Glob": "查找文件",
    "Delete": "删除文件",
    "WebSearch": "联网搜索",
    "WebFetch": "抓取网页",
    "AwaitShell": "等待命令",
    "Task": "子任务",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sessions_path(data_root: Path) -> Path:
    return Path(data_root) / "cursor_sessions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_store(data_root: Path) -> dict[str, Any]:
    path = _sessions_path(data_root)
    if not path.exists():
        return {"sessions": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sessions": {}}


def _save_store(data_root: Path, store: dict[str, Any]) -> None:
    path = _sessions_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def _require_api_key() -> str:
    settings = load_settings()
    key = (settings.cursor_api_key or "").strip()
    if not key:
        raise ValueError("未配置 Cursor API Key，请先在运维页填写并验证")
    apply_cursor_api_key_env(key)
    return key


def _tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name or "工具")


def _get_or_create_agent(session: dict[str, Any], api_key: str, cwd: Path) -> Any:
    from cursor_sdk import Agent

    local_opts = {"cwd": str(cwd)}
    agent_id = (session.get("agent_id") or "").strip()
    if agent_id and agent_id in _agents:
        return _agents[agent_id]

    if agent_id:
        try:
            agent = Agent.resume(
                agent_id,
                {
                    "api_key": api_key,
                    "model": "auto",
                    "local": local_opts,
                },
            )
            _agents[agent.agent_id] = agent
            session["agent_id"] = agent.agent_id
            return agent
        except Exception as e:
            log.warning("resume agent %s failed, creating new: %s", agent_id, e)

    customer = session.get("customer") or "default"
    agent = Agent.create(
        {
            "model": "auto",
            "api_key": api_key,
            "name": f"速影助手 · {customer}",
            "mode": "agent",
            "local": local_opts,
        },
    )
    _agents[agent.agent_id] = agent
    session["agent_id"] = agent.agent_id
    session["created_at"] = session.get("created_at") or _now()
    return agent


def list_session_summary(session_id: str = "default") -> dict[str, Any]:
    settings = load_settings()
    store = _load_store(settings.paths.data_root)
    session = (store.get("sessions") or {}).get(session_id) or {}
    messages = session.get("messages") or []
    return {
        "session_id": session_id,
        "agent_id": session.get("agent_id") or "",
        "customer": session.get("customer") or settings.active_customer or "",
        "message_count": len(messages),
        "messages": messages[-40:],
        "busy": bool(_busy.get(session_id)),
        "updated_at": session.get("updated_at") or "",
    }


def reset_session(session_id: str = "default") -> dict[str, Any]:
    settings = load_settings()
    with _lock:
        store = _load_store(settings.paths.data_root)
        sessions = store.setdefault("sessions", {})
        old = sessions.get(session_id) or {}
        agent_id = (old.get("agent_id") or "").strip()
        if agent_id and agent_id in _agents:
            agent = _agents.pop(agent_id)
            try:
                agent.close()
            except Exception:
                pass
        sessions[session_id] = {
            "agent_id": "",
            "customer": settings.active_customer or "",
            "messages": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        _busy[session_id] = False
        _save_store(settings.paths.data_root, store)
    return list_session_summary(session_id)


def _prepare_turn(
    message: str,
    session_id: str,
    customer: str | None,
) -> tuple[Any, str, str, Path, float]:
    """Lock, mark busy, append user message, return (agent, prompt, agent_id, data_root, started)."""
    text = (message or "").strip()
    if not text:
        raise ValueError("消息不能为空")

    api_key = _require_api_key()
    settings = load_settings()
    data_root = settings.paths.data_root
    cwd = _repo_root()

    with _lock:
        if _busy.get(session_id):
            raise RuntimeError("助手正在处理上一条消息，请稍候")
        _busy[session_id] = True
        store = _load_store(data_root)
        sessions = store.setdefault("sessions", {})
        session = sessions.get(session_id) or {
            "agent_id": "",
            "customer": customer or settings.active_customer or "",
            "messages": [],
            "created_at": _now(),
        }
        if customer:
            session["customer"] = customer
        elif not session.get("customer"):
            session["customer"] = settings.active_customer or ""
        is_first = len(session.get("messages") or []) == 0
        prompt = (BOOTSTRAP + text) if is_first else text
        session.setdefault("messages", []).append({"role": "user", "text": text, "at": _now()})
        agent = _get_or_create_agent(session, api_key, cwd)
        sessions[session_id] = session
        _save_store(data_root, store)
        agent_id = getattr(agent, "agent_id", "") or ""
    return agent, prompt, agent_id, data_root, time.time()


def _finish_turn(
    session_id: str,
    data_root: Path,
    agent: Any,
    reply: str,
    started: float,
) -> dict[str, Any]:
    if not reply:
        reply = "（本轮无文本回复，可能仍在执行工具。可再发一条「继续」查看结果。）"
    with _lock:
        store = _load_store(data_root)
        session = store["sessions"][session_id]
        session["agent_id"] = getattr(agent, "agent_id", session.get("agent_id"))
        session.setdefault("messages", []).append(
            {"role": "assistant", "text": reply, "at": _now()}
        )
        session["updated_at"] = _now()
        store["sessions"][session_id] = session
        _save_store(data_root, store)
        _busy[session_id] = False
        return {
            "ok": True,
            "session_id": session_id,
            "agent_id": session.get("agent_id") or "",
            "reply": reply,
            "elapsed_sec": round(time.time() - started, 2),
            "message_count": len(session.get("messages") or []),
        }


def _clear_busy(session_id: str) -> None:
    with _lock:
        _busy[session_id] = False


def _message_to_events(message: Any) -> list[dict[str, Any]]:
    """Map one SDKMessage into UI-friendly stream events."""
    out: list[dict[str, Any]] = []
    mtype = getattr(message, "type", "") or ""

    if mtype == "assistant":
        content = getattr(getattr(message, "message", None), "content", ()) or ()
        for block in content:
            text = getattr(block, "text", "") or ""
            if text:
                out.append({"type": "delta", "text": text})
        if not out:
            text = getattr(getattr(message, "message", None), "text", "") or ""
            if text:
                out.append({"type": "delta", "text": text})
        return out

    if mtype == "thinking":
        text = (getattr(message, "text", "") or "").strip()
        if text:
            snippet = text if len(text) <= 80 else text[:80] + "…"
            out.append({"type": "status", "text": f"思考中：{snippet}"})
        return out

    if mtype == "tool_call":
        name = getattr(message, "name", "") or ""
        status = getattr(message, "status", "") or ""
        label = _tool_label(str(name))
        if status == "running":
            out.append(
                {
                    "type": "tool",
                    "name": name,
                    "label": label,
                    "status": "running",
                    "text": f"正在{label}…",
                }
            )
        elif status == "completed":
            out.append(
                {
                    "type": "tool",
                    "name": name,
                    "label": label,
                    "status": "completed",
                    "text": f"已完成：{label}",
                }
            )
        elif status == "error":
            out.append(
                {
                    "type": "tool",
                    "name": name,
                    "label": label,
                    "status": "error",
                    "text": f"失败：{label}",
                }
            )
        return out

    if mtype == "status":
        text = str(getattr(message, "message", "") or getattr(message, "status", "") or "")
        # Skip raw engine lifecycle noise that causes UI flicker
        if text.upper() in {"RUNNING", "FINISHED", "COMPLETED", "CANCELLED", "ERROR"}:
            return out
        if text:
            out.append({"type": "status", "text": text})
        return out

    if mtype == "task":
        text = getattr(message, "text", "") or ""
        if text:
            out.append({"type": "status", "text": str(text)})
        return out

    return out


def cancel_session(session_id: str = "default") -> dict[str, Any]:
    """Unlock a stuck busy flag (client abort / disconnect)."""
    _clear_busy(session_id)
    return list_session_summary(session_id)


def iter_chat_events(
    message: str,
    session_id: str = "default",
    customer: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield stream events then a final ``done`` (or ``error``).

    Assistant text deltas are coalesced (~40ms) to reduce UI jank.
    """
    agent = None
    data_root: Path | None = None
    started = time.time()
    turn_closed = False
    try:
        agent, prompt, agent_id, data_root, started = _prepare_turn(message, session_id, customer)
        yield {"type": "start", "session_id": session_id, "agent_id": agent_id}

        run = agent.send(prompt)
        chunks: list[str] = []
        pending = ""
        last_flush = time.monotonic()

        def flush_pending() -> Iterator[dict[str, Any]]:
            nonlocal pending, last_flush
            if not pending:
                return
            yield {"type": "delta", "text": pending}
            pending = ""
            last_flush = time.monotonic()

        for message_obj in run.stream():
            for ev in _message_to_events(message_obj):
                if ev.get("type") == "delta":
                    piece = str(ev.get("text") or "")
                    if not piece:
                        continue
                    chunks.append(piece)
                    pending += piece
                    if time.monotonic() - last_flush >= 0.04 or len(pending) >= 24:
                        yield from flush_pending()
                else:
                    yield from flush_pending()
                    yield ev

        yield from flush_pending()

        try:
            result = run.wait()
        except Exception:
            result = None

        reply = "".join(chunks).strip()
        if not reply and result is not None:
            reply = (getattr(result, "result", None) or "") or ""
            if not isinstance(reply, str):
                reply = str(reply or "")
            reply = reply.strip()

        status = getattr(result, "status", None) if result is not None else None
        if status and str(status) not in ("finished", "success", "completed", "None") and not reply:
            raise RuntimeError(f"助手运行失败: {status}")

        done = _finish_turn(session_id, data_root, agent, reply, started)
        turn_closed = True
        yield {"type": "done", **done}
    except GeneratorExit:
        # Client aborted the SSE stream — must unlock busy (not an Exception).
        _clear_busy(session_id)
        raise
    except Exception as e:
        _clear_busy(session_id)
        turn_closed = True
        log.exception("cursor chat stream failed")
        yield {"type": "error", "message": str(e)}
    finally:
        if not turn_closed:
            _clear_busy(session_id)


def chat(message: str, session_id: str = "default", customer: str | None = None) -> dict[str, Any]:
    """Blocking chat — drains the stream and returns the final done payload."""
    final: dict[str, Any] | None = None
    err: str | None = None
    for ev in iter_chat_events(message, session_id=session_id, customer=customer):
        if ev.get("type") == "done":
            final = ev
        elif ev.get("type") == "error":
            err = str(ev.get("message") or "未知错误")
    if err:
        raise RuntimeError(err)
    if not final:
        raise RuntimeError("助手未返回结果")
    return {k: v for k, v in final.items() if k != "type"}


def sse_bytes(events: Iterator[dict[str, Any]]) -> Iterator[bytes]:
    """Encode events as Server-Sent Events frames."""
    for ev in events:
        payload = json.dumps(ev, ensure_ascii=False)
        yield f"data: {payload}\n\n".encode("utf-8")
