"""Narration diversity: less stock-fill, opener de-dupe, industry lines."""

from __future__ import annotations

import json
from pathlib import Path

from engine.catalog.industry_pack import clear_pack_cache, narration_lines_for_theme
from engine.pack.narration_script import (
    STOCK_BANNED_PHRASES,
    first_sentence,
    narration_script_zh,
    opener_key,
    recent_narration_openers,
    script_has_stock_ban,
)


def test_sparse_script_shorter_than_rich() -> None:
    sparse = narration_script_zh(
        "屏幕标题不要念",
        brand="演示品牌",
        theme="产品",
        clip_hints=[],
        target_duration_sec=30,
        tone="plain",
        variation_seed=11,
    )
    rich = narration_script_zh(
        "屏幕标题不要念",
        brand="演示品牌",
        theme="产品",
        clip_hints=[
            "工人把货往车上码稳",
            "货架上一排一排放齐",
            "三轮车斗里一件件码齐",
        ],
        target_duration_sec=30,
        tone="plain",
        variation_seed=11,
        allowed_facts=["仓配一体可就地取货"],
        recipe_components=["装车后复核件数"],
    )
    # sparse must not pad to ~95% duration (~100 chars); rich can be longer
    assert len(sparse) < len(rich)
    assert len(sparse) < 95


def test_stock_ban_not_in_base_script() -> None:
    script = narration_script_zh(
        "标题",
        brand="演示",
        theme="default",
        clip_hints=["现场工人正在装车"],
        target_duration_sec=22,
        variation_seed=7,
    )
    assert not script_has_stock_ban(script)
    for phrase in STOCK_BANNED_PHRASES:
        assert phrase not in script


def test_exclude_openers_changes_first_sentence() -> None:
    kwargs = {
        "title": "屏幕标题",
        "brand": "演示品牌",
        "theme": "配送",
        "clip_hints": ["工人把货往车上码稳"],
        "target_duration_sec": 20,
        "tone": "plain",
        "variation_seed": 3,
    }
    first = narration_script_zh(**kwargs)
    excluded = [first_sentence(first)]
    second = narration_script_zh(**kwargs, exclude_openers=excluded)
    assert opener_key(first) != opener_key(second)


def test_industry_lines_enter_script() -> None:
    clear_pack_cache()
    lines = narration_lines_for_theme("building-supply", "门店")
    assert len(lines) >= 6
    script = narration_script_zh(
        "标题",
        brand="始峰",
        theme="门店",
        clip_hints=["柜台前能看得见货"],
        product_display=False,
        loading_ops=False,
        target_duration_sec=24,
        variation_seed=1,
        industry_lines=lines,
    )
    # at least one pack body line should land
    assert any(ln.rstrip("。") in script for ln in lines)


def test_variation_seed_reproducible_and_differs() -> None:
    kwargs = {
        "title": "屏幕标题不要念",
        "brand": "演示品牌",
        "theme": "产品",
        "clip_hints": ["画面展示仓库中整齐排列的产品细节", "工作人员正在认真核对现场货物"],
        "target_duration_sec": 22,
        "tone": "professional",
    }
    a = narration_script_zh(**kwargs, variation_seed=42)
    b = narration_script_zh(**kwargs, variation_seed=99)
    assert a != b
    assert narration_script_zh(**kwargs, variation_seed=42) == a
    assert "仓库中整齐排列" in a or "认真核对" in a


