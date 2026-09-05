from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from inspect import getsource, signature
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Base, Cliplet, Customer
from engine.catalog.vector_index import index_cliplet
from engine.ingest.cliplet import (
    create_cliplets_for_asset,
    recaption_existing_cliplets,
    verify_cliplets_on_demand,
)
from engine.ingest.semantic_gate import (
    COARSE_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    STRICT_EMBEDDING_SCHEMA_VERSION,
    semantic_gate_passed,
    semantic_index_admissible,
)
from engine.ops.scheduler import DailyScheduler
from engine.api.app import VerifyClipletsRequest, verify_captions_on_demand
from tests.test_semantic_ingest import valid_analysis


class SemanticOnDemandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        customer = Customer(name="测试客户", profile_json={})
        self.session.add(customer)
        self.session.flush()
        self.customer_id = int(customer.id)
        asset = Asset(
            customer_id=customer.id,
            uuid="asset-1",
            source_path="/tmp/source.mp4",
            storage_path="/tmp/source.mp4",
            category="仓库",
            status="ready",
            duration_sec=5.0,
            width=1080,
            height=1920,
        )
        self.session.add(asset)
        self.session.flush()
        self.asset = asset

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _coarse_cliplet(self) -> Cliplet:
        row = Cliplet(
            asset_id=self.asset.id,
            asset_uuid=self.asset.uuid,
            start_sec=0.0,
            end_sec=4.0,
            duration_sec=4.0,
            description="分类:仓库；素材:测试；片段0-4秒；竖屏；实拍业务素材",
            category="仓库",
            score=0.8,
            status="usable",
            semantic_schema_version=COARSE_SEMANTIC_SCHEMA_VERSION,
            semantic_gate_json={
                "passed": True,
                "mode": "coarse",
                "source": "metadata_heuristic",
            },
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def test_coarse_row_is_indexable_but_not_strict(self) -> None:
        row = self._coarse_cliplet()
        self.assertTrue(semantic_index_admissible(row))
        self.assertFalse(semantic_gate_passed(row))
        with patch(
            "engine.catalog.vector_index.embed_text",
            return_value=([0.1, 0.2, 0.3], "test"),
        ):
            index_cliplet(self.session, row)
        self.assertEqual(row.status, "usable")
        self.assertEqual(row.embedding_json, [0.1, 0.2, 0.3])

    def test_api_respects_semantic_analysis_mode(self) -> None:
        with patch("engine.api.app.assert_runtime_active"), patch(
            "engine.api.app.load_settings",
            return_value=SimpleNamespace(semantic_analysis_mode="off"),
        ):
            with self.assertRaises(HTTPException) as caught:
                verify_captions_on_demand(VerifyClipletsRequest(cliplet_ids=[1]))
        self.assertEqual(caught.exception.status_code, 409)

        fake_session = MagicMock()
        with patch("engine.api.app.assert_runtime_active"), patch(
            "engine.api.app.load_settings",
            return_value=SimpleNamespace(semantic_analysis_mode="on_demand"),
        ), patch("engine.api.app.get_session", return_value=fake_session), patch(
            "engine.api.app._active_scope",
            return_value=(None, SimpleNamespace(id=9), None),
        ), patch(
            "engine.api.app.verify_cliplets_on_demand",
            return_value={"ok": True, "verified": 1},
        ) as verify:
            result = verify_captions_on_demand(
                VerifyClipletsRequest(cliplet_ids=[7])
            )
        self.assertTrue(result["ok"])
        verify.assert_called_once_with(
            fake_session, customer_id=9, cliplet_ids=[7]
        )
        fake_session.close.assert_called_once()

    def test_strict_index_rejects_non_ollama_backend(self) -> None:
        row = self._coarse_cliplet()
        semantic = valid_analysis()
        row.semantic_schema_version = SEMANTIC_SCHEMA_VERSION
        row.semantic_json = semantic
        row.semantic_gate_json = {"passed": True, "attempts": 1, "reasons": []}
        self.session.commit()
        with patch(
            "engine.catalog.vector_index.embed_text",
            return_value=([0.1, 0.2], "hash_fallback"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "strict_embedding_backend_invalid",
            ):
                index_cliplet(self.session, row)
        self.session.refresh(row)
        self.assertIsNone(row.embedding_json)
        self.assertIsNone(row.embedding_backend)
        self.assertFalse(semantic_gate_passed(row))

    def test_default_cliplet_creation_skips_vision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "normalized.mp4"
            video.write_bytes(b"test")
            with patch(
                "engine.ingest.cliplet.analysis_video_path", return_value=video
            ), patch(
                "engine.ingest.cliplet.detect_scenes", return_value=[]
            ), patch(
                "engine.ingest.quality.score_clip_window", return_value=0.8
            ), patch(
                "engine.ingest.cliplet._analyze_with_gate"
            ) as analyze:
                rows = create_cliplets_for_asset(self.session, self.asset)
        self.assertTrue(rows)
        analyze.assert_not_called()
        self.assertTrue(
            all(
                row.semantic_schema_version == COARSE_SEMANTIC_SCHEMA_VERSION
                for row in rows
            )
        )

    def test_background_scheduler_is_coarse_only(self) -> None:
        from engine.catalog.vector_index import index_asset_cliplets

        scheduler_source = getsource(DailyScheduler._loop)
        index_source = getsource(index_asset_cliplets)
        self.assertIn("executor.wake()", scheduler_source)
        self.assertNotIn("use_vision=True", scheduler_source)
        self.assertIn("use_vision=False", index_source)
        self.assertNotIn("use_vision=True", index_source)
        self.assertFalse(
            signature(recaption_existing_cliplets).parameters["use_vision"].default
        )

    def test_failed_9b_verification_preserves_coarse_vector(self) -> None:
        row = self._coarse_cliplet()
        row.embedding_json = [0.4, 0.5]
        self.session.commit()
        failed_audit = {
            "passed": False,
            "attempts": 1,
            "max_attempts": 1,
            "reasons": ["overall_low_confidence"],
            "final_vision_model": "qwen3.5:9b",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.load_settings",
            return_value=SimpleNamespace(
                paths=SimpleNamespace(frames_root=lambda: Path(tmp))
            ),
        ), patch(
            "engine.ingest.cliplet._analyze_with_gate",
            return_value=(None, failed_audit),
        ):
            result = verify_cliplets_on_demand(
                self.session,
                customer_id=self.customer_id,
                cliplet_ids=[row.id],
            )
        self.session.refresh(row)
        self.assertEqual(result["not_verified"], [row.id])
        self.assertEqual(row.status, "usable")
        self.assertEqual(row.embedding_json, [0.4, 0.5])
        self.assertEqual(row.semantic_schema_version, COARSE_SEMANTIC_SCHEMA_VERSION)
        self.assertEqual(
            row.semantic_gate_json["last_verification"]["mode"],
            "on_demand_9b",
        )

    def test_strict_commit_records_real_embedding_provenance(self) -> None:
        row = self._coarse_cliplet()
        row.embedding_json = [0.4, 0.5]
        row.embedding_backend = "hash_fallback"
        row.embedding_model = "legacy"
        self.session.commit()
        audit = {
            "passed": True,
            "attempts": 1,
            "max_attempts": 1,
            "reasons": [],
            "final_vision_model": "qwen3.5:9b",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.load_settings",
            return_value=SimpleNamespace(
                paths=SimpleNamespace(frames_root=lambda: Path(tmp))
            ),
        ), patch(
            "engine.ingest.cliplet._analyze_with_gate",
            return_value=(valid_analysis(), audit),
        ), patch(
            "engine.catalog.vector_index.embed_text",
            return_value=([0.1, 0.2, 0.3], "ollama"),
        ) as embed:
            result = verify_cliplets_on_demand(
                self.session,
                customer_id=self.customer_id,
                cliplet_ids=[row.id],
            )
        self.session.refresh(row)
        self.assertEqual(result["verified"], [row.id])
        self.assertEqual(row.semantic_schema_version, SEMANTIC_SCHEMA_VERSION)
        self.assertEqual(row.embedding_backend, "ollama")
        self.assertEqual(row.embedding_model, "nomic-embed-text")
        self.assertEqual(
            row.embedding_schema_version,
            STRICT_EMBEDDING_SCHEMA_VERSION,
        )
        self.assertTrue(semantic_gate_passed(row))
        self.assertIsNone(row.semantic_claim_token)
        self.assertEqual(embed.call_args.kwargs["model"], "nomic-embed-text")
        self.assertTrue(embed.call_args.kwargs["strict"])

    def test_embedding_failure_preserves_coarse_without_partial_strict(self) -> None:
        row = self._coarse_cliplet()
        row.embedding_json = [0.4, 0.5]
        row.embedding_backend = "hash_fallback"
        row.embedding_model = "legacy"
        self.session.commit()
        audit = {"passed": True, "attempts": 1, "reasons": []}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.load_settings",
            return_value=SimpleNamespace(
                paths=SimpleNamespace(frames_root=lambda: Path(tmp))
            ),
        ), patch(
            "engine.ingest.cliplet._analyze_with_gate",
            return_value=(valid_analysis(), audit),
        ), patch(
            "engine.catalog.vector_index.embed_text",
            side_effect=RuntimeError("ollama unavailable"),
        ):
            result = verify_cliplets_on_demand(
                self.session,
                customer_id=self.customer_id,
                cliplet_ids=[row.id],
            )
        self.session.refresh(row)
        self.assertEqual(result["not_verified"], [row.id])
        self.assertEqual(row.semantic_schema_version, COARSE_SEMANTIC_SCHEMA_VERSION)
        self.assertEqual(row.embedding_json, [0.4, 0.5])
        self.assertEqual(row.embedding_backend, "hash_fallback")
        self.assertIsNone(row.semantic_claim_token)
        self.assertFalse(semantic_gate_passed(row))

    def test_existing_strict_requires_valid_provenance_before_fast_return(self) -> None:
        row = self._coarse_cliplet()
        semantic = valid_analysis()
        row.semantic_schema_version = SEMANTIC_SCHEMA_VERSION
        row.semantic_json = semantic
        row.semantic_gate_json = {"passed": True, "attempts": 1, "reasons": []}
        row.embedding_json = [0.7, 0.8]
        row.embedding_backend = "hash_fallback"
        row.embedding_model = "nomic-embed-text"
        self.session.commit()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.load_settings",
            return_value=SimpleNamespace(
                paths=SimpleNamespace(frames_root=lambda: Path(tmp))
            ),
        ), patch(
            "engine.ingest.cliplet._analyze_with_gate"
        ) as analyze, patch(
            "engine.catalog.vector_index.embed_text",
            return_value=([0.9, 1.0], "ollama"),
        ):
            result = verify_cliplets_on_demand(
                self.session,
                customer_id=self.customer_id,
                cliplet_ids=[row.id],
            )
        analyze.assert_not_called()
        self.session.refresh(row)
        self.assertEqual(result["verified"], [row.id])
        self.assertEqual(row.embedding_json, [0.9, 1.0])
        self.assertTrue(semantic_gate_passed(row))

    def test_active_claim_prevents_duplicate_on_demand_work(self) -> None:
        row = self._coarse_cliplet()
        row.semantic_claim_token = "other-worker"
        row.semantic_claimed_at = datetime.now(timezone.utc)
        self.session.commit()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.load_settings",
            return_value=SimpleNamespace(
                paths=SimpleNamespace(frames_root=lambda: Path(tmp))
            ),
        ), patch("engine.ingest.cliplet._analyze_with_gate") as analyze:
            result = verify_cliplets_on_demand(
                self.session,
                customer_id=self.customer_id,
                cliplet_ids=[row.id],
            )
        analyze.assert_not_called()
        self.assertEqual(
            result["skipped"],
            [{"id": row.id, "reason": "already_claimed"}],
        )


if __name__ == "__main__":
    unittest.main()
