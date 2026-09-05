"""life-service industry pack loads and classifies beauty/store captions."""

from __future__ import annotations

from engine.catalog.industry_pack import clear_pack_cache, load_industry_pack, pack_id_for_customer
from engine.catalog.semantic_tags import classify_actions, classify_objects, classify_scene
from engine.catalog.theme_tags import classify_theme


def setup_function() -> None:
    clear_pack_cache()


def teardown_function() -> None:
    clear_pack_cache()


def test_life_service_pack_loads():
    pack = load_industry_pack("life-service")
    assert pack["id"] == "life-service"
    assert "门店形象" in pack["content_themes"]
    assert pack["_theme_rules_tuples"]
    assert any(r.get("theme") == "storefront" for r in pack.get("scene_rules") or [])


def test_zhenxiang_profile_resolves_life_service():
    assert pack_id_for_customer("臻享丽人", {"industry_pack": "life-service"}) == "life-service"
    assert pack_id_for_customer("臻享丽人", None) == "life-service"


def test_life_service_classify_from_visionish_caption():
    pid = "life-service"
    theme, score = classify_theme("店内走廊干净，拱门与接待区可见", pack_id=pid)
    assert theme == "门店形象"
    assert score > 0
    assert classify_scene("荣誉墙上挂着锦旗和奖状证书", pack_id=pid) == "company_image"
    assert classify_scene("临街门头招牌清楚入画", pack_id=pid) == "storefront"
    assert "护理品" in classify_objects("护理品瓶身特写与托盘器具", pack_id=pid)
    assert "护理操作" in classify_actions("护理台上手法涂抹过程", pack_id=pid)
