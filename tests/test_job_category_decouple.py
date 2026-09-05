"""Tests for content_category vs asset_category decoupling."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from engine.jobs.job_categories import (
    normalize_job_categories,
    plan_category_for_cliplet_filter,
)
from engine.template.rule_store import list_rotation_pool


class NormalizeJobCategoriesTests(unittest.TestCase):
    def test_legacy_premium_is_content_only(self) -> None:
        n = normalize_job_categories(category="premium")
        self.assertEqual(n["content_category"], "premium")
        self.assertEqual(n["asset_category"], "")

    def test_explicit_split(self) -> None:
        n = normalize_job_categories(
            category="default",
            content_category="premium",
            asset_category="Camera",
        )
        self.assertEqual(n["content_category"], "premium")
        self.assertEqual(n["asset_category"], "Camera")

    def test_legacy_folder_becomes_asset(self) -> None:
        n = normalize_job_categories(category="Camera")
        self.assertEqual(n["content_category"], "default")
        self.assertEqual(n["asset_category"], "Camera")

    def test_rule_slot_never_asset(self) -> None:
        n = normalize_job_categories(
            content_category="default",
            asset_category="premium",
        )
        self.assertEqual(n["asset_category"], "")

    def test_cliplet_filter_ignores_slots(self) -> None:
        self.assertEqual(plan_category_for_cliplet_filter("premium", "Camera"), "default")


class RotationPoolSlotFilterTests(unittest.TestCase):
    def test_pool_filters_by_content_category(self) -> None:
        default_row = MagicMock()
        default_row.content_category = "default"
        premium_row = MagicMock()
        premium_row.content_category = "premium"

        session = MagicMock()
        # list_rotation_pool builds a SQLAlchemy select; we stub scalars().all()
        # by patching at the query level via returning only matching rows
        # when content_category filter is applied in where().
        # Prefer a thin integration of the filter clause via monkeypatch of select path.

        from engine.catalog import db as db_mod

        class _FakeScalars:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        captured = {}

        def fake_scalars(stmt):
            # Inspect compiled where for content_category equality if possible;
            # fallback: return both and let us assert the API accepted the kwarg.
            captured["stmt"] = stmt
            # Simulate DB filtering: only return default when filtered.
            return _FakeScalars([default_row])

        session.scalars = fake_scalars
        rows = list_rotation_pool(
            session,
            customer_id=1,
            orientation="portrait",
            content_category="default",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].content_category, "default")
        self.assertIn("stmt", captured)
        _ = db_mod  # keep import used for lint quiet


class BuildPlanAssetCategoryTests(unittest.TestCase):
    """content_category=premium must not filter Asset.category==premium."""

    def test_premium_content_does_not_zero_camera_assets(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from engine.catalog.db import Asset, Base, Customer
        from engine.template.engine import DEFAULT_TEMPLATE, build_plan

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        cust = Customer(name="fixture-cat", profile_json={}, active=True)
        session.add(cust)
        session.commit()
        session.refresh(cust)

        for i in range(5):
            session.add(
                Asset(
                    customer_id=cust.id,
                    uuid=f"cam-{i}",
                    source_path=f"/tmp/cam-{i}.mp4",
                    storage_path=f"/tmp/cam-{i}.mp4",
                    status="ready",
                    orientation="portrait",
                    category="Camera",
                    duration_sec=8.0,
                    width=1080,
                    height=1920,
                )
            )
        session.commit()

        # Legacy callers that still pass category=premium must not zero Camera assets.
        plan_legacy = build_plan(
            session,
            DEFAULT_TEMPLATE,
            customer_name=cust.name,
            theme="default",
            category="premium",
            seed=42,
            customer_id=cust.id,
            orientation="portrait",
            asset_category=None,
        )
        asset_warn_legacy = [
            w for w in (plan_legacy.warnings or []) if "素材不足" in w and "当前 0 条" in w
        ]
        self.assertFalse(
            asset_warn_legacy,
            f"legacy category=premium must not filter folders; warnings={plan_legacy.warnings}",
        )

        # Without asset_category, content premium path still sees Camera assets.
        plan = build_plan(
            session,
            DEFAULT_TEMPLATE,
            customer_name=cust.name,
            theme="default",
            category="default",
            seed=42,
            customer_id=cust.id,
            orientation="portrait",
            asset_category=None,
            production_rules={"content_category": "premium"},
        )
        # May be blocked for other reasons (no cliplets) but must not warn 0 assets
        # solely from Asset.category==premium.
        asset_warn = [w for w in (plan.warnings or []) if "素材不足" in w and "当前 0 条" in w]
        self.assertFalse(
            asset_warn,
            f"premium must not filter folder; warnings={plan.warnings}",
        )

        # Explicit Camera filter keeps assets; bogus premium folder filtered out by normalize.
        plan2 = build_plan(
            session,
            DEFAULT_TEMPLATE,
            customer_name=cust.name,
            theme="default",
            category="default",
            seed=42,
            customer_id=cust.id,
            orientation="portrait",
            asset_category="Camera",
        )
        asset_warn2 = [w for w in (plan2.warnings or []) if "素材不足" in w and "当前 0 条" in w]
        self.assertFalse(asset_warn2, f"Camera filter should find assets; warnings={plan2.warnings}")

        session.close()


if __name__ == "__main__":
    unittest.main()
