"""Tests for copy diversity gate."""

from __future__ import annotations

from engine.pack.copy_diversity_gate import (
    CopyDiversityRecord,
    check_narration,
    check_title,
    script_bigram_jaccard,
)
from engine.pack.narration_script import opener_key


def test_title_exact_collision() -> None:
    recent = [CopyDiversityRecord(title="细节决定体验")]
    assert check_title("细节决定体验", recent) == "title_collision"
    assert check_title("细节 决定体验", recent) == "title_collision"


def test_opener_collision() -> None:
    script = "跟着镜头看现场，步骤都在眼前。欢迎来店详聊。"
    recent = [CopyDiversityRecord(opener=opener_key(script))]
    assert check_narration(script, recent) == "opener_collision"


def test_script_similarity() -> None:
    a = "跟着镜头看现场步骤都在眼前流程讲清楚每一步都不糊弄"
    b = "跟着镜头看现场步骤都在眼前流程讲清楚每一步都不糊弄欢迎来店"
    assert script_bigram_jaccard(a, b) >= 0.72
    recent = [CopyDiversityRecord(script=a)]
    assert check_narration(b, recent) == "script_similarity"
