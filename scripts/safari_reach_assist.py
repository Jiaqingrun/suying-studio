#!/usr/bin/env python3
"""G5.R · Safari 辅助发布准备脚本（本人单账号 · 风控自负）.

Does NOT bypass captcha/login. Does NOT do matrix/anti-detect.
Reads publish_pack → opens Safari official URL → clipboard + Finder reveal.
Optional --mark-published hits local Reach API after human confirms success.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_DEFAULT = "http://127.0.0.1:8766"
PLATFORM_URLS = {
    "douyin": "https://creator.douyin.com/",
    "channels": "https://channels.weixin.qq.com/",
    "xhs": "https://creator.xiaohongshu.com/",
}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode(), strict=False)


def _post(url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode(), strict=False)


def load_pack(pack_dir: Path, platform: str) -> dict:
    pack_dir = pack_dir.expanduser().resolve()
    if not pack_dir.is_dir():
        raise SystemExit(f"pack_dir 不存在: {pack_dir}")
    video = pack_dir / "video.mp4"
    if not video.is_file():
        raise SystemExit(f"缺少 video.mp4: {video}")
    copy_path = pack_dir / "copy.zh.json"
    title, body = "", ""
    if copy_path.is_file():
        copy = json.loads(copy_path.read_text(encoding="utf-8"))
        plat = (copy.get("platforms") or {}).get(platform) or {}
        title = (plat.get("title") or copy.get("source_title") or "").strip()
        body = (plat.get("body") or "").strip()
    paste = pack_dir / f"reach_paste_{platform}.txt"
    if paste.is_file() and (not title or not body):
        text = paste.read_text(encoding="utf-8")
        # fallback: keep full paste for clipboard
        if not body:
            body = text
    return {
        "pack_dir": str(pack_dir),
        "video": str(video),
        "title": title,
        "body": body,
        "paste_card": str(paste) if paste.is_file() else "",
        "platform": platform,
        "url": PLATFORM_URLS.get(platform, PLATFORM_URLS["douyin"]),
    }


def resolve_from_queue(api: str, item_id: int) -> tuple[dict, int]:
    items = _get(f"{api}/reach/queue")
    rows = items.get("items") or items.get("queue") or []
    row = next((x for x in rows if int(x.get("id", -1)) == item_id), None)
    if not row:
        raise SystemExit(f"队列无 id={item_id}")
    pack_dir = row.get("pack_dir") or ""
    if not pack_dir:
        raise SystemExit(f"队列项 {item_id} 无 pack_dir")
    platform = (row.get("platform") or "douyin").lower()
    return load_pack(Path(pack_dir), platform), item_id


def pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def open_safari(url: str) -> None:
    script = f'''
    tell application "Safari"
      activate
      open location "{url}"
    end tell
    '''
    subprocess.run(["osascript", "-e", script], check=False)


def reveal_in_finder(path: str) -> None:
    subprocess.run(["open", "-R", path], check=False)


def main() -> int:
    p = argparse.ArgumentParser(description="速影 Safari 辅助发布准备（风控自负）")
    p.add_argument("--api", default=API_DEFAULT)
    p.add_argument("--pack-dir", type=Path, help="publish_pack 目录")
    p.add_argument("--queue-id", type=int, help="触达队列 id")
    p.add_argument("--platform", default="douyin")
    p.add_argument("--accept-risk", action="store_true", help="必须显式确认风控自负")
    p.add_argument("--clipboard", choices=["title", "body", "none"], default="title")
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--no-reveal", action="store_true")
    p.add_argument("--mark-published", action="store_true", help="人确认发成功后写回队列")
    p.add_argument(
        "--export-json",
        type=Path,
        help="把物料 JSON 写到指定路径（供 G5.V 视觉全自动消费）",
    )
    args = p.parse_args()

    if not args.accept_risk:
        print("拒绝执行：必须加 --accept-risk（表示风控自负）。", file=sys.stderr)
        print("边界见 docs/REACH_NON_GOALS.md", file=sys.stderr)
        return 2

    item_id: int | None = None
    if args.queue_id is not None:
        payload, item_id = resolve_from_queue(args.api, args.queue_id)
    elif args.pack_dir:
        payload = load_pack(args.pack_dir, args.platform.lower())
    else:
        # latest awaiting from API
        try:
            q = _get(f"{args.api}/reach/queue")
            rows = q.get("items") or q.get("queue") or []
            row = next(
                (
                    x
                    for x in rows
                    if str(x.get("status", "")).startswith("await")
                    or x.get("status") in ("awaiting_human", "ready", "queued")
                ),
                None,
            )
            if not row:
                raise SystemExit("无 awaiting 队列项；请传 --pack-dir 或 --queue-id")
            item_id = int(row["id"])
            payload, item_id = resolve_from_queue(args.api, item_id)
        except urllib.error.URLError as e:
            raise SystemExit(f"引擎不可达 {args.api}: {e}") from e

    out = {"ok": True, "risk_accepted": True, "mode": "g5v_payload", **payload, "queue_id": item_id}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"exported: {args.export_json}")

    if not args.no_open:
        open_safari(payload["url"])
        print(f"\n已用 Safari 打开: {payload['url']}")

    if not args.no_reveal:
        reveal_in_finder(payload["video"])
        print(f"已在 Finder 中显示视频: {payload['video']}")

    if args.clipboard == "title" and payload["title"]:
        pbcopy(payload["title"])
        print("剪贴板 ← 标题（Cmd+V 粘贴到标题框）")
    elif args.clipboard == "body" and payload["body"]:
        pbcopy(payload["body"])
        print("剪贴板 ← 正文")

    print(
        """
下一步（人工 / Agent）:
1. 确认 Safari 已登录本人要发的账号
2. 进入「发布视频 / 上传」→ 选 Finder 高亮的 video.mp4
3. 粘贴标题后运行:  ... --clipboard body --no-open --no-reveal --accept-risk
4. 粘贴正文；预览
5. 若出现验证码/扫码/二次验证 → 停，本人处理完再继续
6. 无挑战时可点「发布」（责任自负）
7. 成功后:  ... --queue-id N --mark-published --accept-risk --no-open --no-reveal
"""
    )

    if args.mark_published:
        if item_id is None:
            raise SystemExit("--mark-published 需要 --queue-id")
        res = _post(
            f"{args.api}/reach/queue/{item_id}/status",
            {"status": "published", "note": "safari_assist_human_confirmed"},
        )
        print("mark_published:", json.dumps(res, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
