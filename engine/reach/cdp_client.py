"""Minimal Chrome DevTools Protocol client (stdlib WebSocket)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
from typing import Any
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CDP = os.environ.get("SUYING_CDP_URL", "http://127.0.0.1:9222")


class CdpError(RuntimeError):
    pass


def list_tabs(cdp_http: str | None = None) -> list[dict[str, Any]]:
    base = (cdp_http or DEFAULT_CDP).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/json/list", timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise CdpError(f"CDP 不可用（{base}）: {e}") from e


def find_tab_ws(
    *,
    url_substr: str,
    cdp_http: str | None = None,
    exclude_substr: str = "service-worker",
) -> tuple[str, str]:
    for t in list_tabs(cdp_http):
        # CDP exposes cross-origin iframe targets in /json/list too. Their URL
        # may contain the creator domain only inside a `from=` query parameter,
        # so selecting them causes uploads to search the chat iframe DOM.
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if exclude_substr and exclude_substr in url:
            continue
        if url_substr in url and t.get("webSocketDebuggerUrl"):
            return str(t["webSocketDebuggerUrl"]), url
    raise CdpError(f"未找到含 {url_substr!r} 的 Chrome 标签（请开官方页并启用 --remote-debugging-port）")


class CdpSession:
    """Blocking CDP session over a single WebSocket."""

    def __init__(self, ws_url: str, timeout: float = 30.0):
        self._id = 0
        self._sock, leftover = self._connect(ws_url, timeout=timeout)
        self._buf = leftover or b""

    @staticmethod
    def _connect(ws_url: str, timeout: float = 30.0) -> tuple[socket.socket, bytes]:
        u = urlparse(ws_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path = f"{path}?{u.query}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(req)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise CdpError("CDP WebSocket 握手失败")
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise CdpError(f"CDP WebSocket 握手被拒: {head[:200]!r}")
        accept = None
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"sec-websocket-accept:"):
                accept = line.split(b":", 1)[1].strip()
        expect = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if accept and accept != expect:
            raise CdpError("CDP WebSocket Accept 校验失败")
        return sock, rest

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> CdpSession:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _send_frame(self, payload: bytes) -> None:
        # Client frames must be masked
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x81, 0x80 | n) + mask
        elif n < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, n) + mask
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, n) + mask
        self._sock.sendall(header + masked)

    def _recv_frame(self) -> bytes:
        while True:
            while len(self._buf) < 2:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise CdpError("CDP 连接断开")
                self._buf += chunk
            b1, b2 = self._buf[0], self._buf[1]
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            ln = b2 & 0x7F
            idx = 2
            if ln == 126:
                while len(self._buf) < 4:
                    self._buf += self._sock.recv(4096)
                ln = struct.unpack("!H", self._buf[2:4])[0]
                idx = 4
            elif ln == 127:
                while len(self._buf) < 10:
                    self._buf += self._sock.recv(4096)
                ln = struct.unpack("!Q", self._buf[2:10])[0]
                idx = 10
            mask = b""
            if masked:
                while len(self._buf) < idx + 4:
                    self._buf += self._sock.recv(4096)
                mask = self._buf[idx : idx + 4]
                idx += 4
            while len(self._buf) < idx + ln:
                self._buf += self._sock.recv(4096)
            payload = self._buf[idx : idx + ln]
            self._buf = self._buf[idx + ln :]
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                raise CdpError("CDP WebSocket 已关闭")
            if opcode == 0x9:  # ping → pong
                # reply pong
                n = len(payload)
                mask_b = os.urandom(4)
                masked_p = bytes(b ^ mask_b[i % 4] for i, b in enumerate(payload))
                self._sock.sendall(struct.pack("!BB", 0x8A, 0x80 | n) + mask_b + masked_p)
                continue
            if opcode in (0x1, 0x2, 0x0):
                return payload

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        self._id += 1
        msg_id = self._id
        self._send_frame(json.dumps({"id": msg_id, "method": method, "params": params or {}}).encode())
        old = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            while True:
                raw = self._recv_frame()
                data = json.loads(raw.decode("utf-8"))
                if data.get("id") == msg_id:
                    if "error" in data:
                        raise CdpError(f"{method}: {data['error']}")
                    return data.get("result")
                # ignore events
        finally:
            self._sock.settimeout(old)

    def inject_popup_dismisser(self) -> bool:
        """Inject 速影弹窗拦截 script into every new document (Chrome 137+ 扩展保底)."""
        try:
            from engine.reach.browser import dismiss_popups_extension_dir

            ext = dismiss_popups_extension_dir()
            js_path = (ext / "content.js") if ext else None
            if not js_path or not js_path.is_file():
                root = Path(__file__).resolve().parents[2] / "tools" / "chrome-ext-dismiss-popups" / "content.js"
                js_path = root if root.is_file() else None
            if not js_path:
                return False
            source = js_path.read_text(encoding="utf-8")
            try:
                self.call("Page.enable")
            except CdpError:
                pass
            self.call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
            # also run once on current document (content.js is already an IIFE)
            try:
                self.evaluate(source)
            except CdpError:
                pass
            return True
        except Exception:
            return False

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        r = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        if not r:
            return None
        if r.get("exceptionDetails"):
            raise CdpError(str(r["exceptionDetails"]))
        return (r.get("result") or {}).get("value")

    def evaluate_in_frames(self, expression: str) -> Any:
        """Run expression in main world and each child frame (channels form is often in iframe).

        ``expression`` should be an IIFE. Prefer writing JS that walks same-origin
        iframes via contentDocument; this CDP path covers cross-origin frames too.
        Returns the first non-null / non-undefined value from any frame.
        """
        main = self.evaluate(expression)
        if main not in (None, False, {}, []):
            # still try frames if main looks like a soft-empty probe
            if not (isinstance(main, dict) and main.get("ok") is False and main.get("via") == "none"):
                if not (isinstance(main, dict) and main.get("state") in ("waiting", "upload", "other")):
                    return main
        try:
            self.call("Page.enable")
        except CdpError:
            pass
        try:
            tree = self.call("Page.getFrameTree") or {}
        except CdpError:
            return main
        frames: list[dict[str, Any]] = []

        def _walk(node: dict[str, Any]) -> None:
            fr = node.get("frame") or {}
            if fr.get("id"):
                frames.append(fr)
            for child in node.get("childFrames") or []:
                _walk(child)

        _walk(tree.get("frameTree") or {})
        best = main
        for fr in frames[1:]:  # skip root; already evaluated
            fid = fr.get("id")
            if not fid:
                continue
            try:
                world = self.call(
                    "Page.createIsolatedWorld",
                    {
                        "frameId": fid,
                        "worldName": "suying_reach",
                        "grantUniversalAccess": True,
                    },
                )
                ctx = (world or {}).get("executionContextId")
                if not ctx:
                    continue
                r = self.call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "contextId": int(ctx),
                        "returnByValue": True,
                    },
                )
                if not r or r.get("exceptionDetails"):
                    continue
                val = (r.get("result") or {}).get("value")
                if val in (None, False):
                    continue
                if isinstance(val, dict):
                    if val.get("ok") is True or val.get("state") in (
                        "form",
                        "upload_failed",
                        "need_human",
                        "need_login",
                        "uploading",
                        "inbox",
                        "scrolled",
                    ):
                        return val
                    if val.get("bodyOk") or val.get("titleOk"):
                        return val
                    best = val
                else:
                    return val
            except CdpError:
                continue
        return best

    def set_file_input(self, backend_node_id: int, files: list[str]) -> None:
        self.call("DOM.setFileInputFiles", {"files": files, "backendNodeId": backend_node_id})

    def move_xy(self, x: float, y: float) -> None:
        """Trusted mouse move (hover) at CSS viewport coordinates."""
        self.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": float(x), "y": float(y)},
        )

    def click_xy(self, x: float, y: float) -> None:
        """Trusted mouse click at viewport coordinates (opens modals that ignore element.click())."""
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            params: dict[str, Any] = {"type": typ, "x": float(x), "y": float(y)}
            if typ != "mouseMoved":
                params.update({"button": "left", "clickCount": 1})
            self.call("Input.dispatchMouseEvent", params)

    def screenshot_scale(self) -> dict[str, float]:
        """CSS viewport vs captured PNG scale (retina DPR). Input.* uses CSS px."""
        try:
            m = self.call("Page.getLayoutMetrics") or {}
        except CdpError:
            m = {}
        css = m.get("cssVisualViewport") or m.get("cssLayoutViewport") or {}
        vis = m.get("visualViewport") or m.get("layoutViewport") or {}
        css_w = float(css.get("clientWidth") or 0) or 1.0
        css_h = float(css.get("clientHeight") or 0) or 1.0
        png_w = float(vis.get("clientWidth") or css_w)
        png_h = float(vis.get("clientHeight") or css_h)
        # Prefer devicePixelRatio when layout metrics already CSS-sized
        try:
            dpr = float(
                self.evaluate("window.devicePixelRatio || 1") or 1
            )
        except Exception:
            dpr = png_w / css_w if css_w else 1.0
        if abs(png_w - css_w) < 1.0 and dpr > 1.01:
            # metrics already CSS; screenshot will still be DPR-scaled
            return {"css_w": css_w, "css_h": css_h, "dpr": dpr, "sx": dpr, "sy": dpr}
        sx = png_w / css_w if css_w else dpr
        sy = png_h / css_h if css_h else dpr
        return {"css_w": css_w, "css_h": css_h, "dpr": dpr, "sx": sx, "sy": sy}

    def click_xy_and_set_files(
        self,
        x: float,
        y: float,
        files: list[str],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Trusted mouse click → intercept Page.fileChooserOpened → DOM.setFileInputFiles.

        Douyin cover「上传封面」ignores element.click(); only Input.dispatchMouseEvent opens
        the real file chooser. Returns {ok, backendNodeId, error}.
        """
        try:
            self.call("Page.enable")
        except CdpError:
            pass
        try:
            self.call("Page.setInterceptFileChooserDialog", {"enabled": True})
        except CdpError as e:
            return {"ok": False, "error": f"intercept_unavailable:{e}", "backendNodeId": None}

        # Drain stale events briefly
        old_to = self._sock.gettimeout()
        self._sock.settimeout(0.05)
        try:
            while True:
                try:
                    self._recv_frame()
                except TimeoutError:
                    break
        finally:
            self._sock.settimeout(old_to)

        self.click_xy(x, y)

        chooser: dict[str, Any] | None = None
        deadline = time.time() + timeout
        self._sock.settimeout(0.4)
        try:
            while time.time() < deadline:
                try:
                    raw = self._recv_frame()
                except TimeoutError:
                    continue
                data = json.loads(raw.decode("utf-8"))
                if data.get("method") == "Page.fileChooserOpened":
                    chooser = data.get("params") or {}
                    break
        finally:
            self._sock.settimeout(old_to)

        if not chooser or not chooser.get("backendNodeId"):
            return {"ok": False, "error": "file_chooser_not_opened", "backendNodeId": None}
        bid = int(chooser["backendNodeId"])
        self.set_file_input(bid, files)
        return {"ok": True, "backendNodeId": bid, "mode": chooser.get("mode"), "error": None}

    def query_backend_node(self, selector: str, pierce: bool = True) -> int | None:
        doc = self.call("DOM.getDocument", {"depth": -1, "pierce": pierce})
        root = (doc or {}).get("root") or {}
        node_id = root.get("nodeId")
        if not node_id:
            return None
        r = self.call("DOM.querySelector", {"nodeId": node_id, "selector": selector})
        nid = (r or {}).get("nodeId") or 0
        if not nid:
            return None
        desc = self.call("DOM.describeNode", {"nodeId": nid})
        return ((desc or {}).get("node") or {}).get("backendNodeId")

    def query_all_file_inputs(self, pierce: bool = True) -> list[dict[str, Any]]:
        """List file inputs with backendNodeId + accept (pierce shadow)."""
        raw = self.evaluate(
            """(() => {
              function walk(root, out) {
                const nodes = root.querySelectorAll ? root.querySelectorAll('input[type=file]') : [];
                for (const n of nodes) {
                  out.push({
                    accept: n.getAttribute('accept') || '',
                    id: n.id || '',
                    name: n.name || '',
                    className: String(n.className || '').slice(0, 80),
                  });
                }
                const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of all) {
                  if (el.shadowRoot) walk(el.shadowRoot, out);
                }
              }
              const out = [];
              walk(document, out);
              return out;
            })()"""
        ) or []
        # Map to backend ids via sequential query — prefer image/video accept in callers
        results = []
        for i, meta in enumerate(raw if isinstance(raw, list) else []):
            # Re-query by index among all file inputs including shadow is hard;
            # use pierce document + querySelectorAll via CDP DOM.
            results.append(dict(meta, index=i))
        # Also get backend ids via CDP DOM
        try:
            doc = self.call("DOM.getDocument", {"depth": -1, "pierce": pierce})
            root_id = ((doc or {}).get("root") or {}).get("nodeId")
            if root_id:
                qs = self.call("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type=file]"})
                node_ids = (qs or {}).get("nodeIds") or []
                out = []
                for nid in node_ids:
                    desc = self.call("DOM.describeNode", {"nodeId": nid})
                    node = (desc or {}).get("node") or {}
                    attrs = node.get("attributes") or []
                    attr_map = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
                    bid = node.get("backendNodeId")
                    out.append(
                        {
                            "backendNodeId": bid,
                            "accept": attr_map.get("accept", ""),
                            "id": attr_map.get("id", ""),
                            "name": attr_map.get("name", ""),
                            "class": attr_map.get("class", ""),
                        }
                    )
                if out:
                    return out
        except CdpError:
            pass
        return results

    def capture_screenshot_png(self, path: str | Path) -> Path:
        """Save viewport screenshot via CDP Page.captureScreenshot."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.call("Page.enable")
        except CdpError:
            pass
        r = self.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        data = (r or {}).get("data") or ""
        path.write_bytes(base64.b64decode(data))
        return path
