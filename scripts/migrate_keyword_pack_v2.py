#!/usr/bin/env python3
"""Build a compact, evidence-aware customer content-pack v2 from a v1 pack."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


# True ad-law / absolute / false-authority claims — hard block, no soft rewrite.
HARD_DENY_TERMS = {
    "第一", "最好", "最强", "绝对", "100%", "国家级", "保证", "必达", "秒达",
    "最低", "厂家直销", "授权总代", "核心代理", "现货充足", "不出错",
    "品质稳定", "最快", "免费送货", "包赔", "假一赔十", "保证送达", "全网最低",
}
# Soft marketing tone — caution + short safe_rewrites; must NOT enter hard_deny.
CAUTION_TERMS = {
    "更快", "更稳", "更省", "放心", "靠谱", "不耽误", "跟得上", "一站配齐",
    "品类齐全", "常备", "发货稳", "配货快", "节奏稳", "稳当", "顺畅", "利落",
    "不乱", "少跑", "发货快", "快速发货", "及时", "当天", "送到",
}
# Title-pool scrub during migrate (hard + caution + evidence-ish phrases).
RISK_TERMS = HARD_DENY_TERMS | CAUTION_TERMS | {
    "现货", "库存", "送达",
}
SAFE_REWRITES = {
    "现货充足": "在架情况请以实际确认为准",
    "保证送达": "交付安排请以实际确认为准",
    "品质稳定": "画面展示当前产品外观与包装",
    "放心": "踏实",
    "靠谱": "踏实",
    "放心靠谱": "可先核对品类与实际需求",
}
INTERNAL_KEYS = {
    "legal_representative", "credit_code", "registered", "contacts", "mobile",
    "contact_name", "person_library_path",
}
CONTENT_TYPES = {
    "product_closeup": ["单品外观", "product_closeup", "display"],
    "multi_product_display": ["多品类陈列", "shelf", "display"],
    "warehouse_display": ["仓内陈列", "warehouse", "stacking"],
    "sorting_fulfillment": ["分拣配货", "sorting", "packing"],
    "loading_handling": ["装车搬运", "loading", "unloading", "carrying"],
    "delivery_process": ["配送过程", "delivery", "vehicle"],
    "storefront": ["门店形象", "store", "showroom"],
    "installation_site": ["工地安装现场", "installation_site", "installation"],
    "team_service": ["人物团队服务", "person_visible", "inspection", "demonstration"],
    "daily_overview": ["综合日更", "overview", "warehouse"],
}


def norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text).lower())


def risky(text: str) -> bool:
    compact = norm(text)
    return any(norm(term) in compact for term in RISK_TERMS)


def dedupe(items: list[str], threshold: float = 0.84) -> tuple[list[str], int, int]:
    exact: set[str] = set()
    kept: list[str] = []
    exact_count = 0
    near_count = 0
    for raw in items:
        text = str(raw).strip()
        key = norm(text)
        if not key or risky(text):
            continue
        if key in exact:
            exact_count += 1
            continue
        if any(SequenceMatcher(None, key, norm(old)).ratio() >= threshold for old in kept):
            near_count += 1
            continue
        exact.add(key)
        kept.append(text)
    return kept, exact_count, near_count


def component(component_id: str, text: str, tags: list[str], *, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": component_id,
        "text": text,
        "tags": tags,
        "requires_evidence": evidence or [],
        "risk": "low",
        "platforms": ["douyin", "channels", "xhs", "kuaishou"],
        "weight": 1,
    }


def build_v2(source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    old_meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    company = source.get("company_info") if isinstance(source.get("company_info"), dict) else {}
    pool = source.get("keyword_pool") if isinstance(source.get("keyword_pool"), dict) else {}
    themes = pool.get("themes") if isinstance(pool.get("themes"), dict) else {}
    title_pool = pool.get("title_pool") if isinstance(pool.get("title_pool"), dict) else {}
    categories = title_pool.get("categories") if isinstance(title_pool.get("categories"), dict) else {}
    bindings = title_pool.get("theme_category_bindings") if isinstance(title_pool.get("theme_category_bindings"), dict) else {}
    all_titles = [str(x) for values in categories.values() if isinstance(values, list) for x in values]
    clean_titles, exact_dupes, near_dupes = dedupe(all_titles)

    facts: dict[str, Any] = {}
    for key, value in company.items():
        visibility = "internal_only" if key in INTERNAL_KEYS else "public"
        evidence = "client_statement"
        valid_until = None
        if key in {"headcount", "scale_claimed_by_client"}:
            evidence = "client_statement_expiring"
            valid_until = "2026-10-21"
        facts[f"company.{key}"] = {
            "value": value,
            "visibility": visibility,
            "evidence_status": evidence,
            "valid_until": valid_until,
            "source": str(old_meta.get("source") or "legacy keyword pack"),
        }

    default_hooks = [
        "先看现场实拍", "今天看一组细节", "从画面认识这批货", "这组镜头记录了现场",
        "把镜头拉近看看", "从陈列到搬运逐段看", "今天按画面说", "先看可见信息",
    ]
    safe_titles_by_theme: dict[str, list[str]] = {}
    for theme, cats in bindings.items():
        values = [str(x) for cat in cats if isinstance(cats, list) for x in (categories.get(cat) or [])]
        safe_titles_by_theme[str(theme)], _, _ = dedupe(values)
    if not safe_titles_by_theme:
        safe_titles_by_theme["default"] = clean_titles

    compiled_themes: dict[str, Any] = {}
    for theme, block in themes.items():
        block = block if isinstance(block, dict) else {}
        keywords = block.get("keywords") if isinstance(block.get("keywords"), list) else []
        safe_keywords = [
            item for item in keywords
            if not risky(str(item.get("text") if isinstance(item, dict) else item))
        ]
        theme_titles = safe_titles_by_theme.get(str(theme), [])[:24]
        if not theme_titles and theme == "default":
            theme_titles = clean_titles[:24]
        compiled_themes[str(theme)] = {
            "label": str(block.get("label") or theme),
            "keywords": safe_keywords,
            "hooks": default_hooks,
            "titles": theme_titles,
        }

    universal = {
        "hooks": [
            component(f"hook.{i:02d}", text, ["hook", "visible_only"])
            for i, text in enumerate(default_hooks, 1)
        ],
        "visible_facts": [
            component("visible.01", "画面可见产品外观与包装", ["visible_fact", "product_closeup"], evidence=["semantic.visible_facts"]),
            component("visible.02", "镜头记录了仓内陈列", ["visible_fact", "warehouse"], evidence=["semantic.scene"]),
            component("visible.03", "画面展示装车与搬运过程", ["visible_fact", "loading"], evidence=["semantic.interaction"]),
            component("visible.04", "现场可见多类货品整齐摆放", ["visible_fact", "display"], evidence=["semantic.visible_facts"]),
            component("visible.05", "特写能看清品类与细节", ["transition", "overview"]),
            component("visible.06", "人物正在进行画面可见的操作", ["visible_fact", "people"], evidence=["semantic.people"]),
        ],
        "process": [
            component("process.01", "按画面顺序看陈列分拣与搬运", ["process"]),
            component("process.02", "从外观包装再看到现场操作", ["process"]),
            component("process.03", "这段记录的是实际作业过程", ["process"], evidence=["semantic.interaction"]),
            component("process.04", "各环节现场过程都交代清楚", ["process"]),
        ],
        "value": [
            component("value.01", "信息以当前实拍为准", ["value", "safe_fallback"]),
            component("value.02", "先看清品类，型号再当面定", ["value", "safe_fallback"]),
            component("value.03", "按采购需要挑对应品类", ["value", "safe_fallback"]),
            component("value.04", "我们展示细节来满足您的需求", ["value", "visible_only"]),
        ],
        "cta": [
            component("cta.01", "型号规格价格请当面沟通", ["cta", "safe_fallback"]),
            component("cta.02", "需要哪类产品可以跟我们说", ["cta"]),
            component("cta.03", "库存与交付当面沟通", ["cta", "safe_fallback"]),
            component("cta.04", "按使用需要咨询对应品类", ["cta"]),
        ],
    }
    recipes = {
        key: {
            "id": f"recipe.{key}",
            "label": values[0],
            "match": {"any_tags": values[1:]},
            "sequence": ["hook", "visible_facts", "process", "value", "cta"],
            "required_evidence": ["semantic.strict_v1"],
            "max_components": 5,
            "fallback_recipe": "recipe.daily_overview",
        }
        for key, values in CONTENT_TYPES.items()
    }
    revision = max(6, int(old_meta.get("revision") or old_meta.get("version") or 1) + 1)
    output = {
        "meta": {
            "schema": "suying.customer.content-pack.v2",
            "revision": revision,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": str(old_meta.get("source") or "legacy v1"),
            "customer_key": str(old_meta.get("customer_key") or ""),
            "change_summary": "v1 深度去重并迁移为事实、语义路由、可组合文案、合规四层结构",
        },
        "identity": {
            "customer_key": str(old_meta.get("customer_key") or ""),
            "display_name": str(company.get("display_name_preferred") or company.get("legal_name") or ""),
            "short_name": "始峰五金",
            "positioning_fact_id": "company.positioning_one_liner",
        },
        "facts": facts,
        "taxonomy": {
            "source_schema": "suying.cliplet.semantic.v1",
            "content_types": {
                key: {"label": values[0], "match_any": values[1:]}
                for key, values in CONTENT_TYPES.items()
            },
            "unknown_policy": "不得从文件名、外观或行业常识补造用途与参数",
        },
        "copy_components": {
            "default": {"hooks": default_hooks, "titles": clean_titles[:30]},
            "themes": compiled_themes,
            "library": universal,
            "hashtags": ["始峰五金", "建筑五金", "现场实拍", "仓库日常", "产品实拍"],
        },
        "recipes": recipes,
        "compliance": {
            "policy_version": "2026-08-cn-ads-platform-v2-layered",
            "hard_deny": sorted(HARD_DENY_TERMS),
            "blocked_terms": list(
                dict.fromkeys(
                    [
                        *(
                            str(x)
                            for x in (source.get("compliance", {}).get("blocked_terms") or [])
                            if str(x) and str(x) not in CAUTION_TERMS
                        ),
                        *sorted(HARD_DENY_TERMS),
                    ]
                )
            ),
            "evidence_required": {
                "现货": "inventory_snapshot",
                "库存": "inventory_snapshot",
                "送达": "delivery_commitment",
                "经营年限": "business_registry",
                "仓库面积": "verified_scale",
                "品牌授权": "authorization_document",
                "规格": "product_specification",
                "型号": "product_specification",
            },
            "caution_terms": sorted(CAUTION_TERMS),
            "internal_only_fact_prefixes": [
                "company.legal_representative", "company.credit_code",
                "company.addresses", "company.contacts",
            ],
            "safe_rewrites": dict(SAFE_REWRITES),
        },
    }
    report = {
        "source_revision": old_meta.get("revision") or old_meta.get("version"),
        "target_revision": revision,
        "source_title_count": len(all_titles),
        "retained_title_count": len(clean_titles),
        "exact_duplicates_removed": exact_dupes,
        "near_duplicates_removed": near_dupes,
        "risk_titles_removed": len(all_titles) - len(clean_titles) - exact_dupes - near_dupes,
        "fact_count": len(facts),
        "component_count": sum(len(v) for v in universal.values()),
        "recipe_count": len(recipes),
        "theoretical_combinations_per_recipe": (
            len(universal["hooks"]) * len(universal["visible_facts"])
            * len(universal["process"]) * len(universal["value"]) * len(universal["cta"])
        ),
    }
    return output, report


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--archive-siblings", action="store_true")
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    output, report = build_v2(source)
    if args.revision is not None:
        if args.revision < 1:
            raise ValueError("--revision 必须大于 0")
        output["meta"]["revision"] = args.revision
        report["target_revision"] = args.revision
    report["sha256"] = hashlib.sha256(
        json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if args.report:
        atomic_write(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.promote:
        return 0
    canonical = args.canonical or args.source.parent / "keyword-pack.json"
    archive = canonical.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    if args.archive_siblings:
        names = {
            "keyword-pack.product-introduction.backup-20260727-120558.json",
            "keyword-pack.product-introduction.json",
            "product-introduction-v1.json",
            "北京始峰伟业-公司信息与关键词池.md",
        }
        for item in canonical.parent.iterdir():
            if item.is_file() and item.name in names:
                shutil.move(str(item), str(archive / item.name))
    if canonical.exists():
        old = json.loads(canonical.read_text(encoding="utf-8"))
        old_rev = int((old.get("meta") or {}).get("revision") or (old.get("meta") or {}).get("version") or 1)
        old_sha = hashlib.sha256(canonical.read_bytes()).hexdigest()[:8]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.move(str(canonical), str(archive / f"keyword-pack.r{old_rev}.{stamp}.{old_sha}.json"))
    atomic_write(canonical, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
