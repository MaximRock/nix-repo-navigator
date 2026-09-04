"""Filesystem watcher with watchdog and polling fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from repo_navigator.config import Config
from repo_navigator.indexer.event_router import EventRouter
from repo_navigator.parsers.registry import get_parser_for_file

log = logging.getLogger(__name__)

# Paths that are never watched
SKIP_DIRS = {".git", ".hg", ".svn", ".repo-navigator", "__pycache__", ".mypy_cache", ".pytest_cache", ".direnv", "result", "target", "node_modules"}


class _WatchdogHandler:  # type: ignore[no-redef]
    """Internal handler that forwards to EventRouter (thread-safe)."""

    def __init__(self, root: Path, event_router: EventRouter, loop: asyncio.AbstractEventLoop, config: Config) -> None:
        self.root = root
        self.event_router = event_router
        self.loop = loop
        self.config = config

    def _should_handle(self, path: str | Path) -> bool:
        p = Path(path)
        # Skip navigator runtime dir and DB file
        if self._is_skip_path(p):
            return False
        # Skip pyc
        if p.suffix == ".pyc":
            return False
        # Check that a parser exists (or would be considered via should_parse)
        # For watcher we want to watch all .nix files, but also other parsers
        # We use get_parser_for_file to filter
        if get_parser_for_file(p) is None:
            return False
        # Skip hidden files except .config (handled in collect)
        # For watcher, we watch .nix only, so hidden .nix is rare; skip hidden
        if p.name.startswith(".") and p.name != ".config":
            return False
        return True

    @staticmethod
    def _is_skip_path(p: Path) -> bool:
        for part in p.parts:
            if part in SKIP_DIRS:
                return True
        return False

    def _handle(self, src_path: str) -> None:
        if not self._should_handle(src_path):
            return
        # Use thread-safe entry point
        try:
            self.event_router.on_file_event_threadsafe(src_path, loop=self.loop)
        except Exception:
            log.exception("watcher: on_file_event failed for %s", src_path)

    # Watchdog callbacks (called from observer thread)
    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_deleted(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._handle(event.dest_path)


class RepoWatcher:
    """Watches a repository root and forwards events to an :class:`EventRouter`.

    Mode ``auto`` tries ``watchdog`` first, falls back to polling if
    watchdog is unavailable or fails to start.  ``inotify`` forces watchdog,
    ``polling`` forces polling.
    """

    def __init__(
        self,
        root: Path,
        event_router: EventRouter,
        config: Config | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.event_router = event_router
        self.config = config or Config(root=self.root)
        self._observer = None
        self._poll_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mode: str = "unknown"

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> str:
        """Start watching. Returns the actual mode used (``watchdog`` or ``polling``)."""
        try:
            loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
        self._loop = loop

        mode = self.config.watcher_mode
        if mode == "polling":
            return self._start_polling(loop)
        if mode == "inotify":
            return self._start_watchdog(loop)
        # auto
        try:
            return self._start_watchdog(loop)
        except Exception as exc:
            log.warning("watchdog failed (%s), falling back to polling", exc)
            return self._start_polling(loop)

    def _start_watchdog(self, loop: asyncio.AbstractEventLoop | None) -> str:
        try:
            from watchdog.observers import Observer  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("watchdog not installed") from exc

        handler = _WatchdogHandler(self.root, self.event_router, loop, self.config)  # type: ignore[arg-type]
        observer = Observer()
        observer.schedule(handler, str(self.root), recursive=True)
        observer.start()
        self._observer = observer
        self._mode = "watchdog"
        log.info("watcher: started watchdog on %s", self.root)
        return "watchdog"

    def _start_polling(self, loop: asyncio.AbstractEventLoop | None) -> str:
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_closed():
                        loop = None
                except RuntimeError:
                    loop = None
        self._mode = "polling"
        if loop is not None and loop.is_running():
            self._poll_task = loop.create_task(self._poll_loop())
        log.info("watcher: started polling on %s", self.root)
        return "polling"

    async def start_polling_async(self) -> None:
        """Start polling loop (must be called from running loop)."""
        self._mode = "polling"
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        polling_s = float(self.config.timeouts.get("polling_s", 60))
        # Track mtimes for known files
        mtimes: dict[str, float] = {}
        # Initial scan
        for p in self._collect_nix_files():
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                continue

        while True:
            await asyncio.sleep(polling_s)
            current_files = set(str(p) for p in self._collect_nix_files())
            # Check for new or modified
            for f in current_files:
                try:
                    mtime = Path(f).stat().st_mtime
                except OSError:
                    continue
                old = mtimes.get(f)
                if old is None or mtime != old:
                    mtimes[f] = mtime
                    self.event_router.on_file_event(f)
                # For thread-safety, if called from async loop, on_file_event will schedule correctly
            # Check for deleted
            for f in list(mtimes.keys()):
                if f not in current_files:
                    mtimes.pop(f, None)
                    self.event_router.on_file_event(f)

    def _collect_nix_files(self) -> list[Path]:
        # Reuse collect_files but without graph/config filtering for watcher?
        # We want to watch all .nix files, not just filtered ones, because
        # a file that becomes relevant via Nix-first (e.g. appears in .config)
        # should still be detected.  So we collect all .nix.
        found: list[Path] = []
        stack = [self.root]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except (PermissionError, FileNotFoundError):
                continue
            for entry in entries:
                name = entry.name
                if name in SKIP_DIRS:
                    continue
                if name.startswith(".") and name != ".config":
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file() and entry.suffix == ".nix":
                    found.append(entry)
        return found

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass
            self._observer = None
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None
        log.info("watcher: stopped (%s)", self._mode)

    @property
    def mode(self) -> str:
        return self._mode
