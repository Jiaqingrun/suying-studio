#!/usr/bin/env python3
"""Convert 北京始峰伟业 keyword markdown into Montage Studio pack format."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SRC = Path(
    "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/03-词池/北京始峰伟业-公司信息与关键词池.md"
)
# Fallback: Desktop copy if sync tree missing
if not SRC.exists():
    SRC = Path.home() / "Desktop" / "北京始峰伟业-公司信息与关键词池.md"
OUT_DIR = ROOT / "configs" / "customers" / "北京始峰伟业"
OUT_FILE = OUT_DIR / "keyword-pack.json"


def extract_json(text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        raise SystemExit("No JSON block found in source markdown")
    return json.loads(match.group(1))


def to_engine_pack(raw: dict) -> dict:
    company = raw.get("company_info", {})
    pool = raw.get("keyword_pool", {})

    themes: dict = {
        "default": {
            "label": "综合品牌",
            "keywords": [
                {"text": t, "weight": 2 if i < 8 else 1}
                for i, t in enumerate(pool.get("core_phrases", []))
            ],
        }
    }

    for cat in pool.get("product_categories", []):
        if not cat.get("promote", True):
            continue
        name = cat["name"]
        items = [{"text": item, "weight": 2} for item in cat.get("items", [])]
        # also add category name as keyword
        items.insert(0, {"text": name, "weight": 3})
        for feat in cat.get("objective_features", []):
            items.append({"text": feat, "weight": 1})
        themes[name] = {"label": name, "keywords": items}

    themes["scenario"] = {
        "label": "场景话术",
        "keywords": [{"text": s, "weight": 1} for s in pool.get("scenario_phrases", [])],
    }
    themes["service"] = {
        "label": "服务卖点",
        "keywords": [{"text": s, "weight": 2} for s in pool.get("service_phrases_safe", [])],
    }

    brands = pool.get("brands_on_sale", [])
    brand_keywords = []
    for b in brands:
        brand = b.get("brand", "")
        if not brand:
            continue
        weight = 3 if b.get("client_note") == "主推" else 1
        brand_keywords.append({"text": brand, "weight": weight})
        for p in b.get("products", []):
            brand_keywords.append({"text": f"{brand}{p}", "weight": 1})
    if brand_keywords:
        themes["brands"] = {"label": "在售品牌", "keywords": brand_keywords}

    blocked = list(pool.get("service_phrases_do_not_use", []))
    # also common ad-law terms
    for extra in ("第一", "最好", "根治", "100%", "绝对"):
        if extra not in blocked:
            blocked.append(extra)

    flat = pool.get("flat_keywords_for_search") or pool.get("core_phrases") or []

    return {
        "meta": {
            **raw.get("meta", {}),
            "version": 1,
            "customer_key": "beijing-shifeng-weiye",
            "customer_name": company.get("display_name_preferred") or "北京始峰伟业",
            "converted_for": "montage-studio",
            "source_file": str(SRC),
        },
        "company_info": company,
        "compliance": {
            "blocked_terms": blocked,
            "notes": raw.get("meta", {}).get("notes", []),
        },
        "keyword_pool": {
            "themes": themes,
            "flat_keywords": flat,
            "core_phrases": pool.get("core_phrases", []),
            "hashtags": [
                "#始峰五金",
                "#建筑五金",
                "#工地采购",
                "#仓配一体",
                "#北京五金批发",
            ],
            "homepage_focus_categories": pool.get("homepage_focus_categories", []),
            "product_categories": pool.get("product_categories", []),
            "brands_on_sale": brands,
        },
        "libraries": [
            {
                "id": "xulingfei-album",
                "name": "徐玲飞业务片库",
                "path": "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/01-片库/徐玲飞",
                "notes": "客户业务片库之一；同级还有车凯盛等",
            },
            {
                "id": "chekaisheng-album",
                "name": "车凯盛业务片库",
                "path": "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/01-片库/车凯盛",
                "notes": "与徐玲飞同属 01-片库",
            },
        ],
        "internal_only": raw.get("internal_only", {}),
    }


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source not found: {SRC}")
    raw = extract_json(SRC.read_text(encoding="utf-8"))
    pack = to_engine_pack(raw)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_FILE}")
    print(f"Themes: {list(pack['keyword_pool']['themes'].keys())}")
    print(f"Blocked terms: {len(pack['compliance']['blocked_terms'])}")


if __name__ == "__main__":
    main()
