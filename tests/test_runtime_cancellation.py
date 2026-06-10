"""Regression tests for executor cancellation propagation and bounded staleness.

These tests cover rq-daemon-cancel01 / rq-daemon-cancel01.3 from the
lgrepDaemonOperationalSafety spec. They lock the contract that the bounded
executor releases worker slots when awaiting asyncio coroutines are cancelled
(8s tool timeout) instead of permanently holding them with abandoned
LanceDB-bound index_all threads.

Each test is designed to fail against the pre-fix code and pass after the
fix. See fixLgrepPoolWedgeAbandoned change artifacts.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from lgrep.indexing import Indexer
from lgrep.storage import ChunkStore
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.server.tools_semantic import _check_staleness


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_embedder():
    """Create a mock VoyageEmbedder that returns deterministic embeddings."""
    embedder = MagicMock()

    def side_effect(texts, **kwargs):
        from lgrep.embeddings import EmbeddingResult

        return EmbeddingResult(
            embeddings=[[0.1] * 1024 for _ in texts],
            token_usage=len(texts) * 10,
            model="voyage-code-3",
        )

    embedder.embed_documents.side_effect = side_effect
    return embedder


@pytest.fixture
def mock_storage():
    """Create a mock ChunkStore that records calls but does not write."""
    return MagicMock(spec=ChunkStore)


@pytest.fixture
def tmp_project(tmp_path, mock_storage, mock_embedder):
    """Build a tiny project with N files and an Indexer bound to mocks."""
    for i in range(20):
        (tmp_path / f"file_{i:02d}.py").write_text(f"def f{i}(): pass\n")
    indexer = Indexer(
        project_path=tmp_path,
        storage=mock_storage,
        embedder=mock_embedder,
        chunk_size=500,
    )
    return tmp_path, indexer


# ---------------------------------------------------------------------------
# AC1: index_all honors a cancel_event
# ---------------------------------------------------------------------------


def test_index_all_raises_on_cancel_event(tmp_project):
    """Indexer.index_all(cancel_event=...) must raise OperationCancelled
    within one file-iteration after cancel_event.set() is called, even when
    the underlying index_file call is currently blocked on LanceDB I/O.

    Pre-condition: cancel_event is set BEFORE index_all starts. The check
    at the top of the per-file loop must fire on the first iteration.
    """
    _project_root, indexer = tmp_project

    cancel_event = threading.Event()
    cancel_event.set()  # Set the event immediately — no embedding calls.

    from lgrep.indexing import OperationCancelled

    with pytest.raises(OperationCancelled):
        indexer.index_all(cancel_event=cancel_event)


def test_index_all_raises_on_mid_loop_cancel(tmp_project):
    """Indexer.index_all(cancel_event=...) must raise OperationCancelled
    AFTER a few files have been processed, by setting the event from
    another thread partway through the loop. The next iteration must exit.
    """
    _project_root, indexer = tmp_project

    # Count how many times index_file is invoked.
    call_count = {"n": 0}
    original_index_file = indexer.index_file

    def counting_index_file(file_path):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Set the event during the second file's index_file call.
            cancel_event.set()
        return original_index_file(file_path)

    indexer.index_file = counting_index_file

    cancel_event = threading.Event()

    from lgrep.indexing import OperationCancelled

    with pytest.raises(OperationCancelled):
        indexer.index_all(cancel_event=cancel_event)

    # The check fires on the iteration AFTER cancel_event.set() is observed.
    # The monkey-patch sets it during the 2nd file's index_file, so at most
    # 3 calls to index_file happen before the loop exits.
    assert call_count["n"] <= 3, f"Too many file iterations: {call_count['n']}"


# ---------------------------------------------------------------------------
# AC2: run_blocking sets cancel_event when its coroutine is cancelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_blocking_sets_cancel_event_on_cancellation():
    """RuntimeSupervisor.run_blocking must call cancel_event.set() before
    the awaiting asyncio coroutine is cancelled, so the blocking work can
    unwind.
    """
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)
    started = threading.Event()
    cancel_event = threading.Event()

    def slow_work(**_kwargs) -> str:
        started.set()
        # Sleep well past the cancel — but we expect the event to be set
        # within a few hundred ms so we wake up before the 2s sleep ends.
        for _ in range(20):
            time.sleep(0.05)
            if cancel_event.is_set():
                return "cancelled"
        return "finished"

    async def call_and_cancel():
        coro = asyncio.create_task(
            supervisor.run_blocking(
                kind="index",
                caller="test",
                project="/tmp/p",
                fn=slow_work,
                cancel_event=cancel_event,
            )
        )
        # Wait for the blocking work to start
        await asyncio.get_event_loop().run_in_executor(None, started.wait, 2.0)
        # Give the work a moment to enter its poll loop
        await asyncio.sleep(0.05)
        coro.cancel()
        try:
            await coro
        except (asyncio.CancelledError, RuntimeError):
            pass

    await asyncio.wait_for(call_and_cancel(), timeout=5.0)

    # The cancel event MUST be set by the time the supervisor reports
    # the abandonment. Poll briefly because set() happens in the except
    # handler of the awaiting coroutine.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not cancel_event.is_set():
        time.sleep(0.01)
    assert cancel_event.is_set(), "cancel_event was not set when run_blocking was cancelled"

    supervisor.shutdown(cancel_futures=True)


# ---------------------------------------------------------------------------
# AC3: _check_staleness honors a wall-clock deadline
# ---------------------------------------------------------------------------


def test_check_staleness_deadline_returns_fresh(tmp_path, monkeypatch):
    """_check_staleness must return (stale=False, 0) when its wall-clock
    duration exceeds LGREP_STALENESS_DEADLINE_S, and emit a structured
    staleness_check_deadline_exceeded log line.
    """
    # Force a tiny deadline so the test runs fast.
    monkeypatch.setenv("LGREP_STALENESS_DEADLINE_S", "0.05")

    # Build a project with many files and a stub state that simulates
    # a slow staleness walk.
    from lgrep.server.tools_semantic import ProjectState

    state = MagicMock(spec=ProjectState)
    indexer = MagicMock()
    indexer.project_path = str(tmp_path)
    indexer.discovery = MagicMock()

    def slow_find_files():
        # Sleep long enough to exceed the 0.05s deadline.
        time.sleep(0.3)
        return [tmp_path / "x.py"]

    indexer.discovery.find_files.side_effect = slow_find_files
    state.indexer = indexer
    state.latest_indexed_at = 0.0
    state.db.get_latest_indexed_at.return_value = 0.0
    state.db.get_indexed_files.return_value = set()

    captured: dict = {}
    import structlog

    class _Capture:
        def info(self, event, **kw):
            captured.setdefault("info", []).append((event, kw))

        def warning(self, event, **kw):
            captured.setdefault("warning", []).append((event, kw))

        def debug(self, event, **kw):
            captured.setdefault("debug", []).append((event, kw))

    # Patch the logger used by tools_semantic to capture deadline logs.
    import lgrep.server.tools_semantic as ts_mod

    monkeypatch.setattr(ts_mod, "log", _Capture())

    stale, count = _check_staleness(state)

    assert stale is False, f"Expected stale=False on deadline, got {stale}"
    assert count == 0, f"Expected count=0 on deadline, got {count}"
    warnings = captured.get("warning", [])
    assert any("staleness_check_deadline_exceeded" in e for e, _ in warnings), (
        f"Expected staleness_check_deadline_exceeded log, got {warnings}"
    )
