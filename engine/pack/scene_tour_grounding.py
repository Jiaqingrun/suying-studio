"""跟镜精品 · 画面锚定与错标自纠（不依赖 Cursor）。

合同要点（SCENE_TOUR）：
- 旁白只能写画面里看得见的东西；粗标/无描述不得冒充门头等站位。
- 规划侧发现错配 → 排除该片段并重选（自动纠正）；过不了则拒片。
"""

from __future__ import annotations

import re
from typing import Any

# 无真实画面描述的占位文案（粗标 / 启发式入库）
_STUB_MARKERS = (
    "实拍业务素材",
    "[rejected_semantic]",
    "语义分析未通过",
)
_STUB_PREFIXES = ("分类:", "分类：")

# 各站位「旁白若出现则画面证据里也应有」的强声称词
_BUCKET_CLAIMS: dict[str, tuple[str, ...]] = {
    "storefront": ("门头", "招牌", "店招", "入口", "门店门口", "店面"),
    "entrance": ("门头", "招牌", "入口", "门口"),
    "warehouse": ("仓库", "货架", "仓内", "码垛", "库存", "管材", "建材"),
    "loading": ("装车", "卸货", "货车", "配送", "出库", "车斗"),
    "product_closeup": ("特写", "五金", "工具", "产品", "零件"),
    "corridor": ("走廊", "过道", "廊道", "门洞", "拱门", "通道"),
    "treatment_bed": ("护理床", "巾单", "床位", "铺床"),
    "slippers": ("拖鞋", "鞋架", "鞋柜", "换履"),
}

# 可安全嵌入模板槽位的锚点（防「顺着浴袍往里走」）
_BUCKET_SLOT_ANCHORS: dict[str, tuple[str, ...]] = {
    "corridor": ("走廊", "过道", "廊道", "门洞", "拱门", "通道"),
    "treatment_bed": ("护理床", "巾单", "床位", "护理床位"),
    "slippers": ("拖鞋", "鞋架", "鞋柜"),
    "vanity": ("梳妆", "圆镜", "镜子", "台面", "发梳"),
    "sterilize": ("消毒室", "消毒", "门牌"),
    "honor_wall": ("奖牌", "奖杯", "证书", "荣誉"),
    "culture_wall": ("展板", "海报", "企业文化", "服务说明"),
    "tea": ("茶点", "清茶", "茶具", "小几"),
    "supply": ("护理品", "备料", "货架", "置物架"),
    "treatment_room": ("护理间", "仪器", "包间"),
    "treatment_action": ("护理", "手法", "步骤"),
    "warehouse": ("货架", "仓库", "库存", "码垛", "线材"),
    "loading": ("装车", "卸货", "货车", "出库"),
    "storefront": ("门头", "招牌", "门脸"),
    "product_closeup": ("产品", "工具", "五金", "特写"),
}

# 与门头站位互斥的仓配画面词：出现则不能当 storefront 用
_STOREFRONT_CONFLICT_EVIDENCE = (
    "货架",
    "码垛",
    "砖",
    "管材",
    "叉车",
    "装车",
    "仓库",
    "仓内",
    "托盘",
    "捆扎",
    "灰砖",
    "石材",
    "电动工具",
    "包装盒",
    "纸箱",
    "钢丝",
    "板材",
)

# 真门头硬证据（仅「招牌」字样不够——仓内墙牌也会有）
_STOREFRONT_STRONG = (
    "门头",
    "门脸",
    "外立面",
    "店门口",
    "门口外景",
    "建筑物外墙",
    "外墙悬挂",
    "竖向招牌",
    "门头招牌",
)


def is_stub_description(text: str | None) -> bool:
    raw = str(text or "").strip()
    if not raw or len(raw) < 12:
        return True
    if any(m in raw for m in _STUB_MARKERS):
        return True
    # 「分类:xxx；真实画面…」：前缀是元数据，后面有画面句则不算纯粗标
    for p in _STUB_PREFIXES:
        if raw.startswith(p):
            rest = raw.split("；", 1)[-1].strip() if "；" in raw else raw[len(p) :].strip()
            if "：" in rest[:12]:
                rest = rest.split("：", 1)[-1].strip()
            if len(rest) >= 16 and re.search(r"[\u4e00-\u9fff]{6,}", rest):
                return False
            return True
    # 仅有「分类/素材/片段」元数据句，无视觉名词
    if "片段" in raw[:40] and "素材" in raw and "。" not in raw:
        return True
    return False


