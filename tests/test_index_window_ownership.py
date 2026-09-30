"""Single-flight index ownership outlives a cancelled or budget-detached caller.

A window's storage writes (delete then add per file) have no cancellation
point between them. If the project's single-flight ownership were released
while an abandoned window still ran, a second window could interleave with
those writes and leave duplicate rows. These tests use the real Indexer
window loop, lifecycle coordination, and a two-thread build lane, with
deterministic storage, chunking, and embedding seams.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

from lgrep.indexing import Indexer
from lgrep.server import lifecycle
from lgrep.server.runtime import RuntimeSupervisor

if TYPE_CHECKING:
    from pathlib import Path


class _BlockingStorage:
    """Storage whose first delete blocks until released; rows record adds."""

    def __init__(self) -> None:
        self.first_delete = threading.Event()
        self.release_first = threading.Event()
        self.rows: list[str] = []
        self.deletes = 0

    def get_file_hash(self, path):
        return None

    def adopt_base_version(self, path, file_hash):
        return False

    def delete_by_file(self, path):
        self.deletes += 1
        self.rows.clear()
        if self.deletes == 1:
            self.first_delete.set()
            assert self.release_first.wait(5), "test never released the first delete"

    def add_chunks(self, chunks):
        self.rows.extend(chunks)

    def remove_zero_chunk_file(self, path):
        pass

    def prepare_hybrid_indexes(self):
        pass

    def get_latest_indexed_at(self):
        return 1.0


def _indexer(project: Path, storage: _BlockingStorage) -> Indexer:
    indexer = Indexer.__new__(Indexer)
    indexer.project_path = project
    indexer.storage = storage
    indexer._perf_counter = time.perf_counter
    indexer._compute_file_hash = lambda *args: "hash"
    indexer.chunker = SimpleNamespace(
        chunk_file=lambda path: SimpleNamespace(error=None, chunks=[SimpleNamespace(text="c")])
    )
    indexer.embedder = SimpleNamespace(
        embed_documents=lambda *a, **k: SimpleNamespace(
            embeddings=[[0.0]], token_usage=1, model="voyage-code-4"
        )
    )
    indexer._build_code_chunks = lambda *args, **kwargs: ["one-row"]
    return indexer


def _context(tmp_path: Path, storage: _BlockingStorage):
    project = tmp_path / "trunk"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    runtime = RuntimeSupervisor(max_workers=2, max_build_workers=2)
    ctx = lifecycle.LgrepContext(voyage_api_key="unused", runtime=runtime)
    ctx.projects[str(project)] = lifecycle.ProjectState(
        db=storage, indexer=_indexer(project, storage), pending_index_files=["a.py"]
    )
    return ctx, project


async def _wait(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.001)


async def _drain(ctx) -> None:
    await _wait(
        lambda: (
            not ctx._bg_reindex_tasks
            and not ctx._index_owner_releases
            and not ctx._indexing_events
            and not ctx.runtime.snapshot_active_jobs()
        ),
        timeout=5.0,
    )


async def test_budget_detach_keeps_window_ownership(tmp_path, monkeypatch):
    """The budget stops the caller's wait; it never frees the window's ownership."""
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "0.05")
    monkeypatch.setattr(lifecycle, "_check_staleness", lambda state: (True, 1))
    storage = _BlockingStorage()
    ctx, project = _context(tmp_path, storage)
    try:
        start = time.monotonic()
        ready = await asyncio.wait_for(
            lifecycle._base_index_ready(ctx, str(project), wait=False), timeout=2
        )
        assert time.monotonic() - start < 0.5, "caller waited past the budget"
        assert ready is False
        await _wait(storage.first_delete.is_set)

        # The window is still inside storage: ownership must still be held,
        # so a further scheduling request cannot start a second window.
        assert str(project) in ctx._indexing_events
        await lifecycle._schedule_background_reindex(ctx, str(project), project)
        await asyncio.sleep(0.1)
        assert storage.deletes == 1, "a second window entered storage concurrently"

        storage.release_first.set()
        await _drain(ctx)
        assert storage.rows == ["one-row"]
    finally:
        storage.release_first.set()
        await lifecycle._shutdown(ctx)


async def test_cancelled_leader_holds_ownership_until_window_stops(tmp_path):
    """A cancelled foreground leader detaches; its window keeps ownership."""
    storage = _BlockingStorage()
    ctx, project = _context(tmp_path, storage)
    try:
        leader = asyncio.create_task(
            lifecycle._auto_index_project_single_flight(ctx, str(project), project)
        )
        await _wait(storage.first_delete.is_set)
        leader.cancel()
        start = time.monotonic()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(leader, timeout=2)
        assert time.monotonic() - start < 0.5, "cancelled leader waited for its window"

        assert str(project) in ctx._indexing_events, "ownership released before the window stopped"
        await lifecycle._schedule_background_reindex(ctx, str(project), project)
        await asyncio.sleep(0.1)
        assert storage.deletes == 1, "a second window entered storage concurrently"

        storage.release_first.set()
        await _drain(ctx)
        assert storage.rows == ["one-row"]
    finally:
        storage.release_first.set()
        await lifecycle._shutdown(ctx)
