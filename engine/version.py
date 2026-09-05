"""Single source of truth for engine/product version strings.

Bump only via ``scripts/bump_app_version.py`` (also syncs App package sources).
Do not hardcode version literals in route handlers.
"""

from __future__ import annotations

# Keep in sync with apps/desktop package.json via bump_app_version.py
ENGINE_VERSION = "0.8.11"
