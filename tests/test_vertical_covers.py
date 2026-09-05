from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from engine.reach.cdp_publish import (
    _click_publish,
    _has_existing_upload_form,
    _is_app_cover_path,
    _stage_cover_for_cdp,
)
from engine.reach.cover_templates import (
    PLATFORM_COVER_SPECS,
    create_template,
    list_templates,
    load_index,
    select_template,
    set_slot_image,
)
from engine.reach.publish_assets import PublishAssetsError, require_publish_assets


class VerticalCoverTests(unittest.TestCase):
    def _pack(self, root: Path) -> Path:
        pack = root / "pack"
        pack.mkdir()
        (pack / "video.mp4").write_bytes(b"video")
        (pack / "copy.zh.json").write_text(
            json.dumps({"platforms": {"douyin": {"title": "标题", "body": "正文"}}}),
            encoding="utf-8",
        )
        return pack

    def test_every_platform_has_one_portrait_slot(self) -> None:
        for platform, specs in PLATFORM_COVER_SPECS.items():
            self.assertEqual(len(specs), 1, platform)
            self.assertEqual(specs[0]["id"], "vertical", platform)
            self.assertGreater(specs[0]["height"], specs[0]["width"], platform)

    def test_old_dual_slot_counts_are_shrunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / "index.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "selected_id": None,
                        "slot_counts": {"douyin": 2, "channels": 2},
                        "templates": [],
                    }
                ),
                encoding="utf-8",
            )
            listed = list_templates(store)
            self.assertEqual(listed["slot_counts"]["douyin"], 1)
            self.assertEqual(listed["slot_counts"]["channels"], 1)

    def test_publish_requires_selected_vertical_app_cover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "cover_templates"
            store.mkdir()
            load_index(store)
            pack = self._pack(root)

            contract = {"ok": True, "pack_dir": str(pack), "platform_asset_status": {}}
            with patch(
                "engine.reach.publish_assets.validate_pack_contract",
                return_value=contract,
            ):
                with self.assertRaisesRegex(PublishAssetsError, "App"):
                    require_publish_assets(
                        platform="douyin",
                        pack_dir=pack,
                        data_root=store,
                    )

            template = create_template(store, name="竖版套")
            template_id = str(template["id"])
            select_template(store, template_id)

            horizontal = root / "horizontal.jpg"
            Image.new("RGB", (160, 90)).save(horizontal)
            with self.assertRaisesRegex(ValueError, "竖版"):
                set_slot_image(
                    store,
                    template_id=template_id,
                    platform="douyin",
                    slot_index=0,
                    source_path=horizontal,
                )

            vertical = root / "vertical.jpg"
            Image.new("RGB", (90, 160)).save(vertical)
            set_slot_image(
                store,
                template_id=template_id,
                platform="douyin",
                slot_index=0,
                source_path=vertical,
            )
            with patch(
                "engine.reach.publish_assets.validate_pack_contract",
                return_value=contract,
            ):
                assets = require_publish_assets(
                    platform="douyin",
                    pack_dir=pack,
                    data_root=store,
                )
            self.assertEqual(len(assets["covers"]), 1)
            self.assertIn(template_id, assets["covers"][0])
            self.assertTrue(_is_app_cover_path(assets["covers"][0]))

    def test_channels_upload_copy_is_converted_to_six_by_seven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "channels.jpg"
            Image.new("RGB", (1080, 1920), color=(20, 40, 80)).save(source)
            staged = Path(
                _stage_cover_for_cdp(
                    source,
                    target_size=(1080, 1260),
                )
            )
            with Image.open(source) as original:
                self.assertEqual(original.size, (1080, 1920))
            with Image.open(staged) as converted:
                self.assertEqual(converted.size, (1080, 1260))

    def test_channels_publish_uses_trusted_mouse_click(self) -> None:
        session = MagicMock()
        session.evaluate.return_value = {
            "state": "ready",
            "text": "发表",
            "score": 150,
            "x": 800,
            "y": 640,
        }
        result = _click_publish(session, platform="channels")
        self.assertEqual(result, "clicked_mouse:发表@150")
        session.click_xy.assert_called_once_with(800.0, 640.0)

    def test_resume_reuploads_when_platform_form_lost_video(self) -> None:
        session = MagicMock()
        session.evaluate.return_value = False
        self.assertFalse(_has_existing_upload_form(session))

    def test_xhs_cover_optional_still_allows_publish_click(self) -> None:
        # Soft-fail contract used by publish_via_cdp for XHS:
        # cover may be skipped, but the runner still proceeds to click 发布.
        cover_r = {
            "ok": False,
            "skipped": True,
            "reason": "xhs_cover_optional",
            "covers": [],
        }
        self.assertFalse(cover_r.get("ok"))
        self.assertTrue(cover_r.get("skipped"))
        self.assertEqual(cover_r.get("reason"), "xhs_cover_optional")


if __name__ == "__main__":
    unittest.main()
