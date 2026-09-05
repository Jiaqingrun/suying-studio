"""T2S 离线部署资产：上传 / 暂存 / 清单（极空间 → 运维机 → 客户机）。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.ops.suying_sync import load_zspace_session
from engine.ops.t2s_paths import (
    DEPOT_ROOT,
    LOCAL_ZSPACE_OFFLINE_CACHE,
    OFFLINE_DEPLOY_ROOT,
    OFFLINE_MODELS_ROOT,
    OFFLINE_VERIFY_TOOLS_ROOT,
    PERSONAL_ROOT,
)

TARGET_NAS_ID = "T0210023G0UWV"
MANIFEST_NAME = "MANIFEST.json"
README_NAME = "README.md"

ROOT = Path(__file__).resolve().parents[2]
CLIENT_SOURCE = ROOT / "packaging" / "zspace-sync" / "zspace-team-sync.py"


def _client_class() -> type[Any]:
    spec = importlib.util.spec_from_file_location("suying_zspace_sync", CLIENT_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载极空间客户端: {CLIENT_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    base = module.ZSpaceClient

    class PersonalSpaceClient(base):
        def ensure_remote_dir(self, remote_dir: str) -> str:
            backend = "/" + str(remote_dir).strip("/")
            if backend == PERSONAL_ROOT:
                return PERSONAL_ROOT
            if not backend.startswith(PERSONAL_ROOT + "/"):
                raise RuntimeError(f"路径越界: {backend}")
            cur = PERSONAL_ROOT
            for name in backend[len(PERSONAL_ROOT) :].strip("/").split("/"):
                if not name:
                    continue
                listing = self.list_dir(cur)
                hit = next((item for item in listing if item.get("name") == name), None)
                cur = str(hit["path"]) if hit else self.newdir(cur, name)
            return cur

        def ensure_personal_dir(self, remote_dir: str) -> str:
            return self.ensure_remote_dir(remote_dir)

    return PersonalSpaceClient


def client() -> Any:
    session = load_zspace_session()
    if str(session.get("nas_id") or "") != TARGET_NAS_ID:
        raise RuntimeError(f"当前极空间不是目标 T2S（{TARGET_NAS_ID}），拒绝操作")
    return _client_class()(f"http://127.0.0.1:{session['local_port']}", session)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_cache_root() -> Path:
    return Path.home() / "Suying" / LOCAL_ZSPACE_OFFLINE_CACHE


def models_remote(arch: str, profile: str) -> str:
    return f"{OFFLINE_MODELS_ROOT}/macos-{arch}/{profile}"


def verify_tools_remote(arch: str, profile: str) -> str:
    return f"{OFFLINE_VERIFY_TOOLS_ROOT}/macos-{arch}/{profile}"


def models_cache(arch: str, profile: str) -> Path:
    return local_cache_root() / "models" / f"macos-{arch}" / profile


def verify_tools_cache(arch: str, profile: str) -> Path:
    return local_cache_root() / "verify-tools" / f"macos-{arch}" / profile


def depot_cache(arch: str) -> Path:
    return local_cache_root() / "depot" / f"{arch}-ready"


def _remote_size(item: dict[str, Any]) -> int:
    return int(item.get("size") or item.get("file_size") or 0)


def _walk_local(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store")


def _walk_remote(c: Any, remote_root: str) -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("suying_zspace_sync", CLIENT_SOURCE)
    module = sys.modules.get(spec.name) if spec else None
    if module is None:
        spec = importlib.util.spec_from_file_location("suying_zspace_sync", CLIENT_SOURCE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module.walk_files(c, remote_root)


def _remote_file_index(client_obj: Any, remote_root: str) -> dict[str, int]:
    out: dict[str, int] = {}
    prefix = remote_root.rstrip("/") + "/"
    for item in _walk_remote(client_obj, remote_root):
        path = str(item.get("path") or "")
        if path.startswith(prefix):
            rel = path[len(prefix) :]
        else:
            rel = str(item.get("name") or "")
        if not rel:
            continue
        out[rel.replace("\\", "/")] = _remote_size(item)
    return out


def upload_tree(
    local_root: Path,
    remote_root: str,
    *,
    skip_unchanged: bool = True,
) -> dict[str, Any]:
    """Upload local directory tree to T2S personal space."""
    local_root = local_root.expanduser().resolve()
    if not local_root.is_dir():
        raise FileNotFoundError(local_root)
    c = client()
    c.ensure_remote_dir(remote_root)
    remote_index = _remote_file_index(c, remote_root) if skip_unchanged else {}
    uploaded = 0
    skipped = 0
    prefix = remote_root.rstrip("/")
    for path in _walk_local(local_root):
        rel = path.relative_to(local_root).as_posix()
        size = path.stat().st_size
        if skip_unchanged and remote_index.get(rel) == size:
            skipped += 1
            continue
        c.upload(path, f"{prefix}/{rel}")
        uploaded += 1
    return {
        "ok": True,
        "local_root": str(local_root),
        "remote_root": remote_root,
        "uploaded": uploaded,
        "skipped": skipped,
        "files": len(_walk_local(local_root)),
    }


def download_tree(
    remote_root: str,
    local_root: Path,
    *,
    skip_unchanged: bool = True,
) -> dict[str, Any]:
    """Download T2S directory tree to local cache."""
    local_root = local_root.expanduser().resolve()
    local_root.mkdir(parents=True, exist_ok=True)
    c = client()
    prefix = remote_root.rstrip("/") + "/"
    downloaded = 0
    skipped = 0
    for item in _walk_remote(c, remote_root):
        path = str(item.get("path") or "")
        if path.startswith(prefix):
            rel = path[len(prefix) :]
        else:
            rel = str(item.get("name") or "")
        if not rel or rel.endswith("/"):
            continue
        rel = rel.replace("\\", "/")
        target = local_root / rel
        size = _remote_size(item)
        if skip_unchanged and target.is_file() and target.stat().st_size == size:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        c.download(f"{remote_root.rstrip('/')}/{rel}", target, expected_size=size or None)
        downloaded += 1
    return {
        "ok": True,
        "remote_root": remote_root,
        "local_root": str(local_root),
        "downloaded": downloaded,
        "skipped": skipped,
    }


def build_manifest(*, arch: str, profiles: list[str], sources: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "suying.offline-deploy.manifest.v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "arch": arch,
        "profiles": profiles,
        "depot_remote": DEPOT_ROOT,
        "models_remote_root": OFFLINE_MODELS_ROOT,
        "verify_tools_remote_root": OFFLINE_VERIFY_TOOLS_ROOT,
        "sources": sources,
    }


def write_readme() -> str:
    return """# 速影 · 离线交付（T2S）

