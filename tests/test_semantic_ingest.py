from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Base, Cliplet, Customer, _add_column_if_missing
from engine.catalog.semantic_tags import compose_embed_text
from engine.catalog.vector_index import index_cliplet
from engine.ingest.cliplet import (
    _analyze_with_gate,
    _claim_next_semantic_cliplet,
    recaption_existing_cliplets,
    semantic_backfill_progress,
)
from engine.ingest.semantic_gate import (
    MAX_SEMANTIC_ATTEMPTS,
    SEMANTIC_SCHEMA_VERSION,
    evaluate_semantic_gate,
    semantic_gate_passed,
    semantic_response_json_schema,
)
from engine.ingest.vision_caption import (
    VISION_SEMANTIC_TIMEOUT,
    _extract_frame_near,
    _normalize_semantic_response,
    _parse_json_response,
    analyze_cliplet_semantics,
    vision_semantic_analysis,
)


def valid_analysis() -> dict:
    return {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "source_backend": "vision",
        "description": "仓库内两名穿深色工装的员工将蓝色箱装产品码放到托盘上，后方可见多排货架。",
        "frames": [
            {
                "frame_id": "f1",
                "timestamp_sec": 1.2,
                "visible_facts": ["两名员工站在托盘旁", "地面有蓝色箱装产品"],
                "unknowns": ["箱体文字无法辨认"],
            },
            {
                "frame_id": "f2",
                "timestamp_sec": 2.5,
                "visible_facts": ["员工双手搬动箱体", "背景有多排金属货架"],
                "unknowns": [],
            },
            {
                "frame_id": "f3",
                "timestamp_sec": 3.8,
                "visible_facts": ["蓝色箱体已叠放在托盘上", "两名员工仍在画面内"],
                "unknowns": [],
            },
        ],
        "scenes": [
            {"label": "warehouse", "confidence": 0.94, "evidence_frame_ids": ["f1", "f2"]},
            {"label": "stacking", "confidence": 0.9, "evidence_frame_ids": ["f2", "f3"]},
        ],
        "products": [
            {
                "name": "蓝色箱装产品",
                "category": "箱装货物",
                "use": "未知",
                "key_attributes": ["蓝色外包装", "长方体箱体"],
                "confidence": 0.82,
                "evidence_frame_ids": ["f1", "f3"],
            }
        ],
        "interactions": [
            {"label": "stacking", "confidence": 0.93, "evidence_frame_ids": ["f2", "f3"]},
            {"label": "teamwork", "confidence": 0.81, "evidence_frame_ids": ["f1", "f2"]},
        ],
        "people": {
            "present": True,
            "count": 2,
            "appearance": "两人站立协作，精神状态无法仅凭静帧判断",
            "clothing": "深色长袖工装",
            "promotion_labels": [
                {"label": "employee_group", "confidence": 0.83, "evidence_frame_ids": ["f1", "f2"]}
            ],
        },
        "consistency": {"consistent": True, "issues": []},
        "overall_confidence": 0.87,
    }


