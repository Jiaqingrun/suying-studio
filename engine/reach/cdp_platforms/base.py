"""Shared adapter surface for CDP publish platforms."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BasePlatformAdapter:
    """Thin marker; real CDP steps still live in ``cdp_publish``."""

    platform: str

    def describe(self) -> str:
        return f"cdp_adapter:{self.platform}"
