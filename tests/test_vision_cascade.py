"""Unit tests for host-tier vision defaults and cascade attempt budget."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine.catalog.host_profile import (
    VISION_ESCALATE,
    VISION_FAST,
    HostProfile,
    clamp_primary_for_tier,
    recommend_models,
    recommend_tier,
    resolve_vision_policy,
)
from engine.ingest.cliplet import _analyze_with_gate
from engine.ingest.semantic_gate import (
    MAX_SEMANTIC_ATTEMPTS,
    MIN_LABEL_CONFIDENCE,
    MIN_OVERALL_CONFIDENCE,
)
from engine.ingest.vision_caption import should_escalate_vision
from tests.test_semantic_ingest import valid_analysis


def _fake_host(ram_gb: float, tier: str | None = None) -> HostProfile:
    t = tier or recommend_tier(ram_gb)
    return HostProfile(
        ram_gb=ram_gb,
        arch="arm64",
        chip="test",
        platform="Darwin",
        tier=t,
        python_path="/usr/bin/python3",
        python_version="3.12.0",
        python_ok=True,
        ffmpeg_ok=True,
        ffmpeg_path="/usr/bin/ffmpeg",
        ollama_cli=True,
        ollama_path="/usr/local/bin/ollama",
    )


class TierSelectionTests(unittest.TestCase):
    def test_recommend_tier_bands(self) -> None:
        self.assertEqual(recommend_tier(8), "lite")
        self.assertEqual(recommend_tier(16), "standard")
        self.assertEqual(recommend_tier(32), "pro")
        self.assertEqual(recommend_tier(64), "pro")
        self.assertEqual(recommend_tier(95), "pro")
        self.assertEqual(recommend_tier(96), "max")
        self.assertEqual(recommend_tier(128), "max")

    def test_16gb_defaults_to_9b_not_27b(self) -> None:
        for ram in (8.0, 16.0):
            host = _fake_host(ram)
            rec = recommend_models(host)
            self.assertEqual(rec["vision_model"], VISION_FAST)
            self.assertFalse(rec["cascade"])
            self.assertIsNone(rec["escalate_model"])
            self.assertFalse(rec["allow_27b_default"])
            self.assertEqual(rec["embed_model"], "nomic-embed-text")
            policy = resolve_vision_policy(
                host=host,
                settings=SimpleNamespace(
                    ollama_embed_model="nomic-embed-text",
                    ollama_vision_model=VISION_ESCALATE,  # hostile config
                    ollama_vision_escalate_model="",
                    ollama_vision_cascade=True,
                ),
            )
            self.assertEqual(policy.primary_model, VISION_FAST)
            self.assertIsNone(policy.escalate_model)
            self.assertFalse(policy.cascade)
            self.assertEqual(policy.timeout_sec, 180.0)

    def test_pro_and_max_recommend_9b_only(self) -> None:
        empty = SimpleNamespace(
            ollama_embed_model="nomic-embed-text",
            ollama_vision_model="",
            ollama_vision_escalate_model="",
            ollama_vision_cascade=True,
        )
        for ram, tier, timeout in ((32.0, "pro", 240.0), (128.0, "max", 300.0)):
            host = _fake_host(ram, tier)
            rec = recommend_models(host)
            self.assertEqual(rec["tier"], tier)
            self.assertEqual(rec["vision_model"], VISION_FAST)
            self.assertIsNone(rec["escalate_model"])
            self.assertFalse(rec["cascade"])
            self.assertNotIn(VISION_ESCALATE, rec["pull_order"])
            policy = resolve_vision_policy(host=host, settings=empty)
            self.assertEqual(policy.primary_model, VISION_FAST)
            self.assertIsNone(policy.escalate_model)
            self.assertFalse(policy.cascade)
            self.assertEqual(policy.timeout_sec, timeout)

    def test_clamp_primary_blocks_27b_on_every_tier(self) -> None:
        self.assertEqual(
            clamp_primary_for_tier("standard", VISION_ESCALATE), VISION_FAST
        )
        self.assertEqual(clamp_primary_for_tier("max", VISION_ESCALATE), VISION_FAST)

    def test_lab_cascade_enables_27b_on_pro_max_only(self) -> None:
        lab_settings = SimpleNamespace(
            ollama_embed_model="nomic-embed-text",
            ollama_vision_model=VISION_FAST,
            ollama_vision_escalate_model=VISION_ESCALATE,
            ollama_vision_cascade=True,
        )
        with patch.dict("os.environ", {"SUYING_ALLOW_VISION_CASCADE": "1"}):
            for ram, tier in ((32.0, "pro"), (128.0, "max")):
                host = _fake_host(ram, tier)
                rec = recommend_models(host)
                self.assertFalse(rec["cascade"])
                self.assertIsNone(rec["escalate_model"])
                self.assertNotIn(VISION_ESCALATE, rec["pull_order"])
                policy = resolve_vision_policy(host=host, settings=lab_settings)
                self.assertEqual(policy.primary_model, VISION_FAST)
                self.assertEqual(policy.escalate_model, VISION_ESCALATE)
                self.assertTrue(policy.cascade)
            std = resolve_vision_policy(
                host=_fake_host(16.0, "standard"), settings=lab_settings
            )
            self.assertFalse(std.cascade)
            self.assertIsNone(std.escalate_model)

    def test_without_lab_env_settings_cascade_is_ignored(self) -> None:
        settings = SimpleNamespace(
            ollama_embed_model="nomic-embed-text",
            ollama_vision_model=VISION_FAST,
            ollama_vision_escalate_model=VISION_ESCALATE,
            ollama_vision_cascade=True,
        )
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("SUYING_ALLOW_VISION_CASCADE", None)
            policy = resolve_vision_policy(host=_fake_host(128.0, "max"), settings=settings)
            self.assertFalse(policy.cascade)
            self.assertIsNone(policy.escalate_model)


class CascadeGateTests(unittest.TestCase):
    def test_thresholds_unchanged(self) -> None:
        self.assertEqual(MAX_SEMANTIC_ATTEMPTS, 3)
        self.assertEqual(MIN_LABEL_CONFIDENCE, 0.65)
        self.assertEqual(MIN_OVERALL_CONFIDENCE, 0.72)

    def test_should_escalate_on_timeout_and_reject(self) -> None:
        self.assertTrue(
            should_escalate_vision(
                gate={"passed": False, "reasons": ["x"]},
                extraction_audit={"error": "vision_timeout"},
            )
        )
        self.assertTrue(
            should_escalate_vision(
                gate={"passed": False, "reasons": ["schema_not_object"]},
                extraction_audit={"error": "schema_not_object"},
            )
        )
        self.assertTrue(
            should_escalate_vision(
                gate={"passed": False, "reasons": ["description_speculative"]},
                extraction_audit={"model_returned": True},
            )
        )
        self.assertFalse(
            should_escalate_vision(
                gate={"passed": True, "reasons": []},
                extraction_audit={"model_returned": True},
            )
        )

    def test_on_demand_forces_one_9b_attempt(self) -> None:
        host = _fake_host(128.0, "max")
        policy = resolve_vision_policy(
            host=host,
            settings=SimpleNamespace(
                ollama_embed_model="nomic-embed-text",
                ollama_vision_model=VISION_FAST,
                ollama_vision_escalate_model=VISION_ESCALATE,
                ollama_vision_cascade=True,
            ),
        )
        calls: list[dict] = []

        def fake_analyze(*_a, **kwargs):
            stage = kwargs.get("cascade_stage") or "primary"
            attempt = int(kwargs.get("attempt") or 1)
            calls.append({"attempt": attempt, "stage": stage})
            return valid_analysis(), {
                "attempt": attempt,
                "model_returned": True,
                "vision_model": kwargs.get("model"),
                "cascade_stage": stage,
            }

        asset = SimpleNamespace(uuid="u1", source_path="/tmp/x.mp4")
        with patch(
            "engine.catalog.host_profile.resolve_vision_policy", return_value=policy
        ), patch(
            "engine.ingest.cliplet.analyze_cliplet_semantics", side_effect=fake_analyze
        ):
            data, audit = _analyze_with_gate(
                asset,
                0.0,
                4.0,
                cache_dir=Path(tempfile.mkdtemp()),
                use_vision=True,
                max_attempts=1,
                allow_cascade=False,
                force_model=VISION_FAST,
            )
        self.assertIsNotNone(data)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["attempts"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stage"], "primary")
        self.assertFalse(audit["cascade"])
        self.assertEqual(audit.get("final_vision_model"), VISION_FAST)

    def test_standard_never_escalates_attempt_budget(self) -> None:
        host = _fake_host(16.0, "standard")
        policy = resolve_vision_policy(
            host=host,
            settings=SimpleNamespace(
                ollama_embed_model="nomic-embed-text",
                ollama_vision_model="",
                ollama_vision_escalate_model="",
                ollama_vision_cascade=True,
            ),
        )
        self.assertFalse(policy.cascade)
        calls: list[str] = []

        def fake_analyze(*_a, **kwargs):
            stage = kwargs.get("cascade_stage") or "primary"
            calls.append(stage)
            return None, {
                "attempt": kwargs.get("attempt"),
                "error": "vision_timeout",
                "vision_model": VISION_FAST,
                "cascade_stage": stage,
            }

        asset = SimpleNamespace(uuid="u2", source_path="/tmp/y.mp4")
        with patch(
            "engine.catalog.host_profile.resolve_vision_policy", return_value=policy
        ), patch(
            "engine.ingest.cliplet.analyze_cliplet_semantics", side_effect=fake_analyze
        ):
            data, audit = _analyze_with_gate(
                asset, 0.0, 4.0, cache_dir=Path(tempfile.mkdtemp()), use_vision=True
            )
        self.assertIsNone(data)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["attempts"], MAX_SEMANTIC_ATTEMPTS)
        self.assertEqual(calls, ["primary"] * MAX_SEMANTIC_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
