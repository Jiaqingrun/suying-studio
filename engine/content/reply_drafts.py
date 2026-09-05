"""Local reply draft helpers — never auto-send."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import ReachMessage, ReplyDraft
from engine.content.store import reply_draft_to_dict


def _now() -> datetime:
    return datetime.now(timezone.utc)


def suggest_replies(
    *,
    sender: str,
    summary: str,
    brand: str = "",
    extra_context: str = "",
) -> list[str]:
    """2–3 short drafts clearly based on desensitized summary only."""
    who = (sender or "您好").strip() or "您好"
    snip = (summary or "").strip()[:120]
    brand = (brand or "我们").strip() or "我们"
    base_note = "（基于脱敏摘要生成，上下文可能不完整，发送前请在官方页核对。）"
    drafts = [
        f"{who}，感谢留言。关于「{snip or '您的问题'}」，{brand}已收到，我们会在官方渠道尽快核实后回复。{base_note}",
        f"{who}，您好。当前仅看到摘要信息：{snip or '（无摘要）'}。请补充具体需求（产品/地区/时间），我们按确认口径答复。{base_note}",
        f"{who}，谢谢关注。{brand}建议您优先查看官网最新说明；若需人工跟进，请留下可公开的联系方式。{base_note}",
    ]
    if (extra_context or "").strip():
        drafts.insert(
            0,
            f"{who}，结合您补充的上下文（{extra_context.strip()[:80]}），我们的回复是：针对「{snip}」，请以人工确认后的官网口径为准。{base_note}",
        )
    return drafts[:3]


def create_reply_drafts_for_message(
    session: Session,
    *,
    customer_id: int,
    message_id: int,
    brand: str = "",
    extra_context: str = "",
    business_scope: str = "content",
) -> list[dict[str, Any]]:
    from engine.reach.business_scope import normalize_scope

    scope = normalize_scope(business_scope)
    msg = session.get(ReachMessage, message_id)
    if not msg or msg.customer_id != customer_id:
        raise ValueError("消息不存在或不属于当前客户")
    msg_scope = getattr(msg, "business_scope", None) or "video"
    if msg_scope != scope:
        raise ValueError(f"消息属于 {msg_scope} 业务域，不能用 {scope} 接口生成草稿")
    bodies = suggest_replies(
        sender=msg.sender,
        summary=msg.summary,
        brand=brand,
        extra_context=extra_context,
    )
    rows: list[ReplyDraft] = []
    for body in bodies:
        row = ReplyDraft(
            customer_id=customer_id,
            business_scope=scope,
            message_id=message_id,
            interaction_id=None,
            body=body,
            based_on_summary_only=not bool((extra_context or "").strip()),
            context_note=(extra_context or "")[:500],
            status="draft",
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return [reply_draft_to_dict(r) for r in rows]
