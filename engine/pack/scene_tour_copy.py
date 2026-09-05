"""跟镜精品 · App 中文文案对照（用户可见一律用这里）。"""

from __future__ import annotations

CATEGORY_LABELS: dict[str, str] = {
    "default": "日常日更",
    "premium": "精品样式",
    "scene_tour": "跟镜精品",
    "store_culture": "门店文化",
}

# 对内键 → 界面文案
UI: dict[str, str] = {
    "scene_tour": "跟镜精品",
    "brief": "跟镜简报",
    "open": "开场",
    "body": "巡店",
    "close": "收束",
    "visible_anchors": "画面要点",
    "scene_bucket": "场景分类",
    "reference_hooks": "参考提示",
    "banned_terms": "禁用词",
    "banned_phrases": "禁用句",
    "validation_report": "成片检查报告",
    "block_reasons": "未通过原因",
    "cooldown": "素材冷却",
    "industry_pack": "行业资料",
    "industry_key": "行业类型",
    "coverage": "场景覆盖",
    "generate": "生成跟镜精品",
    "title_pool_small": "标题小集",
}

# 场景分类键 → 中文
BUCKET_LABELS: dict[str, str] = {
    "entrance": "进店门头",
    "honor_wall": "荣誉展示",
    "culture_wall": "服务说明墙",
    "corridor": "走廊过道",
    "slippers": "换履区",
    "vanity": "梳妆台",
    "sterilize": "消毒准备",
    "treatment_bed": "护理床位",
    "treatment_room": "护理间",
    "supply": "备料柜",
    "treatment_action": "护理过程",
    "tea": "茶点休憩",
    "other": "其他景别",
    # building-supply / 仓配（与 ingest scene 标签对齐）
    "storefront": "门店门头",
    "warehouse": "仓内货架",
    "loading": "装车卸货",
    "product_closeup": "产品特写",
    "transport": "配送在途",
    "inventory_full": "库存陈列",
}

# 行业黑名单词（串味检测）
INDUSTRY_CROSS_BANS: dict[str, list[str]] = {
    "life-service": [
        "仓库", "装车", "叉车", "批发", "工地", "建材", "水泥", "钢筋", "配送出库",
    ],
    "building-supply": [
        "护理床", "美容", "医美", "淋巴", "皮肤管理", "换鞋跪", "茶点养生",
    ],
}


def category_label(key: str | None) -> str:
    k = (key or "default").strip() or "default"
    return CATEGORY_LABELS.get(k, k)


def bucket_label(key: str | None) -> str:
    k = (key or "other").strip() or "other"
    return BUCKET_LABELS.get(k, k)


def reason_zh(code: str, **kwargs: object) -> str:
    """将检查失败码转为中文说明。"""
    templates = {
        "missing_industry": "缺少行业资料，请先在客户资料中配置行业类型。",
        "industry_mismatch": "跟镜规则的行业类型与当前客户不一致，未通过则不出片。",
        "no_rule": "尚未启用跟镜精品规则，请先到规则实验室保存并启用。",
        "coverage_low": "场景「{bucket}」可用素材不足（需至少 {need} 条），请先在片库完成场景分类或补充素材。",
        "required_missing": "必选场景「{bucket}」没有可用素材，无法生成跟镜精品。",
        "order_regress": "巡店顺序回跳，篇章结构未通过。",
        "no_anchor": "旁白缺少画面要点，请重试写词。",
        "banned_hit": "旁白含禁用表述：{term}",
        "cross_industry": "旁白出现其他行业用语「{term}」，已拦截以免串味。",
        "empty_script": "旁白为空，未通过则不出片。",
        "too_few_shots": "镜头过少，无法构成完整开场—巡店—收束。",
        "elegance_fail": "雅述未通过",
        "ungrounded_clip": "所选片段缺少真实画面描述或场景错标，已拦截。",
        "visual_mismatch": "旁白与画面不符：{detail}",
    }
    tpl = templates.get(code, "未通过：{code}")
    try:
        return tpl.format(code=code, **kwargs)
    except Exception:
        return f"未通过：{code}"
