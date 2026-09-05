#!/usr/bin/env python3
"""Backfill coarse visual tags for a customer (description + theme/scene/objects/actions + re-embed).

Designed for life-service customers whose cliplets still carry metadata-only
heuristic descriptions (IMG_* / uncategorized). Does NOT claim semantic.v1.

Usage:
  python3 scripts/backfill_customer_visual_tags.py --customer 臻享丽人 --force
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from engine.catalog.db import Asset, Cliplet, Customer, get_session
from engine.catalog.industry_pack import clear_pack_cache, pack_id_for_customer
from engine.catalog.ollama_runtime import OLLAMA_KEEP_ALIVE, heavy_request
from engine.catalog.semantic_tags import annotate_cliplet
from engine.catalog.vector_index import index_cliplet
from engine.config.settings import load_settings
from engine.ingest.proxy import analysis_video_path
from engine.ingest.vision_caption import VISION_NUM_CTX, extract_frame, _parse_json_response

ALLOWED_SCENES = (
    "storefront",
    "company_image",
    "office",
    "product_closeup",
    "product_use",
    "people_activity",
    "other_visible",
)
ALLOWED_THEMES = (
    "门店形象",
    "护肤护理",
    "身体与形体",
    "服务沟通",
    "品牌故事",
    "default",
)

SCENE_TO_THEME = {
    "storefront": "门店形象",
    "office": "门店形象",
    "company_image": "品牌故事",
    "product_closeup": "护肤护理",
    "product_use": "护肤护理",
    "people_activity": "服务沟通",
    "other_visible": "default",
}


def _is_placeholder_desc(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 40:
        return True
    markers = ("分类:", "实拍业务素材", "素材:IMG_", "片段")
    return sum(1 for m in markers if m in t) >= 2


def _vision_tag_frame(frame: Path, *, model: str, timeout: float) -> dict[str, Any] | None:
    prompt = f"""你是门店生活服务实拍素材的粗标注器。只写肉眼可见事实。
