"""Cover upload is opt-in: default skip; confirmed → hard-gate + assets carry covers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from engine.config.settings import AppSettings
from engine.reach.cdp_publish import _should_upload_covers, _skipped_cover_result
from engine.reach.cover_templates import create_template, load_index, select_template, set_slot_image
from engine.reach.publish_assets import (
    PublishAssetsError,
    publish_confirm_upload_cover_enabled,
    require_publish_assets,
)


VIDEO_PLATS = ("douyin", "kuaishou", "channels", "xhs")


class CoverUploadOptInTests(unittest.TestCase):
    def _pack(self, root: Path) -> Path:
        pack = root / "pack"
        pack.mkdir()
        (pack / "video.mp4").write_bytes(b"video")
        return pack

    def _contract(self, pack: Path) -> dict:
        return {"ok": True, "pack_dir": str(pack), "platform_asset_status": {}}

    def test_settings_default_confirm_upload_cover_is_off(self) -> None:
        self.assertFalse(AppSettings().publish_confirm_upload_cover)
        with patch(
            "engine.config.settings.load_settings",
            return_value=AppSettings(publish_confirm_upload_cover=False),
        ):
            self.assertFalse(publish_confirm_upload_cover_enabled())
        with patch(
            "engine.config.settings.load_settings",
            return_value=AppSettings(publish_confirm_upload_cover=True),
        ):
            self.assertTrue(publish_confirm_upload_cover_enabled())

    def test_default_skips_covers_for_all_video_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "covers"
            store.mkdir()
            load_index(store)
            pack = self._pack(root)
            with patch(
                "engine.reach.publish_assets.validate_pack_contract",
                return_value=self._contract(pack),
            ), patch(
                "engine.reach.publish_assets._load_copy",
                return_value={"title": "标题", "body": "正文"},
            ):
                for plat in VIDEO_PLATS:
                    assets = require_publish_assets(
                        platform=plat,
                        pack_dir=pack,
                        data_root=store,
                        upload_cover=False,
                    )
                    self.assertTrue(assets["ok"])
                    self.assertEqual(assets["covers"], [])
                    self.assertTrue(assets["cover_optional"])
                    self.assertFalse(assets["cover_upload_confirmed"])
                    self.assertEqual(assets["cover_meta"]["source"], "skipped")
                    self.assertFalse(_should_upload_covers(assets, plat))

    def test_confirmed_requires_cover_then_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "covers"
            store.mkdir()
            load_index(store)
            pack = self._pack(root)
            contract = self._contract(pack)
            with patch(
                "engine.reach.publish_assets.validate_pack_contract",
                return_value=contract,
            ), patch(
                "engine.reach.publish_assets._load_copy",
                return_value={"title": "标题", "body": "正文"},
            ):
                for plat in ("douyin", "kuaishou", "channels"):
                    with self.assertRaises(PublishAssetsError):
                        require_publish_assets(
                            platform=plat,
                            pack_dir=pack,
                            data_root=store,
                            upload_cover=True,
                        )

                tpl = create_template(store, name="确认上传套")
                tid = str(tpl["id"])
                select_template(store, tid)
                vertical = root / "v.jpg"
                Image.new("RGB", (90, 160)).save(vertical)
                for plat in ("douyin", "kuaishou", "channels"):
                    set_slot_image(
                        store,
                        template_id=tid,
                        platform=plat,
                        slot_index=0,
                        source_path=vertical,
                    )

                for plat in ("douyin", "kuaishou", "channels"):
                    assets = require_publish_assets(
                        platform=plat,
                        pack_dir=pack,
                        data_root=store,
                        upload_cover=True,
                    )
                    self.assertEqual(len(assets["covers"]), 1)
                    self.assertTrue(assets["cover_upload_confirmed"])
                    self.assertTrue(_should_upload_covers(assets, plat))

                xhs = require_publish_assets(
                    platform="xhs",
                    pack_dir=pack,
                    data_root=store,
                    upload_cover=True,
                )
                self.assertTrue(xhs["cover_optional"])
                self.assertFalse(_should_upload_covers(xhs, "xhs"))

    def test_skipped_cover_result_reason(self) -> None:
        r = _skipped_cover_result(reason="cover_upload_not_confirmed")
        self.assertTrue(r["skipped"])
        self.assertEqual(r["reason"], "cover_upload_not_confirmed")
        self.assertEqual(r["covers"], [])


if __name__ == "__main__":
    unittest.main()
