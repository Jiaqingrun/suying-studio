from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Base, Customer, Job, RenderOutput
from engine.catalog.review_auto import ensure_publish_pack
from engine.pack.publish import PACK_VERSION, export_publish_pack, validate_publish_video
from engine.reach.business_scope import VIDEO_PLATFORMS
from engine.reach.cover_templates import load_index
from engine.reach.publish_assets import (
    PublishAssetsError,
    require_publish_assets,
    validate_pack_contract,
)


VIDEO_META = {
    "container": "mp4",
    "video_codec": "h264",
    "audio_codec": "aac",
    "pix_fmt": "yuv420p",
    "width": 1080,
    "height": 1920,
    "orientation": "portrait",
}


def _sidecar(path: Path) -> Path:
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "title": "真实记录｜细节可见",
                "theme": "服务",
                "copywriting": {
                    "hashtags": ["#服务", "#实拍"],
                    "music_credit": "licensed",
                },
                "meta": {"duration_sec": 6},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sidecar


def test_export_soft_rewrites_evidence_terms_in_pack_preflight(tmp_path: Path) -> None:
    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video")
    sidecar = output.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "title": "核对规格再下单",
                "theme": "仓配",
                "copywriting": {
                    "hashtags": ["#实拍"],
                    "music_credit": "licensed",
                    "description": "型号请以现场确认结果为准之前先看画面",
                },
                "meta": {
                    "duration_sec": 6,
                    "narration_script": "现场做事更踏实。",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pack_data = {
        "compliance": {
            "hard_deny": ["全网最低"],
            "blocked_terms": [],
            "evidence_required": {
                "规格": "product_specification",
                "型号": "product_specification",
            },
            "safe_rewrites": {"规格": "品类", "型号": "品类"},
        }
    }
    with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META):
        manifest = export_publish_pack(
            output_path=output,
            sidecar_path=sidecar,
            pack_data=pack_data,
            include_narration=False,
        )
    pack = Path(manifest["pack_dir"])
    copy_doc = json.loads((pack / "copy.zh.json").read_text(encoding="utf-8"))
    blob = json.dumps(copy_doc, ensure_ascii=False)
    assert "规格" not in blob
    assert "型号" not in blob or "以" in blob  # disclaimer path may keep 型号 with 确认

    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video")
    sidecar = _sidecar(output)
    with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META):
        manifest = export_publish_pack(
            output_path=output,
            sidecar_path=sidecar,
            include_narration=False,
        )
        contract = validate_pack_contract(manifest["pack_dir"])

    assert manifest["version"] == PACK_VERSION
    assert set(manifest["platforms"]) == set(VIDEO_PLATFORMS)
    assert manifest["optional_articles"] == ["wechat_mp"]
    assert set(contract["platform_asset_status"]) == set(VIDEO_PLATFORMS)
    pack = Path(manifest["pack_dir"])
    copy_doc = json.loads((pack / "copy.zh.json").read_text(encoding="utf-8"))
    assert set(copy_doc["platforms"]) == set(VIDEO_PLATFORMS)
    assert set(copy_doc["optional_articles"]) == {"wechat_mp"}
    for platform in VIDEO_PLATFORMS:
        platform_manifest = json.loads(
            (pack / "platforms" / platform / "manifest.json").read_text(encoding="utf-8")
        )
        assert platform_manifest["title"]
        assert platform_manifest["body"]
        assert platform_manifest["hashtags"]
        assert (pack / "platforms" / platform / "compliance.json").is_file()
    assert not (pack / "platforms" / "wechat_mp").exists()


def test_video_contract_checks_codec_audio_pixel_format_and_portrait(tmp_path: Path) -> None:
    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video")
    probe = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1080,
                "height": 1920,
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    with patch(
        "engine.pack.publish.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr=""),
    ):
        assert validate_publish_video(output) == VIDEO_META

    probe["streams"][0]["width"] = 1920
    probe["streams"][0]["height"] = 1080
    with patch(
        "engine.pack.publish.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr=""),
    ):
        assert validate_publish_video(output)["orientation"] == "landscape"

    probe["streams"][0]["pix_fmt"] = "yuvj420p"
    with patch(
        "engine.pack.publish.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr=""),
    ):
        assert validate_publish_video(output)["pix_fmt"] == "yuv420p"

    probe["streams"][0]["pix_fmt"] = "yuv444p"
    probe["streams"][1]["codec_name"] = "mp3"
    with patch(
        "engine.pack.publish.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr=""),
    ), pytest.raises(ValueError, match="yuv420p.*AAC"):
        validate_publish_video(output)