禁止抄写功效/诊疗/承诺/广告法极限词；看不清的文字不要编造。
严格返回单个 JSON 对象，不要 Markdown。
scene 只能选自：{json.dumps(list(ALLOWED_SCENES), ensure_ascii=False)}
theme 只能选自：{json.dumps(list(ALLOWED_THEMES), ensure_ascii=False)}
JSON 形状：
{{
  "description": "18-120字中文，写空间/物体/动作，不要口号",
  "scene": "office",
  "theme": "门店形象",
  "objects": ["最多5个可见物体短名"],
  "actions": ["最多3个可见动作短名"],
  "people_present": false
}}
规则：有清晰人物则 people_present=true 且 scene=people_activity；无人则 false。
荣誉墙/奖状/锦旗/品牌介绍墙 → company_image；门头招牌 → storefront；
走廊/接待/休息区/店内空间 → office；护理品器具特写 → product_closeup；
护理操作台面过程 → product_use；其他 → other_visible。
"""
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": VISION_NUM_CTX},
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(frame.read_bytes()).decode("ascii")],
            }
        ],
    }
    result = heavy_request(
        kind="vision",
        path="/api/chat",
        payload=payload,
        timeout_sec=timeout,
        model=model,
    )
    if not result.get("ok"):
        return None
    body = result.get("body") or {}
    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    parsed = _parse_json_response(str(message.get("content") or ""))
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_tag(raw: dict[str, Any]) -> dict[str, Any]:
    scene = str(raw.get("scene") or "").strip()
    if scene not in ALLOWED_SCENES:
        scene = "other_visible"
    people = bool(raw.get("people_present"))
    if people:
        scene = "people_activity"
    theme = str(raw.get("theme") or "").strip()
    if theme not in ALLOWED_THEMES:
        theme = SCENE_TO_THEME.get(scene, "default")
    desc = str(raw.get("description") or "").strip()
    desc = desc.replace("\n", " ").strip()
    if len(desc) > 180:
        desc = desc[:180].rstrip()
    objects = [str(x).strip() for x in (raw.get("objects") or []) if str(x).strip()][:5]
    actions = [str(x).strip() for x in (raw.get("actions") or []) if str(x).strip()][:3]
    return {
        "description": desc,
        "scene": scene,
        "theme": theme,
        "objects": objects,
        "actions": actions,
        "people_present": people,
    }


def _ensure_customer_pack(session, customer: Customer, pack_id: str) -> None:
    profile = dict(customer.profile_json or {}) if isinstance(customer.profile_json, dict) else {}
    if profile.get("industry_pack") != pack_id:
        profile["industry_pack"] = pack_id
        customer.profile_json = profile
        session.add(customer)
        session.commit()
        clear_pack_cache()


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", default="臻享丽人")
    ap.add_argument("--pack", default="life-service")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    cache_dir = Path(settings.paths.data_root) / "cache" / "visual_tags"
    cache_dir.mkdir(parents=True, exist_ok=True)

    session = get_session()
    try:
        customer = session.scalar(select(Customer).where(Customer.name == args.customer))
        if customer is None:
            print(f"customer_not_found:{args.customer}", file=sys.stderr, flush=True)
            return 2
        if not args.dry_run:
            _ensure_customer_pack(session, customer, args.pack)
        pack_id = pack_id_for_customer(customer.name, customer.profile_json)
        _log(f"customer={customer.name} id={customer.id} pack={pack_id}")

        rows = list(
            session.scalars(
                select(Cliplet)
                .join(Asset, Cliplet.asset_id == Asset.id)
                .where(Asset.customer_id == customer.id)
                .order_by(Cliplet.id.asc())
                .limit(args.limit)
            ).all()
        )
        stats = {
            "scanned": len(rows),
            "updated": 0,
            "skipped": 0,
            "vision_fail": 0,
            "embed_ok": 0,
            "embed_fail": 0,
            "by_scene": {},
            "by_theme": {},
        }
        t0 = time.time()
        for i, row in enumerate(rows, 1):
            scene_ok = (row.scene or "").strip() not in ("", "default")
            desc_ok = not _is_placeholder_desc(row.description or "")
            if not args.force and scene_ok and desc_ok:
                stats["skipped"] += 1
                continue
            asset = session.get(Asset, row.asset_id)
            if asset is None:
                stats["vision_fail"] += 1
                continue
            video = analysis_video_path(asset)
            if not video.exists():
                stats["vision_fail"] += 1
                _log(f"[{i}/{len(rows)}] missing_video clip={row.id}")
                continue
            start = float(row.start_sec or 0.0)
            end = float(row.end_sec or (start + 4.0))
            mid = start + max(0.1, (end - start) * 0.4)
            frame = cache_dir / f"c{row.id}_{int(mid * 10)}.jpg"
            if not extract_frame(video, mid, frame):
                stats["vision_fail"] += 1
                _log(f"[{i}/{len(rows)}] frame_fail clip={row.id}")
                continue
            if args.dry_run:
                _log(f"[{i}/{len(rows)}] dry-run clip={row.id} frame={frame.name}")
                stats["updated"] += 1
                continue
            raw = _vision_tag_frame(frame, model=args.model, timeout=args.timeout)
            if not raw:
                stats["vision_fail"] += 1
                _log(f"[{i}/{len(rows)}] vision_fail clip={row.id}")
                continue
            tag = _normalize_tag(raw)
            if not tag["description"]:
                stats["vision_fail"] += 1
                continue
            cat = (asset.category or row.category or "未分类").strip() or "未分类"
            row.description = f"分类:{cat}；{tag['description']}；时段{start:.1f}-{end:.1f}秒"
            row.theme = tag["theme"]
            row.theme_score = 0.7
            row.scene = tag["scene"]
            row.objects_json = tag["objects"]
            row.actions_json = tag["actions"]
            # Refine with industry-pack keyword rules on the new description.
            annotate_cliplet(row, asset, pack_id=pack_id)
            # Keep vision scene when pack falls back to default.
            if (row.scene or "default") == "default" and tag["scene"] != "default":
                row.scene = tag["scene"]
            if (row.theme or "default") == "default" and tag["theme"] != "default":
                row.theme = tag["theme"]
                row.theme_score = 0.7
            if not row.objects_json:
                row.objects_json = tag["objects"]
            if not row.actions_json:
                row.actions_json = tag["actions"]
            session.add(row)
            session.commit()
            if not args.skip_embed:
                try:
                    index_cliplet(session, row, require_model=False)
                    stats["embed_ok"] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["embed_fail"] += 1
                    _log(f"[{i}/{len(rows)}] embed_fail clip={row.id}: {exc}")
            stats["updated"] += 1
            stats["by_scene"][row.scene or "default"] = stats["by_scene"].get(row.scene or "default", 0) + 1
            stats["by_theme"][row.theme or "default"] = stats["by_theme"].get(row.theme or "default", 0) + 1
            elapsed = time.time() - t0
            _log(
                f"[{i}/{len(rows)}] ok clip={row.id} scene={row.scene} theme={row.theme} "
                f"objs={row.objects_json} {elapsed:.0f}s"
            )
        stats["elapsed_sec"] = round(time.time() - t0, 1)
        _log(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0 if stats["updated"] or stats["skipped"] else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
