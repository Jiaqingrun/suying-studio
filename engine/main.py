from __future__ import annotations

import logging

import uvicorn

from engine.api.app import app
from engine.catalog.host_profile import ensure_host_bin_path
from engine.config.settings import load_settings


def main() -> None:
    # .app 启动时常缺 Homebrew PATH；出片依赖 ffmpeg / ollama CLI。
    ensure_host_bin_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
