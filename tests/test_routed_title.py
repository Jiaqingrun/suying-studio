from __future__ import annotations

import random

from engine.catalog.keyword_pack import (
    KeywordPack,
    compile_keyword_pack,
    pick_routed_on_screen_title,
)


def _v2_pack(*titles: str) -> KeywordPack:
    data = compile_keyword_pack(
        {
            "meta": {"schema": "suying.customer.content-pack.v2", "revision": 1},
            "identity": {"display_name": "测试客户"},
            "facts": {},
            "taxonomy": {
                "content_types": {
                    "daily_overview": {"label": "综合日更", "match_any": ["overview"]},
                    "warehouse_display": {"label": "仓内陈列", "match_any": ["warehouse"]},
                }
            },
            "copy_components": {
                "default": {
                    "hooks": ["先看现场实拍"],
                    "titles": list(titles),
                },
                "themes": {
                    "default": {
                        "keywords": [{"text": "仓配", "weight": 1}],
                        "titles": list(titles),
                        "hooks": ["先看现场实拍"],
                    }
                },
            },
            "recipes": {},
            "compliance": {"blocked_terms": []},
        }
    )
    return KeywordPack(
        id=1,
        customer_id=1,
        revision=1,
        schema_version="suying.customer.content-pack.v2",
        data_json=data,
    )


def test_pick_routed_title_uses_pool_not_taxonomy_suffix():
    pack = _v2_pack("始峰五金", "仓配一体", "分拣又快又齐")
    rng = random.Random(42)
    title = pick_routed_on_screen_title(
        rng,
        pack,
        theme="default",
        content_theme="warehouse_display",
    )
    assert title in {"始峰五金", "仓配一体", "分拣又快又齐"}
    assert title != "综合日更现场实拍"
    assert title != "仓内陈列现场实拍"


def test_pick_routed_title_respects_paper_slip_exclusions():
    pack = _v2_pack("始峰五金", "仓配一体", "分拣又快又齐")
    rng = random.Random(7)
    title = pick_routed_on_screen_title(
        rng,
        pack,
        theme="default",
        content_theme="default",
        exclude_phrases={"始峰五金", "仓配一体", "分拣又快又齐"},
    )
    assert title is None


def _product_ops_pack() -> KeywordPack:
    data = compile_keyword_pack(
        {
            "meta": {"schema": "suying.customer.content-pack.v2", "revision": 1},
            "identity": {"display_name": "始峰五金"},
            "facts": {},
            "taxonomy": {"content_types": {}},
            "copy_components": {
                "default": {"hooks": ["先看现场"], "titles": ["默认凑数标题"]},
                "title_banks": {
                    "产品介绍标题": ["外观细节展示清楚", "品类介绍可见"],
                    "装车搬运标题": ["张家湾仓装车搬运过程清晰可见", "五金大件装车摆放过程一次看完"],
                    "配送标题": ["出库装车连贯", "本地直发工地"],
                },
                "themes": {
                    "产品介绍": {
                        "keywords": [{"text": "五金", "weight": 1}],
                        "titles": ["外观细节展示清楚", "品类介绍可见"],
                        "hooks": ["先看现场"],
                    },
                    "产品": {
                        "keywords": [{"text": "五金", "weight": 1}],
                        "titles": ["工地五金机具齐"],
                        "hooks": ["先看现场"],
                    },
                },
            },
            "recipes": {},
            "compliance": {"blocked_terms": []},
        }
    )
    return KeywordPack(
        id=1,
        customer_id=1,
        revision=1,
        schema_version="suying.customer.content-pack.v2",
        data_json=data,
    )


def test_prefer_product_title_theme_pins_product_intro():
    from engine.catalog.keyword_pack import prefer_product_title_theme

    pack = _product_ops_pack()
    assert prefer_product_title_theme(pack, "default") == "产品介绍"


def test_product_reel_routed_title_excludes_loading_bank():
    pack = _product_ops_pack()
    rng = random.Random(1)
    title = pick_routed_on_screen_title(
        rng,
        pack,
        theme="产品介绍",
        content_theme=None,
        exclude_phrases={"装车", "配送", "发货", "仓配", "搬运"},
        strict_theme=True,
    )
    assert title in {"外观细节展示清楚", "品类介绍可见"}
    assert "装车" not in (title or "")
    assert "配送" not in (title or "")
