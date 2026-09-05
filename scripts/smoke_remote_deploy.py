#!/usr/bin/env python3
"""Behavior smoke for remote deployment contracts; never opens SSH or builds a DMG."""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy-remote.sh"
INSTALL = ROOT / "scripts" / "remote-install.sh"
RUNTIME = ROOT / "scripts" / "install-runtime.sh"
EMBED = ROOT / "scripts" / "embed-app-runtime.sh"
PACKAGE = ROOT / "apps" / "desktop" / "scripts" / "package-macos.sh"
PRODUCT = ROOT / "scripts" / "package-product.sh"


def main() -> None:
    for script in (DEPLOY, INSTALL, RUNTIME, EMBED, PACKAGE, PRODUCT):
        subprocess.run(["bash", "-n", str(script)], check=True)

    deploy_text = DEPLOY.read_text(encoding="utf-8")
    assert 'ALLOW_ONLINE_MODEL_PULL=0' in deploy_text
    assert 'UPDATE_REMOTE_ROOT="/nvme11/my/data/速影/更新包"' in deploy_text
    assert "正式远程部署缺少 offline-tools" in deploy_text
    assert "远端若已具备 FFmpeg/Ollama 可继续" not in deploy_text
    assert "chrome_runtime 缺少 LaunchServices 启动路径" in deploy_text
    assert 'remote_stage_root="\\$HOME/Suying/incoming/remote-deploy"' in deploy_text
    assert 'remote_base="${remote_stage_root}/${deployment_id}"' in deploy_text
    assert "STAGING-RETENTION.txt" in deploy_text
    assert 'record_remote_retention "PARTIAL"' in deploy_text
    assert 'record_remote_retention "FAILED"' in deploy_text
    assert 'cleanup_remote_staging' in deploy_text
    assert 'case \\"\\$BASE\\" in' in deploy_text
    assert 'rm -rf -- \\"\\$BASE\\"' in deploy_text
    assert 'rm -rf -- \\"\\$ROOT\\"' not in deploy_text
    assert "远程产品 ZIP 仍包含 DMG" in deploy_text
    assert "远程产品 ZIP 仍包含 offline-models" in deploy_text
    assert "--skip-models" in deploy_text
    assert "SKIP_MODELS=0" in deploy_text
    assert "覆盖升级：跳过模型套件传输" in deploy_text
    assert "--skip-models 与 --model-bundle 互斥" in deploy_text

    install_text = INSTALL.read_text(encoding="utf-8")
    receipt_written = install_text.index('temp.replace(target)')
    accepted = install_text.index("INSTALL_SUCCEEDED=1")
    prune_called = install_text.index("prune_accepted_app_backups ||")
    assert receipt_written < accepted < prune_called
    assert "keep_n = max(1, int(os.environ.get(\"KEEP_APP_BACKUPS\", \"1\")))" in install_text
    assert r'(\d{8}T\d{6}Z)' in install_text
    assert "速影 Studio.app.*" in install_text
    assert "仅保留最近 1 个回滚点" in install_text
    assert "kept_recent = candidates[0][1]" in install_text
    assert "BUNDLE_FLAVOR" in install_text
    prune_start = install_text.index("prune_accepted_app_backups")
    prune_end = install_text.index('INSTALL_LOCK=', prune_start)
    assert "chrome-profiles" not in install_text[prune_start:prune_end]

    product_text = PRODUCT.read_text(encoding="utf-8")
    package_text = PACKAGE.read_text(encoding="utf-8")
    exact_dmg = '速影-${VERSION}-macos-${ARCH}-${RUNTIME_FLAVOR}.dmg'
    assert exact_dmg in product_text and exact_dmg in package_text
    assert '速影-"*.dmg' not in product_text
    assert 'bundle/dmg/"*.dmg' not in product_text
    assert 'cp "${DMG_SRC}" "${RELEASE_KIT}' not in product_text
    assert 'cp "$DMG_SRC" "${RELEASE_KIT}' not in package_text
    assert "SUYING_RELEASE_ROOT" in product_text and "SUYING_RELEASE_ROOT" in package_text
    assert "${HOME}/Desktop/${OUT_NAME}" not in product_text
    assert "${HOME}/Desktop/${OUT_NAME}" not in package_text
    assert 'DMG (独立)' in product_text and 'DMG (独立)' in package_text
    assert "RUNTIME_FLAVOR" in product_text and "RUNTIME_FLAVOR" in package_text
    assert "SUYING_MAX_APP_MIB" in package_text
    assert "跨架构部署：本机不执行客户 Python" in deploy_text
    assert "BUNDLE_ARCH" in deploy_text
    assert "Suying/releases" in deploy_text
    release_script = (ROOT / "scripts" / "release-to-t2s.sh").read_text(encoding="utf-8")
    assert "publish_update_repo.py" in release_script
    assert "bump_app_version.py" in release_script
    assert "SUYING_RELEASE_ROOT" in release_script
    assert 'KEEP_LOCAL="${KEEP_LOCAL:-0}"' in release_script
    assert "purge_local_release_artifacts" in release_script
    assert 'INSTALL_LOCAL="${INSTALL_LOCAL:-1}"' in release_script
    assert "Install local App from kit" in release_script

    with tempfile.TemporaryDirectory(prefix="suying-remote-smoke-") as raw:
        temp = Path(raw)

        # The production status helper must reject every non-real-smoke outcome.
        for status in ("SMOKE_NOT_REQUESTED", "NEED_MEDIA", "PARTIAL"):
            result = subprocess.run(
                ["bash", str(INSTALL), "--verify-status-only", status],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
            )
            assert result.returncode == 20, (status, result)
            assert result.stdout.strip() == f"PARTIAL:{status}"
        for status in ("passed", "verified_existing"):
            result = subprocess.run(
                ["bash", str(INSTALL), "--verify-status-only", status],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": "/usr/bin:/bin"},
            )
            assert result.returncode == 0, (status, result)
            assert result.stdout.strip() == f"COMPLETE:{status}"

        # Fingerprint behavior is exercised in an isolated miniature source tree.
        fixture = temp / "fingerprint-fixture"
        (fixture / "scripts").mkdir(parents=True)
        (fixture / "engine").mkdir()
        shutil.copy2(EMBED, fixture / "scripts" / EMBED.name)
        fingerprint_tool = ROOT / "scripts" / "runtime_source_fingerprint.py"
        shutil.copy2(fingerprint_tool, fixture / "scripts" / fingerprint_tool.name)
        (fixture / "engine" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
        (fixture / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        (fixture / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
        first = subprocess.check_output(
            ["bash", str(fixture / "scripts" / EMBED.name), "--print-fingerprint"],
            text=True,
        ).strip()
        (fixture / "engine" / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
        second = subprocess.check_output(
            ["bash", str(fixture / "scripts" / EMBED.name), "--print-fingerprint"],
            text=True,
        ).strip()
        assert len(first) == 64 and len(second) == 64 and first != second

        # The remote archive filter must remove nested DMGs and offline-models.
        kit = temp / "速影-test-product-macos-arm64"
        kit.mkdir()
        (kit / "payload.txt").write_text("keep\n", encoding="utf-8")
        (kit / "stale.dmg").write_text("drop\n", encoding="utf-8")
        models = kit / "offline-models" / "pro"
        models.mkdir(parents=True)
        (models / "bundle.json").write_text('{"profile":"pro"}\n', encoding="utf-8")
        archive = temp / "remote.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(kit), str(archive)],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/zip", "-d", str(archive), "*.[dD][mM][gG]"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        listed = subprocess.check_output(["unzip", "-Z1", str(archive)])
        model_entries = b"\n".join(
            line for line in listed.splitlines() if b"offline-models" in line
        )
        if model_entries:
            subprocess.run(
                ["/usr/bin/zip", "-d", str(archive), "-@"],
                input=model_entries + b"\n",
                check=True,
                stdout=subprocess.DEVNULL,
            )
        archived = subprocess.check_output(["unzip", "-Z1", str(archive)]).splitlines()
        assert any(name.endswith(b"/payload.txt") for name in archived), archived
        assert not any(name.lower().endswith(b".dmg") for name in archived), archived
        assert not any(b"offline-models" in name for name in archived), archived

        # Exercise the exact embedded backup-pruning implementation.
        prune_marker = 'BACKUP_ROOT="$BACKUP_ROOT" "$PY" - <<\'PY\'\n'
        prune_code = install_text.split(prune_marker, 1)[1].split("\nPY\n}", 1)[0]

        def backup_name(age_days: int) -> str:
            stamp = datetime.now(timezone.utc) - timedelta(days=age_days)
            return f"速影-Studio.app.{stamp.strftime('%Y%m%dT%H%M%SZ')}"

        backup_root = temp / "backups-recent"
        backup_root.mkdir()
        recent = backup_root / backup_name(2)
        newest = backup_root / backup_name(1)
        expired = backup_root / backup_name(10)
        for path in (recent, newest, expired):
            path.mkdir()
        subprocess.run(
            [sys.executable, "-c", prune_code],
            check=True,
            env={**os.environ, "BACKUP_ROOT": str(backup_root)},
            stdout=subprocess.DEVNULL,
        )
        assert not recent.exists() and newest.is_dir() and not expired.exists()

        all_old_root = temp / "backups-all-old"
        all_old_root.mkdir()
        latest_old = all_old_root / backup_name(9)
        older_old = all_old_root / backup_name(12)
        latest_old.mkdir()
        older_old.mkdir()
        subprocess.run(
            [sys.executable, "-c", prune_code],
            check=True,
            env={**os.environ, "BACKUP_ROOT": str(all_old_root)},
            stdout=subprocess.DEVNULL,
        )
        assert latest_old.is_dir() and not older_old.exists()

    print("SMOKE_REMOTE_DEPLOY OK")


if __name__ == "__main__":
    main()
