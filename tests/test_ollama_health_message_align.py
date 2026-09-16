"""SA-R1-003: /health/ollama message must match inference_available layer."""

from __future__ import annotations

import unittest

from engine.catalog.ollama_status import align_health_ollama_message


class AlignHealthOllamaMessage(unittest.TestCase):
    def test_model_present_without_probe_not_ready(self) -> None:
        msg = align_health_ollama_message(
            base_message="Ollama 就绪：快筛 qwen3.5:9b",
            reachable=True,
            model_present=True,
            inference_available=False,
            circuit_open=False,
            vision_model="qwen3.5:9b",
        )
        self.assertNotIn("就绪", msg)
        self.assertIn("推理探针未确认", msg)
        self.assertIn("qwen3.5:9b", msg)

    def test_inference_available_says_ready(self) -> None:
        msg = align_health_ollama_message(
            base_message="模型已安装（qwen3.5:9b）；推理探针未确认",
            reachable=True,
            model_present=True,
            inference_available=True,
            circuit_open=False,
            vision_model="qwen3.5:9b",
        )
        self.assertTrue(msg.startswith("Ollama 就绪"))

    def test_circuit_open_overrides(self) -> None:
        msg = align_health_ollama_message(
            base_message="Ollama 就绪：快筛 qwen3.5:9b",
            reachable=True,
            model_present=True,
            inference_available=False,
            circuit_open=True,
            circuit_state="open",
            vision_model="qwen3.5:9b",
        )
        self.assertIn("熔断", msg)

    def test_half_open_wording(self) -> None:
        msg = align_health_ollama_message(
            base_message="Ollama 就绪：快筛 qwen3.5:9b",
            reachable=True,
            model_present=True,
            inference_available=False,
            circuit_open=False,
            circuit_state="half_open",
            vision_model="qwen3.5:9b",
        )
        self.assertIn("半开", msg)
        self.assertNotIn("就绪", msg)


if __name__ == "__main__":
    unittest.main()
