from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Base, Cliplet, Customer
from engine.catalog.vector_index import search_cliplets
from engine.ingest.semantic_gate import (
    SEMANTIC_SCHEMA_VERSION,
    STRICT_EMBEDDING_MODEL,
    STRICT_EMBEDDING_SCHEMA_VERSION,
)
from engine.qc.ready_gate import _check_semantic_source
from engine.template.engine import (
    ClipPlan,
    SlotDefinition,
    TemplateDefinition,
    _assemble_clips,
    _pick_from_cliplets,
    build_plan,
)
from tests.test_semantic_ingest import valid_analysis


def strict_clip(clip_id: int, asset_uuid: str) -> ClipPlan:
    return ClipPlan(
        slot=f"slot-{clip_id}",
        asset_uuid=asset_uuid,
        source_path=f"/tmp/{asset_uuid}.mp4",
        start_sec=0.0,
        duration_sec=3.0,
        cliplet_id=clip_id,
        score=0.8,
        description="仓库内合规实拍切片",
        scene="warehouse",
    )


class StrictSemanticPlannerTests(unittest.TestCase):
    def template(self, slots: int = 2) -> TemplateDefinition:
        return TemplateDefinition(
            name="strict-test",
            slots=[
                SlotDefinition(name=f"body{i}", min_duration=2.0, max_duration=3.0, role="body")
                for i in range(slots)
            ],
            min_assets_per_category=1,
            consistency_threshold=0.0,
        )

    def assemble(self, *, strict: bool) -> tuple[list[ClipPlan], list[str]]:
        warnings: list[str] = []
        asset = SimpleNamespace(
            uuid="asset-fallback",
            storage_path="/tmp/fallback.mp4",
            duration_sec=10.0,
            category="default",
        )
        with patch("engine.template.engine._pick_from_cliplets", return_value=None):
            clips, _ = _assemble_clips(
                random.Random(1),
                MagicMock(),
                self.template(1),
                assets=[asset],
                category="default",
                theme="default",
                keyword="仓库",
                hook="实拍",
                brand="品牌",
                semantic_query="仓库 实拍",
                cooldown_ids=set(),
                warnings=warnings,
                strict_semantic_v1=strict,
            )
        return clips, warnings

    def test_strict_mode_never_uses_whole_asset_fallback(self) -> None:
        clips, warnings = self.assemble(strict=True)
        self.assertEqual(clips, [])
        self.assertFalse(any("回退到整片抽样" in warning for warning in warnings))
        self.assertTrue(any("禁止整片回退" in warning for warning in warnings))

    def test_legacy_mode_keeps_whole_asset_compatibility(self) -> None:
        clips, warnings = self.assemble(strict=False)
        self.assertEqual(len(clips), 1)
        self.assertIsNone(clips[0].cliplet_id)
        self.assertTrue(any("回退到整片抽样" in warning for warning in warnings))

    def test_enough_strict_cliplets_assemble_normally(self) -> None:
        picks = [strict_clip(1, "asset-1"), strict_clip(2, "asset-2")]
        warnings: list[str] = []
        with patch("engine.template.engine._pick_from_cliplets", side_effect=picks):
            clips, _ = _assemble_clips(
                random.Random(1),
                MagicMock(),
                self.template(2),
                assets=[],
                category="default",
                theme="default",
                keyword="仓库",
                hook="实拍",
                brand="品牌",
                semantic_query="仓库 实拍",
                cooldown_ids=set(),
                warnings=warnings,
                strict_semantic_v1=True,
            )
        self.assertEqual([clip.cliplet_id for clip in clips], [1, 2])
        self.assertFalse(any("整片" in warning for warning in warnings))

    def test_strict_shortage_blocks_plan(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            customer = Customer(name="严格规划测试", profile_json={})
            session.add(customer)
            session.flush()
            session.add(
                Asset(
                    customer_id=customer.id,
                    uuid="asset-ready",
                    source_path="/tmp/source.mp4",
                    storage_path="/tmp/normalized.mp4",
                    category="default",
                    status="ready",
                    duration_sec=10.0,
                    metadata_json={},
                )
            )
            session.commit()
            partial = [strict_clip(1, "asset-1")]
            with patch("engine.template.engine.resolve_job_themes", return_value=("default", "default")), patch(
                "engine.template.engine.get_active_pack", return_value=None
            ), patch("engine.template.engine._assemble_clips", return_value=(partial, "仓库")), patch(
                "engine.template.engine.embed_text", return_value=([1.0, 0.0], "test")
            ):
                plan = build_plan(
                    session,
                    self.template(2),
                    customer_name=customer.name,
                    theme="default",
                    category="default",
                    seed=7,
                    strict_semantic_v1=True,
                )
        engine.dispose()
        self.assertTrue(plan.blocked)
        self.assertTrue(any("严格 semantic v1 素材不足" in reason for reason in plan.block_reasons))

    def test_strict_search_filters_legacy_vectors(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            customer = Customer(name="严格检索测试", profile_json={})
            session.add(customer)
            session.flush()
            asset = Asset(
                customer_id=customer.id,
                uuid="asset-search",
                source_path="/tmp/source.mp4",
                storage_path="/tmp/normalized.mp4",
                category="default",
                status="ready",
                duration_sec=10.0,
                metadata_json={},
            )
            session.add(asset)
            session.flush()
            data = valid_analysis()
            passed = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=0,
                end_sec=3,
                duration_sec=3,
                description=data["description"],
                score=0.8,
                status="usable",
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                semantic_json=data,
                semantic_gate_json={"passed": True, "attempts": 1},
                embedding_json=[1.0, 0.0],
                embedding_backend="ollama",
                embedding_model=STRICT_EMBEDDING_MODEL,
                embedding_schema_version=STRICT_EMBEDDING_SCHEMA_VERSION,
            )
            legacy = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=3,
                end_sec=6,
                duration_sec=3,
                description="旧向量",
                score=0.8,
                status="usable",
                embedding_json=[1.0, 0.0],
            )
            session.add_all([passed, legacy])
            session.commit()
            with patch(
                "engine.catalog.vector_index.embed_text",
                return_value=([1.0, 0.0], "ollama"),
            ):
                rows = search_cliplets(
                    session,
                    "仓库",
                    customer_id=customer.id,
                    strict_semantic_v1=True,
                )
        engine.dispose()
        self.assertEqual([row.id for row, _ in rows], [passed.id])

    def test_ready_gate_requires_complete_strict_source_audit(self) -> None:
        sidecar = {
            "clips": [{"cliplet_id": 1}],
            "meta": {"strict_semantic_v1": True, "semantic_source_audit": []},
        }
        self.assertTrue(_check_semantic_source(sidecar))

        sidecar["meta"]["semantic_source_audit"] = [
            {
                "cliplet_id": 1,
                "status": "usable",
                "quality_score": 0.8,
                "has_embedding": True,
                "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                "semantic_v1_passed": True,
            }
        ]
        self.assertEqual(_check_semantic_source(sidecar), [])