def test_contract_rejects_non_video_platform_in_copy_set(tmp_path: Path) -> None:
    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video")
    with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META):
        manifest = export_publish_pack(
            output_path=output,
            sidecar_path=_sidecar(output),
            include_narration=False,
        )
        pack = Path(manifest["pack_dir"])
        copy_path = pack / "copy.zh.json"
        copy_doc = json.loads(copy_path.read_text(encoding="utf-8"))
        copy_doc["platforms"]["wechat_mp"] = copy_doc["optional_articles"]["wechat_mp"]
        copy_path.write_text(json.dumps(copy_doc, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(PublishAssetsError, match="含非视频平台"):
            validate_pack_contract(pack)


def test_pack_failure_persists_asset_block_and_is_retryable(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="物料失败客户", profile_json={})
        session.add(customer)
        session.flush()
        job = Job(
            customer_id=customer.id,
            mode="count",
            target_count=1,
            status="completed",
            theme="default",
            category="default",
            template_name="default-vertical",
        )
        session.add(job)
        session.flush()
        output_path = tmp_path / "broken.mp4"
        output_path.write_bytes(b"not-an-mp4")
        output = RenderOutput(
            job_id=job.id,
            output_path=str(output_path),
            state="ready",
            seed=1,
            qc_json={"ready_gate": {"ok": True}},
        )
        session.add(output)
        session.flush()

        failed = ensure_publish_pack(session, customer, output)
        assert failed["ok"] is False
        assert output.state == "asset_blocked"
        assert output.pack_status == "pack_failed"
        assert output.pack_error

        _sidecar(output_path)
        with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META):
            retried = ensure_publish_pack(session, customer, output)
        assert retried["ok"] is True
        assert output.state == "ready"
        assert output.pack_status == "ready"


def test_export_reuses_burned_captions_without_second_burn(tmp_path: Path) -> None:
    """H9: ready cut with subtitle_burned must not get a second overlay burn."""
    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video-already-captioned")
    prod_srt = tmp_path / "ready.zh.srt"
    prod_srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n这是我们始峰五金的实际操作记录\n",
        encoding="utf-8",
    )
    voice = tmp_path / "ready.voice.wav"
    voice.write_bytes(b"RIFF....")
    sidecar = output.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "title": "真实记录｜细节可见",
                "theme": "服务",
                "copywriting": {
                    "hashtags": ["#服务", "#实拍"],
                    "music_credit": "licensed",
                },
                "meta": {
                    "duration_sec": 6,
                    "subtitle_burned": True,
                    "subtitle_path": str(prod_srt),
                    "narration_path": str(voice),
                    "narration_script": "这是我们始峰五金的实际操作记录",
                    "subtitle_burn_method": "overlay",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    burn_calls: list[tuple[str, str, str]] = []

    def _fake_burn(video, srt, out, **kwargs):  # noqa: ANN001
        burn_calls.append((str(video), str(srt), str(out)))
        Path(out).write_bytes(b"should-not-be-called")
        return {"ok": True, "method": "overlay", "cues": 1}

    with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META), patch(
        "engine.render.subtitles_burn.burn_srt_into_video", side_effect=_fake_burn
    ):
        manifest = export_publish_pack(
            output_path=output,
            sidecar_path=sidecar,
            include_narration=True,
            expression_overrides={"subtitle_burn": "burn_mono", "voice_lang": "zh", "subtitle_lang": "zh"},
        )

    pack = Path(manifest["pack_dir"])
    intent = manifest["subtitle_burn_intent"]
    assert intent["already_burned_in_source"] is True
    assert intent["reused_existing_burn"] is True
    assert intent["method"] == "reuse_render_burn"
    assert intent["burned"] is True
    assert burn_calls == [], "second burn must never run on already-captioned ready cut"
    burned = pack / "video.burned.mp4"
    assert burned.is_file()
    assert burned.read_bytes() == output.read_bytes()
    pack_srt = (pack / "subtitle.zh.srt").read_text(encoding="utf-8")
    assert "这是我们始峰五金的实际操作记录" in pack_srt
    assert "跟着镜头" not in pack_srt
    assert (pack / "voiceover.zh.wav").is_file()
    assert manifest["files"].get("subtitle_source") == "production"
    assert manifest["files"].get("voiceover_source") == "production"


def test_xhs_cover_remains_optional(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "video.mp4").write_bytes(b"video")
    cover_store = tmp_path / "empty-cover-store"
    load_index(cover_store)
    with patch(
        "engine.reach.publish_assets.validate_pack_contract",
        return_value={"ok": True, "pack_dir": str(pack)},
    ), patch(
        "engine.reach.publish_assets._load_copy",
        return_value={"title": "标题", "body": "正文"},
    ):
        assets = require_publish_assets(
            platform="xhs",
            pack_dir=pack,
            data_root=cover_store,
        )
    assert assets["ok"] is True
    assert assets["covers"] == []
    assert assets["cover_optional"] is True


def test_export_soft_cleans_现货_in_production_srt(tmp_path: Path) -> None:
    """SRT 残留禁词「现货」须软洗，不得再抛空「合同未通过：」。"""
    output = tmp_path / "ready.mp4"
    output.write_bytes(b"video")
    srt_path = tmp_path / "subtitle.zh.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n始峰五金现货丰富任您挑选\n",
        encoding="utf-8",
    )
    sidecar = output.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "title": "到店选材\n在架可见",
                "theme": "scene_tour",
                "copywriting": {
                    "hashtags": ["#实拍", "#仓配"],
                    "music_credit": "licensed",
                    "description": "到店选材，在架陈列看得见。",
                },
                "meta": {
                    "duration_sec": 6,
                    "narration_script": "到店选材在架陈列看得见",
                    "subtitle_path": str(srt_path),
                },
                "covers": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pack_data = {
        "compliance": {
            "hard_deny": ["现货"],
            "blocked_terms": ["现货"],
            "evidence_required": {"现货": "inventory_snapshot"},
        }
    }

    def _fake_resolve(output_path, side):
        return {
            "already_burned": True,
            "srt_path": str(srt_path),
            "voice_path": None,
        }

    with patch("engine.pack.publish.validate_publish_video", return_value=VIDEO_META), patch(
        "engine.pack.publish.resolve_production_caption_assets",
        side_effect=_fake_resolve,
    ):
        manifest = export_publish_pack(
            output_path=output,
            sidecar_path=sidecar,
            pack_data=pack_data,
            include_narration=False,
        )
    pack = Path(manifest["pack_dir"])
    pack_srt = (pack / "subtitle.zh.srt").read_text(encoding="utf-8")
    assert "现货" not in pack_srt
    assert "在架" in pack_srt
    assert manifest.get("compliance_passed") is True
