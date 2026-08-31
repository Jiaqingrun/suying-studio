#!/usr/bin/env python3
"""Seed 臻享丽人 D3 周槽位日历（life-service 五面，跳过未达 D1 的「服务沟通」）。"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlalchemy import select

from engine.catalog.calendar import CalendarEntryIn, upsert_entry
from engine.catalog.db import get_session, Customer

CUSTOMER = "臻享丽人"
# 周一→周日 facet 槽（服务沟通片库<8，用品牌故事替补）
WEEK_FACETS = [
    "门店形象",
    "护肤护理",
    "身体与形体",
    "品牌故事",
    "门店形象",
    "护肤护理",
    "品牌故事",
]


def main() -> int:
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    # 对齐到本周一
    monday = start - timedelta(days=start.weekday())
    with get_session() as s:
        c = s.scalar(select(Customer).where(Customer.name == CUSTOMER))
        if not c:
            print(f"客户不存在: {CUSTOMER}", file=sys.stderr)
            return 1
        for i, facet in enumerate(WEEK_FACETS):
            day = (monday + timedelta(days=i)).isoformat()
            upsert_entry(
                s,
                c.id,
                CalendarEntryIn(
                    day=day,
                    theme=facet,
                    category="default",
                    customer_name=CUSTOMER,
                    template_name="default-vertical",
                    quota=1,
                    note=f"D3 周槽 · {facet}",
                    active=True,
                ),
            )
            print(f"  {day} → {facet} quota=1")
        s.commit()
    print(f"已写入 {len(WEEK_FACETS)} 天日历（自 {monday.isoformat()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
