"""Workspace identity + fail-closed probe (no mkdir / no empty DB).

Local-first: default authority is main-disk ~/Suying/data (state=local).
External volumes remain optional path overrides with fail-closed missing/mismatch.

「同步/刷新」语义：重新识别配置中的权威工作区并刷新 App，绝不复制/合并/覆盖 SQLite。
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from engine.config.settings import AppSettings, bootstrap_data_root, settings_file

WORKSPACE_MARKER = ".suying-workspace.json"
STATE_READY = "ready"
STATE_MISSING = "missing"
STATE_MISMATCH = "mismatch"
STATE_LOCAL = "local"
STATE_UNKNOWN = "unknown"


@dataclass
class WorkspaceIdentity:
    workspace_id: str
    volume_uuid: str | None = None
    data_root: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class WorkspaceProbe:
    state: str
    ok: bool
    data_root: str
    bootstrap_root: str
    workspace_id: str | None = None
    bound_workspace_id: str | None = None
    volume_uuid: str | None = None
    bound_volume_uuid: str | None = None
    mount_point: str | None = None
    db_path: str | None = None
    db_ok: bool = False
    db_counts: dict[str, Any] = field(default_factory=dict)
    marker_exists: bool = False
    is_external: bool = False
    can_init_db: bool = False
    can_mkdir: bool = False
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def marker_path(data_root: Path) -> Path:
    return Path(data_root) / WORKSPACE_MARKER


def read_marker(data_root: Path) -> WorkspaceIdentity | None:
    path = marker_path(data_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    wid = str(raw.get("workspace_id") or "").strip()
    if not wid:
        return None
    return WorkspaceIdentity(
        workspace_id=wid,
        volume_uuid=(str(raw["volume_uuid"]).strip() if raw.get("volume_uuid") else None),
        data_root=str(raw.get("data_root") or data_root),
        created_at=str(raw.get("created_at") or "") or None,
    )


def write_marker(data_root: Path, identity: WorkspaceIdentity) -> WorkspaceIdentity:
    payload = identity.to_dict()
    payload["data_root"] = str(data_root)
    _atomic_write_json(marker_path(data_root), payload)
    return identity


def is_under_home_suying(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    home = Path.home() / "Suying"
    try:
        return resolved == home.resolve() or home.resolve() in resolved.parents
    except OSError:
        return str(resolved).startswith(str(home))


def is_ephemeral_path(path: Path) -> bool:
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    markers = (
        "/pytest-",
        "/pytest_of_",
        "/tmp/",
        "/var/folders/",
        "/private/var/folders/",
        "/T/tmp",
    )
    return any(m in resolved for m in markers)


def is_external_data_root(data_root: Path, bootstrap: Path | None = None) -> bool:
    """True when data_root is not the local bootstrap / ~/Suying tree / ephemeral test dir."""
    boot = bootstrap or bootstrap_data_root()
    try:
        if data_root.resolve() == boot.resolve():
            return False
    except OSError:
        if str(data_root) == str(boot):
            return False
    if is_ephemeral_path(data_root):
        return False
    if is_under_home_suying(data_root):
        return False
    return True


def volume_info_for_path(path: Path) -> dict[str, Any]:
    """Resolve volume UUID / mount point for a path via diskutil (macOS)."""
    out: dict[str, Any] = {
        "path": str(path),
        "volume_uuid": None,
        "mount_point": None,
        "ok": False,
        "error": None,
    }

    def _diskutil_plist(target: str) -> dict[str, Any] | None:
        try:
            info = subprocess.run(
                ["diskutil", "info", "-plist", target],
                capture_output=True,
                check=False,
            )
            if info.returncode != 0:
                return None
            return plistlib.loads(info.stdout)
        except FileNotFoundError:
            raise
        except Exception:  # noqa: BLE001
            return None

    try:
        # Prefer df mount point — diskutil often rejects nested directory paths.
        mount_point: str | None = None
        try:
            df = subprocess.run(
                ["df", "-P", str(path if path.exists() else path.parent)],
                capture_output=True,
                text=True,
                check=False,
            )
            if df.returncode == 0:
                lines = [ln for ln in df.stdout.splitlines() if ln.strip()]
                if len(lines) >= 2:
                    # Last column is Mounted on (may contain spaces).
                    parts = lines[-1].split()
                    if parts:
                        mount_point = parts[-1]
        except Exception:  # noqa: BLE001
            mount_point = None

        candidates: list[str] = []
        if mount_point:
            candidates.append(mount_point)
        cur = path if path.exists() else path.parent
        for _ in range(10):
            candidates.append(str(cur))
            if cur == cur.parent:
                break
            cur = cur.parent

        volume = None
        for cand in candidates:
            volume = _diskutil_plist(cand)
            if volume:
                break
        if not volume:
            out["error"] = "diskutil_failed"
            return out
        out["volume_uuid"] = (
            str(volume.get("VolumeUUID") or volume.get("DiskUUID") or "").strip() or None
        )
        out["mount_point"] = str(volume.get("MountPoint") or mount_point or "").strip() or None
        out["ok"] = True
        return out
    except FileNotFoundError:
        out["error"] = "diskutil_unavailable"
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        return out


def volume_mount_point(volume_uuid: str) -> str | None:
    uuid_s = (volume_uuid or "").strip()
    if not uuid_s:
        return None
    try:
        info = subprocess.run(
            ["diskutil", "info", "-plist", uuid_s],
            capture_output=True,
            check=False,
        )
        if info.returncode != 0:
            return None
        volume = plistlib.loads(info.stdout)
        mount = str(volume.get("MountPoint") or "").strip()
        return mount or None
    except Exception:  # noqa: BLE001
        return None


def bound_identity_from_settings(settings: AppSettings) -> WorkspaceIdentity | None:
    wid = str(getattr(settings, "workspace_id", "") or "").strip()
    vuuid = str(getattr(settings, "workspace_volume_uuid", "") or "").strip() or None
    if not wid and not vuuid:
        return None
    return WorkspaceIdentity(
        workspace_id=wid or "",
        volume_uuid=vuuid,
        data_root=str(settings.paths.data_root),
    )


def ensure_identity_on_ready(settings: AppSettings, *, volume_uuid: str | None) -> WorkspaceIdentity:
    """Create or refresh workspace marker + bind ids into settings object (caller may save)."""
    from datetime import datetime, timezone

    data_root = Path(settings.paths.data_root)
    existing = read_marker(data_root)
    wid = str(getattr(settings, "workspace_id", "") or "").strip()
    if existing and existing.workspace_id:
        wid = existing.workspace_id
    if not wid:
        wid = str(uuid.uuid4())
    identity = WorkspaceIdentity(
        workspace_id=wid,
        volume_uuid=volume_uuid or (existing.volume_uuid if existing else None),
        data_root=str(data_root),
        created_at=(existing.created_at if existing else None)
        or datetime.now(timezone.utc).isoformat(),
    )
    if data_root.exists():
        write_marker(data_root, identity)
    settings.workspace_id = identity.workspace_id
    if identity.volume_uuid:
        settings.workspace_volume_uuid = identity.volume_uuid
    return identity


def probe_workspace(settings: AppSettings | None = None) -> WorkspaceProbe:
    """Probe configured data_root without creating directories or databases."""
    from engine.config.settings import load_settings
    from engine.ops.db_health import inspect_montage_db

    settings = settings or load_settings()
    boot = bootstrap_data_root()
    data_root = Path(settings.paths.data_root)
    external = is_external_data_root(data_root, boot)
    bound = bound_identity_from_settings(settings)
    marker = read_marker(data_root) if data_root.exists() else None
    vol = volume_info_for_path(data_root) if external else {"ok": True, "volume_uuid": None, "mount_point": None}
    reasons: list[str] = []
    warnings: list[str] = []

    db_file = data_root / "montage.db"
    settings_ok = settings_file(data_root).is_file() if data_root.exists() else False
    db_info = inspect_montage_db(db_file) if db_file.is_file() else {
        "ok": False,
        "exists": False,
        "counts": {},
        "errors": ["montage.db 不存在"],
    }

    probe = WorkspaceProbe(
        state=STATE_UNKNOWN,
        ok=False,
        data_root=str(data_root),
        bootstrap_root=str(boot),
        workspace_id=(marker.workspace_id if marker else None) or (bound.workspace_id if bound and bound.workspace_id else None),
        bound_workspace_id=bound.workspace_id if bound and bound.workspace_id else None,
        volume_uuid=vol.get("volume_uuid"),
        bound_volume_uuid=bound.volume_uuid if bound else None,
        mount_point=vol.get("mount_point"),
        db_path=str(db_file),
        db_ok=bool(db_info.get("ok")),
        db_counts=dict(db_info.get("counts") or {}),
        marker_exists=marker is not None,
        is_external=external,
        can_init_db=False,
        can_mkdir=not external,
        reasons=reasons,
        warnings=warnings,
    )

    if not external:
        # Local / ephemeral: allow mkdir + init (smoke / first-run under ~/Suying).
        probe.state = STATE_LOCAL
        probe.ok = True
        probe.can_mkdir = True
        probe.can_init_db = True
        if not data_root.exists():
            probe.warnings.append("本机工作区目录尚不存在，将按需创建")
        return probe

    # External workspace: fail closed.
    if not data_root.exists():
        probe.state = STATE_MISSING
        reasons.append(f"外接工作区未挂载或不存在: {data_root}")
        # If a bound volume UUID is known, check whether the volume itself is present.
        if bound and bound.volume_uuid:
            mount = volume_mount_point(bound.volume_uuid)
            if not mount:
                reasons.append(f"绑定卷未连接: {bound.volume_uuid}")
            else:
                probe.mount_point = mount
                reasons.append(f"卷已挂载于 {mount}，但工作区路径仍缺失")
        return probe

    if bound and bound.volume_uuid:
        mount = volume_mount_point(bound.volume_uuid)
        if not mount:
            probe.state = STATE_MISSING
            reasons.append(f"绑定卷未连接: {bound.volume_uuid}")
            return probe
        probe.mount_point = mount
        # Path must live under the bound volume mount.
        try:
            resolved = str(data_root.resolve())
            if not resolved.startswith(str(Path(mount).resolve())):
                probe.state = STATE_MISMATCH
                reasons.append("工作区路径不在绑定卷挂载点下")
                return probe
        except OSError:
            probe.state = STATE_MISMATCH
            reasons.append("无法解析工作区路径与挂载点")
            return probe
        live_uuid = vol.get("volume_uuid")
        if live_uuid and live_uuid != bound.volume_uuid:
            probe.state = STATE_MISMATCH
            reasons.append(
                f"卷身份不符：期望 {bound.volume_uuid}，当前 {live_uuid}"
            )
            return probe

    if bound and bound.workspace_id and marker and marker.workspace_id:
        if bound.workspace_id != marker.workspace_id:
            probe.state = STATE_MISMATCH
            reasons.append(
                f"工作区身份不符：期望 {bound.workspace_id}，目录标记 {marker.workspace_id}"
            )
            return probe

    if not settings_ok:
        warnings.append("工作区 settings.json 缺失")
    if not db_file.is_file():
        probe.state = STATE_MISSING
        reasons.append("权威库 montage.db 不存在（拒绝创建空库）")
        return probe
    if not db_info.get("ok"):
        errs = db_info.get("errors") or ["数据库不可用"]
        probe.state = STATE_MISMATCH
        reasons.extend(str(e) for e in errs)
        return probe

    probe.state = STATE_READY
    probe.ok = True
    probe.can_init_db = True
    # Never mkdir parents of an external tree that suddenly vanished; but if the
    # directory already exists and is ready, ensure_layout may create subdirs.
    probe.can_mkdir = True
    if db_info.get("empty"):
        warnings.append("权威库为空（无生产记录）")
    if db_info.get("warnings"):
        warnings.extend(str(w) for w in db_info["warnings"])
    return probe


def assert_workspace_ready(settings: AppSettings | None = None) -> WorkspaceProbe:
    probe = probe_workspace(settings)
    if not probe.ok or probe.state not in (STATE_READY, STATE_LOCAL):
        msg = "；".join(probe.reasons) or f"工作区不可用（{probe.state}）"
        raise RuntimeError(msg)
    return probe


def workspace_allows_layout(settings: AppSettings | None = None) -> bool:
    probe = probe_workspace(settings)
    return bool(probe.can_mkdir and probe.state in (STATE_READY, STATE_LOCAL))