def test_recent_narration_openers_from_sidecar(tmp_path: Path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from engine.catalog.db import Base, Customer, Job, RenderOutput

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session: Session = SessionLocal()
    try:
        cust = Customer(name="旁白客户", profile_json={"industry_pack": "building-supply"})
        session.add(cust)
        session.commit()
        session.refresh(cust)
        job = Job(
            customer_id=cust.id,
            status="done",
            mode="count",
            template_name="default-vertical",
            theme="配送",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        side = tmp_path / "out.json"
        side.write_text(
            json.dumps(
                {
                    "meta": {
                        "narration_script": "镜头先对准演示这边的动作。工人把货往车上码稳。",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = RenderOutput(
            job_id=job.id,
            output_path=str(tmp_path / "out.mp4"),
            state="ready",
            seed=1,
            sidecar_path=str(side),
        )
        session.add(out)
        session.commit()

        openers = recent_narration_openers(session, cust.id, limit_outputs=5)
        assert openers
        assert "镜头先对准" in openers[0] or opener_key(openers[0])
    finally:
        session.close()


def test_oral_filler_banned_in_script() -> None:
    from engine.pack.narration_script import ORAL_FILLER_BANS, script_has_oral_filler

    assert script_has_oral_filler("你看这仓库里头工具排齐")
    assert script_has_oral_filler("真的看着就踏实了赶紧看细节")
    assert script_has_oral_filler("就是这样装车")
    bad = "你看这工具。真的整齐。就是这样。赶紧看完。"
    assert script_has_stock_ban(bad)
    # generated scripts must not include key fillers
    s = narration_script_zh(
        "标题",
        brand="始峰",
        theme="产品",
        clip_hints=["橙色包装盒内钳子并排放放", "绿色工具箱打开见电动起子"],
        scene_tags=["product_closeup", "product_closeup"],
        target_duration_sec=20,
        tone="passionate",
        product_display=True,
        variation_seed=5,
    )
    assert not script_has_oral_filler(s)
    assert "你看" not in s and "真的" not in s and "赶紧" not in s


def test_product_display_blocks_shipping_claims() -> None:
    from engine.pack.narration_script import script_has_packaging_meta, script_has_shipping_claim

    s = narration_script_zh(
        "大件装车一次看完",
        brand="始峰五金",
        theme="default",
        clip_hints=[
            "白色平面上并排放着扳手套装",
            "黄色卷尺包装盒正面朝上",
            "橙色钳子精品包装打开",
        ],
        scene_tags=["product_closeup", "product_closeup", "product_closeup"],
        industry_lines=["工人把货往车上码稳。", "盒内三把钳子并排。", "装车发货也准时。", "扳手类，紧固拆装好搭档。"],
        product_display=True,
        target_duration_sec=22,
        tone="passionate",
        variation_seed=2,
    )
    assert not script_has_shipping_claim(s)
    assert not script_has_packaging_meta(s)
    assert "装车" not in s and "发货" not in s
    assert "包装" not in s and "字样" not in s and "平面" not in s
    assert any(x in s for x in ("扳手", "卷尺", "钳", "五金", "工具"))


def test_product_commercial_not_packaging_caption() -> None:
    from engine.pack.narration_script import commercial_product_lines_from_hints

    lines = commercial_product_lines_from_hints(
        ["橙色包装盒内三把钳子", "黄色卷尺包装印长城精工字样", "扳手套装并排"]
    )
    assert lines
    joined = "".join(lines)
    assert "包装" not in joined and "字样" not in joined
    assert "钳" in joined or "卷尺" in joined or "扳手" in joined


def test_product_script_has_light_purchase_close() -> None:
    """Phase B lock: product VO ends lightly purchase-oriented (user: 更卖)."""
    s = narration_script_zh(
        "五金在售",
        brand="始峰五金",
        theme="产品",
        clip_hints=["扳手套装并排", "钢卷尺陈列", "钳子三件"],
        scene_tags=["product_closeup"] * 3,
        product_display=True,
        target_duration_sec=18,
        tone="professional",
        variation_seed=5,
    )
    purchaseish = (
        "到店",
        "选配",
        "按需",
        "跟我们",
        "直接拿",
        "现货",
        "配齐",
        "缺哪样",
        "看中",
        "要哪一类",
        "单买",
    )
    assert any(p in s for p in purchaseish)
    assert "保证" not in s and "第一" not in s and "厂家直销" not in s and "马上发货" not in s


def test_loading_ops_flow_is_continuous_not_telegram() -> None:
    from engine.pack.narration_script import script_is_telegram_choppy

    s = narration_script_zh(
        "真实现场装载",
        brand="始峰五金",
        theme="配送",
        clip_hints=[
            "工作人员正忙碌地装载货物",
            "配件码放在车厢旁仔细核对",
            "钢缆与纸箱一排待运",
            "货车门敞开向内码货",
        ],
        scene_tags=["warehouse", "loading", "truck"],
        product_display=False,
        loading_ops=True,
        target_duration_sec=20,
        tone="passionate",
        variation_seed=9,
    )
    assert not script_is_telegram_choppy(s)
    assert any(k in s for k in ("跟着镜头", "真实现场", "先走近", "顺着画面", "镜头带着", "过程听清楚", "现场走一遍"))
    assert "，" in s  # multi-clause flow
    assert not script_has_stock_ban(s)


def test_ollama_rejects_telegram_loading() -> None:
    from engine.pack.ollama_narration import _finalize_ok

    choppy = _finalize_ok(
        data={"script": "货车门敞开码货中。工人忙装载。配件核对后。钢缆列队。", "emoji_cues": []},
        base_script="跟着镜头看看始峰五金的真实现场，工作人员正忙碌地装载货物。",
        model="test",
        theme="配送",
        target_duration_sec=18.0,
        forbidden_claims=None,
        hints=["工作人员装载", "货车门敞开"],
        attempts=1,
        loading_ops=True,
    )
    assert choppy.get("ok") is False
    assert choppy.get("error") == "rewrite_telegram_choppy"


def test_resolve_delivery_tone_phase_b() -> None:
    from engine.pack.narration_script import resolve_delivery_tone

    # Explicit friendly tones stay warm even on product reels (life-service / brand rules)
    assert resolve_delivery_tone("passionate", product_display=True) == "warm"
    assert resolve_delivery_tone("warm", product_display=True) == "warm"
    assert resolve_delivery_tone("plain", product_display=True) == "professional"
    assert resolve_delivery_tone("passionate", loading_ops=True) == "warm"
    assert resolve_delivery_tone("plain", product_display=False, loading_ops=False) == "plain"
    assert resolve_delivery_tone("warm", product_display=False, loading_ops=False) == "warm"


def test_ollama_rejects_filler_and_shipping() -> None:
    from engine.pack.ollama_narration import _finalize_ok

    filler = _finalize_ok(
        data={"script": "你看这工具排得整齐。真的赶紧看细节。就是这样。", "emoji_cues": []},
        base_script="扳手类紧固拆装好搭档。钳类剪切夹持常用。",
        model="test",
        theme="产品",
        target_duration_sec=18.0,
        forbidden_claims=None,
        hints=["扳手套装", "钳子三把"],
        attempts=1,
        product_display=True,
    )
    assert filler.get("ok") is False
    assert filler.get("error") == "rewrite_stock_ban"

    ship = _finalize_ok(
        data={"script": "扳手套装齐全。随后装车发货出库。", "emoji_cues": []},
        base_script="扳手类紧固拆装好搭档。",
        model="test",
        theme="产品",
        target_duration_sec=18.0,
        forbidden_claims=None,
        hints=["扳手套装", "钳子"],
        attempts=1,
        product_display=True,
    )
    assert ship.get("ok") is False
    assert ship.get("error") == "rewrite_shipping_on_product"

    pkg = _finalize_ok(
        data={"script": "黄色包装盒上印有字样。盒内三把钳子并排。", "emoji_cues": []},
        base_script="钳类工具剪切夹持常用。",
        model="test",
        theme="产品",
        target_duration_sec=18.0,
        forbidden_claims=None,
        hints=["钳子三把", "扳手套装"],
        attempts=1,
        product_display=True,
    )
    assert pkg.get("ok") is False
    assert pkg.get("error") == "rewrite_packaging_meta"


def test_product_slots_silence_same_class_no_repeat_line() -> None:
    """Same product class (e.g. 卷尺) only spoken once; later cuts may be silent."""
    from engine.pack.narration_script import product_slot_scripts_from_plan

    clips = [
        {
            "slot": "hook",
            "duration_sec": 4.0,
            "description": "黄色包装盒印长城精工与钢卷尺50m字样，画面无人物。",
        },
        {
            "slot": "body1",
            "duration_sec": 7.0,
            "description": "打开的绿色硬质塑料工具箱，电动起子机与电池充电器套装。",
        },
        {
            "slot": "body2",
            "duration_sec": 7.0,
            "description": "白色平面上多个卷尺产品并排，有5m与10m钢带。",
        },
        {
            "slot": "cta",
            "duration_sec": 6.3,
            "description": "黄色包装盒内钢卷尺产品，盒面写着长城精工与50mx12.5mm。",
        },
    ]
    slots = product_slot_scripts_from_plan(clips, brand="始峰五金", variation_seed=1)
    texts = [str(s.get("text") or "") for s in slots]
    full = "".join(texts)
    assert "量尺划线方便带" not in full or full.count("量尺划线方便带") == 1
    # First tape-class VO may be 精工卷尺系列 or 钢卷尺 — only once either form
    tape_hits = full.count("卷尺")
    assert tape_hits <= 2  # opener brand line + one product callout at most
    # Latter two beams must not re-read the full 钢卷尺 template
    assert "量尺划线方便带" not in (texts[2] + texts[3])
    assert "钢卷尺，量尺" not in (texts[2] + texts[3])
    # Middle same-class cut may be fully silent (empty OK)
    assert texts[2] == "" or "工具箱" not in texts[2]
    # Toolbox once if present
    assert full.count("工具箱") <= 1

