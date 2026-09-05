"""tighten_srt_to_voiceover must not treat the next utterance as this cue's speech."""

from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from engine.pack.narration_script import tighten_srt_to_voiceover
from engine.qc.ready_gate import _check_align


def _tone_wav(path: Path, *, spans: list[tuple[float, float]], rate: int = 16000) -> None:
    """Write a mono wav with audible tone only inside ``spans`` (seconds)."""
    total = max(e for _, e in spans) + 0.5
    n = int(total * rate)
    samples = bytearray(n * 2)
    amp = 12000
    for start, end in spans:
        a = int(start * rate)
        b = min(n, int(end * rate))
        for i in range(a, b):
            # crude square-ish tone
            val = amp if (i // 40) % 2 == 0 else -amp
            struct.pack_into("<h", samples, i * 2, val)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(samples))


def _srt_ts(sec: float) -> str:
    ms = int(round(sec * 1000.0))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


class TightenSrtVoiceoverTests(unittest.TestCase):
    def test_does_not_absorb_next_utterance_span(self) -> None:
        # Cue ends 0.5s after speech; next utterance starts 0.2s later (within
        # the old buggy end+0.25 window). Tighten must still clamp this cue.
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "vo.wav"
            _tone_wav(wav, spans=[(0.0, 1.0), (1.7, 2.5)])
            srt = (
                "1\n"
                f"{_srt_ts(0.0)} --> {_srt_ts(1.5)}\n"
                "第一句\n\n"
                "2\n"
                f"{_srt_ts(1.7)} --> {_srt_ts(2.6)}\n"
                "第二句\n"
            )
            tight = tighten_srt_to_voiceover(srt, wav, tail_trim_seconds=0.12)
            self.assertEqual(_check_align(tight, wav), [])
            # First cue must end near speech_end - trim, not stay at 1.5
            self.assertIn("00:00:00,880", tight)  # 1.0 - 0.12


if __name__ == "__main__":
    unittest.main()
