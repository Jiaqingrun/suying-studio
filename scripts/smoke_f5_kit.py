#!/usr/bin/env python3
"""Smoke: F5 Runtime Kit contract (no full torch import required)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pack.f5_kit import (  # noqa: E402
    build_kit_manifest,
    default_f5_overlay_root,
    kit_compat_status,
    overlay_has_f5_marker,
    overlay_path_allowed,
    python_tag_from_executable,
    write_kit_manifest,
)
from engine.pack.voice_clone import (  # noqa: E402
    apply_f5_site_overlay,
    clone_runtime_status,
    default_f5_overlay_root as vc_root,
)


def main() -> int:
    assert overlay_path_allowed(default_f5_overlay_root())
    assert not overlay_path_allowed(Path("/tmp/evil_f5"))
    tag = python_tag_from_executable()
    assert tag.startswith("cpython-"), tag
    assert vc_root() == default_f5_overlay_root()

    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "f5_site_packages"
        overlay.mkdir()
        (overlay / "f5_tts").mkdir()
        (overlay / "f5_tts" / "__init__.py").write_text("# marker\n", encoding="utf-8")
        assert overlay_has_f5_marker(overlay)
        man = build_kit_manifest(
            overlay=overlay,
            kit_rev=1,
            python_tag=tag,
            source_build="smoke",
        )
        assert man["schema_version"] == 1
        assert man["kit_rev"] == 1
        path = write_kit_manifest(man, path=Path(tmp) / "f5_kit.manifest.json")
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["python_tag"] == tag

    st = clone_runtime_status()
    assert "clone_available" in st
    assert "f5_kit_rev" in st
    assert "f5_kit_compat_ok" in st
    assert "f5_source" in st
    apply_f5_site_overlay(force=True)
    compat = kit_compat_status()
    assert "f5_kit_compat_ok" in compat

    print(
        json.dumps(
            {
                "ok": True,
                "python_tag": tag,
                "overlay": str(default_f5_overlay_root()),
                "clone_available": st.get("clone_available"),
                "f5_source": st.get("f5_source"),
                "f5_kit_rev": st.get("f5_kit_rev"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
