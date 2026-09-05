from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
from engine.catalog.db import get_session
from engine.config.settings import AppSettings, load_settings
from engine.ingest.metadata import VIDEO_EXTENSIONS, ingest_file

log = logging.getLogger("montage.watcher")


class LibraryHandler(FileSystemEventHandler):
    def __init__(self, settings: AppSettings, library_root: Path, customer_id: int) -> None:
        self.settings = settings
        self.library_root = library_root
        self.customer_id = customer_id

    def on_created(self, event) -> None:
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path) -> None:
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        # Wait for copy to finish (NAS/copy can be slow)
        time.sleep(1.5)
        if not path.exists():
            return
        session = get_session()
        try:
            log.info("ingest detect customer=%s path=%s", self.customer_id, path)
            asset = ingest_file(
                session,
                self.settings,
                path,
                library_root=self.library_root,
                customer_id=self.customer_id,
            )
            if asset:
                log.info("ingest done asset=%s uuid=%s", asset.id, asset.uuid)
        except Exception:
            log.exception("ingest failed path=%s", path)
        finally:
            session.close()


class IngestWatcher:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._observer: Observer | None = None
        self._customer_id: int | None = None

    def _active_context(self) -> tuple[AppSettings, int]:
        session = get_session()
        try:
            base = load_settings()
            customer = require_active_customer(session, base)
            scoped = settings_with_customer_paths(base, customer)
            return scoped, customer.id
        finally:
            session.close()

    def start(self) -> None:
        scoped, customer_id = self._active_context()
        self.settings = scoped
        self._customer_id = customer_id
        self._observer = Observer()
        roots = list(scoped.paths.all_library_roots())
        log.info("watcher start customer=%s roots=%s", customer_id, [str(r) for r in roots])
        for root in roots:
            if not root.exists():
                log.warning("library root missing: %s", root)
                continue
            handler = LibraryHandler(scoped, root, customer_id)
            self._observer.schedule(handler, str(root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def is_alive(self) -> bool:
        return bool(self._observer and self._observer.is_alive())

    def scan_existing(
        self,
        limit: int | None = None,
        on_progress: Callable[[int], None] | None = None,
        *,
        defer_index: bool = True,
    ) -> int:
        """Walk library roots and ingest missing videos.

        By default defers cliplet/embedding to the background reconciler so a
        full scan of hundreds of files stays tractable (normalize+proxy only).
        Realtime watcher still indexes immediately.
        """
        from engine.catalog.db import Asset
        from sqlalchemy import select

        scoped, customer_id = self._active_context()
        self.settings = scoped
        self._customer_id = customer_id
        count = 0
        session = get_session()
        try:
            # Skip completed/in-flight rows. Migration marks formerly rejected
            # landscape sources as pending so one explicit scan can re-ingest them.
            known = {
                row
                for row in session.scalars(
                    select(Asset.source_path).where(
                        Asset.customer_id == customer_id,
                        Asset.status != "pending",
                    )
                ).all()
            }
            log.info("scan known_assets=%s", len(known))
            for root in scoped.paths.all_library_roots():
                if not root.exists():
                    continue
                candidates: list[Path] = []
                for path in root.rglob("*"):
                    if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file():
                        if "Screenshots" in path.parts:
                            continue
                        candidates.append(path)
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                missing = [p for p in candidates if str(p.resolve()) not in known]
                if limit is not None:
                    missing = missing[: max(0, limit - count)]
                log.info(
                    "scan root=%s candidates=%s missing=%s defer_index=%s",
                    root,
                    len(candidates),
                    len(missing),
                    defer_index,
                )
                attempted = 0
                for path in missing:
                    try:
                        from engine.runtime.quiesce import scan_cancel_requested

                        if scan_cancel_requested():
                            log.info("scan cancelled by system pause after %s ready", count)
                            return count
                    except Exception:
                        pass
                    try:
                        asset = ingest_file(
                            session,
                            scoped,
                            path,
                            library_root=root,
                            customer_id=customer_id,
                            defer_index=defer_index,
                        )
                        known.add(str(path.resolve()))
                        attempted += 1
                        if asset and getattr(asset, "status", None) == "ready":
                            count += 1
                            log.info("scan ingested #%s asset=%s %s", count, asset.id, path)
                        if on_progress:
                            # Report ready count; also surface attempts via side channel
                            on_progress(count)
                            try:
                                from engine.ops.scan_state import scan_state

                                scan_state["attempted"] = int(scan_state.get("attempted") or 0) + 1
                                scan_state["ingested"] = count
                            except Exception:
                                pass
                    except Exception:
                        log.exception("scan ingest failed %s", path)
                        known.add(str(path.resolve()))
                    if limit is not None and attempted >= limit:
                        log.info(
                            "scan finished ingested=%s attempted=%s (hit limit)",
                            count,
                            attempted,
                        )
                        return count
        finally:
            session.close()
        log.info("scan finished ingested=%s", count)
        return count

    def reload(self, settings: AppSettings | None = None) -> None:
        if settings:
            self.settings = settings
        self.stop()
        self.start()
