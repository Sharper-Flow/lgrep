"""Off-loop ensure: store assembly runs as one ``ensure_store`` job.

LGREP-25: ChunkStore construction, overlay init, project-meta write, Indexer
construction, and the forced first table touch are synchronous LanceDB/disk
I/O. They run inside a single ``run_blocking`` job (kind ``ensure_store``) so
the event loop keeps servicing concurrent callers while a slow cache opens;
the app lock stays on the loop.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from structlog.testing import capture_logs

from lgrep.server.lifecycle import (
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
)


def _make_slow_store(opened: threading.Event, release: threading.Event, counter: list):
    """ChunkStore stand-in whose construction blocks until released."""

    class SlowStore:
        constructions = 0

        def __init__(self, db_path, project_path=None):
            counter.append(1)
            self.db_path = db_path
            self.project_path = project_path
            opened.set()
            release.wait(timeout=10)

        def for_checkout(self, checkout):
            return self

        @property
        def table(self):
            return object()

    return SlowStore


def _job_events(logs: list[dict], project) -> list[dict]:
    # Scope to this test's project: an abandoned job from an earlier test can
    # finish and log while this test captures.
    return [
        entry
        for entry in logs
        if entry["event"] == "runtime_job_finished" and entry.get("project") == str(project)
    ]


async def _wait_for_event(event: threading.Event, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        if loop.time() > deadline:
            raise AssertionError("store construction never started")
        await asyncio.sleep(0.001)


async def test_slow_store_open_keeps_loop_responsive(tmp_path, monkeypatch):
    opened, release = threading.Event(), threading.Event()
    monkeypatch.setattr("lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, []))
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()  # skip VoyageEmbedder construction; irrelevant here

    with capture_logs() as logs:
        ensure_task = asyncio.create_task(_ensure_project_initialized(ctx, project))
        await _wait_for_event(opened)

        # While the store opens inside the worker thread, the loop must keep
        # servicing other work. A loop frozen by the store open yields no ticks.
        ticks = 0
        end = time.monotonic() + 0.15
        while time.monotonic() < end:
            ticks += 1
            await asyncio.sleep(0.001)

        release.set()
        result = await asyncio.wait_for(ensure_task, timeout=5)

    assert ticks >= 10, "event loop was blocked while the store opened"
    assert isinstance(result, ProjectState)
    assert str(project) in ctx.projects
    assert len(ctx._stores) == 1
    events = _job_events(logs, project)
    assert [e["kind"] for e in events] == ["ensure_store", "ensure_checkout"]
    assert events[0]["lane"] == "query"
    ctx.runtime.shutdown(cancel_futures=True)


async def test_ensure_store_real_assembly_registers_store_and_opens_table(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    with capture_logs() as logs:
        result = await asyncio.wait_for(_ensure_project_initialized(ctx, project), timeout=60)

    assert isinstance(result, ProjectState)
    assert str(project) in ctx.projects
    assert len(ctx._stores) == 1
    assert result.db._table is not None, "first table touch did not happen during ensure"
    kinds = [e["kind"] for e in _job_events(logs, project)]
    assert kinds == ["ensure_store", "ensure_checkout"]
    ctx.runtime.shutdown(cancel_futures=True)


async def test_ensure_store_failure_registers_nothing(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr("lgrep.server.lifecycle.ChunkStore", explode)
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    result = await asyncio.wait_for(_ensure_project_initialized(ctx, project), timeout=5)

    assert isinstance(result, dict)
    assert "error" in result
    assert ctx.projects == {}
    assert ctx._stores == {}
    ctx.runtime.shutdown(cancel_futures=True)


async def test_cancelled_ensure_detaches_promptly_and_assembly_publishes(tmp_path, monkeypatch):
    """A cancelled caller returns at once; the shared assembly still publishes."""
    opened, release = threading.Event(), threading.Event()
    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, constructions)
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    first = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(opened)
    first.cancel()
    start = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, timeout=5)
    assert time.monotonic() - start < 0.5, "cancelled caller waited for the assembly"

    release.set()
    deadline = time.monotonic() + 5
    while not ctx._stores and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(ctx._stores) == 1, "detached assembly never published the store"
    assert ctx._store_assemblies == {}
    ctx.runtime.shutdown(cancel_futures=True)


async def test_retry_during_assembly_joins_instead_of_duplicating(tmp_path, monkeypatch):
    """A retry for the same owner joins the in-flight assembly; one store."""
    opened, release = threading.Event(), threading.Event()
    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, constructions)
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    first = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(opened)
    first.cancel()
    # Assembly still running: a retry must join it, not start a second one.
    second = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await asyncio.sleep(0.1)
    assert len(constructions) == 1, "retry constructed a second store concurrently"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, timeout=5)
    result = await asyncio.wait_for(second, timeout=5)

    assert isinstance(result, ProjectState)
    assert len(constructions) == 1
    assert len(ctx._stores) == 1
    ctx.runtime.shutdown(cancel_futures=True)


async def test_checkout_assembly_stays_off_loop(tmp_path, monkeypatch):
    """Overlay init, the alias meta write, and Indexer run off the loop."""
    import lgrep.server.lifecycle as lifecycle

    blocked = threading.Event()
    release = threading.Event()

    class SlowIndexer:
        def __init__(self, *args, **kwargs):
            blocked.set()
            release.wait(timeout=10)

    monkeypatch.setattr(lifecycle, "Indexer", SlowIndexer)
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 20)
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    ensure_task = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(blocked)

    ticks = 0
    end = time.monotonic() + 0.15
    while time.monotonic() < end:
        ticks += 1
        await asyncio.sleep(0.001)

    release.set()
    result = await asyncio.wait_for(ensure_task, timeout=5)

    assert ticks >= 10, "event loop was blocked during checkout assembly"
    assert isinstance(result, ProjectState)


async def test_repeated_cancellation_keeps_owner_single_flight(tmp_path, monkeypatch):
    """A second cancellation still cannot break the owner assembly."""
    opened, release = threading.Event(), threading.Event()
    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, constructions)
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    first = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(opened)
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)

    retry = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await asyncio.sleep(0.1)
    assert len(constructions) == 1, "repeated cancellation broke owner single-flight"
    assert ctx._store_assemblies, "registry dropped the in-flight assembly early"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, timeout=5)
    result = await asyncio.wait_for(retry, timeout=5)

    assert isinstance(result, ProjectState)
    assert len(constructions) == 1
    ctx.runtime.shutdown(cancel_futures=True)


async def test_concurrent_owners_obey_project_limit(tmp_path, monkeypatch):
    """Admission counts in-flight assemblies, so concurrent owners cannot bypass the cap."""
    import lgrep.server.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    opened, release = threading.Event(), threading.Event()
    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, constructions)
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    proj_a = tmp_path / "a"
    proj_a.mkdir()
    proj_b = tmp_path / "b"
    proj_b.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    first = asyncio.create_task(_ensure_project_initialized(ctx, proj_a))
    await _wait_for_event(opened)

    second = asyncio.create_task(_ensure_project_initialized(ctx, proj_b))
    result_b = await asyncio.wait_for(second, timeout=5)

    release.set()
    result_a = await asyncio.wait_for(first, timeout=5)

    assert isinstance(result_a, ProjectState)
    assert isinstance(result_b, dict) and "Maximum project limit" in result_b["error"]
    assert len(ctx._stores) == 1
    ctx.runtime.shutdown(cancel_futures=True)


async def test_shutdown_cancels_and_reconciles_in_flight_assemblies(tmp_path, monkeypatch):
    """Shutdown owns outstanding assemblies; nothing publishes after teardown."""
    from lgrep.server.lifecycle import _shutdown

    opened, release = threading.Event(), threading.Event()
    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release, constructions)
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    ensure_task = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(opened)
    ensure_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(ensure_task, timeout=5)
    assert ctx._store_assemblies, "no in-flight assembly to reconcile"

    await _shutdown(ctx)

    assert ctx._store_assemblies == {}
    assert ctx._stores == {}

    release.set()
    await asyncio.sleep(0.1)
    assert ctx._stores == {}, "store published after shutdown returned"


async def test_shutdown_refuses_checkout_published_after_teardown(tmp_path, monkeypatch):
    """A checkout assembly still running at shutdown never publishes its state."""
    import lgrep.server.lifecycle as lifecycle
    from lgrep.server.lifecycle import _shutdown

    blocked = threading.Event()
    release = threading.Event()

    class SlowIndexer:
        def __init__(self, *args, **kwargs):
            blocked.set()
            release.wait(timeout=10)

    monkeypatch.setattr(lifecycle, "Indexer", SlowIndexer)
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()

    ensure_task = asyncio.create_task(_ensure_project_initialized(ctx, project))
    await _wait_for_event(blocked)

    await _shutdown(ctx)
    assert ctx.projects == {}

    release.set()
    result = await asyncio.wait_for(ensure_task, timeout=5)

    assert isinstance(result, dict), "checkout assembly published after shutdown"
    assert ctx.projects == {}, "checkout published a ProjectState after shutdown completed"
    assert ctx._stores == {}


async def test_closed_context_admits_no_new_assembly(tmp_path, monkeypatch):
    """After shutdown, ensure refuses instead of assembling a new store."""
    from lgrep.server.lifecycle import _shutdown

    constructions: list = []
    monkeypatch.setattr(
        "lgrep.server.lifecycle.ChunkStore",
        _make_slow_store(threading.Event(), _released(), constructions),
    )
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    project = tmp_path / "proj"
    project.mkdir()

    ctx = LgrepContext(voyage_api_key="k", transport="stdio")
    ctx.embedder = object()
    await _shutdown(ctx)

    result = await asyncio.wait_for(_ensure_project_initialized(ctx, project), timeout=5)

    assert isinstance(result, dict)
    assert constructions == []
    assert ctx._stores == {} and ctx._store_assemblies == {}


def _released() -> threading.Event:
    event = threading.Event()
    event.set()
    return event
