"""Fail-soft compliance rewrites for evidence gaps and soft marketing denies."""

from __future__ import annotations

from engine.content.compliance import (
    apply_claim_soft_rewrites,
    apply_evidence_claim_rewrites,
    check_output_fields,
    sanitize_script_claims,
)


def _policy() -> dict:
    return {
        "hard_deny": ["放心", "全网最低", "厂家直销"],
        "blocked_terms": ["放心", "全网最低"],
        "caution_terms": ["放心", "靠谱"],
        "safe_rewrites": {"放心": "踏实", "靠谱": "踏实"},
        "evidence_required": {
            "规格": "product_specification",
            "型号": "product_specification",
        },
    }


def test_soft_rewrite_clears_放心_and_规格():
    policy = _policy()
    fields = {
        "title": "核对规格再下单",
        "narration": "用心服务更放心。现场做事更踏实。",
        "subtitle": "用心服务更放心。",
    }
    report = check_output_fields(fields, policy=policy, evidence_ids=set())
    assert report["passed"] is False
    rewritten, applied = apply_claim_soft_rewrites(
        fields, report, policy=policy, allow_soft_deny=True
    )
    assert applied
    again = check_output_fields(rewritten, policy=policy, evidence_ids=set())
    assert again["passed"] is True
    assert "放心" not in rewritten["narration"]
    assert "规格" not in rewritten["title"]
    assert "品类" in rewritten["title"] or "踏实" in rewritten["narration"]


def test_true_hard_deny_still_blocks():
    policy = _policy()
    fields = {"title": "全网最低", "narration": "厂家直销秒发"}
    report = check_output_fields(fields, policy=policy, evidence_ids=set())
    assert report["passed"] is False
    rewritten, applied = apply_claim_soft_rewrites(
        fields, report, policy=policy, allow_soft_deny=True
    )
    # No short rewrite for 全网最低 / 厂家直销 → still fails after pass.
    again = check_output_fields(rewritten, policy=policy, evidence_ids=set())
    assert again["passed"] is False
    assert any(
        i.get("term") in {"全网最低", "厂家直销"} for i in again["issues"] if i.get("level") == "error"
    )


def test_evidence_only_wrapper_skips_hard_deny():
    policy = _policy()
    fields = {"narration": "服务更放心，型号请看标签"}
    report = check_output_fields(fields, policy=policy, evidence_ids=set())
    rewritten, applied = apply_evidence_claim_rewrites(fields, report, policy=policy)
    assert applied == []
    assert rewritten["narration"] == fields["narration"]


def test_sanitize_script_claims_strips_soft_terms():
    out = sanitize_script_claims(
        "真实过程看得见。用心服务更放心。",
        policy={"safe_rewrites": {"放心": "踏实"}},
    )
    assert "放心" not in out
    assert "踏实" in out


def test_sanitize_script_claims_rewrites_现货_to_在架():
    from engine.content.compliance import apply_rewrites_to_text

    out = sanitize_script_claims(
        "始峰五金现货丰富任您挑选。现货装车省时又省力。",
        policy={"hard_deny": ["现货"], "blocked_terms": ["现货"]},
    )
    assert "现货" not in out
    assert "在架" in out
    applied = [{"term": "现货", "replacement": "在架"}]
    assert "现货" not in apply_rewrites_to_text("仓配现货能力", applied)
    assert "在架" in apply_rewrites_to_text("仓配现货能力", applied)


def test_scene_tour_slots_sanitize_aligns_with_script():
    """跟镜槽位经 sanitize 后与汇总 script 均不得残留禁词「现货」。"""
    from engine.pack.narration_script import script_from_product_slots

    policy = {"hard_deny": ["现货"], "blocked_terms": ["现货"]}
    slots = [
        {"slot": "s0", "text": "仓里货架码得整齐，现货当场能看清。"},
        {"slot": "s1", "text": "跟到装卸现场看装车，出库配货有序可跟。"},
    ]
    for row in slots:
        row["text"] = sanitize_script_claims(str(row["text"]), policy=policy)
    script = script_from_product_slots(slots)
    assert "现货" not in script
    assert all("现货" not in str(s["text"]) for s in slots)
    assert "在架" in slots[0]["text"]


def test_dirty_safe_rewrites_reject_库存_replacement():
    """活库坏映射「现货充足→库存…」不得被采用；回退到无库存/现货替换。"""
    policy = {
        "hard_deny": ["现货"],
        "blocked_terms": ["现货"],
        "evidence_required": {"现货": "inventory_snapshot", "库存": "inventory_snapshot"},
        "safe_rewrites": {
            "现货充足": "库存情况请以实际确认结果为准",
        },
    }
    out = sanitize_script_claims("现场现货充足可看。", policy=policy)
    assert "现货" not in out
    assert "库存" not in out
    assert "在架" in out


def test_product_display_slots_sanitize_after_rebuild():
    """日更 product_display 重建槽位后须 sanitize，与跟镜同一路径语义。"""
    from engine.pack.narration_script import script_from_product_slots

    policy = {
        "hard_deny": ["现货"],
        "blocked_terms": ["现货"],
        "evidence_required": {"现货": "inventory_snapshot", "库存": "inventory_snapshot"},
        "safe_rewrites": {"现货充足": "库存情况请以实际确认结果为准"},
    }
    slots = [
        {"slot": "s0", "text": "这款五金现货充足，当面核对外观。"},
        {"slot": "s1", "text": "台面摆样看得清，按需选品类。"},
    ]
    for row in slots:
        row["text"] = sanitize_script_claims(str(row["text"]), policy=policy)
    script = script_from_product_slots(slots)
    assert "现货" not in script
    assert "库存" not in script
    assert all("现货" not in str(s["text"]) and "库存" not in str(s["text"]) for s in slots)
