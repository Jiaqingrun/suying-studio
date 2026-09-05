#!/usr/bin/env python3
"""Publish a verified product ZIP to the T2S personal update repository."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.ops.release_notes import (  # noqa: E402
    NOTES_FILENAME,
    compact_notes,
    render_changelog_markdown,
    render_notes_markdown,
)
from engine.ops.suying_sync import load_zspace_session  # noqa: E402
from engine.ops.t2s_paths import (  # noqa: E402
    PERSONAL_ROOT,
    UPDATE_ROOT as REMOTE_ROOT,
    UPDATE_UI,
)
from engine.security.update_manifest import verify_latest, verify_release  # noqa: E402
from scripts.sign_update_release import sign_release  # noqa: E402

CLIENT_SOURCE = ROOT / "packaging" / "zspace-sync" / "zspace-team-sync.py"


def _client_class() -> type[Any]:
    spec = importlib.util.spec_from_file_location("suying_zspace_sync", CLIENT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载极空间客户端: {CLIENT_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    # exec_module() may run dataclasses before the module is registered in
    # sys.modules (Python 3.13+). Register it first to avoid intermittent
    # AttributeError in dataclasses processing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    base = module.ZSpaceClient

    class UpdateRepoClient(base):
        def ensure_remote_dir(self, remote_dir: str) -> str:
            backend = "/" + str(remote_dir).strip("/")
            if backend == PERSONAL_ROOT:
                return PERSONAL_ROOT
            if not backend.startswith(PERSONAL_ROOT + "/"):
                raise RuntimeError(f"更新仓库路径越界: {backend}")
            cur = PERSONAL_ROOT
            for name in backend[len(PERSONAL_ROOT) :].strip("/").split("/"):
                if not name:
                    continue
                listing = self.list_dir(cur)
                hit = next((item for item in listing if item.get("name") == name), None)
                cur = str(hit["path"]) if hit else self.newdir(cur, name)
            return cur

    return UpdateRepoClient


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_size(item: dict[str, Any]) -> int:
    return int(item.get("size") or item.get("file_size") or 0)


def _build_signed_artifacts(
    package: Path,
    *,
    version: str,
    build_date: str,
    release_seq: int,
    runtime_manifest: Path,
    customer_ref: str,
    delivery_id: str | None,
    signed_notes: str,
    staging: Path,
) -> dict[str, Any]:
    digest = _sha256(package)
    size = package.stat().st_size
    signed = sign_release(
        argparse.Namespace(
            account="release-v1",
            trusted_keys=ROOT / "engine" / "security" / "trusted_release_keys.json",
            package=package,
            runtime_manifest=runtime_manifest,
            release_seq=release_seq,
            version=version,
            arch=("arm64" if platform.machine() == "arm64" else "x86_64"),
            customer_ref=customer_ref,
            delivery_id=delivery_id,
            release_path=f"releases/{version}/{build_date}/release.json",
            notes=signed_notes,
            output=staging,
        )
    )
    release_file = Path(str(signed["release"]))
    release_sig = Path(str(signed["release_signature"]))
    latest_file = Path(str(signed["latest"]))
    latest_sig = Path(str(signed["latest_signature"]))
    pointer, _ = verify_latest(latest_file, latest_sig)
    manifest, release_digest = verify_release(
        release_file,
        release_sig,
        expected_arch=("arm64" if platform.machine() == "arm64" else "x86_64"),
        state_path=None,
    )
    if pointer.release_sha256 != release_digest:
        raise RuntimeError("签名 latest 与 release 摘要不一致")
    checksum = staging / "SHA256.txt"
    checksum.write_text(f"{digest}  {package.name}\n", encoding="utf-8")
    readme = staging / "README.md"
    readme.write_text(
        "# 速影更新包（T2S · 速影/更新包）\n\n"
        "正式更新按 `releases/<版本>/<构建日期>/` 保存，旧版本不得覆盖。\n"
        "`latest.json`、`release.json` 及其 `.sig` 必须通过内嵌 Ed25519 公钥验签。\n"
        "各版本目录的 `更新说明.md` 为可读变更说明；后补说明不得改写签名清单或 ZIP。\n"
        "更新包不得包含客户配置、Cookie、API Key、数据库或片库。\n",
        encoding="utf-8",
    )
    notes_md = staging / NOTES_FILENAME
    try:
        notes_body = render_notes_markdown(
            version,
            build_date=build_date,
            release_seq=release_seq,
        )
    except KeyError:
        notes_body = f"# 速影 Studio {version} 更新说明\n\n{signed_notes}\n"
    notes_md.write_text(notes_body, encoding="utf-8")
    index_md = staging / f"index-{NOTES_FILENAME}"
    try:
        index_md.write_text(render_changelog_markdown(for_t2s=True), encoding="utf-8")
    except Exception:
        index_md = None
    return {
        "digest": digest,
        "size": size,
        "release_file": release_file,
        "release_sig": release_sig,
        "latest_file": latest_file,
        "latest_sig": latest_sig,
        "checksum": checksum,
        "readme": readme,
        "notes_md": notes_md,
        "index_md": index_md,
        "manifest": manifest,
        "release_digest": release_digest,
    }


def _write_web_stage(
    package: Path,
    *,
    version: str,
    build_date: str,
    artifacts: dict[str, Any],
    stage_dir: Path,
) -> Path:
    """Materialize the exact remote tree for 极空间网页上传."""
    root = stage_dir.expanduser().resolve()
    release_rel = root / "releases" / version / build_date
    release_rel.mkdir(parents=True, exist_ok=True)
    (root / "releases" / version).mkdir(parents=True, exist_ok=True)
    shutil.copy2(package, release_rel / package.name)
    shutil.copy2(artifacts["checksum"], release_rel / "SHA256.txt")
    shutil.copy2(artifacts["release_file"], release_rel / "release.json")
    shutil.copy2(artifacts["release_sig"], release_rel / "release.json.sig")
    shutil.copy2(artifacts["notes_md"], release_rel / NOTES_FILENAME)
    shutil.copy2(artifacts["notes_md"], root / "releases" / version / NOTES_FILENAME)
    shutil.copy2(artifacts["readme"], root / "README.md")
    if artifacts.get("index_md") and Path(artifacts["index_md"]).is_file():
        shutil.copy2(artifacts["index_md"], root / NOTES_FILENAME)
    shutil.copy2(artifacts["latest_sig"], root / "latest.json.sig")
    shutil.copy2(artifacts["latest_file"], root / "latest.json")
    guide = root / "WEB_UPLOAD.txt"
    guide.write_text(
        "速影 T2S 网页推送清单\n"
        "====================\n"
        f"目标：极空间网页 → T2S 个人空间 → {UPDATE_UI}/\n"
        f"（对应 API：{REMOTE_ROOT}）\n\n"
        "禁止依赖本机 NFS 挂载 ~/QR/dev/T2s/ZSPACE。\n\n"
        "上传顺序（必须）：\n"
        f"1) releases/{version}/{build_date}/ 下全部文件"
        f"（含 {package.name}、SHA256.txt、release.json、release.json.sig、{NOTES_FILENAME}）\n"
        f"2) releases/{version}/{NOTES_FILENAME}\n"
        "3) README.md（可选覆盖）与根目录更新说明（若有）\n"
        "4) latest.json.sig\n"
        "5) latest.json（最后上传）\n\n"
        "上传后：在网页核对 ZIP 大小；有条件时再回读 SHA256。\n"
        "旧版本目录不得覆盖或删除。\n",
        encoding="utf-8",
    )
    return root


def publish(
    package: Path,
    *,
    version: str,
    build_date: str,
    release_seq: int,
    runtime_manifest: Path,
    customer_ref: str,
    delivery_id: str | None = None,
    notes: str | None = None,
    stage_dir: Path | None = None,
) -> dict[str, Any]:
    package = package.expanduser().resolve()
    if not package.is_file() or package.suffix.lower() != ".zip":
        raise ValueError(f"更新包必须是存在的 ZIP: {package}")
    if "/" in version or ".." in version or "/" in build_date or ".." in build_date:
        raise ValueError("version/build-date 不得包含路径")

    signed_notes = (notes or "").strip()
    if not signed_notes:
        try:
            signed_notes = compact_notes(version, build_date=build_date)
        except KeyError as exc:
            raise RuntimeError(
                f"缺少更新说明：请在 packaging/release_notes.json 登记 {version}，"
                "或传入 --notes"
            ) from exc

    release_dir = f"{REMOTE_ROOT}/releases/{version}/{build_date}"

    with tempfile.TemporaryDirectory(prefix="suying-update-repo-") as temp:
        staging = Path(temp)
        artifacts = _build_signed_artifacts(
            package,
            version=version,
            build_date=build_date,
            release_seq=release_seq,
            runtime_manifest=runtime_manifest,
            customer_ref=customer_ref,
            delivery_id=delivery_id,
            signed_notes=signed_notes,
            staging=staging,
        )
        manifest = artifacts["manifest"]
        digest = artifacts["digest"]
        size = artifacts["size"]
        release_digest = artifacts["release_digest"]

        if stage_dir is not None:
            out = _write_web_stage(
                package,
                version=version,
                build_date=build_date,
                artifacts=artifacts,
                stage_dir=stage_dir,
            )
            return {
                "ok": True,
                "mode": "web_stage",
                "stage_dir": str(out),
                "remote_root_ui": UPDATE_UI,
                "remote_root_api": REMOTE_ROOT,
                "release_dir": release_dir,
                "file": package.name,
                "sha256": digest,
                "size": size,
                "release_seq": manifest.release_seq,
                "release_digest": release_digest,
                "delivery_id": manifest.delivery_id,
                "key_id": manifest.key_id,
                "notes": signed_notes,
                "guide": str(out / "WEB_UPLOAD.txt"),
            }

        session = load_zspace_session()
        if str(session.get("nas_id") or "") != "T0210023G0UWV":
            raise RuntimeError("当前极空间不是目标 T2S（T0210023G0UWV），拒绝上传")
        client = _client_class()(
            f"http://127.0.0.1:{session['local_port']}",
            session,
        )

        backend_release = client.ensure_remote_dir(release_dir)
        # Big zip uploads can occasionally fail with broken pipes.
        # If the exact same zip already exists in the target release_dir with
        # matching size, we can safely skip re-upload and proceed with the
        # small metadata artifacts (SHA256.txt, release.json, latest.json).
        existing_zip_size = None
        try:
            listing0 = client.list_dir(backend_release)
            hit0 = next((item for item in listing0 if item.get("name") == package.name), None)
            if hit0:
                existing_zip_size = _remote_size(hit0)
        except Exception:
            existing_zip_size = None

        if existing_zip_size is None or existing_zip_size != size:
            client.upload(package, f"{release_dir}/{package.name}")
        client.upload(artifacts["checksum"], f"{release_dir}/SHA256.txt")
        client.upload(artifacts["release_file"], f"{release_dir}/release.json")
        client.upload(artifacts["release_sig"], f"{release_dir}/release.json.sig")
        client.upload(artifacts["notes_md"], f"{release_dir}/{NOTES_FILENAME}")
        client.upload(artifacts["notes_md"], f"{REMOTE_ROOT}/releases/{version}/{NOTES_FILENAME}")

        listing = client.list_dir(backend_release)
        uploaded = next((item for item in listing if item.get("name") == package.name), None)
        if not uploaded or _remote_size(uploaded) != size:
            raise RuntimeError(
                f"远端更新包大小校验失败: local={size}, remote={_remote_size(uploaded or {})}"
            )
        downloaded = staging / "remote-verify.zip"
        client.download(f"{release_dir}/{package.name}", downloaded, expected_size=size)
        if _sha256(downloaded) != digest:
            raise RuntimeError("远端更新包回读 SHA256 校验失败")

        # Repository metadata is committed only after the versioned artifacts verify.
        client.ensure_remote_dir(REMOTE_ROOT)
        client.upload(artifacts["readme"], f"{REMOTE_ROOT}/README.md")
        if artifacts.get("index_md") and Path(artifacts["index_md"]).is_file():
            try:
                client.upload(artifacts["index_md"], f"{REMOTE_ROOT}/{NOTES_FILENAME}")
            except Exception:
                pass
        client.upload(artifacts["latest_sig"], f"{REMOTE_ROOT}/latest.json.sig")
        client.upload(artifacts["latest_file"], f"{REMOTE_ROOT}/latest.json")

        return {
            "ok": True,
            "mode": "api",
            "backend_root": client.to_backend_path(REMOTE_ROOT),
            "release_dir": client.to_backend_path(release_dir),
            "file": package.name,
            "sha256": digest,
            "size": size,
            "release_seq": manifest.release_seq,
            "release_digest": release_digest,
            "delivery_id": manifest.delivery_id,
            "key_id": manifest.key_id,
            "notes": signed_notes,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-date", required=True)
    parser.add_argument("--release-seq", type=int, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--customer-ref", required=True)
    parser.add_argument("--delivery-id")
    parser.add_argument(
        "--notes",
        default="",
        help="覆盖 packaging/release_notes.json 中该版本的签名 notes",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        help="只生成本机网页上传目录（默认推送方式）；不走 API/NFS 挂载",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            publish(
                args.package,
                version=args.version,
                build_date=args.build_date,
                release_seq=args.release_seq,
                runtime_manifest=args.runtime_manifest,
                customer_ref=args.customer_ref,
                delivery_id=args.delivery_id,
                notes=args.notes or None,
                stage_dir=args.stage_dir,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
