"""Customer-scoped notification delivery for G7 message summaries.

Only redacted message metadata and an official reply URL leave the machine.
Credentials are kept in a mode-0600 workspace secret file and are never
returned by the API.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import Header
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from engine.config.settings import load_settings

NTFY_TIMEOUT_SEC = 6
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_config_lock = threading.Lock()
_SENSITIVE_RE = re.compile(
    r"(?:\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\d[ -]?){12,19})"
)


@dataclass(frozen=True)
class NtfyPublicConfig:
    enabled: bool
    server_url: str
    topic: str
    auth_mode: str
    token_configured: bool
    username_configured: bool
    password_configured: bool


def _secrets_path() -> Path:
    path = load_settings().paths.data_root / "secrets" / "reach-notifications.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    return path


def _read_all() -> dict[str, Any]:
    path = _secrets_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(value: dict[str, Any]) -> None:
    path = _secrets_path()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _customer_key(customer_id: int) -> str:
    return str(int(customer_id))


def _public_addresses(host: str, port: int) -> list[str]:
    try:
        values = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("ntfy server 无法解析") from exc
    if not values:
        raise ValueError("ntfy server 无解析结果")
    addresses: list[str] = []
    for item in values:
        address = ipaddress.ip_address(item[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("ntfy server 不得解析到内网、回环或保留地址")
        value = str(address)
        if value not in addresses:
            addresses.append(value)
    return addresses


def validate_ntfy_server(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ntfy server URL 不得包含凭证、查询参数或片段")
    if not parsed.hostname or parsed.path not in ("", "/"):
        raise ValueError("ntfy server URL 只能填写服务根地址")
    is_dev_loopback = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("SUYING_ALLOW_LOCAL_NTFY_HTTP") == "1"
    )
    if parsed.scheme != "https" and not is_dev_loopback:
        raise ValueError("ntfy server 必须使用 HTTPS；本机开发需显式设置 SUYING_ALLOW_LOCAL_NTFY_HTTP=1")
    if parsed.scheme == "https":
        _public_addresses(parsed.hostname, parsed.port or 443)
    if parsed.port and not (1 <= parsed.port <= 65535):
        raise ValueError("ntfy server 端口无效")
    return value


def validate_topic(topic: str) -> str:
    value = (topic or "").strip()
    if not _TOPIC_RE.fullmatch(value):
        raise ValueError("ntfy topic 仅允许 1–64 位字母、数字、下划线或连字符")
    return value


def public_config(customer_id: int) -> NtfyPublicConfig:
    cfg = _read_all().get(_customer_key(customer_id)) or {}
    return NtfyPublicConfig(
        enabled=bool(cfg.get("enabled")),
        server_url=str(cfg.get("server_url") or ""),
        topic=str(cfg.get("topic") or ""),
        auth_mode=str(cfg.get("auth_mode") or "none"),
        token_configured=bool(cfg.get("token")),
        username_configured=bool(cfg.get("username")),
        password_configured=bool(cfg.get("password")),
    )


def save_config(customer_id: int, body: dict[str, Any]) -> NtfyPublicConfig:
    with _config_lock:
        all_cfg = _read_all()
        key = _customer_key(customer_id)
        old = dict(all_cfg.get(key) or {})
        enabled = bool(body.get("enabled", old.get("enabled", False)))
        raw_server = str(body.get("server_url") or old.get("server_url") or "")
        raw_topic = str(body.get("topic") or old.get("topic") or "")
        if enabled and (not raw_server or not raw_topic):
            raise ValueError("启用 ntfy 前必须填写 server URL 与 topic")
        server_url = validate_ntfy_server(raw_server) if raw_server else ""
        topic = validate_topic(raw_topic) if raw_topic else ""
        auth_mode = str(body.get("auth_mode") or old.get("auth_mode") or "none")
        if auth_mode not in {"none", "token", "basic"}:
            raise ValueError("ntfy auth_mode 仅允许 none、token、basic")
        cfg = {
            **old,
            "enabled": enabled,
            "server_url": server_url,
            "topic": topic,
            "auth_mode": auth_mode,
        }
        for field in ("token", "username", "password"):
            if bool(body.get(f"clear_{field}")):
                cfg.pop(field, None)
            elif field in body and body[field] is not None:
                value = str(body[field])
                if value:
                    cfg[field] = value
        if auth_mode == "token" and not cfg.get("token"):
            raise ValueError("token 认证需要填写 token")
        if auth_mode == "basic" and (not cfg.get("username") or not cfg.get("password")):
            raise ValueError("用户名密码认证需要同时填写用户名和密码")
        all_cfg[key] = cfg
        _write_all(all_cfg)
    return public_config(customer_id)


def redact_summary(value: str) -> str:
    compact = " ".join((value or "").split())[:200]
    return _SENSITIVE_RE.sub("[已脱敏]", compact)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection pinned to an already-vetted address to prevent DNS rebinding."""

    def __init__(self, host: str, port: int, address: str, *, timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def send_ntfy(
    customer_id: int,
    *,
    title: str,
    summary: str,
    reply_url: str,
    test: bool = False,
) -> dict[str, Any]:
    cfg = _read_all().get(_customer_key(customer_id)) or {}
    if not cfg.get("enabled") and not test:
        return {"sent": False, "status": "disabled"}
    server = validate_ntfy_server(str(cfg.get("server_url") or ""))
    topic = validate_topic(str(cfg.get("topic") or ""))
    parsed = urlparse(server)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    target_path = f"/{quote(topic, safe='')}"
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": Header(
            ("[测试] " if test else "") + (title.strip()[:100] or "速影 · 新消息"),
            "utf-8",
        ).encode(),
        "Tags": "test_tube" if test else "incoming_envelope",
        "Priority": "default",
    }
    if reply_url:
        headers["Click"] = quote(reply_url, safe=":/?&=#%")
    mode = str(cfg.get("auth_mode") or "none")
    if mode == "token":
        headers["Authorization"] = f"Bearer {cfg.get('token', '')}"
    elif mode == "basic":
        raw = f"{cfg.get('username', '')}:{cfg.get('password', '')}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    payload = redact_summary(summary).encode("utf-8")
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        # Resolve once, reject the entire answer set if any address is private,
        # then connect to that exact vetted address while preserving TLS SNI.
        addresses = _public_addresses(str(parsed.hostname), port)
        connection = _PinnedHTTPSConnection(
            str(parsed.hostname), port, addresses[0], timeout=NTFY_TIMEOUT_SEC
        )
    else:
        # Only explicit loopback development HTTP reaches this branch.
        connection = http.client.HTTPConnection(
            str(parsed.hostname), port=port, timeout=NTFY_TIMEOUT_SEC
        )
    try:
        connection.request("POST", target_path, body=payload, headers=headers)
        response = connection.getresponse()
        status = int(response.status)
        response.read(4096)
        if not 200 <= status < 300:
            raise RuntimeError(f"ntfy 返回 HTTP {status}")
    except (http.client.HTTPException, ssl.SSLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"ntfy 推送失败：{type(exc).__name__}") from exc
    finally:
        connection.close()
    return {
        "sent": True,
        "status": "sent",
        "test": test,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


def send_ntfy_async(
    customer_id: int, *, title: str, summary: str, reply_url: str, audit: Any
) -> None:
    """Fire-and-forget delivery; failures never block or fail the scan."""

    def run() -> None:
        try:
            result = send_ntfy(
                customer_id, title=title, summary=summary, reply_url=reply_url
            )
            audit(result.get("status", "unknown"), "")
        except Exception as exc:  # noqa: BLE001
            audit("failed", str(exc)[:300])

    threading.Thread(target=run, name="reach-ntfy", daemon=True).start()
