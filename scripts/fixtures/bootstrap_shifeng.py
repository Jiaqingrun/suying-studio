#!/usr/bin/env python3
"""Apply customer library path + import 始峰伟业 keyword pack into local DB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.catalog.db import init_db, get_session  # noqa: E402
from engine.catalog.keyword_pack import import_keyword_pack  # noqa: E402
from engine.config.paths import ensure_layout  # noqa: E402
from engine.config.settings import AppSettings, PathConfig, save_settings  # noqa: E402

LIBRARY = Path("/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/01-片库")
OUTPUT = Path("/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片")
WORK = Path("/Users/qr/QR-Volume/速影工作区")
PACK = ROOT / "configs" / "customers" / "北京始峰伟业" / "keyword-pack.json"
CUSTOMER = "北京始峰伟业"


def main() -> None:
    if not PACK.exists():
        raise SystemExit(f"Keyword pack missing: {PACK}. Run convert_shifeng_keyword_pack.py first.")

    settings = AppSettings(
        paths=PathConfig(
            library_root=LIBRARY,
            library_roots=[],
            output_root=OUTPUT,
            cache_root=WORK / "cache",
            data_root=WORK / "db",
            render_root=WORK / "render",
            external_required=True,
        ),
        active_customer=CUSTOMER,
        onboarded=True,
    )
    save_settings(settings)
    ensure_layout(settings)
    init_db(settings)

    data = json.loads(PACK.read_text(encoding="utf-8"))
    session = get_session()
    try:
        pack = import_keyword_pack(session, CUSTOMER, data, pack_name="default")
        print(
            json.dumps(
                {
                    "ok": True,
                    "customer": CUSTOMER,
                    "pack_id": pack.id,
                    "library_root": str(LIBRARY),
                    "library_exists": LIBRARY.exists(),
                    "output_root": str(settings.paths.output_root),
                    "themes": list(data.get("keyword_pool", {}).get("themes", {}).keys()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