class StyleHardPreferTests(unittest.TestCase):
    def test_hard_prefer_sql_fill_ignores_unscoped_semantic_hits(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            customer = Customer(name="风格硬切测试", profile_json={})
            session.add(customer)
            session.flush()
            warehouse = Asset(
                customer_id=customer.id,
                uuid="asset-warehouse",
                source_path="/tmp/wh.mp4",
                storage_path="/tmp/wh.mp4",
                category="default",
                status="ready",
                duration_sec=10.0,
                metadata_json={},
            )
            loading = Asset(
                customer_id=customer.id,
                uuid="asset-loading",
                source_path="/tmp/ld.mp4",
                storage_path="/tmp/ld.mp4",
                category="default",
                status="ready",
                duration_sec=10.0,
                metadata_json={},
            )
            session.add_all([warehouse, loading])
            session.flush()
            wh_clip = Cliplet(
                asset_id=warehouse.id,
                asset_uuid=warehouse.uuid,
                start_sec=0,
                end_sec=8,
                duration_sec=8,
                description="室内货架",
                scene="warehouse",
                score=0.9,
                status="usable",
            )
            ld_clip = Cliplet(
                asset_id=loading.id,
                asset_uuid=loading.uuid,
                start_sec=0,
                end_sec=8,
                duration_sec=8,
                description="卡车装车",
                scene="loading",
                score=0.9,
                status="usable",
            )
            session.add_all([wh_clip, ld_clip])
            session.commit()
            template = TemplateDefinition(
                name="style-lock",
                slots=[SlotDefinition(name="hook", min_duration=2.0, max_duration=4.0, role="hook")],
                min_assets_per_category=1,
                use_semantic=True,
                min_semantic_score=0.08,
                min_cliplet_quality=0.35,
            )
            with patch(
                "engine.template.engine.search_cliplets",
                return_value=[(wh_clip, 0.99)],
            ), patch(
                "engine.ingest.quality.is_usable_quality",
                return_value=True,
            ):
                picked = _pick_from_cliplets(
                    random.Random(1),
                    session,
                    template.slots[0],
                    "装车配送",
                    "default",
                    set(),
                    set(),
                    set(),
                    template,
                    customer_id=customer.id,
                    prefer_scenes=["loading", "transport"],
                    hard_prefer_scenes=True,
                )
        engine.dispose()
        self.assertIsNotNone(picked)
        self.assertEqual(picked.scene, "loading")
        self.assertEqual(picked.asset_uuid, "asset-loading")


if __name__ == "__main__":
    unittest.main()
