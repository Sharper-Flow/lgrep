"""Tests for file watcher."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from watchdog.events import FileModifiedEvent

from lgrep.watcher import IndexingHandler


class TestFileWatcher:
    """Tests for FileWatcher and IndexingHandler."""

    @pytest.mark.asyncio
    async def test_handler_schedules_index(self):
        """Should schedule re-indexing on file modification."""
        indexer = MagicMock()
        indexer.discovery.is_ignored.return_value = False
        indexer.project_path = Path("/project")

        loop = asyncio.get_running_loop()
        handler = IndexingHandler(indexer, loop, debounce_ms=10)

        # Trigger modification
        event = FileModifiedEvent("/project/test.py")
        handler.on_modified(event)

        # Give loop time to process call_soon_threadsafe
        await asyncio.sleep(0.01)

        assert Path("/project/test.py") in handler.pending_files

        # Wait for debounce
        await asyncio.sleep(0.05)

        # Verify indexer.index_file was called (in executor)
        assert indexer.index_file.called

    @pytest.mark.asyncio
    async def test_handler_respects_ignore(self):
        """Should not schedule re-indexing for ignored files."""
        indexer = MagicMock()
        indexer.discovery.is_ignored.return_value = True

        loop = asyncio.get_running_loop()
        handler = IndexingHandler(indexer, loop, debounce_ms=10)

        event = FileModifiedEvent("/project/ignored.py")
        handler.on_modified(event)

        # Give loop time to process call_soon_threadsafe
        await asyncio.sleep(0.01)

        assert Path("/project/ignored.py") not in handler.pending_files

    @pytest.mark.asyncio
    async def test_handler_debounce(self):
        """Should debounce multiple rapid changes to same file."""
        indexer = MagicMock()
        indexer.discovery.is_ignored.return_value = False

        loop = asyncio.get_running_loop()
        handler = IndexingHandler(indexer, loop, debounce_ms=50)

        path = Path("/project/test.py")
        event = FileModifiedEvent(str(path))

        handler.on_modified(event)
        await asyncio.sleep(0.01)  # Process threadsafe call
        handle1 = handler.pending_files[path]

        await asyncio.sleep(0.01)

        handler.on_modified(event)
        await asyncio.sleep(0.01)  # Process threadsafe call
        handle2 = handler.pending_files[path]

        assert handle1 != handle2

        await asyncio.sleep(0.1)
        assert indexer.index_file.call_count == 1

    @pytest.mark.asyncio
    async def test_handler_skips_non_code_files(self):
        """Should not schedule re-indexing for non-code files (images, binaries, .lock)."""
        indexer = MagicMock()
        indexer.discovery.is_ignored.return_value = False

        loop = asyncio.get_running_loop()
        handler = IndexingHandler(indexer, loop, debounce_ms=10)

        # Non-code file types should be skipped
        for ext in [".png", ".jpg", ".gif", ".lock", ".bin", ".exe", ".pdf"]:
            event = FileModifiedEvent(f"/project/file{ext}")
            handler.on_modified(event)

        await asyncio.sleep(0.01)

        assert len(handler.pending_files) == 0

    @pytest.mark.asyncio
    async def test_handler_accepts_code_files(self):
        """Should schedule re-indexing for supported code files."""
        indexer = MagicMock()
        indexer.discovery.is_ignored.return_value = False

        loop = asyncio.get_running_loop()
        handler = IndexingHandler(indexer, loop, debounce_ms=100)

        # Code files should be accepted
        for ext in [".py", ".ts", ".js", ".rs", ".go"]:
            event = FileModifiedEvent(f"/project/file{ext}")
            handler.on_modified(event)

        await asyncio.sleep(0.01)

        assert len(handler.pending_files) == 5