def evidence_tokens_from_cliplet(row: Any) -> list[str]:
    """从片段描述 / objects 抽出可核对画面词（优先完整名词，避免碎切）。"""
    parts: list[str] = []
    desc = str(getattr(row, "description", "") or "")
    if not is_stub_description(desc):
        parts.append(desc)
    objs = getattr(row, "objects_json", None)
    obj_list: list[str] = []
    if isinstance(objs, list):
        obj_list = [str(x).strip() for x in objs if str(x).strip()]
        parts.extend(obj_list)
    elif isinstance(objs, str) and objs.strip():
        obj_list = [objs.strip()]
        parts.append(objs)
    sem = getattr(row, "semantic_json", None)
    if isinstance(sem, dict):
        for key in ("summary", "caption", "visible_text", "scene_summary"):
            v = sem.get(key)
            if isinstance(v, str) and v.strip() and not is_stub_description(v):
                parts.append(v)
        vis = sem.get("visible_objects") or sem.get("objects")
        if isinstance(vis, list):
            for x in vis:
                s = str(x).strip()
                if s:
                    obj_list.append(s)
                    parts.append(s)
    # 优先 objects 全名
    out: list[str] = []
    seen: set[str] = set()
    try:
        from engine.pack.scene_tour_diction import is_usable_anchor
    except Exception:  # noqa: BLE001

        def is_usable_anchor(token: str) -> bool:  # type: ignore[misc]
            return bool(re.search(r"[\u4e00-\u9fff]{2,}", str(token or "")))

    for t in obj_list:
        if not is_usable_anchor(t):
            continue
        if t not in seen and 2 <= len(t) <= 16:
            seen.add(t)
            out.append(t)
    blob = "。".join(parts)
    # 按标点切短句，取含名词的片段（4–12 字）
    for clause in re.split(r"[，。；、\s]+", blob):
        c = clause.strip()
        if not is_usable_anchor(c):
            continue
        if 4 <= len(c) <= 14 and not c.startswith(("画面", "视频", "展示")):
            if c not in seen:
                seen.add(c)
                out.append(c)
        if len(out) >= 24:
            break
    if len(out) < 4:
        for t in re.findall(r"[\u4e00-\u9fff]{2,6}", blob):
            if t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= 24:
                break
    return out[:32]


def evidence_blob(tokens: list[str] | None, extra: str = "") -> str:
    return ("".join(tokens or []) + str(extra or "")).lower()


def anchors_from_evidence(
    bucket: str,
    *,
    weak: list[str] | None,
    tokens: list[str] | None,
    visual_description: str = "",
    max_n: int = 4,
) -> list[str]:
    """优先：证据里出现的骨架弱锚点 → 物体名 → 描述短句。"""
    weak_list = [str(a).strip() for a in (weak or []) if str(a).strip()]
    toks = [str(t).strip() for t in (tokens or []) if str(t).strip()]
    desc = str(visual_description or "")
    blob = "".join(toks) + desc
    hit_weak = [w for w in weak_list if w and w in blob]
    if hit_weak:
        return hit_weak[:max_n]
    _bad = (
        "画面",
        "展示",
        "一个",
        "视频",
        "视角",
        "片段",
        "分类",
        "素材",
        "可见",
        "可以",
        "进行",
        "以及",
        "部分",
        "背景有",
        "带有红",
    )
    preferred: list[str] = []
    claims = _BUCKET_CLAIMS.get(str(bucket), ())
    try:
        from engine.pack.scene_tour_diction import is_usable_anchor
    except Exception:  # noqa: BLE001

        def is_usable_anchor(token: str) -> bool:  # type: ignore[misc]
            return bool(re.search(r"[\u4e00-\u9fff]", str(token or "")))

    for t in toks:
        if not is_usable_anchor(t):
            continue
        if any(b in t for b in _bad) and not any(c in t for c in claims):
            continue
        if t not in preferred:
            preferred.append(t)
        if len(preferred) >= max_n:
            break
    if preferred:
        return preferred[:max_n]
    weak_ok = [w for w in weak_list if is_usable_anchor(w)]
    return weak_ok[:max_n] or ([bucket_label_safe(bucket)])


