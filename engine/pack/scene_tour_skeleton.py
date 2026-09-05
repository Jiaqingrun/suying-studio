"""跟镜精品 · 行业游览骨架与路线配方（有才排；须拷贝到客户实例后使用）。"""

from __future__ import annotations

import hashlib
from typing import Any

TourStage = dict[str, Any]

INDUSTRY_SKELETONS: dict[str, dict[str, Any]] = {
    "life-service": {
        "industry_key": "life-service",
        "label_zh": "生活服务 / 护理门店",
        "stages": [
            {"key": "honor_wall", "required": False, "role": "open"},
            {"key": "culture_wall", "required": False, "role": "open"},
            {"key": "slippers", "required": False, "role": "body"},
            {"key": "vanity", "required": False, "role": "body"},
            {"key": "corridor", "required": False, "role": "body"},
            {"key": "sterilize", "required": False, "role": "body"},
            {"key": "treatment_bed", "required": True, "role": "body"},
            {"key": "treatment_room", "required": False, "role": "body"},
            {"key": "supply", "required": False, "role": "body"},
            {"key": "treatment_action", "required": False, "role": "body"},
            {"key": "tea", "required": False, "role": "close"},
        ],
        "min_shots": 8,
        "max_shots": 16,
        "title_seeds": [
            "门店实景\n静雅巡礼",
            "到店所见\n细处用心",
            "店内一览\n从容可见",
        ],
        "weak_anchors": {
            "honor_wall": ["奖牌", "奖杯", "证书", "展示墙"],
            "culture_wall": ["服务说明", "墙面", "章程"],
            "slippers": ["拖鞋", "换履", "柜子"],
            "vanity": ["圆镜", "梳妆台", "发梳", "香氛"],
            "corridor": ["走廊", "过道"],
            "sterilize": ["消毒室", "门牌"],
            "treatment_bed": ["护理床", "巾单", "床位"],
            "treatment_room": ["护理间", "仪器"],
            "supply": ["备料柜", "货架", "物料"],
            "treatment_action": ["护理", "手法", "步骤"],
            "tea": ["茶点", "鲜花", "小桌"],
            "entrance": ["门头", "入口"],
            "other": ["空间", "陈设"],
        },
        # 配方：只含站位序列模板；实际展开时剔除库存为 0 的站位
        "route_recipes": [
            {
                "id": "arrive_care",
                "label_zh": "到店护理",
                "keys": ["slippers", "corridor", "treatment_bed", "tea"],
            },
            {
                "id": "room_focus",
                "label_zh": "护理间沉浸",
                "keys": ["treatment_room", "treatment_bed", "treatment_action", "tea"],
            },
            {
                "id": "space_walk",
                "label_zh": "店内漫步",
                "keys": ["honor_wall", "vanity", "corridor", "treatment_bed"],
            },
        ],
    },
    "building-supply": {
        "industry_key": "building-supply",
        "label_zh": "建材 / 仓配",
        "stages": [
            {"key": "storefront", "required": False, "role": "open"},
            {"key": "warehouse", "required": True, "role": "body"},
            {"key": "loading", "required": False, "role": "body"},
            {"key": "product_closeup", "required": False, "role": "body"},
            {"key": "other", "required": False, "role": "close"},
        ],
        "min_shots": 6,
        "max_shots": 14,
        "title_seeds": [
            "仓配实景\n一目了然",
            "到店选材\n在架可见",
        ],
        "weak_anchors": {
            "storefront": ["门头", "入口", "招牌", "门店"],
            "warehouse": ["仓库", "货架", "库存", "仓内", "码垛"],
            "loading": ["装车", "卸货", "配送", "出库", "货车"],
            "product_closeup": ["特写", "产品", "工具", "五金"],
            "other": ["场地", "陈列"],
        },
        "route_recipes": [
            {
                "id": "ops_follow",
                "label_zh": "作业跟拍",
                "keys": ["loading", "warehouse", "product_closeup", "other"],
            },
            {
                "id": "stock_immerse",
                "label_zh": "货品沉浸",
                "keys": ["product_closeup", "warehouse", "loading", "other"],
            },
            {
                "id": "warehouse_walk",
                "label_zh": "仓内漫步",
                "keys": ["warehouse", "loading", "product_closeup", "other"],
            },
            {
                "id": "gate_if_any",
                "label_zh": "从外到内",
                "keys": ["storefront", "warehouse", "loading", "product_closeup"],
            },
        ],
    },
}


