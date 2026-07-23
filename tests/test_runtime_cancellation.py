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
import contextlib
import threading
import time
from unittest.mock import MagicMock

import pytest

from lgrep.indexing import Indexer
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.server.tools_semantic import _check_staleness
from lgrep.storage import ChunkStore

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

    def counting_index_file(file_path, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Set the event during the second file's index_file call.
            cancel_event.set()
        return original_index_file(file_path, **kwargs)

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
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await coro

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

    # Build a state with explicit db mock. We use a plain MagicMock (no spec)
    # so we can freely populate the fields the staleness check accesses.
    state = MagicMock()
    state.latest_indexed_at = 0.0
    state.db.get_latest_indexed_at.return_value = 0.0
    state.db.get_indexed_files.return_value = set()

    # Build a slow find_files that exceeds the 0.05s deadline.
    indexer = MagicMock()
    indexer.project_path = str(tmp_path)

    def slow_find_files():
        time.sleep(0.3)  # exceeds 0.05s deadline
        return [tmp_path / "x.py"]

    indexer.discovery.find_files.side_effect = slow_find_files
    state.indexer = indexer

    # Patch the logger used by tools_semantic to capture deadline logs.
    captured: dict = {}

    class _Capture:
        def info(self, event, **kw):
            captured.setdefault("info", []).append((event, kw))

        def warning(self, event, **kw):
            captured.setdefault("warning", []).append((event, kw))

        def debug(self, event, **kw):
            captured.setdefault("debug", []).append((event, kw))

    import lgrep.server.tools_semantic as ts_mod

    monkeypatch.setattr(ts_mod, "log", _Capture())

    stale, count = _check_staleness(state)

    assert stale is False, f"Expected stale=False on deadline, got {stale}"
    assert count == 0, f"Expected count=0 on deadline, got {count}"
    warnings = captured.get("warning", [])
    assert any("staleness_check_deadline_exceeded" in e for e, _ in warnings), (
        f"Expected staleness_check_deadline_exceeded log, got {warnings}"
    )


# ---------------------------------------------------------------------------
# AC8: embed_documents / _embed_batch_with_retry honor a cancel_event
# ---------------------------------------------------------------------------


def _make_real_embedder(monkeypatch):
    """Build a real VoyageEmbedder with a fake voyage client so we can drive
    the batching + retry-backoff code paths without network calls.
    """
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key-not-used")
    from lgrep.embeddings import VoyageEmbedder

    embedder = VoyageEmbedder(api_key="test-key-not-used")
    return embedder


def test_embed_documents_raises_between_batches_on_cancel(monkeypatch):
    """VoyageEmbedder.embed_documents(texts, cancel_event=...) must raise
    OperationCancelled between batches when the event is already set.
    """
    from lgrep.exceptions import OperationCancelled

    embedder = _make_real_embedder(monkeypatch)

    # Fake client.embed returns a trivial successful result so we isolate
    # the cancellation behavior (not retry).
    class _FakeResult:
        def __init__(self, n):
            self.embeddings = [[0.1] * 1024 for _ in range(n)]
            self.total_tokens = n * 10

    embedder.client.embed = lambda texts, model, input_type: _FakeResult(len(texts))

    cancel_event = threading.Event()
    cancel_event.set()  # set before any batch runs

    # Enough texts to form at least one batch.
    texts = [f"chunk {i}" for i in range(10)]

    with pytest.raises(OperationCancelled):
        embedder.embed_documents(texts, cancel_event=cancel_event)


def test_embed_batch_retry_aborts_wait_immediately_on_cancel(monkeypatch):
    """_embed_batch_with_retry, when the cancel_event is set DURING its
    exponential-backoff wait, must abort within the wait granularity (not
    sleep the full backoff delay). This is the dominant wedge contributor:
    up to ~31s of un-cancellable time.sleep in the retry loop.
    """
    from lgrep.exceptions import OperationCancelled

    embedder = _make_real_embedder(monkeypatch)

    cancel_event = threading.Event()

    # Fake client.embed always raises a retryable error so we enter the
    # backoff path. Setting the event from a background thread shortly after
    # the call begins must wake the wait and raise OperationCancelled fast.
    def failing_embed(texts, model, input_type):
        raise RuntimeError("simulated transient voyage error")

    embedder.client.embed = failing_embed

    # Set the event 0.2s after we start, while the retry backoff (BASE_DELAY=1s+)
    # would otherwise be sleeping. A correct implementation aborts ~immediately.
    def set_soon():
        time.sleep(0.2)
        cancel_event.set()

    setter = threading.Thread(target=set_soon, daemon=True)

    start = time.monotonic()
    setter.start()
    with pytest.raises(OperationCancelled):
        embedder._embed_batch_with_retry(
            ["chunk a", "chunk b"], "document", cancel_event=cancel_event
        )
    elapsed = time.monotonic() - start
    setter.join(timeout=1.0)

    # Must abort well before a single full BASE_DELAY (1s) backoff completes,
    # and far before the ~31s worst-case full retry budget. Allow generous
    # slack for scheduling: < 0.9s proves the wait() aborted on the event.
    assert elapsed < 0.9, (
        f"retry backoff did not abort on cancel: took {elapsed:.2f}s "
        "(expected immediate abort via cancel_event.wait)"
    )


# ---------------------------------------------------------------------------
# AC9: index_file honors a cancel_event before embed and before storage
# ---------------------------------------------------------------------------


def test_index_file_raises_before_embed_on_cancel(tmp_project):
    """Indexer.index_file(file_path, cancel_event=...) must raise
    OperationCancelled if the event is set before the embedding step.
    """
    from lgrep.exceptions import OperationCancelled

    project_root, indexer = tmp_project

    cancel_event = threading.Event()
    cancel_event.set()  # set before index_file runs -> must raise before embed

    target = project_root / "file_00.py"

    with pytest.raises(OperationCancelled):
        indexer.index_file(target, cancel_event=cancel_event)

    # Embedding must NOT have been attempted (cancelled before embed step).
    indexer.embedder.embed_documents.assert_not_called()


def test_index_file_raises_before_storage_on_cancel(tmp_project):
    """Indexer.index_file must raise OperationCancelled before the storage
    step when the event is set after embedding begins. We set the event
    inside the embed_documents mock so the post-embed check fires.
    """
    from lgrep.embeddings import EmbeddingResult
    from lgrep.exceptions import OperationCancelled

    project_root, indexer = tmp_project

    cancel_event = threading.Event()

    def embed_then_cancel(texts, **kwargs):
        cancel_event.set()  # simulate cancellation arriving during embed
        return EmbeddingResult(
            embeddings=[[0.1] * 1024 for _ in texts],
            token_usage=len(texts) * 10,
            model="voyage-code-3",
        )

    indexer.embedder.embed_documents.side_effect = embed_then_cancel

    target = project_root / "file_00.py"

    with pytest.raises(OperationCancelled):
        indexer.index_file(target, cancel_event=cancel_event)

    # Storage write must NOT have happened (cancelled before storage step).
    indexer.storage.add_chunks.assert_not_called()


# ---------------------------------------------------------------------------
# AC10: index_all enforces a hard wall-clock backstop
# ---------------------------------------------------------------------------


def test_index_all_raises_on_wall_clock_budget(tmp_project, monkeypatch):
    """Indexer.index_all must raise OperationCancelled once total wall-clock
    exceeds LGREP_INDEX_MAX_WALL_S, independent of cancel_event, as a
    defense-in-depth backstop.
    """
    from lgrep.exceptions import OperationCancelled

    project_root, indexer = tmp_project

    # Tiny budget so the backstop fires quickly.
    monkeypatch.setenv("LGREP_INDEX_MAX_WALL_S", "0.1")

    # Make each index_file slow enough that the wall-clock budget is exceeded
    # within the first couple of files.
    original = indexer.index_file

    def slow_index_file(file_path, **kwargs):
        time.sleep(0.08)
        return original(file_path, **kwargs)

    indexer.index_file = slow_index_file

    with pytest.raises(OperationCancelled):
        indexer.index_all()  # no cancel_event — wall-clock backstop only


# ---------------------------------------------------------------------------
# AC11: background reindex tasks are cancelled and awaited during shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_reindex_cancelled_on_shutdown(tmp_project, monkeypatch):
    """_shutdown must cancel outstanding background reindexes, await them, and
    guarantee the underlying RuntimeJob reaches a terminal status before
    runtime.shutdown() returns.
    """
    from lgrep.embeddings import EmbeddingResult
    from lgrep.server import LgrepContext, ProjectState
    from lgrep.server.lifecycle import _schedule_background_reindex, _shutdown

    project_root, indexer = tmp_project
    project_path = str(project_root.resolve())

    app_ctx = LgrepContext(voyage_api_key="mock-key")

    # Provide a fake embedder so index_all does not hit the network.
    embedder = MagicMock()

    def fake_embed(texts, **kwargs):
        return EmbeddingResult(
            embeddings=[[0.1] * 1024 for _ in texts],
            token_usage=len(texts) * 10,
            model="voyage-code-3",
        )

    embedder.embed_documents.side_effect = fake_embed
    indexer.embedder = embedder

    # Slow index_all that cooperatively exits once cancel_event is set.
    def slow_index_all(cancel_event=None):
        for _ in range(200):
            if cancel_event is not None and cancel_event.is_set():
                from lgrep.exceptions import OperationCancelled

                raise OperationCancelled("cancelled by shutdown")
            time.sleep(0.01)
        return MagicMock(file_count=1, chunk_count=1, duration_ms=10.0)

    indexer.index_all = slow_index_all

    state = ProjectState(db=indexer.storage, indexer=indexer)
    app_ctx.projects[project_path] = state

    # Schedule a background reindex.
    await _schedule_background_reindex(app_ctx, project_path, project_root)

    # Wait until the runtime job has actually been submitted.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not app_ctx.runtime.snapshot_active_jobs():
        await asyncio.sleep(0.01)
    assert app_ctx.runtime.snapshot_active_jobs(), "background job was never submitted"

    # Shutdown must cancel and await the background task.
    await _shutdown(app_ctx)

    assert not app_ctx._bg_reindex_tasks
    recent = app_ctx.runtime.snapshot_recent_jobs()
    terminal_statuses = {
        "finished",
        "failed",
        "cancelled",
        "finished_after_abandon",
        "failed_after_abandon",
    }
    assert any(j["status"] in terminal_statuses for j in recent), recent