def cliplet_is_grounded_for_bucket(row: Any, bucket: str) -> tuple[bool, str]:
    """粗标/无描述/证据与站位冲突 → 不可用（规划自动跳过）。"""
    key = str(bucket or "other").strip() or "other"
    desc = str(getattr(row, "description", "") or "")
    tokens = evidence_tokens_from_cliplet(row)
    blob = evidence_blob(tokens, desc if not is_stub_description(desc) else "")

    if is_stub_description(desc) and not tokens:
        return False, "无真实画面描述（粗标占位），不能当跟镜锚点"

    if key in ("storefront", "entrance"):
        # 仓配互斥优先：有货架/码垛等 → 绝不当门头（即使描述里有「招牌」）
        if any(w in blob for w in _STOREFRONT_CONFLICT_EVIDENCE):
            return False, "画面像仓配货架/码垛/货品区，不能当门头"
        if not any(w in blob for w in _STOREFRONT_STRONG):
            return False, "缺少门头/门脸/外立面等真门头证据（仅场内招牌不算）"

    claims = _BUCKET_CLAIMS.get(key)
    if claims and blob and not any(w in blob for w in claims):
        if key == "warehouse" and any(w in blob for w in ("护理", "美容", "拖鞋")):
            return False, "仓配站位出现生活服务画面词"
        if key in ("corridor", "treatment_bed", "slippers"):
            return False, f"画面缺少「{'/'.join(claims[:3])}」证据，不能当该站位"
    return True, ""


def pick_slot_anchor(bucket: str, anchors: list[str] | None) -> str:
    """为模板槽位挑可入句的锚点；不合格则退回简短站位词。"""
    from engine.pack.scene_tour_diction import is_usable_anchor

    allow = _BUCKET_SLOT_ANCHORS.get(str(bucket) or "", ())
    for a in anchors or []:
        t = str(a or "").strip()
        if not is_usable_anchor(t):
            continue
        if allow and not any(k in t for k in allow):
            continue
        # 过长短语压成允许词本身
        for k in allow:
            if k in t and 2 <= len(k) <= 4:
                return k
        if 2 <= len(t) <= 6:
            return t
    fallback = {
        "corridor": "走廊",
        "treatment_bed": "护理床",
        "slippers": "拖鞋",
        "vanity": "台面",
        "honor_wall": "奖牌",
        "culture_wall": "展板",
        "tea": "茶点",
        "sterilize": "消毒室",
        "warehouse": "货架",
        "loading": "装车",
        "storefront": "门头",
        "product_closeup": "产品",
        "supply": "备料",
        "treatment_room": "护理间",
        "treatment_action": "护理",
    }
    return fallback.get(str(bucket), bucket_label_safe(bucket))


def bucket_label_safe(bucket: str) -> str:
    try:
        from engine.pack.scene_tour_copy import bucket_label

        return bucket_label(bucket)
    except Exception:
        return str(bucket or "画面")


def narration_conflicts_evidence(
    text: str,
    *,
    bucket: str,
    tokens: list[str] | None,
    stub: bool,
) -> list[str]:
    """旁白强声称 vs 画面证据：错配则返回中文原因（用于校验与自纠）。"""
    reasons: list[str] = []
    body = str(text or "").strip()
    if not body:
        return reasons
    if stub and str(bucket) in ("storefront", "entrance"):
        reasons.append("开场片段无真实画面描述，旁白不可编造门头。")
        return reasons
    blob = evidence_blob(tokens)
    key = str(bucket or "")
    claims = _BUCKET_CLAIMS.get(key, ())
    spoken_claims = [c for c in claims if c in body]
    if spoken_claims and blob and not any(c in blob for c in claims):
        reasons.append(
            f"旁白写了「{'/'.join(spoken_claims[:3])}」，但所选画面看不到对应内容。"
        )
    storefront_fluff = ("门头招牌", "阳光下清晰", "过往客户", "吸引着过往")
    if any(f in body for f in storefront_fluff):
        if not any(c in blob for c in _BUCKET_CLAIMS["storefront"]):
            reasons.append("旁白像生活服务门头开场，但画面没有门头证据（疑似串味/错标）。")
    return list(dict.fromkeys(reasons))


def maybe_rebucket_mislabeled(session: Any, row: Any, *, customer_id: int) -> str | None:
    """错标自愈：标成门头但无真门头证据 / 实为仓配 → 改标，返回新 bucket。"""
    from engine.pack.scene_tour_buckets import cliplet_bucket, set_cliplet_bucket

    key = cliplet_bucket(row)
    if key not in ("storefront", "entrance"):
        return None
    desc = str(getattr(row, "description", "") or "")
    tokens = evidence_tokens_from_cliplet(row)
    blob = evidence_blob(tokens, desc if not is_stub_description(desc) else "")
    has_conflict = any(w in blob for w in _STOREFRONT_CONFLICT_EVIDENCE)
    has_strong = any(w in blob for w in _STOREFRONT_STRONG)
    if has_strong and not has_conflict:
        return None
    new_key = "warehouse"
    if any(w in blob for w in _BUCKET_CLAIMS["loading"]):
        new_key = "loading"
    elif any(w in blob for w in _BUCKET_CLAIMS["product_closeup"]):
        new_key = "product_closeup"
    try:
        set_cliplet_bucket(session, int(row.id), new_key, customer_id=customer_id)
        return new_key
    except Exception:
        return None
