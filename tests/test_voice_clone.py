"""GVoiceClone: bundled pack resolve + optional live F5 synth."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.pack.voice_clone import (
    DEFAULT_CLONE_PACK_ID,
    bundled_voice_packs_root,
    f5_available,
    is_clone_provider,
    resolve_voice_pack,
)
from engine.ops.publish_trail import NONCOMPLIANT_TTS_SAY, tts_lock_violation
from engine.qc.ready_gate import _check_voice


class VoiceClonePackTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertTrue(is_clone_provider("clone"))
        self.assertTrue(is_clone_provider("f5"))
        self.assertTrue(is_clone_provider("aunt"))
        self.assertFalse(is_clone_provider("edge"))

    def test_bundled_aunt_slow(self) -> None:
        root = bundled_voice_packs_root() / DEFAULT_CLONE_PACK_ID
        self.assertTrue((root / "ref.wav").is_file(), root)
        self.assertTrue((root / "ref.txt").is_file(), root)
        pack = resolve_voice_pack(pack_id="aunt_slow")
        self.assertEqual(pack.id, "aunt_slow")
        self.assertTrue(pack.ref_wav.is_file())
        self.assertIn("咖啡", pack.ref_text)
        self.assertAlmostEqual(pack.chars_per_sec_zh, 3.3, places=1)

    def test_ready_gate_accepts_clone(self) -> None:
        fails = _check_voice(
            {
                "meta": {
                    "tts_provider": "clone",
                    "voice_lang": "zh",
                    "narration_path": "/tmp/x.wav",
                    "clone_pack": {"id": "aunt_slow", "ref_wav": "/tmp/ref.wav"},
                    "tts_pitch": "clone",
                    "tts_volume": "clone",
                    "narration_script": "仓配现货本地发货",
                }
            }
        )
        self.assertEqual(fails, [])

    def test_ready_gate_rejects_mock(self) -> None:
        fails = _check_voice(
            {
                "meta": {
                    "tts_provider": "mock",
                    "voice_lang": "zh",
                    "narration_path": "/tmp/x.wav",
                }
            }
        )
        self.assertTrue(any("mock" in f for f in fails))

    def test_existing_edge_and_clone_do_not_follow_later_lock_changes(self) -> None:
        edge_meta = {"narration_path": "/tmp/edge.wav", "tts_provider": "edge"}
        clone_meta = {"narration_path": "/tmp/clone.wav", "tts_provider": "clone"}
        edge_lock = {"locked": True, "voice": {"provider": "edge"}}
        clone_lock = {"locked": True, "voice": {"provider": "clone"}}
        self.assertIsNone(tts_lock_violation(edge_meta, clone_lock))
        self.assertIsNone(tts_lock_violation(clone_meta, edge_lock))
        self.assertEqual(
            tts_lock_violation(
                {"narration_path": "/tmp/say.wav", "tts_provider": "say"},
                edge_lock,
            ),
            NONCOMPLIANT_TTS_SAY,
        )


def _f5_synth_runtime_ok() -> bool:
    """ENV_KNOWN: f5 site-packages may import package name yet fail under wrong CPython ABI."""
    if not f5_available():
        return False
    try:
        from f5_tts.api import F5TTS  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(
    _f5_synth_runtime_ok(),
    "f5-tts synth runtime not importable under this Python (ENV_KNOWN ABI/site-packages)",
)
class VoiceCloneLiveTests(unittest.TestCase):
    def test_synth_one_line(self) -> None:
        from engine.pack.tts import probe_audio_duration, synthesize_script

        pack = resolve_voice_pack(pack_id="aunt_slow")
        with tempfile.TemporaryDirectory() as tmp:
            narr = synthesize_script(
                "仓库货源充足。本地发货更省心。",
                Path(tmp),
                lang="zh",
                provider="clone",
                strip_punctuation=True,
                allow_fallback=False,
                clone_pack=pack,
                inter_sentence_gap_sec=0.2,
            )
            self.assertEqual(narr.provider, "clone")
            self.assertGreaterEqual(len(narr.segments), 2)
            for seg in narr.segments:
                self.assertTrue(Path(seg.audio_path).is_file())
                self.assertGreater(probe_audio_duration(seg.audio_path), 0.4)
            # Slow literary pace: should not race like product promo (~7 cps)
            cps = len("仓库货源充足本地发货更省心") / max(0.1, narr.total_duration_sec)
            self.assertLess(cps, 5.5, f"too fast cps={cps}")


if __name__ == "__main__":
    unittest.main()
