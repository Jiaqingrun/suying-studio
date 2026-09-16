"""SA-R2-005: GET /outputs must not write (backfill) or run unbounded."""

from __future__ import annotations

import inspect
import unittest


class TestListOutputsHotPath(unittest.TestCase):
    def test_list_outputs_signature_has_job_id_and_limit(self) -> None:
        from engine.api.library_review_routes import list_outputs

        sig = inspect.signature(list_outputs)
        self.assertIn("job_id", sig.parameters)
        self.assertIn("limit", sig.parameters)

    def test_list_outputs_source_skips_backfill_write(self) -> None:
        from pathlib import Path

        src = Path(inspect.getsourcefile(__import__("engine.api.library_review_routes", fromlist=["list_outputs"])))
        text = src.read_text(encoding="utf-8")
        start = text.index("def list_outputs")
        end = text.index("\ndef ", start + 1)
        body = text[start:end]
        self.assertNotIn("backfill_display_nos(", body)
        self.assertIn("job_id", body)
        self.assertIn("limit", body)
        self.assertIn("q.limit", body)


if __name__ == "__main__":
    unittest.main()
