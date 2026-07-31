"""Bounded index-window convergence tests.

These tests cover AC4, AC5, AC6 of fixDeployedLgrepDefects: a repository
larger than one indexing budget must converge through bounded windows,
search must not wait for background continuation, partial indexes must be
hybrid-searchable, and cancellation must release the worker while
preserving pending work.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from lgrep.exceptions import OperationCancelled
from lgrep.indexing import Indexer, IndexWindowResult
from lgrep.server.lifecycle import LgrepContext, ProjectState, _auto_index_project_single_flight
from lgrep.server.tools_semantic import _check_staleness
from lgrep.storage import ChunkStore


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
def tmp_project(tmp_path, mock_storage, mock_embedder, fake_clock):
    """Build a tiny project with N files and an Indexer bound to mocks."""
    for i in range(5):
        (tmp_path / f"file_{i:02d}.py").write_text(f"def f{i}(): pass\n")
    indexer = Indexer(
        project_path=tmp_path,
        storage=mock_storage,
        embedder=mock_embedder,
        chunk_size=500,
        perf_counter=fake_clock,
    )
    return tmp_path, indexer


# ---------------------------------------------------------------------------
# AC4: bounded index windows converge
# ---------------------------------------------------------------------------


def test_index_window_processes_at_least_one_file_per_window(tmp_project, monkeypatch, fake_clock):
    """With a budget smaller than one file, index_window must still process
    at least one file and return the remaining pending paths."""
    project_root, indexer = tmp_project
    monkeypatch.setenv("LGREP_INDEX_MAX_WALL_S", "0.015")

    original_index_file = indexer.index_file

    def slow_index_file(file_path, cancel_event=None):
        fake_clock.advance(0.02)
        return original_index_file(file_path, cancel_event=cancel_event)

    indexer.index_file = slow_index_file

    result = indexer.index_window()

    assert isinstance(result, IndexWindowResult)
    assert result.indexed_files == ["file_00.py"]
    assert result.files_indexed == 1
    assert not result.complete
    assert result.remaining_files == [
        "file_01.py",
        "file_02.py",
        "file_03.py",
        "file_04.py",
    ]


def test_indexer_uses_perf_counter_by_default(tmp_path, mock_storage, mock_embedder, monkeypatch):
    """Production Indexers must retain time.perf_counter when no seam is injected."""
    import lgrep.indexing as indexing_module

    monkeypatch.setattr(indexing_module.time, "perf_counter", lambda: 123.0)

    indexer = Indexer(
        project_path=tmp_path,
        storage=mock_storage,
        embedder=mock_embedder,
    )

    assert indexer._perf_counter() == 123.0


def test_index_window_converges_to_all_files_indexed(tmp_project, monkeypatch, fake_clock):
    """Repeatedly resuming index_window from the remaining pending list must
    eventually index every file, with each individual window bounded by
    LGREP_INDEX_MAX_WALL_S."""
    project_root, indexer = tmp_project
    monkeypatch.setenv("LGREP_INDEX_MAX_WALL_S", "0.015")

    original_index_file = indexer.index_file

    def slow_index_file(file_path, cancel_event=None):
        fake_clock.advance(0.02)
        return original_index_file(file_path, cancel_event=cancel_event)

    indexer.index_file = slow_index_file

    pending = None
    total_indexed = 0
    windows = 0

    while True:
        windows += 1
        result = indexer.index_window(pending_files=pending)
        total_indexed += result.files_indexed
        assert result.files_indexed == 1
        if result.complete:
            break
        pending = result.remaining_files
        assert windows < 20, "index_window did not converge"

    assert total_indexed == 5
    assert result.complete
    assert not result.remaining_files
    assert windows > 1, "multiple bounded windows should have been required"


def test_index_window_prepares_hybrid_indexes_at_boundary(tmp_project, monkeypatch, fake_clock):
    """An incomplete window must call prepare_hybrid_indexes before returning
    so that the partial corpus is usable for hybrid search."""
    _project_root, indexer = tmp_project
    monkeypatch.setenv("LGREP_INDEX_MAX_WALL_S", "0.015")

    original_index_file = indexer.index_file

    def slow_index_file(file_path, cancel_event=None):
        fake_clock.advance(0.02)
        return original_index_file(file_path, cancel_event=cancel_event)

    indexer.index_file = slow_index_file

    indexer.index_window()

    assert indexer.storage.prepare_hybrid_indexes.called


# ---------------------------------------------------------------------------
# AC5: cancellation safety and lifecycle continuation
# ---------------------------------------------------------------------------


def test_index_window_raises_operation_cancelled(tmp_project):
    """index_window must propagate OperationCancelled when cancel_event is set."""
    _project_root, indexer = tmp_project

    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(OperationCancelled):
        indexer.index_window(cancel_event=cancel_event)


@pytest.mark.asyncio
async def test_lifecycle_preserves_pending_on_cancellation(tmp_project, monkeypatch):
    """When a window is cancelled, the lifecycle must store the remaining
    pending file list on ProjectState so a subsequent continuation can resume."""
    project_root, indexer = tmp_project
    project_path = str(project_root.resolve())

    app_ctx = LgrepContext(voyage_api_key="mock-key")
    state = ProjectState(db=indexer.storage, indexer=indexer)
    app_ctx.projects[project_path] = state

    # Make the first (and only) file raise OperationCancelled after recording.
    def cancel_after_one(file_path, cancel_event=None):
        if cancel_event is not None:
            cancel_event.set()
        raise OperationCancelled("cancelled mid-window")

    indexer.index_file = cancel_after_one

    result = await _auto_index_project_single_flight(
        app_ctx, project_path, project_root, continue_until_complete=False
    )

    assert isinstance(result, ProjectState)
    assert result.pending_index_files is not None
    assert len(result.pending_index_files) == 5


@pytest.mark.asyncio
async def test_lifecycle_continuation_converges_without_blocking_search(
    tmp_project, monkeypatch, fake_clock
):
    """A single background continuation task must converge remaining pending
    files while search callers receive the initialized state immediately."""
    project_root, indexer = tmp_project
    project_path = str(project_root.resolve())

    monkeypatch.setenv("LGREP_INDEX_MAX_WALL_S", "0.015")

    app_ctx = LgrepContext(voyage_api_key="mock-key")
    state = ProjectState(db=indexer.storage, indexer=indexer)
    app_ctx.projects[project_path] = state

    original_index_file = indexer.index_file

    def slow_index_file(file_path, cancel_event=None):
        fake_clock.advance(0.02)
        return original_index_file(file_path, cancel_event=cancel_event)

    indexer.index_file = slow_index_file

    # Initial single-flight call: should do one window and schedule a
    # background continuation because continue_until_complete=False.
    result = await _auto_index_project_single_flight(
        app_ctx, project_path, project_root, continue_until_complete=False
    )

    # Search caller gets state back immediately, not waiting for convergence.
    assert isinstance(result, ProjectState)

    # A continuation task should have been scheduled.
    assert project_path in app_ctx._bg_reindex_tasks
    task = app_ctx._bg_reindex_tasks[project_path]

    # Wait for the background continuation to converge.
    await asyncio.wait_for(task, timeout=5.0)

    # After convergence, pending should be cleared.
    final_state = app_ctx.projects[project_path]
    assert final_state.pending_index_files is None


# ---------------------------------------------------------------------------
# AC6: staleness detects never-indexed files
# ---------------------------------------------------------------------------


def test_check_staleness_flags_unindexed_files(tmp_path, monkeypatch):
    """_check_staleness must report stale when current files exist that are
    not present in the indexed file set, so a restarted daemon resumes a
    partial backlog."""
    (tmp_path / "indexed.py").write_text("def indexed(): pass\n")
    (tmp_path / "never_indexed.py").write_text("def never_indexed(): pass\n")

    state = MagicMock()
    state.latest_indexed_at = time.time()
    state.db.get_latest_indexed_at.return_value = state.latest_indexed_at
    state.db.get_indexed_files.return_value = {"indexed.py"}

    indexer = MagicMock()
    indexer.project_path = str(tmp_path)

    # Yield the two current files.
    class FakeDiscovery:
        def find_files(self):
            return [tmp_path / "indexed.py", tmp_path / "never_indexed.py"]

    indexer.discovery = FakeDiscovery()
    state.indexer = indexer

    stale, count = _check_staleness(state)

    assert stale is True
    assert count >= 1
