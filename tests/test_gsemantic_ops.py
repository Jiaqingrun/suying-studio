from __future__ import annotations

import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Asset,
    Base,
    Cliplet,
    Customer,
    OfficialObjectEvidence,
    SemanticClusterMember,
    SemanticHealthCandidate,
)
from engine.catalog.semantic_ops import (
    run_health_check,
    suggest_clusters,
    validate_official_url,
    verify_official_catalog,
)
from engine.content.content_fingerprint import build_content_fingerprint
from engine.ingest.semantic_gate import (
    COARSE_SEMANTIC_SCHEMA_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    STRICT_EMBEDDING_SCHEMA_VERSION,
)
from tests.test_semantic_ingest import valid_analysis


class GSemanticOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.a = Customer(
            name="客户A",
            profile_json={"semantic_ops": {"official_catalog_domains": ["example.com"]}},
        )
        self.b = Customer(name="客户B", profile_json={})
        self.session.add_all([self.a, self.b])
        self.session.flush()
        self.asset_a = self._asset(self.a.id, "asset-a")
        self.asset_b = self._asset(self.b.id, "asset-b")
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _asset(self, customer_id: int, uuid: str) -> Asset:
        row = Asset(
            customer_id=customer_id,
            uuid=uuid,
            source_path=f"/tmp/{uuid}.mp4",
            storage_path=f"/tmp/{uuid}.mp4",
            status="ready",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _cliplet(
        self,
        asset: Asset,
        *,
        strict: bool,
        description: str = "仓库货架旁可见两名人物搬动蓝色箱体，背景中有托盘与多排金属货架。",
        embedding: list[float] | None = None,
    ) -> Cliplet:
        data = valid_analysis() if strict else None
        row = Cliplet(
            asset_id=asset.id,
            asset_uuid=asset.uuid,
            start_sec=0,
            end_sec=4,
            duration_sec=4,
            description=description,
            status="usable",
            theme="warehouse",
            scene="warehouse",
            objects_json=["box"],
            semantic_schema_version=(
                SEMANTIC_SCHEMA_VERSION if strict else COARSE_SEMANTIC_SCHEMA_VERSION
            ),
            semantic_json=data,
            semantic_gate_json=(
                {"passed": True, "reasons": []}
                if strict
                else {"passed": True, "mode": "coarse"}
            ),
            embedding_json=embedding or [1.0, 0.0],
            embedding_backend="ollama" if strict else "hash_fallback",
            embedding_model="nomic-embed-text" if strict else "hash",
            embedding_schema_version=(
                STRICT_EMBEDDING_SCHEMA_VERSION if strict else "coarse.v1"
            ),
            indexed_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.commit()
        return row

    def test_fingerprint_rejects_coarse_and_aggregates_frame_unknown_time(self) -> None:
        strict = self._cliplet(self.asset_a, strict=True)
        coarse = self._cliplet(self.asset_a, strict=False)
        fingerprint = build_content_fingerprint([strict, coarse], None)
        self.assertFalse(fingerprint["strict_semantic_only"])
        self.assertEqual(fingerprint["cliplet_ids"], [strict.id])
        self.assertEqual(fingerprint["excluded_non_strict_cliplet_ids"], [coarse.id])
        self.assertEqual(fingerprint["evidence_frames"][0]["timestamp_sec"], 1.2)
        self.assertIn("箱体文字无法辨认", fingerprint["unknowns"])
        coarse_only = build_content_fingerprint([coarse], None)
        self.assertEqual(coarse_only["evidence_level"], "none")
        self.assertEqual(coarse_only["visible_facts"], [])

    def test_health_is_customer_scoped_and_idempotent(self) -> None:
        own = self._cliplet(self.asset_a, strict=False, description="")
        foreign = self._cliplet(self.asset_b, strict=False, description="")
        first = run_health_check(
            self.session,
            customer_id=self.a.id,
            idempotency_key="same-run",
        )
        second = run_health_check(
            self.session,
            customer_id=self.a.id,
            idempotency_key="same-run",
        )
        self.assertEqual(first.id, second.id)
        candidates = list(
            self.session.scalars(
                select(SemanticHealthCandidate).where(
                    SemanticHealthCandidate.customer_id == self.a.id
                )
            ).all()
        )
        self.assertTrue(any(row.cliplet_id == own.id for row in candidates))
        self.assertFalse(any(row.cliplet_id == foreign.id for row in candidates))

    def test_cluster_suggestions_are_stable_and_tag_filtered(self) -> None:
        one = self._cliplet(self.asset_a, strict=False, embedding=[1.0, 0.0])
        two = self._cliplet(self.asset_a, strict=False, embedding=[0.99, 0.01])
        other = self._cliplet(self.asset_a, strict=False, embedding=[1.0, 0.0])
        other.scene = "storefront"
        self.session.commit()
        first = suggest_clusters(self.session, customer_id=self.a.id)
        second = suggest_clusters(self.session, customer_id=self.a.id)
        self.assertEqual([row.cluster_key for row in first], [row.cluster_key for row in second])
        member_sets = [
            set(
                self.session.scalars(
                    select(SemanticClusterMember.cliplet_id).where(
                        SemanticClusterMember.cluster_id == row.id
                    )
                ).all()
            )
            for row in first
        ]
        self.assertIn({one.id, two.id}, member_sets)
        self.assertIn({other.id}, member_sets)

    def test_official_catalog_ssrf_offline_and_conflict_fail_closed(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                validate_official_url("https://example.com/catalog", {"example.com"})

        class OfflineOpener:
            def open(self, *_args, **_kwargs):
                raise urllib.error.URLError("offline")

        with patch(
            "engine.catalog.semantic_ops.validate_official_url",
            return_value="https://example.com/catalog",
        ), patch("urllib.request.build_opener", return_value=OfflineOpener()):
            offline = verify_official_catalog(
                self.session,
                customer_id=self.a.id,
                profile=self.a.profile_json,
                url="https://example.com/catalog",
                candidate_name="物品甲",
            )
        self.assertEqual(offline.status, "unknown")
        self.assertFalse(offline.visual_fact_supported)

        class Response:
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return "<html>官方目录：物品乙</html>".encode()

        class OnlineOpener:
            def open(self, *_args, **_kwargs):
                return Response()

        with patch(
            "engine.catalog.semantic_ops.validate_official_url",
            return_value="https://example.com/conflict",
        ), patch("urllib.request.build_opener", return_value=OnlineOpener()):
            conflict = verify_official_catalog(
                self.session,
                customer_id=self.a.id,
                profile=self.a.profile_json,
                url="https://example.com/conflict",
                candidate_name="物品甲",
                visual_facts=["画面可见物品乙"],
            )
        self.assertEqual(conflict.status, "unknown")
        self.assertTrue(conflict.conflict_json)
        self.assertFalse(conflict.visual_fact_supported)
        self.assertEqual(
            self.session.scalar(
                select(OfficialObjectEvidence).where(
                    OfficialObjectEvidence.customer_id == self.a.id,
                    OfficialObjectEvidence.url_hash == conflict.url_hash,
                )
            ).source_type,
            "official_catalog",
        )


if __name__ == "__main__":
    unittest.main()
