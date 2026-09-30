"""Foreground base-window budget for worktree searches (LGREP-25).

``_base_index_ready(wait=False)`` runs the staleness check plus at most one
base index window inside ``LGREP_ENSURE_BUDGET_S`` (default 8.0). Beyond the
budget, or when the window does not converge, the remaining base work
continues as the existing single-flight background continuation and the
worktree search answers from the current partial index with the existing
staleness surfacing.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from structlog.testing import capture_logs

from lgrep.exceptions import OperationCancelled
from lgrep.server.lifecycle import LgrepContext, ProjectState, _base_index_ready


class _FakeDb:
    """ChunkStore surface used by the staleness pre-flight."""

    def __init__(self):
        self.latest = 0.0

    def needs_full_recheck(self):
        return False

    def get_latest_indexed_at(self):
        return self.latest

    def get_indexed_files(self):
        return set()

    def get_zero_chunk_files(self):
        return set()

    def get_file_hashes(self):
        return {}


class _FakeDiscovery:
    def __init__(self, files):
        self._files = files

    def find_files(self):
        return iter(self._files)


class _FakeIndexer:
    """Indexer stand-in with controllable window behavior.

    Modes:
    - "hangs": the window blocks until its cancel_event is set, then raises
      OperationCancelled like the real indexer does at a file boundary.
    - "converges": the window finishes everything.
    - "partial": the window finishes within budget but work remains.
    """

    def __init__(self, project_path: Path, files: list[Path], mode: str):
        self.project_path = str(project_path)
        self.discovery = _FakeDiscovery(files)
        self._files_rel = [str(f.relative_to(project_path)) for f in files]
        self._mode = mode
        self.windows_started = 0

    def compute_pending_files(self):
        return list(self._files_rel)

    def index_window(self, cancel_event=None, pending_files=None):
        self.windows_started += 1
        if self._mode == "hangs":
            while not (cancel_event and cancel_event.is_set()):
                time.sleep(0.005)
            raise OperationCancelled()
        if self._mode == "converges":
            return SimpleNamespace(
                status=SimpleNamespace(
                    file_count=len(pending_files or []),
                    chunk_count=1,
                    duration_ms=1.0,
                    total_tokens=10,
                ),
                complete=True,
                remaining_files=[],
                indexed_files=list(pending_files or []),
            )
        return SimpleNamespace(
            status=SimpleNamespace(file_count=1, chunk_count=1, duration_ms=1.0, total_tokens=10),
            complete=False,
            remaining_files=self._files_rel[1:],
            indexed_files=self._files_rel[:1],
        )


def _register_base(ctx: LgrepContext, base_path: Path, mode: str) -> ProjectState:
    """Pre-register the base project so ensure takes its fast path."""
    files = [base_path / "a.py", base_path / "b.py"]
    for f in files:
        f.write_text("def a():\n    pass\n", encoding="utf-8")
    state = ProjectState(db=_FakeDb(), indexer=_FakeIndexer(base_path, files, mode), base_path=None)
    ctx.projects[str(base_path)] = state
    return state


async def _cleanup(ctx: LgrepContext) -> None:
    """Cancel this test's tasks and wait for its executor jobs to finish.

    A budget-abandoned job keeps running in its worker thread and logs
    ``runtime_job_finished`` when it ends. Returning before that lets the
    event leak into a later test's captured logs.
    """
    for task in list(ctx._bg_reindex_tasks.values()):
        task.cancel()
    if ctx._bg_reindex_tasks:
        await asyncio.gather(*ctx._bg_reindex_tasks.values(), return_exceptions=True)
    deadline = time.monotonic() + 5.0
    while ctx.runtime.snapshot_active_jobs():
        assert time.monotonic() < deadline, "test-owned executor jobs never finished"
        await asyncio.sleep(0.01)
    ctx.runtime.shutdown(cancel_futures=True)


async def test_budget_exceeded_defers_base_window_to_background(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "0.1")
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "hangs")

    start = time.monotonic()
    with capture_logs() as logs:
        result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=5)
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0, f"foreground base window ran unbounded ({elapsed:.2f}s)"
    assert str(base) in ctx._bg_reindex_tasks, "no background continuation scheduled"
    assert state.pending_index_files, "remaining base work was not preserved"
    assert any(e["event"] == "base_window_budget_exceeded" for e in logs)
    await _cleanup(ctx)


async def test_window_converging_within_budget_returns_true(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "5.0")
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "converges")

    result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=10)

    assert result is True
    assert state.pending_index_files is None
    assert str(base) not in ctx._bg_reindex_tasks
    assert state.indexer.windows_started == 1
    await _cleanup(ctx)


async def test_non_converging_window_schedules_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "5.0")
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "partial")

    result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=10)

    assert result is False
    assert state.pending_index_files == ["b.py"]
    assert str(base) in ctx._bg_reindex_tasks
    assert state.indexer.windows_started >= 1
    await _cleanup(ctx)


async def test_fresh_base_skips_window(tmp_path, monkeypatch):
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "converges")
    state.db.latest = time.time() + 3600  # index newer than every file mtime
    state.db.get_indexed_files = lambda: {"a.py", "b.py"}

    result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=10)

    assert result is True
    assert state.indexer.windows_started == 0
    await _cleanup(ctx)


async def test_zero_budget_goes_straight_to_background(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "0")
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "converges")

    result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=5)

    assert result is False
    assert str(base) in ctx._bg_reindex_tasks
    assert state.indexer.windows_started == 0
    await _cleanup(ctx)


async def test_slow_staleness_check_is_bounded_by_budget(tmp_path, monkeypatch):
    """The budget covers the staleness check, not just the index window."""
    monkeypatch.setenv("LGREP_ENSURE_BUDGET_S", "0.05")
    base = tmp_path / "trunk"
    base.mkdir()
    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    state = _register_base(ctx, base, "converges")

    def slow_fresh_check(state_arg):
        time.sleep(0.3)
        return False, 0

    monkeypatch.setattr("lgrep.server.lifecycle._check_staleness", slow_fresh_check)

    start = time.monotonic()
    with capture_logs() as logs:
        result = await asyncio.wait_for(_base_index_ready(ctx, str(base), wait=False), timeout=5)
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 0.2, f"staleness check escaped the budget ({elapsed:.2f}s)"
    assert str(base) in ctx._bg_reindex_tasks, "no background continuation scheduled"
    assert any(e["event"] == "base_window_budget_exceeded" for e in logs)
    assert state.indexer.windows_started == 0
    await _cleanup(ctx)
