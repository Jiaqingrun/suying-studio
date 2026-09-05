"""标题样式：VIDEO_LOCK + Job 冻结规则同源，避免跟镜默认黄字分叉熔断。"""

from __future__ import annotations

from engine.pack.video_lock import (
    is_title_style_lock_desync,
    overlay_production_rules_on_title_style,
    resolve_title_style,
)


def test_overlay_frozen_rules_win_over_yellow_base() -> None:
    styled = overlay_production_rules_on_title_style(
        {
            "color": "#FFE600",
            "stroke_color": "#000000",
            "stroke_width": 6,
            "font_size": 84,
        },
        {
            "title_color": "#F3E2C5",
            "title_stroke_color": "#3A271C",
            "title_stroke_width": 5,
            "title_font_size": 88,
        },
    )
    assert styled["color"] == "#F3E2C5"
    assert styled["stroke_color"] == "#3A271C"
    assert styled["stroke_width"] == 5
    assert styled["font_size"] == 88


def test_resolve_title_style_prefers_frozen_over_default_yellow() -> None:
    """即使 VIDEO_LOCK 加载失败落到默认黄，冻结奶油色仍须进实渲。"""
    styled = resolve_title_style(
        {"font_size": 84, "color": "#FFE600", "stroke_width": 6},
        customer_name="__no_such_customer_for_lock__",
        production_rules={
            "title_color": "#F3E2C5",
            "title_stroke_color": "#3A271C",
            "title_stroke_width": 5,
        },
    )
    assert styled["color"] == "#F3E2C5"
    assert styled["stroke_color"] == "#3A271C"
    assert styled["stroke_width"] == 5


def test_title_desync_detects_yellow_vs_cream_gate_fails() -> None:
    fails = [
        "title: color='#FFE600' want frozen #F3E2C5",
        "title: stroke_color='#000000' want frozen #3A271C",
        "title: stroke_width=6 want frozen 5",
    ]
    assert is_title_style_lock_desync(fails) is True
    assert is_title_style_lock_desync(fails + ["subtitle: margin"]) is False
