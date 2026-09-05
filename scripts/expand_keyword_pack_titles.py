#!/usr/bin/env python3
"""Expand v2 keyword-pack titles with length/compliance guards."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from engine.catalog.keyword_pack import install_keyword_pack, validate_keyword_pack
from engine.catalog.db import Customer, init_db
from engine.config.settings import load_settings
from engine.content.compliance import check_output_fields

MIN_CHARS = 6
MAX_CHARS = 48
MAX_LINE = 24

HARD_DENY = {
    "第一", "最好", "最强", "绝对", "100%", "国家级", "保证", "必达", "秒达", "最低",
    "厂家直销", "授权总代", "核心代理", "当天", "现货", "现货充足", "不出错", "品质稳定",
    "最快", "免费送货", "包赔", "假一赔十", "全网最低", "根治", "央企指定", "更牢固",
    "一站配齐", "品类齐全", "常备", "库存", "送达", "送到", "发货快", "快速发货", "及时",
    "更省", "放心", "靠谱", "不耽误", "跟得上", "配货快", "节奏稳", "稳当", "顺畅", "利落",
    "少跑", "不乱", "高效", "快速响应", "倾心服务", "口碑", "效率最高", "零抱怨",
    "2000", "10000", "189", "徐玲", "始峰集团",
}


def compact(text: str) -> str:
    return re.sub(r"[\s\r\n　]+", "", str(text or ""))


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", compact(text).lower(), flags=re.UNICODE)


def title_length_ok(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if "\n" in raw:
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        if not lines or len(lines) > 2:
            return False
        for ln in lines:
            n = len(compact(ln))
            if n < 2 or n > MAX_LINE:
                return False
        return MIN_CHARS <= len(compact(raw)) <= MAX_CHARS
    n = len(compact(raw))
    return MIN_CHARS <= n <= MAX_CHARS


def risky(text: str) -> bool:
    blob = norm(text)
    return any(norm(term) in blob for term in HARD_DENY)


def dedupe(items: list[str], threshold: float = 0.82) -> list[str]:
    kept: list[str] = []
    keys: set[str] = set()
    for raw in items:
        text = str(raw).strip()
        key = norm(text)
        if not key or not title_length_ok(text) or risky(text):
            continue
        if key in keys:
            continue
        if any(SequenceMatcher(None, key, norm(old)).ratio() >= threshold for old in kept):
            continue
        keys.add(key)
        kept.append(text)
    return kept


def dual(a: str, b: str) -> str | None:
    text = f"{a}\n{b}"
    return text if title_length_ok(text) and not risky(text) else None


def generate_titles(facts: dict[str, Any]) -> dict[str, list[str]]:
    addresses = (facts.get("company.addresses") or {}).get("value") or {}
    order_types = (facts.get("company.target_order_types_12m") or {}).get("value") or []
    brands = (facts.get("company.brand_names") or {}).get("value") or ["始峰五金"]
    positioning = str((facts.get("company.positioning_one_liner") or {}).get("value") or "")

    singles: list[str] = []
    duals: list[str] = []

    brand_bits = ["始峰五金", "始峰伟业", "北京始峰五金", "通州始峰五金", "始峰五金批发"]
    loc_bits = ["通州", "张家湾", "云杉路", "北京通州", "通州张家湾"]
    scene_bits = [
        "仓内", "仓库", "货架", "装车", "出库", "码放", "堆叠", "托盘", "陈列", "分拣",
        "搬运", "装运", "门店", "展厅", "工地", "现场",
    ]
    object_bits = [
        "板材", "金属板材", "白色板材", "编织袋", "包装箱", "纸箱", "木托盘", "货物",
        "建筑材料", "五金机具", "劳防用品", "电动工具", "焊材", "电线电缆", "配电配套",
        "钢筋机械", "塔机料斗", "磨具磨料", "工程三轮", "吊料容器",
    ]
    tail_bits = [
        "实拍", "现场记录", "画面可见", "细节展示", "外观可见", "包装可见", "过程记录",
        "环境实拍", "陈列可见", "堆叠可见", "装车可见", "出库可见", "按需了解", "欢迎咨询",
    ]
    safe_cta = [
        "具体规格请先咨询", "按需了解对应品类", "详情以沟通为准", "品类信息欢迎了解",
        "画面可见仅供参考", "采购需求可沟通", "型号规格请确认", "以实际沟通为准",
    ]

    for b in brand_bits:
        for t in ("实拍", "现场", "仓配", "仓库", "建材", "批发", "商行"):
            singles.append(f"{b}{t}")
        singles.append(f"{b}云杉路仓")
        singles.append(f"{b}张家湾仓")

    for loc in loc_bits:
        for mid in ("五金", "建材", "仓库", "仓配", "商行", "门店"):
            for tail in ("实拍", "现场", "记录"):
                singles.append(f"{loc}{mid}{tail}")

    for scene in scene_bits:
        for obj in object_bits:
            for tail in ("实拍", "可见", "记录", "展示"):
                singles.append(f"{scene}{obj}{tail}")
        for tail in tail_bits:
            singles.append(f"{scene}{tail}")

    for obj in object_bits:
        for tail in ("实拍", "细节", "外观", "包装", "陈列", "可见", "展示", "介绍"):
            singles.append(f"{obj}{tail}")
            singles.append(f"在售{obj}展示")

    for item in order_types:
        item = str(item).strip()
        if len(compact(item)) >= 2:
            singles.append(f"{item}品类实拍")
            singles.append(f"{item}相关展示")
            singles.append(f"工地{item}备料")

    if "建筑五金" in positioning or "五金" in positioning:
        singles.extend(
            [
                "建筑五金材料商", "工地常用五金材", "工地五金采购点",
                "北京建筑五金商", "通州建筑五金仓",
            ]
        )

    operating = str(addresses.get("operating") or "")
    if "云杉路" in operating:
        singles.extend(["云杉路始峰五金", "云杉路仓库实拍", "云杉路仓内陈列"])
    if "张家湾" in operating:
        singles.extend(["张家湾始峰仓库", "张家湾仓内实拍", "张家湾仓库环境"])

    dual_pairs = [
        ("仓内陈列", "包装可见"), ("产品实拍", "外观细节"), ("建筑材料", "实拍展示"),
        ("板材堆叠", "托盘可见"), ("货架陈列", "货物可见"), ("出库环节", "装车可见"),
        ("仓库环境", "画面记录"), ("五金机具", "按需了解"), ("焊材耗材", "品类展示"),
        ("电工电料", "实拍可见"), ("劳防用品", "陈列展示"), ("塔机料斗", "外观实拍"),
        ("钢筋机械", "品类展示"), ("配电配套", "实拍记录"), ("门店展厅", "环境实拍"),
        ("工地备料", "品类咨询"), ("包装货物", "堆叠可见"), ("木托盘码放", "画面可见"),
        ("白色板材", "堆叠实拍"), ("金属板材", "托盘可见"), ("始峰五金", "仓内实拍"),
        ("通州仓库", "现场记录"), ("装车搬运", "过程可见"), ("分拣配货", "环节记录"),
        ("多类货品", "陈列可见"), ("工程进场", "备料展示"), ("二次结构", "材料实拍"),
        ("市政配套", "品类了解"), ("具体型号", "请先咨询"), ("规格信息", "以咨询为准"),
        ("红梁仓库", "内部实拍"), ("编织袋货", "堆叠可见"), ("银灰板材", "多层堆叠"),
        ("出库装车", "过程记录"), ("仓库通道", "陈列区域"), ("工业仓库", "环境实拍"),
        ("矩形货物", "托盘支撑"), ("顶部采光", "库内可见"), ("货架货堆", "并列陈列"),
        ("水泥地面", "仓库内部"), ("大型仓库", "内部场景"), ("货物装车", "环节可见"),
        ("人员作业", "仓库内部"), ("包装箱", "整齐码放"), ("建筑材料", "形态可见"),
        ("电动工具", "陈列区域"), ("电线电缆", "品类展示"), ("磨具磨料", "货架可见"),
    ]
    for a, b in dual_pairs:
        item = dual(a, b)
        if item:
            duals.append(item)

    singles.extend(safe_cta)

    semantic_singles = [
        "大型仓库内部景", "水泥地面仓库内", "红梁结构仓库景", "货架货物堆叠",
        "木质托盘支撑货", "银灰板材堆叠", "白色板材多层堆", "编织袋包装货物",
        "包装箱整齐码放", "出库装车环节", "仓库人员作业中", "货物装车过程",
        "库内通道陈列区", "多品类货架陈列", "工业仓库内实拍", "室内仓储环境",
        "货箱堆叠在托盘", "矩形货物堆叠", "仓库顶部采光明", "库内货架与货堆",
    ]
    singles.extend(semantic_singles)

    by_theme: dict[str, list[str]] = {
        "default": dedupe(singles + duals),
        "配送": dedupe([t for t in singles + duals if any(x in t for x in ("装车", "出库", "装运", "配送", "搬运", "发货", "车队"))] + [
            "装车环节现场", "出库过程记录", "货物装车可见", "仓库出库实拍", "搬运过程记录",
            "装车区域实拍", "运输前装车景", "车队装车现场", "工地方向发运", "出库口装车景",
        ]),
        "服务与配送": dedupe([
            "仓配发货连贯", "拣装发货一体", "配货到齐再走", "出库装车连贯",
            "仓储装运实拍", "仓库出库记录", "装车区域可见", "发货环节记录",
        ] + [t for t in singles if "装车" in t or "出库" in t]),
        "建筑与施工机械": dedupe([
            f"{k}实拍" for k in ("钢筋机械", "切断机", "弯曲机", "调直机", "弯箍机", "小型机械", "施工机械")
        ] + [f"{k}品类展示" for k in ("钢筋机械", "施工机械", "调直机", "切断机")]),
        "电动工具与五金工具": dedupe([
            f"{k}{t}" for k in ("电动工具", "五金工具", "角磨机", "电钻电锤", "冲击钻", "切割机")
            for t in ("实拍", "陈列", "展示", "可见", "细节")
        ]),
        "焊接与耗材": dedupe([f"焊材{t}" for t in ("实拍", "陈列", "展示", "可见")] + [f"焊接耗材{t}" for t in ("实拍", "展示")]),
        "电工电料与照明设备": dedupe([
            f"{k}{t}" for k in ("电工电料", "电线电缆", "配电配套", "照明设备")
            for t in ("实拍", "陈列", "展示", "可见")
        ]),
        "劳防用品": dedupe([f"劳防用品{t}" for t in ("实拍", "陈列", "展示", "可见", "介绍")]),
        "吊料容器": dedupe([f"塔机料斗{t}" for t in ("实拍", "外观", "展示", "可见")] + ["吊料容器实拍", "料斗外观可见"]),
        "五金耗材": dedupe([f"五金耗材{t}" for t in ("实拍", "陈列", "展示", "可见")] + ["扎丝焊条陈列", "工地耗材展示"]),
        "磨具磨料": dedupe([f"磨具磨料{t}" for t in ("实拍", "陈列", "展示", "可见")]),
        "电动工程三轮车": dedupe(["工程三轮实拍", "电动三轮展示", "工地三轮可见"]),
        "其他工程配套": dedupe(["工程配套品类", "配套材料展示", "进场配套实拍"]),
        "场景采购": dedupe([
            f"工程{scene}备料" for scene in ("进场", "土建", "市政", "二次结构")
        ] + [f"{scene}采购场景" for scene in ("进场", "土建", "市政")]),
        "产品介绍": dedupe(duals + [
            "产品实拍先看", "品类介绍可见", "外观细节展示", "包装信息可见",
            "在售品类展示", "材料形态可见", "画面细节展示",
        ] + [t for t in singles if "实拍" in t or "展示" in t or "可见" in t]),
        "产品介绍·建筑材料": dedupe([
            "建筑材料实拍", "建材品类展示", "板材管材实拍", "材料形态可见",
            "白色板材堆叠", "金属板材托盘", "包装建材可见", "建材堆叠实拍",
        ] + duals[:20]),
        "五金": dedupe([t for t in singles if "五金" in t]),
    }

    # Fill empty product themes from default subset
    for key in (
        "建筑与施工机械", "电动工具与五金工具", "焊接与耗材", "电工电料与照明设备",
        "劳防用品", "吊料容器", "五金耗材", "磨具磨料", "电动工程三轮车",
    ):
        if len(by_theme.get(key, [])) < 20:
            by_theme[key] = dedupe(by_theme.get(key, []) + by_theme["default"][:60])

    return by_theme


def apply_titles(pack: dict[str, Any], by_theme: dict[str, list[str]]) -> dict[str, Any]:
    out = json.loads(json.dumps(pack, ensure_ascii=False))
    meta = out.setdefault("meta", {})
    meta["revision"] = int(meta.get("revision") or 1) + 1
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    meta["change_summary"] = "扩充 6-20 字合规标题语库（语义+事实+去风险）"
    meta["title_constraints"] = {
        "min_chars": MIN_CHARS,
        "max_chars": MAX_CHARS,
        "max_chars_per_line": MAX_LINE,
        "spoken": False,
    }

    components = out.setdefault("copy_components", {})
    default = components.setdefault("default", {})
    merged_default: list[str] = []
    for items in by_theme.values():
        merged_default.extend(items)
    default["titles"] = dedupe(merged_default)

    themes = components.setdefault("themes", {})
    for theme, titles in by_theme.items():
        block = themes.setdefault(theme, {"label": theme, "keywords": [], "hooks": default.get("hooks", [])})
        if not isinstance(block, dict):
            continue
        existing = [str(x).strip() for x in (block.get("titles") or []) if str(x).strip()]
        block["titles"] = dedupe(existing + titles)

    components["title_banks"] = {
        "仓内陈列": dedupe([t for t in default["titles"] if any(x in t for x in ("仓", "库", "货架", "堆叠", "托盘", "陈列"))]),
        "装车搬运": dedupe(by_theme.get("配送", [])),
        "产品实拍": dedupe(by_theme.get("产品介绍", [])),
        "品类介绍": dedupe(by_theme.get("产品介绍·建筑材料", [])),
        "品牌门店": dedupe([t for t in default["titles"] if "始峰" in t or "通州" in t or "张家湾" in t]),
    }
    return out


def compliance_report(pack: dict[str, Any]) -> dict[str, Any]:
    titles = (pack.get("copy_components") or {}).get("default", {}).get("titles") or []
    return check_output_fields(
        {"titles": titles[:500]},
        policy=pack.get("compliance") if isinstance(pack.get("compliance"), dict) else None,
        evidence_ids={"semantic.strict_v1", "client_statement"},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack", type=Path)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--customer", default="北京始峰伟业")
    args = parser.parse_args()

    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    facts = pack.get("facts") if isinstance(pack.get("facts"), dict) else {}
    by_theme = generate_titles(facts)
    updated = apply_titles(pack, by_theme)

    report = validate_keyword_pack(updated)
    comp = compliance_report(updated)
    stats = {
        "revision": updated["meta"]["revision"],
        "total_default_titles": len(updated["copy_components"]["default"]["titles"]),
        "by_theme_counts": {k: len(v) for k, v in by_theme.items()},
        "validation": report,
        "compliance_passed": comp["passed"],
        "compliance_errors": [i for i in comp["issues"] if i.get("level") == "error"][:20],
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if not report["ok"] or not comp["passed"]:
        return 1

    if not args.promote:
        out_path = args.pack.with_suffix(".expanded.json")
        out_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_path}")
        return 0

    args.pack.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    settings = load_settings()
    init_db(settings)
    from engine.catalog.db import get_session

    session = get_session()
    try:
        customer = session.scalar(
            __import__("sqlalchemy").select(Customer).where(Customer.name == args.customer)
        )
        if not customer:
            raise SystemExit(f"customer not found: {args.customer}")
        result = install_keyword_pack(session, customer, args.pack, source_kind="title-expand")
        print(json.dumps({"ok": True, "revision": result["pack"].revision, "sha256": result["pack"].content_sha256}, ensure_ascii=False))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