def resolve_industry_key(profile: dict[str, Any] | None) -> str | None:
    if not isinstance(profile, dict):
        return None
    raw = profile.get("industry_pack") or profile.get("industry_key")
    if not raw:
        return None
    key = str(raw).strip()
    aliases = {
        "life_service": "life-service",
        "lifeservice": "life-service",
        "building_supply": "building-supply",
        "building": "building-supply",
    }
    return aliases.get(key, key)


def skeleton_for(industry_key: str | None) -> dict[str, Any] | None:
    if not industry_key:
        return None
    return INDUSTRY_SKELETONS.get(industry_key)


def path_fingerprint(keys: list[str]) -> str:
    blob = ">".join(keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def expand_route_stages(
    skel: dict[str, Any],
    *,
    inventory: dict[str, int],
    seed: int,
    recent_path_fps: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按库存过滤后抽路线配方；返回 stages 与 meta。

    有才排：库存为 0 的站位永不进入路线。
    """
    import random

    stage_by_key = {
        str(s.get("key")): dict(s) for s in (skel.get("stages") or []) if s.get("key")
    }
    inv = {str(k): int(v or 0) for k, v in (inventory or {}).items()}
    recipes = list(skel.get("route_recipes") or [])
    rng = random.Random(int(seed) or 1)
    recent = set(recent_path_fps or [])

    def _materialize(keys: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for i, raw_key in enumerate(keys):
            key = str(raw_key)
            if inv.get(key, 0) <= 0:
                continue
            if key in seen:
                continue  # 同站位不重复，避免篇章顺序回跳
            base = dict(stage_by_key.get(key) or {"key": key, "required": False, "role": "body"})
            if not out:
                base["role"] = "open"
            out.append(base)
            seen.add(key)
        if out:
            out[0]["role"] = "open"
            if len(out) > 1:
                for mid in out[1:-1]:
                    mid["role"] = "body"
                out[-1]["role"] = "close"
        return out

    def _pad_forward(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order = {str(s.get("key")): i for i, s in enumerate(skel.get("stages") or []) if s.get("key")}
        have = {str(s.get("key")) for s in stages}
        last = max((order.get(str(s.get("key")), -1) for s in stages), default=-1)
        for st in skel.get("stages") or []:
            if len(stages) >= 4:
                break
            key = str(st.get("key") or "")
            if not key or inv.get(key, 0) <= 0 or key in have:
                continue
            idx = order.get(key, last + 1)
            if idx < last:
                continue
            stages.append(dict(st))
            have.add(key)
            last = idx
        if stages:
            stages[0]["role"] = "open"
            if len(stages) > 1:
                stages[-1]["role"] = "close"
        return stages

    candidates: list[tuple[dict[str, Any], list[dict[str, Any]], str]] = []
    for recipe in recipes:
        keys = [str(k) for k in (recipe.get("keys") or [])]
        # 明示「从外到内」的配方：无真门头库存则不抽
        if str(recipe.get("id") or "") == "gate_if_any" and inv.get("storefront", 0) <= 0:
            continue
        stages = _materialize(keys)
        if len(stages) < 3:
            continue
        # 必选站位仍须在路线中（若库存>0）
        for st in skel.get("stages") or []:
            if not st.get("required"):
                continue
            rk = str(st.get("key") or "")
            if inv.get(rk, 0) > 0 and not any(s.get("key") == rk for s in stages):
                stages.insert(min(1, len(stages)), dict(st))
        stages = _pad_forward(stages)
        fp = path_fingerprint([str(s.get("key")) for s in stages])
        candidates.append((recipe, stages, fp))

    # 无配方可用时：退回骨架中库存>0 的站位序列
    if not candidates:
        fallback = [
            dict(s)
            for s in (skel.get("stages") or [])
            if inv.get(str(s.get("key") or ""), 0) > 0
        ]
        fallback = _pad_forward(fallback)
        fp = path_fingerprint([str(s.get("key")) for s in fallback])
        return fallback, {
            "recipe_id": "inventory_fallback",
            "label_zh": "按库存顺逛",
            "path_fingerprint": fp,
            "keys": [str(s.get("key")) for s in fallback],
        }

    fresh = [c for c in candidates if c[2] not in recent]
    pool = fresh or candidates
    recipe, stages, fp = pool[rng.randrange(len(pool))]
    stages = _pad_forward(list(stages))
    fp = path_fingerprint([str(s.get("key")) for s in stages])
    return stages, {
        "recipe_id": str(recipe.get("id") or "recipe"),
        "label_zh": str(recipe.get("label_zh") or ""),
        "path_fingerprint": fp,
        "keys": [str(s.get("key")) for s in stages],
    }
