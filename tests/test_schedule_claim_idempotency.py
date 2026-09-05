"""Schedule trigger atomic claim + occurrence terminal sync."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from engine.reach.publish_schedule import (
    claim_trigger_pending,
    sync_trigger_with_occurrence,
)


class ScheduleClaimTests(unittest.TestCase):
    def test_claim_pending_uses_conditional_update(self) -> None:
        session = MagicMock()
        result = MagicMock()
        result.rowcount = 1
        session.execute.return_value = result
        ok = claim_trigger_pending(session, 42, now=datetime.now(timezone.utc))
        self.assertTrue(ok)
        session.execute.assert_called_once()
        session.commit.assert_called_once()

    def test_claim_lost_when_rowcount_zero(self) -> None:
        session = MagicMock()
        result = MagicMock()
        result.rowcount = 0
        session.execute.return_value = result
        self.assertFalse(claim_trigger_pending(session, 42))

    def test_sync_trigger_maps_blocked_quality(self) -> None:
        session = MagicMock()
        now = datetime.now(timezone.utc)
        trig = MagicMock()
        trig.status = "preparing"
        trig.note = ""
        # Real datetimes: production compares window_end / planned_at under BAD occ.
        trig.window_end = now - timedelta(minutes=1)
        trig.planned_at = now - timedelta(minutes=15)
        session.get.return_value = trig
        sync_trigger_with_occurrence(
            session, occurrence_status="blocked_quality", trigger_id=9
        )
        self.assertEqual(trig.status, "failed")


if __name__ == "__main__":
    unittest.main()