class SemanticGateTests(unittest.TestCase):
    def test_json_schema_matches_gate_contract(self) -> None:
        schema = semantic_response_json_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            SEMANTIC_SCHEMA_VERSION,
        )
        self.assertIn("frames", schema["required"])
        self.assertIn("people", schema["required"])
        people_labels = schema["properties"]["people"]["properties"]["promotion_labels"][
            "items"
        ]["properties"]["label"]["enum"]
        self.assertIn("person_visible_unclassified", people_labels)
        self.assertIn("none_visible", people_labels)

    def test_valid_multilabel_analysis_passes(self) -> None:
        result = evaluate_semantic_gate(valid_analysis())
        self.assertTrue(result["passed"], result)

    def test_low_confidence_fails(self) -> None:
        data = valid_analysis()
        data["overall_confidence"] = 0.4
        data["products"][0]["confidence"] = 0.3
        result = evaluate_semantic_gate(data)
        self.assertFalse(result["passed"])
        self.assertIn("overall_low_confidence", result["reasons"])
        self.assertIn("products[0]_low_confidence", result["reasons"])

    def test_out_of_range_product_confidence_fails(self) -> None:
        data = valid_analysis()
        data["products"][0]["confidence"] = 1.2
        self.assertIn(
            "products[0]_invalid_confidence",
            evaluate_semantic_gate(data)["reasons"],
        )

    def test_vague_and_speculative_description_fails(self) -> None:
        data = valid_analysis()
        data["description"] = "可能是一个相关场景"
        reasons = evaluate_semantic_gate(data)["reasons"]
        self.assertIn("description_length", reasons)
        self.assertIn("description_speculative", reasons)

    def test_frame_contradiction_fails(self) -> None:
        data = valid_analysis()
        data["consistency"] = {"consistent": False, "issues": ["f1与f3产品颜色冲突"]}
        self.assertIn("frame_inconsistent", evaluate_semantic_gate(data)["reasons"])

    def test_unknown_content_must_not_be_visible_fact(self) -> None:
        data = valid_analysis()
        data["frames"][0]["visible_facts"][0] = "箱体品牌无法辨认"
        data["frames"][0]["unknowns"] = ["箱体品牌无法辨认"]
        self.assertIn(
            "frames[0]_fact_invalid",
            evaluate_semantic_gate(data)["reasons"],
        )

    def test_identity_emotion_and_use_speculation_fail(self) -> None:
        for phrase in ("人物身份是客户", "人物情绪热情", "箱体用于运输"):
            data = valid_analysis()
            data["description"] = f"仓库货架旁可见两个人物和蓝色箱体，{phrase}，画面中还有一张木托盘。"
            self.assertIn(
                "description_speculative",
                evaluate_semantic_gate(data)["reasons"],
                phrase,
            )

    def test_normalizer_only_keeps_supplied_frame_ids(self) -> None:
        data = valid_analysis()
        data["frames"].append(
            {
                "frame_id": "f9",
                "timestamp_sec": 99,
                "visible_facts": ["虚构帧事实一", "虚构帧事实二"],
                "unknowns": [],
            }
        )
        data["scenes"][0]["evidence_frame_ids"] = ["f1", "f9", "f1"]
        data["products"][0]["key_attributes"] = "蓝色外包装"
        frame_paths = [
            ("f1", 1.1, Path("/tmp/f1.jpg")),
            ("f2", 2.2, Path("/tmp/f2.jpg")),
            ("f3", 3.3, Path("/tmp/f3.jpg")),
        ]
        normalized = _normalize_semantic_response(data, frame_paths)
        self.assertIsNotNone(normalized)
        self.assertEqual([row["frame_id"] for row in normalized["frames"]], ["f1", "f2", "f3"])
        self.assertEqual(normalized["frames"][0]["timestamp_sec"], 1.1)
        self.assertEqual(normalized["scenes"][0]["evidence_frame_ids"], ["f1"])
        self.assertEqual(normalized["products"][0]["key_attributes"], ["蓝色外包装"])

    def test_normalizer_does_not_invent_missing_product_attributes(self) -> None:
        data = valid_analysis()
        data["products"][0].pop("key_attributes")
        normalized = _normalize_semantic_response(
            data,
            [
                ("f1", 1.2, Path("/tmp/f1.jpg")),
                ("f2", 2.5, Path("/tmp/f2.jpg")),
                ("f3", 3.8, Path("/tmp/f3.jpg")),
            ],
        )
        self.assertNotIn("key_attributes", normalized["products"][0])
        self.assertIn(
            "products[0]_key_attributes_missing",
            evaluate_semantic_gate(normalized)["reasons"],
        )

    def test_people_conflict_is_not_silently_rewritten(self) -> None:
        data = valid_analysis()
        data["people"]["promotion_labels"].append(
            {"label": "none_visible", "confidence": 0.9, "evidence_frame_ids": ["f1"]}
        )
        normalized = _normalize_semantic_response(
            data,
            [
                ("f1", 1.2, Path("/tmp/f1.jpg")),
                ("f2", 2.5, Path("/tmp/f2.jpg")),
                ("f3", 3.8, Path("/tmp/f3.jpg")),
            ],
        )
        self.assertIn(
            "people_presence_contradiction",
            evaluate_semantic_gate(normalized)["reasons"],
        )

    def test_visible_person_can_remain_identity_unclassified(self) -> None:
        data = valid_analysis()
        data["people"]["appearance"] = "三个人物站在货架旁"
        data["people"]["promotion_labels"] = [
            {
                "label": "person_visible_unclassified",
                "confidence": 0.83,
                "evidence_frame_ids": ["f1", "f2"],
            }
        ]
        result = evaluate_semantic_gate(data)
        self.assertTrue(result["passed"], result)

    def test_frame_extraction_uses_bounded_nearby_fallback(self) -> None:
        with patch(
            "engine.ingest.vision_caption.extract_frame",
            side_effect=[False, True],
        ) as mocked:
            ok, actual = _extract_frame_near(
                Path("/tmp/video.mp4"),
                2.0,
                Path("/tmp/frame.jpg"),
                start=0.0,
                end=4.0,
            )
        self.assertTrue(ok)
        self.assertEqual(actual, 1.92)
        self.assertEqual(mocked.call_count, 2)

    def test_parse_json_handles_fence_think_array_and_truncation(self) -> None:
        valid = json.dumps(valid_analysis(), ensure_ascii=False)
        fenced = f"```json\n{valid}\n```"
        thinky = f"<think>{{not: json}}</think>\n{valid}\n"
        prose = f"这是说明\n{valid}\n完毕"
        self.assertIsNotNone(_parse_json_response(fenced))
        self.assertIsNotNone(_parse_json_response(thinky))
        self.assertIsNotNone(_parse_json_response(prose))
        self.assertIsNone(_parse_json_response(""))
        self.assertIsNone(_parse_json_response("not json at all"))
        self.assertIsNone(_parse_json_response('[{"a":1}]'))
        self.assertIsNone(_parse_json_response('{"schema_version":"x","frames":['))
        # Trailing fence noise after a complete object must still parse.
        noisy = f"```\n{valid}\n```\nextra"
        self.assertIsNotNone(_parse_json_response(noisy))

    def test_vision_client_bypasses_proxy_and_uses_long_timeout(self) -> None:
        captured: dict = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"message": {"content": __import__("json").dumps(valid_analysis())}}

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, _url, *, json):
                captured["payload"] = json
                return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, timestamp in enumerate((1.2, 2.5, 3.8), 1):
                path = Path(tmp) / f"f{index}.jpg"
                path.write_bytes(b"image")
                paths.append((f"f{index}", timestamp, path))
            with patch("engine.ingest.vision_caption.httpx.Client", FakeClient):
                result = vision_semantic_analysis(paths, model="test-model")
        self.assertIsNotNone(result)
        self.assertIs(captured.get("trust_env"), False)
        self.assertEqual(captured.get("timeout"), VISION_SEMANTIC_TIMEOUT)
        self.assertGreaterEqual(VISION_SEMANTIC_TIMEOUT, 300.0)
        self.assertEqual(captured["payload"].get("options"), {"temperature": 0})

    def test_vision_timeout_is_audited_not_schema_not_object(self) -> None:
        asset = SimpleNamespace(uuid="asset-timeout", id=1)

        def boom(*_args, **_kwargs):
            raise httpx.TimeoutException("timed out")

        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.vision_caption.vision_semantic_analysis", side_effect=boom
        ), patch(
            "engine.ingest.vision_caption.analysis_video_path",
            return_value=Path(tmp) / "missing.mp4",
        ):
            # Force frame path: create fake video existence + frame extraction
            video = Path(tmp) / "missing.mp4"
            video.write_bytes(b"x")
            with patch(
                "engine.ingest.vision_caption.analysis_video_path", return_value=video
            ), patch(
                "engine.ingest.vision_caption._extract_frame_near",
                return_value=(True, 1.0),
            ):
                data, audit = analyze_cliplet_semantics(
                    asset, 0.0, 4.0, cache_dir=Path(tmp), attempt=1, use_vision=True
                )
        self.assertIsNone(data)
        self.assertEqual(audit.get("error"), "vision_timeout")

        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.analyze_cliplet_semantics",
            return_value=(None, {"attempt": 1, "error": "vision_timeout"}),
        ):
            semantic, gate_audit = _analyze_with_gate(
                asset, 0.0, 4.0, cache_dir=Path(tmp), use_vision=True
            )
        self.assertIsNone(semantic)
        self.assertIn("vision_timeout", gate_audit.get("reasons") or [])
        self.assertNotIn("schema_not_object", gate_audit.get("reasons") or [])

    def test_vision_prompt_locks_actual_ids_and_fact_boundaries(self) -> None:
        captured: dict = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"message": {"content": __import__("json").dumps(valid_analysis())}}

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, _url, *, json):
                captured.update(json)
                return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, timestamp in enumerate((1.2, 2.5, 3.8), 1):
                path = Path(tmp) / f"f{index}.jpg"
                path.write_bytes(b"image")
                paths.append((f"f{index}", timestamp, path))
            with patch("engine.ingest.vision_caption.httpx.Client", FakeClient):
                result = vision_semantic_analysis(paths, model="test-model")
        self.assertIsNotNone(result)
        prompt = captured["messages"][0]["content"]
        for expected in (
            '["f1", "f2", "f3"]',
            "只能逐字引用",
            "不得漏帧",
            "visible_facts",
            "unknowns",
            "promotion_labels 禁止 none_visible",
            "person_visible_unclassified",
            '["unknown"]',
            "不得为过门禁虚报",
        ):
            self.assertIn(expected, prompt)
        fmt = captured.get("format")
        self.assertIsInstance(fmt, dict)
        self.assertEqual(fmt.get("type"), "object")
        self.assertEqual(
            fmt.get("properties", {}).get("schema_version", {}).get("const"),
            SEMANTIC_SCHEMA_VERSION,
        )
        self.assertNotEqual(fmt, "json")
        self.assertEqual(captured.get("options"), {"temperature": 0})

    def test_retry_exhaustion_is_bounded_and_audited(self) -> None:
        calls: list[int] = []

        def fail(*args, **kwargs):
            calls.append(kwargs["attempt"])
            return None, {"attempt": kwargs["attempt"], "error": "vision_response_invalid"}

        asset = SimpleNamespace(uuid="asset-1")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.analyze_cliplet_semantics", side_effect=fail
        ):
            semantic, audit = _analyze_with_gate(
                asset, 0.0, 4.0, cache_dir=Path(tmp), use_vision=True
            )
        self.assertIsNone(semantic)
        self.assertEqual(calls, list(range(1, MAX_SEMANTIC_ATTEMPTS + 1)))
        self.assertEqual(audit["attempts"], MAX_SEMANTIC_ATTEMPTS)
        self.assertEqual(audit["disposition"], "quarantined_no_embedding")

    def test_analysis_exceptions_use_bounded_retry_budget(self) -> None:
        calls: list[int] = []

        def fail(*args, **kwargs):
            calls.append(kwargs["attempt"])
            raise RuntimeError("model unavailable")

        asset = SimpleNamespace(uuid="asset-1")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "engine.ingest.cliplet.analyze_cliplet_semantics", side_effect=fail
        ):
            semantic, audit = _analyze_with_gate(
                asset, 0.0, 4.0, cache_dir=Path(tmp), use_vision=True
            )
        self.assertIsNone(semantic)
        self.assertEqual(calls, list(range(1, MAX_SEMANTIC_ATTEMPTS + 1)))
        self.assertEqual(audit["attempts"], MAX_SEMANTIC_ATTEMPTS)
        self.assertEqual(
            audit["history"][0]["extraction"]["error"],
            "semantic_analysis_exception",
        )

    def test_embedding_text_contains_all_structured_classes(self) -> None:
        cliplet = SimpleNamespace(
            semantic_json=valid_analysis(),
            theme="operations",
            scene="warehouse",
            objects_json=["蓝色箱装产品"],
            actions_json=["stacking", "teamwork"],
            description=valid_analysis()["description"],
        )
        text_value = compose_embed_text(cliplet)
        for expected in ("场景分类=", "产品=", "互动=", "人物宣传=", "逐帧可见事实="):
            self.assertIn(expected, text_value)

    def test_vectorization_requires_persisted_semantic_pass(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            asset = Asset(
                uuid="00000000-0000-0000-0000-000000000001",
                source_path="/tmp/source.mp4",
                storage_path="/tmp/normalized.mp4",
                status="ready",
                metadata_json={},
            )
            session.add(asset)
            session.flush()
            rejected = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=0,
                end_sec=3,
                duration_sec=3,
                description="旧描述没有结构化语义",
                score=0.8,
                status="usable",
            )
            session.add(rejected)
            session.commit()
            index_cliplet(session, rejected)
            self.assertEqual(rejected.status, "rejected_semantic")
            self.assertIsNone(rejected.embedding_json)
            self.assertEqual(
                session.scalar(
                    text("SELECT embedding_json IS NULL FROM cliplets WHERE id=:id"),
                    {"id": rejected.id},
                ),
                1,
            )

            data = valid_analysis()
            accepted = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=3,
                end_sec=6,
                duration_sec=3,
                description=data["description"],
                score=0.8,
                status="usable",
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                semantic_json=data,
                semantic_gate_json={"passed": True, "attempts": 1},
                semantic_attempts=1,
            )
            session.add(accepted)
            session.commit()
            with patch("engine.catalog.vector_index.embed_text", return_value=([0.6, 0.8], "test")):
                index_cliplet(session, accepted)
            self.assertEqual(accepted.status, "usable")
            self.assertEqual(accepted.embedding_json, [0.6, 0.8])
        engine.dispose()

    def test_legacy_row_stays_readable_after_nullable_columns(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE cliplets (id INTEGER PRIMARY KEY, description TEXT)"))
            conn.execute(text("INSERT INTO cliplets(id, description) VALUES (1, '旧描述')"))
            _add_column_if_missing(conn, "cliplets", "semantic_schema_version", "VARCHAR(64)")
            _add_column_if_missing(conn, "cliplets", "semantic_json", "JSON")
            _add_column_if_missing(conn, "cliplets", "semantic_gate_json", "JSON")
            _add_column_if_missing(conn, "cliplets", "semantic_attempts", "INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "cliplets", "semantic_claim_token", "VARCHAR(64)")
            _add_column_if_missing(conn, "cliplets", "semantic_claimed_at", "DATETIME")
            row = conn.execute(
                text(
                    "SELECT description, semantic_json, semantic_gate_json, semantic_attempts, "
                    "semantic_claim_token, semantic_claimed_at "
                    "FROM cliplets WHERE id=1"
                )
            ).one()
        self.assertEqual(row.description, "旧描述")
        self.assertIsNone(row.semantic_json)
        self.assertIsNone(row.semantic_gate_json)
        self.assertEqual(row.semantic_attempts, 0)
        self.assertIsNone(row.semantic_claim_token)
        self.assertIsNone(row.semantic_claimed_at)
        self.assertFalse(
            semantic_gate_passed(
                SimpleNamespace(
                    semantic_schema_version=None, semantic_json=None, semantic_gate_json=None
                )
            )
        )
        engine.dispose()

    def test_backfill_pages_skip_terminal_rows_and_is_idempotent(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        data = valid_analysis()
        with Session(engine) as session:
            customer = Customer(name="语义测试客户", profile_json={})
            session.add(customer)
            session.flush()
            asset = Asset(
                customer_id=customer.id,
                uuid="00000000-0000-0000-0000-000000000010",
                source_path="/tmp/source.mp4",
                storage_path="/tmp/normalized.mp4",
                status="ready",
                metadata_json={},
            )
            session.add(asset)
            session.flush()

            def row(start: int, **kwargs) -> Cliplet:
                return Cliplet(
                    asset_id=asset.id,
                    asset_uuid=asset.uuid,
                    start_sec=start,
                    end_sec=start + 3,
                    duration_sec=3,
                    description=kwargs.pop("description", "旧语义描述"),
                    score=0.8,
                    status=kwargs.pop("status", "usable"),
                    **kwargs,
                )

            old_first = row(0)
            already_passed = row(
                3,
                description=data["description"],
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                semantic_json=data,
                semantic_gate_json={"passed": True, "attempts": 1},
                semantic_attempts=1,
                embedding_json=[0.1, 0.2],
            )
            final_rejected = row(
                6,
                status="rejected_semantic",
                semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
                semantic_gate_json={"passed": False, "attempts": 3},
                semantic_attempts=3,
            )
            old_second = row(9, semantic_schema_version="legacy.v0")
            session.add_all([old_first, already_passed, final_rejected, old_second])
            session.commit()

            audit = {"passed": True, "attempts": 1, "history": [], "reasons": []}
            with patch(
                "engine.ingest.cliplet._analyze_with_gate",
                return_value=(data, audit),
            ), patch(
                "engine.catalog.vector_index.embed_text",
                return_value=([0.6, 0.8], "test"),
            ):
                page1 = recaption_existing_cliplets(
                    session, customer_id=customer.id, limit=1
                )
                page2 = recaption_existing_cliplets(
                    session, customer_id=customer.id, limit=10
                )
                page3 = recaption_existing_cliplets(
                    session, customer_id=customer.id, limit=10
                )

            self.assertEqual(page1["claimed_ids"], [old_first.id])
            self.assertEqual(page2["claimed_ids"], [old_second.id])
            self.assertEqual(page3["queued"], 0)
            self.assertEqual(page3["remaining"], 0)
            self.assertEqual(already_passed.embedding_json, [0.1, 0.2])
            self.assertEqual(final_rejected.semantic_attempts, 3)
            progress = semantic_backfill_progress(session, customer_id=customer.id)
            self.assertEqual(progress["passed"], 3)
            self.assertEqual(progress["rejected"], 1)
            self.assertEqual(progress["processed"], 4)
            self.assertEqual(progress["last_cursor"], old_second.id)
        engine.dispose()

    def test_atomic_claims_do_not_duplicate_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'claims.db'}")
            Base.metadata.create_all(engine)
            with Session(engine) as setup:
                customer = Customer(name="并发测试客户", profile_json={})
                setup.add(customer)
                setup.flush()
                asset = Asset(
                    customer_id=customer.id,
                    uuid="00000000-0000-0000-0000-000000000020",
                    source_path="/tmp/source.mp4",
                    storage_path="/tmp/normalized.mp4",
                    status="ready",
                    metadata_json={},
                )
                setup.add(asset)
                setup.flush()
                for start in (0, 3):
                    setup.add(
                        Cliplet(
                            asset_id=asset.id,
                            asset_uuid=asset.uuid,
                            start_sec=start,
                            end_sec=start + 3,
                            duration_sec=3,
                            description="旧语义描述",
                            score=0.8,
                            status="usable",
                        )
                    )
                setup.commit()
                customer_id = customer.id

            with Session(engine) as first, Session(engine) as second:
                claim1 = _claim_next_semantic_cliplet(first, customer_id=customer_id)
                claim2 = _claim_next_semantic_cliplet(second, customer_id=customer_id)
                self.assertIsNotNone(claim1)
                self.assertIsNotNone(claim2)
                self.assertNotEqual(claim1[0].id, claim2[0].id)
            engine.dispose()

    def test_backfill_rejection_physically_clears_legacy_vector(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            customer = Customer(name="拒绝隔离测试", profile_json={})
            session.add(customer)
            session.flush()
            asset = Asset(
                customer_id=customer.id,
                uuid="00000000-0000-0000-0000-000000000030",
                source_path="/tmp/source.mp4",
                storage_path="/tmp/normalized.mp4",
                status="ready",
                metadata_json={},
            )
            session.add(asset)
            session.flush()
            cliplet = Cliplet(
                asset_id=asset.id,
                asset_uuid=asset.uuid,
                start_sec=0,
                end_sec=3,
                duration_sec=3,
                description="旧语义描述",
                score=0.8,
                status="usable",
                embedding_json=[0.2, 0.3],
            )
            session.add(cliplet)
            session.commit()
            audit = {
                "passed": False,
                "attempts": MAX_SEMANTIC_ATTEMPTS,
                "history": [],
                "reasons": ["overall_low_confidence"],
            }
            with patch(
                "engine.ingest.cliplet._analyze_with_gate",
                return_value=(None, audit),
            ):
                result = recaption_existing_cliplets(
                    session, customer_id=customer.id, limit=1
                )
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(cliplet.status, "rejected_semantic")
            self.assertEqual(
                session.scalar(
                    text("SELECT embedding_json IS NULL FROM cliplets WHERE id=:id"),
                    {"id": cliplet.id},
                ),
                1,
            )
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
