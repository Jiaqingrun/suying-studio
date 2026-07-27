#!/usr/bin/env python3
"""Static smoke for the one-command remote deployment contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy-remote.sh"
INSTALL = ROOT / "scripts" / "remote-install.sh"
EMBED = ROOT / "scripts" / "embed-app-runtime.sh"
PACKAGE = ROOT / "apps" / "desktop" / "scripts" / "package-macos.sh"


def main() -> None:
    for script in (DEPLOY, INSTALL, EMBED, PACKAGE):
        subprocess.run(["bash", "-n", str(script)], check=True)

    deploy = DEPLOY.read_text(encoding="utf-8")
    install = INSTALL.read_text(encoding="utf-8")
    embed = EMBED.read_text(encoding="utf-8")
    package = PACKAGE.read_text(encoding="utf-8")

    for text in (deploy, install):
        assert "/Users/qr" not in text
        assert "Cnn719092" not in text

    assert "PREFER_STANDALONE=\"${PREFER_STANDALONE:-1}\"" in embed
    assert "ALLOW_HOST_VENV_FALLBACK" in embed
    assert "production runtime unexpectedly contains host-bound pyvenv.cfg" in embed
    assert "relocated python ok" in package
    assert "http://tauri.localhost" in package
    assert "Access-Control-Request-Method: POST" in package

    for required in (
        "codesign --verify --deep --strict",
        "standalone python ok",
        "brew install ffmpeg",
        "api/tags",
        "/ops/zspace-sync/ensure-customer",
        "/ops/zspace-sync/bind",
        "install-sync-service.sh",
        "/ops/carrier/install-update-agent",
        "Origin: http://tauri.localhost",
        "POST \"${API}/jobs\"",
    ):
        assert required in install, required

    print("SMOKE_REMOTE_DEPLOY OK")


if __name__ == "__main__":
    main()
