from __future__ import annotations

import logging
import os

import uvicorn

from engine.api.app import app
from engine.catalog.host_profile import ensure_host_bin_path
from engine.config.settings import (
    allow_semantic_full_backfill_env,
    apply_local_runtime_env,
    load_settings,
)
from engine.security.license import require_runtime_license


def main() -> None:
    # Machine-local lab env (~/Suying/runtime/local.env) before any settings load.
    # Never packaged; missing file is a no-op on customer machines.
    apply_local_runtime_env()
    # Full-library backfill and 27B cascade cannot share one GPU safely.
    # local.env may re-inject CASCADE after a parent `env -u`; strip it here.
    if allow_semantic_full_backfill_env():
        os.environ.pop("SUYING_ALLOW_VISION_CASCADE", None)
    from engine.pack.voice_clone import apply_bundled_clone_hf_env, apply_f5_site_overlay

    apply_bundled_clone_hf_env()
    # Outside-bundle F5 site-packages (survives core App overwrite). No-op if empty.
    apply_f5_site_overlay()
    require_runtime_license()
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
