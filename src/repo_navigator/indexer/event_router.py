"""Event router: debounces filesystem events into batches."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class EventRouter:
    """Batches filesystem events with debounce.

    - ``on_file_event(path)`` — called for each watchdog event; resets the
      debounce timer.  After ``debounce_ms`` of quiet, the accumulated
      ``pending`` set is flushed as a single batch into ``queue``.
    - ``on_git_hook(changed_files)`` — bulk path, bypasses debounce and
      pushes immediately.  If ``>50`` files, sets ``sync_in_progress``.
    """

    def __init__(
        self,
        debounce_ms: float = 500,
        queue: asyncio.Queue[list[str]] | None = None,
    ) -> None:
        self.debounce_ms = debounce_ms
        self.queue: asyncio.Queue[list[str]] = queue or asyncio.Queue()
        self._pending: set[str] = set()
        self._debounce_task: asyncio.Task | None = None
        self.sync_in_progress: bool = False

    # ------------------------------------------------------------------ file

    def on_file_event(self, path: str | Path) -> None:
        """Called from watcher thread (or event loop) for a single path.

        Thread-safe: may be called from the watchdog observer thread.
        """
        path_str = str(path)
        self._pending.add(path_str)
        self._reset_debounce_timer()

    async def on_file_event_async(self, path: str | Path) -> None:
        """Async variant for callers already in the event loop."""
        self._pending.add(str(path))
        self._reset_debounce_timer()

    def on_file_event_threadsafe(self, path: str | Path, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Thread-safe entry point used by the watcher."""
        path_str = str(path)
        self._pending.add(path_str)
        # Try to schedule debounce on the provided loop or the running loop
        target_loop = loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    target_loop = asyncio.get_event_loop()
                except RuntimeError:
                    target_loop = None
        if target_loop is not None and target_loop.is_running():
            # Schedule reset on the loop thread
            target_loop.call_soon_threadsafe(self._reset_debounce_timer_threadsafe)
        else:
            # No loop yet, will be flushed when loop starts or on next async call
            pass

    def _reset_debounce_timer_threadsafe(self) -> None:
        # This runs inside the event loop thread (via call_soon_threadsafe)
        self._reset_debounce_timer()

    def _reset_debounce_timer(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed() or not loop.is_running():
                    return
            except RuntimeError:
                return

        # Cancel previous debounce
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()

        # Schedule new debounce
        self._debounce_task = loop.create_task(self._debounce_sleep())

    async def _debounce_sleep(self) -> None:
        try:
            await asyncio.sleep(self.debounce_ms / 1000)
            await self._flush_pending()
        except asyncio.CancelledError:
            pass

    async def _flush_pending(self) -> None:
        if not self._pending:
            return
        batch = sorted(self._pending)
        self._pending.clear()
        self._debounce_task = None
        await self.queue.put(batch)
        log.debug("event_router: flushed batch %s", batch)

    # For tests: allow awaiting flush directly
    async def flush(self) -> None:
        """Force flush pending immediately (for tests)."""
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            self._debounce_task = None
        await self._flush_pending()

    # ------------------------------------------------------------------ git

    async def on_git_hook(self, changed_files: list[str]) -> None:
        """Bulk git hook: immediate, no debounce."""
        if not changed_files:
            return
        if len(changed_files) > 50:
            self.sync_in_progress = True
            log.info("event_router: bulk git hook %d files, sync_in_progress=True", len(changed_files))
        # Cancel any pending debounce (these files will be in bulk batch anyway)
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            self._debounce_task = None
        # Include pending as well? For bulk, we flush pending together?
        # We put pending + changed_files together
        batch = sorted(set(self._pending) | set(changed_files))
        self._pending.clear()
        await self.queue.put(batch)
        log.debug("event_router: git hook batch %s", batch)

    def on_git_hook_sync(self, changed_files: list[str]) -> None:
        """Sync version for non-async callers (schedules put)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.on_git_hook(changed_files))
        except RuntimeError:
            # No loop: try to put directly via queue (may be called from thread)
            # We need a loop to put, so try get_event_loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.on_git_hook(changed_files), loop)
                else:
                    # Fallback: if queue is not async, just put via put_nowait
                    # But queue is async, so we need loop.  For tests without loop,
                    # we can directly put into queue if it's already created?
                    # As fallback, store for later
                    self._pending.update(changed_files)
            except RuntimeError:
                self._pending.update(changed_files)
