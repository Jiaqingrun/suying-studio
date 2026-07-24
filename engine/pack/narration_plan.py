"""Bridge narration timings into template duration budgets (G4)."""

from __future__ import annotations

from engine.pack.tts import NarrationResult
from engine.template.engine import TemplateDefinition, template_with_duration_budget


def template_from_narration(
    base: TemplateDefinition,
    narration: NarrationResult,
    *,
    slack_sec: float = 0.12,
) -> TemplateDefinition:
    """Build a TTS-timed template from a NarrationResult (real segment durations)."""
    durations = [s.duration_sec for s in narration.segments]
    return template_with_duration_budget(base, durations, slack_sec=slack_sec)


def duration_budget_from_narration(narration: NarrationResult) -> list[float]:
    return [float(s.duration_sec) for s in narration.segments]
