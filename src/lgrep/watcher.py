"""File watcher for lgrep.

Uses watchdog to monitor file changes and trigger incremental re-indexing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from lgrep.chunking import LANGUAGE_MAP

if TYPE_CHECKING:
    from lgrep.indexing import Indexer

# Extensions we index (from LANGUAGE_MAP)
SUPPORTED_EXTENSIONS = frozenset(LANGUAGE_MAP.keys())

log = structlog.get_logger()


class IndexingHandler(FileSystemEventHandler):
    """Handles file system events by triggering re-indexing."""

    def __init__(
        self,
        indexer: Indexer,
        loop: asyncio.AbstractEventLoop,
        debounce_ms: int = 500,
    ) -> None:
        """Initialize the handler.

        Args:
            indexer: Indexer instance to call for re-indexing
            loop: Asyncio event loop to schedule tasks
            debounce_ms: Time to wait before indexing after a change
        """
        self.indexer = indexer
        self.loop = loop
        self.debounce_ms = debounce_ms
        self.pending_files: dict[Path, asyncio.TimerHandle] = {}

    def on_modified(self, event):
        """Handle file modification."""
        if event.is_directory:
            return
        self._schedule_index(Path(event.src_path))

    def on_created(self, event):
        """Handle file creation."""
        if event.is_directory:
            return
        self._schedule_index(Path(event.src_path))

    def on_deleted(self, event):
        """Handle file deletion."""
        if event.is_directory:
            return
        # Remove from index
        path = Path(event.src_path)
        log.info("file_deleted", path=str(path))

        # We need to run this in the loop
        self.loop.call_soon_threadsafe(self._delete_file, path)

    def _schedule_index(self, path: Path):
        """Schedule a file for re-indexing (called from watchdog thread)."""
        self.loop.call_soon_threadsafe(self._debounced_schedule, path)

    def _debounced_schedule(self, path: Path):
        """Schedule a file for re-indexing with debounce (called on loop thread)."""
        # Skip non-code files (images, binaries, .lock, etc.)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            log.debug("watcher_skipped_non_code", path=str(path), suffix=path.suffix)
            return

        # Skip if ignored
        if self.indexer.discovery.is_ignored(path):
            return

        # Cancel existing timer for this file if any
        if path in self.pending_files:
            self.pending_files[path].cancel()

        # Schedule new timer
        handle = self.loop.call_later(
            self.debounce_ms / 1000.0,
            lambda: self.loop.create_task(self._do_index(path)),
        )
        self.pending_files[path] = handle

    async def _do_index(self, path: Path):
        """Perform the actual indexing."""
        if path in self.pending_files:
            del self.pending_files[path]

        log.info("incremental_index_triggered", file=str(path))
        try:
            # We must run indexer.index_file in a thread if it's blocking,
            # but currently it's synchronous. Let's run it in an executor.
            await self.loop.run_in_executor(None, self.indexer.index_file, path)
        except Exception as e:
            log.error("incremental_index_failed", file=str(path), error=str(e))

    def _delete_file(self, path: Path):
        """Delete file from index."""
        try:
            rel_path = str(path.relative_to(self.indexer.project_path))
            self.indexer.storage.delete_by_file(rel_path)
        except Exception as e:
            log.error("delete_from_index_failed", file=str(path), error=str(e))


class FileWatcher:
    """Manages the file system observer for a project."""

    def __init__(
        self,
        indexer: Indexer,
        debounce_ms: int = 500,
    ) -> None:
        """Initialize the watcher.

        Args:
            indexer: Indexer instance
            debounce_ms: Debounce time for changes
        """
        self.indexer = indexer
        self.debounce_ms = debounce_ms
        self._observer: Observer | None = None
        self.handler: IndexingHandler | None = None
        self._running = False

    def start(self):
        """Start the observer. Safe to call after stop()."""
        if self._running:
            return

        loop = asyncio.get_running_loop()
        self.handler = IndexingHandler(
            indexer=self.indexer,
            loop=loop,
            debounce_ms=self.debounce_ms,
        )

        # Create a fresh Observer each time so start/stop is restartable
        self._observer = Observer()
        self._observer.schedule(
            self.handler,
            str(self.indexer.project_path),
            recursive=True,
        )
        self._observer.start()
        self._running = True
        log.info("watcher_started", path=str(self.indexer.project_path))

    def stop(self):
        """Stop the observer."""
        if not self._running or not self._observer:
            return

        self._observer.stop()
        self._observer.join()
        self._observer = None
        self._running = False
        log.info("watcher_stopped")
