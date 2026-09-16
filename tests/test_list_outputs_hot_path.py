"""SA-R2-005: GET /outputs must not write (backfill) or run unbounded."""

from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path


class TestListOutputsHotPath(unittest.TestCase):
    def test_list_outputs_signature_has_job_id_and_limit(self) -> None:
        from engine.api.library_review_routes import list_outputs

        sig = inspect.signature(list_outputs)
        self.assertIn("job_id", sig.parameters)
        self.assertIn("limit", sig.parameters)

    def test_list_outputs_ast_has_no_backfill_call(self) -> None:
        src_path = Path(__import__("engine.api.library_review_routes", fromlist=["x"]).__file__)
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        fn = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "list_outputs":
                fn = node
                break
        self.assertIsNotNone(fn)
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == "backfill_display_nos")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "backfill_display_nos")
            )
        ]
        self.assertEqual(calls, [], "list_outputs must not call backfill_display_nos")
        # limit applied on query
        src = ast.get_source_segment(src_path.read_text(encoding="utf-8"), fn) or ""
        self.assertIn("q.limit", src)
        self.assertIn("job_id", src)


if __name__ == "__main__":
    unittest.main()