运维机 **不长期堆** 模型/离线仓；真相源在本目录，部署时经运维 Mac 暂存后 LAN 推到客户机。

| 路径 | 内容 |
|------|------|
| `models/macos-<arch>/<profile>/` | Ollama 离线模型套件（`bundle.json` + blobs） |
| `verify-tools/macos-<arch>/<profile>/` | 已物化的 ollama + ffmpeg（无 App/模型） |
| （引用）`速影/更新包/depot/` | 签名 CAS 离线仓 · 见 `MANIFEST.json` → `depot_remote` |

## 运维命令

```bash
# 推送本机资产到 T2S（首次 / 更新后）
python3 scripts/publish_offline_deploy_to_zspace.py

# 部署前暂存到 ~/Suying/incoming/zspace-offline/（deploy-remote 默认自动）
python3 scripts/stage_offline_deploy_from_zspace.py --profile pro --arch arm64

# 远程部署（本地无套件时自动从极空间拉）
./scripts/deploy-remote.sh --host <别名> ...
```

规范：`docs/T2S_OFFLINE_DEPLOY.md` · 路径常量：`engine/ops/t2s_paths.py`
"""


def verify_tools_ok(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "ollama" / "ollama").is_file()
        and (path / "ffmpeg" / "bin" / "ffmpeg").is_file()
        and (path / "ffmpeg" / "bin" / "ffprobe").is_file()
    )


def stage_for_deploy(
    *,
    profile: str,
    arch: str,
    need_models: bool = True,
    need_tools: bool = True,
    need_depot: bool = False,
) -> dict[str, Any]:
    """Stage offline assets from T2S to local cache; return paths for deploy-remote."""
    result: dict[str, Any] = {
        "ok": True,
        "profile": profile,
        "arch": arch,
        "cache_root": str(local_cache_root()),
    }
    if need_models:
        remote = models_remote(arch, profile)
        cache = models_cache(arch, profile)
        download_tree(remote, cache)
        bundle = cache / "bundle.json"
        if not bundle.is_file():
            raise FileNotFoundError(f"T2S 缺少模型套件: {remote}")
        result["model_bundle"] = str(cache)
        result["models_remote"] = remote
    if need_tools:
        remote = verify_tools_remote(arch, profile)
        cache = verify_tools_cache(arch, profile)
        download_tree(remote, cache)
        if not verify_tools_ok(cache):
            raise FileNotFoundError(f"T2S 缺少 verify-tools: {remote}")
        result["offline_tools"] = str(cache)
        result["verify_tools_remote"] = remote
    if need_depot:
        cache = depot_cache(arch)
        download_tree(DEPOT_ROOT, cache)
        if not (cache / "depot.json").is_file():
            raise FileNotFoundError(f"T2S 缺少 depot: {DEPOT_ROOT}")
        result["offline_depot"] = str(cache)
        result["depot_remote"] = DEPOT_ROOT
    return result
