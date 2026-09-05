"""跟镜精品 · 场景分类覆盖与选片。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, ClipletUsage
from engine.ingest.quality import CLIPLET_STATUS_REJECTED_BLUR, MIN_QUALITY_SCORE, is_usable_quality
from engine.pack.scene_tour_copy import bucket_label, reason_zh

# 跟镜内时长探测缓存，避免规划时对全库反复 ffprobe（卡顿热点）
_PROBE_CACHE: dict[str, tuple[float, float]] = {}  # path -> (mtime, duration)


def _probe_duration_cached(path: Path) -> float:
    try:
        mtime = float(path.stat().st_mtime)
    except OSError:
        return 0.0
    key = str(path.resolve()) if path.exists() else str(path)
    hit = _PROBE_CACHE.get(key)
    if hit and abs(hit[0] - mtime) < 0.01:
        return float(hit[1])
    from engine.pack.scene_tour_timing import _probe_media_duration

    dur = float(_probe_media_duration(path) or 0.0)
    _PROBE_CACHE[key] = (mtime, dur)
    if len(_PROBE_CACHE) > 4000:
        # 简单裁剪，避免无限涨
        for k in list(_PROBE_CACHE.keys())[:800]:
            _PROBE_CACHE.pop(k, None)
    return dur


def cliplet_bucket(row: Cliplet) -> str:
    sem = row.semantic_json if isinstance(row.semantic_json, dict) else {}
    raw = sem.get("scene_bucket") or row.scene or "other"
    key = str(raw).strip() or "other"
    if key in ("default", ""):
        return "other"
    # 仓配常见中文/别名 → 跟镜站位键
    aliases = {
        "门店门头": "storefront",
        "门头": "storefront",
        "仓内货架": "warehouse",
        "仓库": "warehouse",
        "装车卸货": "loading",
        "产品特写": "product_closeup",
        "entrance": "storefront",
        # 注意：life-service 站位键也叫 supply，禁止再映射成 warehouse
        "inventory_full": "warehouse",
        "stacking": "warehouse",
        "delivery": "loading",
        "transport": "loading",
        "other_visible": "other",
    }
    return aliases.get(key, key)


_LIFE_SERVICE_RULES: list[tuple[str, tuple[str, ...]]] = [
    # 更具体的优先
    ("sterilize", ("消毒室", "sterilizing", "消毒间")),
    ("slippers", ("拖鞋", "鞋架", "鞋柜", "换履", "换鞋")),
    ("honor_wall", ("奖牌", "奖杯", "荣誉墙", "奖状")),
    ("culture_wall", ("企业文化", "服务原则", "品牌介绍", "展板", "章程", "承诺文字")),
    ("vanity", ("梳妆", "圆镜", "发梳", "香氛", "化妆台")),
    ("tea", ("茶点", "清茶", "茶具", "小几", "鲜花摆")),
    ("treatment_bed", ("护理床", "巾单", "床位", "铺床")),
    ("treatment_action", ("护理手法", "护理操作", "手法", "技师护理", "做护理")),
    ("treatment_room", ("护理间", "护理室", "包间", "仪器", "理疗床")),
    ("supply", ("护理用品", "护理品", "备料", "置物架", "酒精湿巾", "棉签", "喷雾瓶")),
    ("corridor", ("走廊", "过道", "拱形门", "门洞", "廊道")),
    ("honor_wall", ("证书", "展示墙")),  # 证书墙偏荣誉/展示
]


def infer_station_bucket(
    industry_key: str | None,
    *,
    description: str = "",
    objects: list[str] | None = None,
    actions: list[str] | None = None,
) -> str | None:
    """按画面文案推断跟镜站位；推不出则返回 None（由调用方兜底）。"""
    blob = (
        str(description or "")
        + " "
        + " ".join(str(x) for x in (objects or []))
        + " "
        + " ".join(str(x) for x in (actions or []))
    ).lower()
    if not blob.strip():
        return None
    key = str(industry_key or "").strip()
    if key in ("life-service", "life_service", "lifeservice"):
        # 门头/外景：生活服务骨架无 storefront，归到开场向展示
        if any(w in blob for w in ("门头", "门脸", "外立面", "店门口")):
            return "culture_wall"
        for bucket, markers in _LIFE_SERVICE_RULES:
            if any(m.lower() in blob for m in markers):
                return bucket
        if "床" in blob and any(w in blob for w in ("护理", "美容", "养生", "巾")):
            return "treatment_bed"
        if any(w in blob for w in ("护理", "美容", "养生")) and any(
            w in blob for w in ("手", "脸", "客人", "技师")
        ):
            return "treatment_action"
        return None
    if key in ("building-supply", "building_supply", "building"):
        rules = [
            ("storefront", ("门头", "门脸", "外立面", "店门口")),
            ("loading", ("装车", "卸货", "货车", "出库", "配送")),
            ("warehouse", ("仓库", "货架", "仓内", "码垛", "库存")),
            ("product_closeup", ("特写", "五金", "工具", "产品近景")),
        ]
        for bucket, markers in rules:
            if any(m in blob for m in markers):
                return bucket
    return None


def reclassify_cliplets_for_customer(
    session: Session,
    *,
    customer_id: int,
    industry_key: str,
    orientation: str = "portrait",
    force: bool = True,
    stage_keys: list[str] | None = None,
) -> dict[str, Any]:
    """按画面描述重写场景分类；force 时覆盖非跟镜站位旧标。"""
    stages = [str(k) for k in (stage_keys or []) if str(k).strip()]
    known = set(stages) if stages else set()
    rows = list(
        session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(
                Asset.customer_id == int(customer_id),
                Asset.status == "ready",
                Asset.orientation == orientation,
            )
        ).all()
    )
    updated = 0
    skipped = 0
    inferred = 0
    fallback_i = 0
    counts: dict[str, int] = {}
    for row in rows:
        if (row.status or "") == CLIPLET_STATUS_REJECTED_BLUR:
            continue
        if not is_usable_quality(float(row.score or 0.0)):
            continue
        if float(row.score or 0.0) < float(MIN_QUALITY_SCORE):
            continue
        cur = cliplet_bucket(row)
        in_known = (not known) or (cur in known)
        if in_known and not force:
            skipped += 1
            counts[cur] = counts.get(cur, 0) + 1
            continue
        objs = row.objects_json if isinstance(row.objects_json, list) else []
        acts = row.actions_json if isinstance(row.actions_json, list) else []
        guessed = infer_station_bucket(
            industry_key,
            description=str(row.description or ""),
            objects=[str(x) for x in objs],
            actions=[str(x) for x in acts],
        )
        if guessed and (not known or guessed in known):
            bucket = guessed
            inferred += 1
        elif stages:
            bucket = stages[fallback_i % len(stages)]
            fallback_i += 1
        else:
            bucket = "other"
        set_cliplet_bucket(session, int(row.id), bucket, customer_id=int(customer_id))
        updated += 1
        counts[bucket] = counts.get(bucket, 0) + 1
    return {
        "ok": True,
        "updated": updated,
        "skipped": skipped,
        "inferred": inferred,
        "counts": counts,
    }


def set_cliplet_bucket(session: Session, cliplet_id: int, bucket: str, *, customer_id: int) -> Cliplet:
    row = session.get(Cliplet, cliplet_id)
    if row is None:
        raise ValueError("片段不存在")
    asset = session.scalar(select(Asset).where(Asset.uuid == row.asset_uuid))
    if asset is None or int(asset.customer_id) != int(customer_id):
        raise ValueError(reason_zh("customer_mismatch"))
    key = str(bucket).strip() or "other"
    row.scene = key[:64]
    sem = dict(row.semantic_json) if isinstance(row.semantic_json, dict) else {}
    sem["scene_bucket"] = key
    row.semantic_json = sem
    return row


def coverage_report(
    session: Session,
    *,
    customer_id: int,
    stages: list[dict[str, Any]],
    min_per_bucket: int = 2,
    orientation: str = "portrait",
) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(
                Asset.customer_id == customer_id,
                Asset.status == "ready",
                Asset.orientation == orientation,
            )
        ).all()
    )
    counts: dict[str, int] = {}
    usable = 0
    for row in rows:
        if (row.status or "") == CLIPLET_STATUS_REJECTED_BLUR:
            continue
        if not is_usable_quality(float(row.score or 0.0)):
            continue
        if float(row.score or 0.0) < float(MIN_QUALITY_SCORE):
            continue
        if float(row.duration_sec or 0) < 2.5:
            continue
        usable += 1
        b = cliplet_bucket(row)
        counts[b] = counts.get(b, 0) + 1

    buckets_out = []
    reasons: list[str] = []
    ok = True
    for st in stages:
        key = str(st.get("key") or "")
        need = int(min_per_bucket)
        have = int(counts.get(key, 0))
        required = bool(st.get("required"))
        item = {
            "key": key,
            "label_zh": bucket_label(key),
            "required": required,
            "have": have,
            "need": need,
            "ok": have >= need if required else True,
        }
        if required and have < need:
            ok = False
            reasons.append(reason_zh("coverage_low", bucket=bucket_label(key), need=need))
        if required and have <= 0:
            ok = False
            reasons.append(reason_zh("required_missing", bucket=bucket_label(key)))
        buckets_out.append(item)

    return {
        "ok": ok,
        "usable_total": usable,
        "buckets": buckets_out,
        "counts": counts,
        "reasons": list(dict.fromkeys(reasons)),
        "label_zh": "场景覆盖",
    }


def station_inventory(
    session: Session,
    *,
    customer_id: int,
    orientation: str = "portrait",
    require_grounding: bool = True,
    min_available_sec: float = 6.8,
) -> dict[str, Any]:
    """客户站位硬库存：只计 grounding 通过且片源够长的片段。有才排的真相源。"""
    from engine.pack.scene_tour_grounding import (
        cliplet_is_grounded_for_bucket,
        maybe_rebucket_mislabeled,
    )

    rows = list(
        session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(
                Asset.customer_id == customer_id,
                Asset.status == "ready",
                Asset.orientation == orientation,
            )
        ).all()
    )
    for row in rows:
        b0 = cliplet_bucket(row)
        if b0 in ("storefront", "entrance"):
            maybe_rebucket_mislabeled(session, row, customer_id=customer_id)

    counts: dict[str, int] = {}
    for row in rows:
        if (row.status or "") == CLIPLET_STATUS_REJECTED_BLUR:
            continue
        if not is_usable_quality(float(row.score or 0.0)):
            continue
        if float(row.score or 0.0) < float(MIN_QUALITY_SCORE):
            continue
        if float(row.duration_sec or 0) < 2.8:
            continue
        b = cliplet_bucket(row)
        if require_grounding:
            ok_g, _why = cliplet_is_grounded_for_bucket(row, b)
            if not ok_g:
                continue
        asset = session.scalar(select(Asset).where(Asset.uuid == row.asset_uuid))
        if not asset or int(asset.customer_id) != int(customer_id):
            continue
        src = Path(str(asset.storage_path))
        if not src.is_file():
            continue
        avail = max(0.5, _probe_duration_cached(src) - float(row.start_sec or 0) - 0.05)
        if avail < float(min_available_sec):
            continue
        counts[b] = counts.get(b, 0) + 1
    return {
        "counts": counts,
        "label_zh": "站位硬库存",
        "min_available_sec": float(min_available_sec),
    }


def recent_cliplet_cooldown_ids(session: Session, *, customer_id: int, limit: int = 40) -> set[int]:
    if limit <= 0:
        return set()
    rows = session.scalars(
        select(ClipletUsage.cliplet_id)
        .where(ClipletUsage.customer_id == customer_id)
        .order_by(ClipletUsage.id.desc())
        .limit(limit * 3)
    ).all()
    out: set[int] = set()
    for cid in rows:
        if cid is None:
            continue
        out.add(int(cid))
        if len(out) >= limit:
            break
    return out


def pick_clips_for_stages(
    session: Session,
    *,
    customer_id: int,
    stages: list[dict[str, Any]],
    cooldown_ids: set[int],
    orientation: str = "portrait",
    seed: int = 1,
    exclude_ids: set[int] | None = None,
    require_grounding: bool = True,
    inventory_counts: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按游览站位选片；返回 shot 草案（尚无旁白）与中文告警。

    ``exclude_ids``：自纠重试时排除已证明错配的片段。
    ``require_grounding``：跳过粗标/无画面描述/站位证据冲突的片段。
    ``inventory_counts``：有才排；库存为 0 的站位直接跳过。
    """
    import random

    from engine.pack.scene_tour_grounding import (
        cliplet_is_grounded_for_bucket,
        evidence_tokens_from_cliplet,
        is_stub_description,
        maybe_rebucket_mislabeled,
    )

    rng = random.Random(seed)
    blocked = set(exclude_ids or ()) | set(cooldown_ids or ())
    inv = {str(k): int(v or 0) for k, v in (inventory_counts or {}).items()}
    rows = list(
        session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(
                Asset.customer_id == customer_id,
                Asset.status == "ready",
                Asset.orientation == orientation,
            )
        ).all()
    )
    # 先自愈明显错标（仅门头粗标），再按 bucket 分桶
    for row in rows:
        b0 = cliplet_bucket(row)
        if b0 in ("storefront", "entrance"):
            maybe_rebucket_mislabeled(session, row, customer_id=customer_id)
    by_bucket: dict[str, list[Cliplet]] = {}
    for row in rows:
        if (row.status or "") == CLIPLET_STATUS_REJECTED_BLUR:
            continue
        if not is_usable_quality(float(row.score or 0.0)):
            continue
        if float(row.score or 0.0) < float(MIN_QUALITY_SCORE):
            continue
        if float(row.duration_sec or 0) < 2.8:
            continue
        if row.id in blocked:
            continue
        b = cliplet_bucket(row)
        by_bucket.setdefault(b, []).append(row)

    for lst in by_bucket.values():
        rng.shuffle(lst)
        lst.sort(
            key=lambda r: (
                0 if not is_stub_description(str(r.description or "")) else 1,
                -float(r.score or 0),
            )
        )

    used_assets: set[str] = set()
    used_ids: set[int] = set()
    shots: list[dict[str, Any]] = []
    warnings: list[str] = []

    for st in stages:
        key = str(st.get("key") or "")
        role = str(st.get("role") or "body")
        required = bool(st.get("required"))
        # 有才排：库存已知且为 0 → 不安排、不告警（除非必选）
        if inv and inv.get(key, 0) <= 0:
            if required:
                warnings.append(reason_zh("required_missing", bucket=bucket_label(key)))
            continue
        candidates = [r for r in by_bucket.get(key, []) if r.id not in used_ids]
        picked = None
        skip_notes: list[str] = []
        for row in candidates:
            au = str(row.asset_uuid or "")
            if au in used_assets:
                continue
            if require_grounding:
                ok_g, why = cliplet_is_grounded_for_bucket(row, key)
                if not ok_g:
                    skip_notes.append(why)
                    continue
            asset = session.scalar(select(Asset).where(Asset.uuid == row.asset_uuid))
            if not asset or int(asset.customer_id) != int(customer_id):
                continue
            src = Path(str(asset.storage_path))
            if not src.is_file():
                continue
            src_dur = _probe_duration_cached(src)
            start = float(row.start_sec or 0.0)
            avail = max(0.5, src_dur - start - 0.05)
            if require_grounding and avail < 6.8:
                skip_notes.append("片源可用时长过短，已跳过")
                continue
            picked = (row, asset, avail)
            break
        if picked is None:
            if required:
                warnings.append(reason_zh("required_missing", bucket=bucket_label(key)))
            elif skip_notes:
                warnings.append(
                    f"可选场景「{bucket_label(key)}」暂无可用素材，已跳过（{skip_notes[0]}）。"
                )
            continue
        row, asset, avail = picked
        take = min(9.0, max(5.5, min(float(row.duration_sec or 5.5), float(avail))))
        tokens = evidence_tokens_from_cliplet(row)
        shots.append(
            {
                "bucket": key,
                "role": role,
                "cliplet_id": int(row.id),
                "asset_uuid": str(asset.uuid),
                "source_path": asset.storage_path,
                "start_sec": float(row.start_sec or 0.0),
                "duration_sec": round(take, 3),
                "score": float(row.score or 0.9),
                "visual_description": str(row.description or ""),
                "visual_tokens": tokens,
                "visual_stub": is_stub_description(str(row.description or "")),
                "available_sec": round(float(avail), 3),
            }
        )
        used_ids.add(int(row.id))
        used_assets.add(str(asset.uuid))

    return shots, warnings
