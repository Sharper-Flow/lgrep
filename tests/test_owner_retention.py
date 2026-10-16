"""Owner admission, activity lifetime and persisted-cache retention."""

import asyncio
import threading
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lgrep.server import lifecycle, time_tool
from lgrep.server.lifecycle import (
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
    _shutdown,
)
from lgrep.storage import ChunkStore
from lgrep.watcher import IndexingHandler
from tests.test_storage import make_chunk


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
    ctx = LgrepContext(voyage_api_key="unused")
    ctx.embedder = object()
    yield ctx
    ctx.runtime.shutdown(cancel_futures=True)


@pytest.fixture
def lightweight(monkeypatch):
    class Store:
        def __init__(self, owner):
            self.owner = owner

    monkeypatch.setattr(lifecycle, "_assemble_owner_store", lambda owner, key: Store(key))
    monkeypatch.setattr(
        lifecycle,
        "_assemble_checkout_state",
        lambda store, checkout, owner, path, embedder: ProjectState(db=store, indexer=object()),
    )


async def test_25_projects_share_one_server_lifetime(context, lightweight, tmp_path):
    for i in range(25):
        result = await _ensure_project_initialized(context, tmp_path / str(i))
        assert isinstance(result, ProjectState), result
        assert len(context._stores) <= 20
    assert len(context.projects) == 20
    assert str(tmp_path / "0") not in context.projects
    await _shutdown(context)


async def test_hit_updates_idle_lru(context, lightweight, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 2)
    a, b, c = [tmp_path / name for name in ("a", "b", "c")]
    first = await _ensure_project_initialized(context, a)
    await _ensure_project_initialized(context, b)
    assert await _ensure_project_initialized(context, a) is first
    assert isinstance(await _ensure_project_initialized(context, c), ProjectState)
    assert str(a) in context.projects
    assert str(b) not in context.projects
    await _shutdown(context)


async def test_async_call_pins_owner_and_capacity_is_retryable(
    context,
    lightweight,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    entered, release = asyncio.Event(), asyncio.Event()

    @time_tool
    async def query():
        state = await _ensure_project_initialized(context, tmp_path / "a")
        entered.set()
        await release.wait()  # An embedding request has no RuntimeJob here.
        return state

    task = asyncio.create_task(query())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        refused = await _ensure_project_initialized(context, tmp_path / "b")
        assert isinstance(refused, dict)
        assert refused["code"] == "project_capacity_busy"
        assert refused["retryable"] is True
        assert "Restart" not in refused["error"]
        release.set()
        await task
        assert isinstance(await _ensure_project_initialized(context, tmp_path / "b"), ProjectState)
    finally:
        release.set()
        await task
        await _shutdown(context)


async def test_evict_idle_peer_during_active_call(context, lightweight, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 2)
    entered, release = asyncio.Event(), asyncio.Event()

    @time_tool
    async def query():
        await _ensure_project_initialized(context, tmp_path / "busy")
        entered.set()
        await release.wait()

    task = asyncio.create_task(query())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await _ensure_project_initialized(context, tmp_path / "idle")
        result = await _ensure_project_initialized(context, tmp_path / "new")
        assert isinstance(result, ProjectState), result
        assert str(tmp_path / "busy") in context.projects
        assert str(tmp_path / "idle") not in context.projects
    finally:
        release.set()
        await task
        await _shutdown(context)


def test_connection_failure_preserves_last_good_cache(tmp_path, monkeypatch):
    sentinel = tmp_path / "last-good"
    sentinel.write_bytes(b"stored vectors")
    failure = PermissionError("transient access failure")
    connect = Mock(side_effect=[failure, object()])
    monkeypatch.setattr("lgrep.storage._chunk_store.lancedb.connect", connect)
    with pytest.raises(PermissionError, match="transient access failure") as error:
        ChunkStore(tmp_path)
    assert error.value is failure
    assert sentinel.read_bytes() == b"stored vectors"
    assert connect.call_count == 1


@pytest.mark.parametrize(
    "error_type", [PermissionError, ValueError, RuntimeError, FileNotFoundError]
)
def test_table_open_failure_preserves_last_good_cache(tmp_path, monkeypatch, error_type):
    (tmp_path / "chunks.lance").mkdir()
    sentinel = tmp_path / "last-good"
    sentinel.write_bytes(b"stored vectors")
    failure = error_type("transient table error")
    connection = SimpleNamespace(
        open_table=Mock(side_effect=failure),
        create_table=Mock(),
        drop_table=Mock(),
    )
    monkeypatch.setattr("lgrep.storage._chunk_store.lancedb.connect", lambda _: connection)
    store = ChunkStore(tmp_path)
    with pytest.raises(error_type, match="transient table error") as error:
        _ = store.table
    assert error.value is failure
    assert sentinel.read_bytes() == b"stored vectors"
    connection.drop_table.assert_not_called()
    connection.create_table.assert_not_called()


def test_failed_table_validation_never_publishes_partial_handle(tmp_path, monkeypatch):
    (tmp_path / "chunks.lance").mkdir()
    failure = PermissionError("cannot inspect stored rows")
    table = SimpleNamespace(count_rows=Mock(side_effect=failure))
    connection = SimpleNamespace(open_table=Mock(return_value=table))
    monkeypatch.setattr("lgrep.storage._chunk_store.lancedb.connect", lambda _: connection)
    store = ChunkStore(tmp_path)
    for _ in range(2):
        with pytest.raises(PermissionError, match="cannot inspect stored rows"):
            _ = store.table
        assert store._table is None
    assert connection.open_table.call_count == 2


async def test_abandoned_physical_job_keeps_owner_busy(
    context,
    lightweight,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    started, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()

    def blocked():
        started.set()
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)

    @time_tool
    async def query():
        await _ensure_project_initialized(context, tmp_path / "a")
        await context.runtime.run_blocking("query", "test", str(tmp_path / "a"), blocked)

    task = asyncio.create_task(query())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        refused = await _ensure_project_initialized(context, tmp_path / "b")
        assert refused["code"] == "project_capacity_busy"
        release.set()
        # The executor callback can outlive the cancelled asyncio waiter. Wait
        # for this local worker, not for a guessed duration.
        async with asyncio.timeout(5):
            while context.runtime.snapshot_active_jobs():
                await asyncio.sleep(0)
        assert isinstance(await _ensure_project_initialized(context, tmp_path / "b"), ProjectState)
    finally:
        release.set()
        await _shutdown(context)


async def test_real_cache_reopens_rows_and_overlay_without_reindex(context, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    owner, peer, checkout = [tmp_path / part for part in ("owner", "peer", "checkout")]
    for path in (owner, peer, checkout):
        path.mkdir()
    monkeypatch.setattr(
        "lgrep.storage._chunk_store.canonical_repo_key",
        lambda path: owner if path == checkout else path,
    )
    first = await _ensure_project_initialized(context, owner)
    first.db.add_chunks([make_chunk(content="base row")])
    overlay = await _ensure_project_initialized(context, checkout)
    overlay.db.add_chunks([make_chunk(file_path="overlay.py", content="checkout row")])
    ref = weakref.ref(first.db)
    del first, overlay
    await _ensure_project_initialized(context, peer)
    assert str(owner) not in context.projects and str(checkout) not in context.projects
    assert ref() is None, "the evicted owner retained itself"
    monkeypatch.setattr(
        lifecycle, "_auto_index_project_single_flight", Mock(side_effect=AssertionError("reindex"))
    )
    reopened = await lifecycle._ensure_search_project_state(context, str(checkout))
    assert isinstance(reopened, ProjectState)
    assert reopened.db.count_chunks() == 2
    base = await _ensure_project_initialized(context, owner)
    assert base.db.count_chunks() == 1
    assert base.db.for_checkout(str(checkout)).count_chunks() == 2
    await _shutdown(context)


async def test_watcher_retirement_reserves_slot_and_keeps_loop_responsive(
    context,
    lightweight,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    state = await _ensure_project_initialized(context, tmp_path / "a")
    on_loop, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    handler = IndexingHandler(
        SimpleNamespace(discovery=SimpleNamespace(is_ignored=lambda _: False)), loop
    )
    indexed = Mock()
    handler.indexer.index_file = indexed
    handler._debounced_schedule(tmp_path / "a.py")

    def stop():
        loop.call_soon_threadsafe(on_loop.set)
        assert release.wait(5)

    state.watcher = SimpleNamespace(handler=handler, stop=stop)
    state.watching = True
    replacement = asyncio.create_task(_ensure_project_initialized(context, tmp_path / "b"))
    try:
        await asyncio.wait_for(on_loop.wait(), 5)
        assert handler.closed and not handler.pending_files
        snapshot = lifecycle.owner_store_snapshot(context)
        assert snapshot["retiring"] == 1 and snapshot["open"] == 0
        refused = await _ensure_project_initialized(context, tmp_path / "c")
        assert refused["retryable"] is True
        handler._debounced_schedule(tmp_path / "late.py")
        await handler._do_index(tmp_path / "late.py")
        await handler._async_delete_file(tmp_path / "late.py")
        assert not handler.pending_files
        indexed.assert_not_called()
        release.set()
        assert isinstance(await replacement, ProjectState)
        assert lifecycle.owner_store_snapshot(context)["evictions"] == 1
    finally:
        release.set()
        await replacement
        await _shutdown(context)


async def test_diagnostics_uses_canonical_owner_population(
    context, lightweight, tmp_path, monkeypatch
):
    from lgrep.server import mcp

    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    await _ensure_project_initialized(context, tmp_path / "a")
    await _ensure_project_initialized(context, tmp_path / "b")
    result = await mcp._tool_manager._tools["lgrep_diagnostics"].fn(
        ctx=SimpleNamespace(request_context=SimpleNamespace(lifespan_context=context)),
    )
    assert result["owner_stores"] == {
        "cap": 1,
        "open": 1,
        "assembling": 0,
        "retiring": 0,
        "busy": 0,
        "idle": 1,
        "evictions": 1,
    }
    await _shutdown(context)


async def test_checkout_assembly_pins_its_shared_owner(context, lightweight, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    owner, checkout = tmp_path / "owner", tmp_path / "checkout"
    from lgrep.storage import BASE_CHECKOUT

    monkeypatch.setattr(
        lifecycle,
        "checkout_scope",
        lambda path: (owner, str(checkout)) if path == checkout else (path, BASE_CHECKOUT),
    )
    await _ensure_project_initialized(context, owner)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def assemble(store, *args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return ProjectState(db=store, indexer=object(), owner_key=str(owner))

    monkeypatch.setattr(lifecycle, "_assemble_checkout_state", assemble)
    task = asyncio.create_task(_ensure_project_initialized(context, checkout))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        refused = await _ensure_project_initialized(context, tmp_path / "peer")
        assert refused["code"] == "project_capacity_busy"
        assert lifecycle.owner_store_snapshot(context)["busy"] == 1
        release.set()
        assert isinstance(await task, ProjectState)
    finally:
        release.set()
        await task
        await _shutdown(context)


async def test_background_index_keeps_its_own_scope_after_caller_returns(
    context,
    lightweight,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "owner"
    entered, release = asyncio.Event(), asyncio.Event()
    state = await _ensure_project_initialized(context, path)
    state.base_path = str(path)
    state.db.get_latest_indexed_at = lambda: 1.0
    state.indexer = SimpleNamespace(
        compute_pending_files=lambda: [],
        index_window=lambda **kwargs: SimpleNamespace(
            complete=True,
            remaining_files=[],
            status=SimpleNamespace(file_count=0, chunk_count=0, duration_ms=0),
        ),
    )

    async def base_ready(*args, **kwargs):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(lifecycle, "_base_index_ready", base_ready)

    @time_tool
    async def query():
        await _ensure_project_initialized(context, path)
        await lifecycle._schedule_background_reindex(context, str(path), path)

    await query()
    task = context._bg_reindex_tasks[str(path)]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert lifecycle.owner_store_snapshot(context)["busy"] == 1
    finally:
        release.set()
        await task
        await _shutdown(context)


async def test_failed_watcher_retirement_does_not_free_capacity(
    context,
    lightweight,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    state = await _ensure_project_initialized(context, tmp_path / "a")
    state.watching = True
    state.watcher = SimpleNamespace(
        handler=None, stop=Mock(side_effect=RuntimeError("observer stuck"))
    )
    refused = await _ensure_project_initialized(context, tmp_path / "b")
    assert "error" in refused
    assert lifecycle.owner_store_snapshot(context)["retiring"] == 1
    assert len(context._stores) == 1
    refused = await _ensure_project_initialized(context, tmp_path / "c")
    assert refused["retryable"] is True
    await _shutdown(context)


@pytest.mark.parametrize("stop_fails", [False, True])
@pytest.mark.parametrize("watching", [False, True])
async def test_worktree_invalidation_cannot_orphan_a_watcher(
    context, lightweight, tmp_path, monkeypatch, stop_fails, watching
):
    from lgrep.server import mcp, tools_maintenance

    monkeypatch.setenv("LGREP_ALLOW_DESTRUCTIVE_MCP", "1")
    path = tmp_path / "checkout"
    state = await _ensure_project_initialized(context, path)
    stop = Mock(side_effect=RuntimeError("observer stuck") if stop_fails else None)
    state.watcher = SimpleNamespace(stop=stop)
    state.watching = watching
    entry = {"path": str(path), "cache_dir": str(tmp_path / "cache"), "error": None}
    monkeypatch.setattr(
        tools_maintenance, "_invalidate_worktree_cache", Mock(return_value=([entry], 1, 0))
    )
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=context))
    try:
        result = await mcp._tool_manager._tools["invalidate_worktree_cache"].fn(
            paths=[str(path)], ctx=ctx
        )
        stop.assert_called_once()
        if stop_fails:
            assert context.projects[str(path)] is state
            assert "watcher" in result["entries"][0]["error"]
        else:
            assert str(path) not in context.projects
            assert state.watcher is None
    finally:
        await _shutdown(context)


async def test_capacity_fields_survive_mcp_validation(context, lightweight, tmp_path, monkeypatch):
    from lgrep.server import mcp

    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    owner = tmp_path / "a"
    await _ensure_project_initialized(context, owner)
    release = context._activity.acquire(str(owner))
    peer = tmp_path / "b"
    peer.mkdir()
    tool = mcp._tool_manager._tools["search_semantic"]
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=context))
    arguments = {"query": "no embedding expected", "path": str(peer)}
    try:
        await tool.run(arguments, context=ctx, convert_result=True)
        raw = await tool.run(arguments, context=ctx, convert_result=False)
        assert raw["code"] == "project_capacity_busy" and raw["retryable"] is True
    finally:
        release()
        await _shutdown(context)


async def test_retirement_fences_path_leases_before_observer_stop(
    context, lightweight, tmp_path, monkeypatch
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    owner = tmp_path / "owner"
    state = await _ensure_project_initialized(context, owner)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def stop():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)

    state.watcher = SimpleNamespace(handler=None, stop=stop)
    state.watching = True
    replacement = asyncio.create_task(_ensure_project_initialized(context, tmp_path / "peer"))
    late_hold = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # A capacity probe must not recreate aliases for the retiring owner.
        refused = await _ensure_project_initialized(context, tmp_path / "other")
        assert refused["code"] == "project_capacity_busy"
        late_hold = context.runtime.retain_project(str(owner))
        assert late_hold is None
        release.set()
        assert isinstance(await replacement, ProjectState)
        snapshot = lifecycle.owner_store_snapshot(context)
        assert snapshot["open"] == 1 and snapshot["retiring"] == 0
        assert isinstance(await _ensure_project_initialized(context, owner), ProjectState)
    finally:
        if late_hold is not None:
            late_hold()
        release.set()
        await replacement
        await _shutdown(context)


async def test_owner_publication_replaces_assembly_reservation_atomically(
    context, lightweight, tmp_path, monkeypatch
):
    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    snapshots = []

    class ObservedLock(asyncio.Lock):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            if context._stores:
                snapshots.append(lifecycle.owner_store_snapshot(context))

    context._lock = ObservedLock()
    assert isinstance(await _ensure_project_initialized(context, tmp_path / "owner"), ProjectState)
    assert snapshots
    assert all(s["open"] + s["assembling"] + s["retiring"] <= 1 for s in snapshots)
    await _shutdown(context)


async def test_diagnostics_counts_index_cleanup_reservations_as_busy(
    context, lightweight, tmp_path, monkeypatch
):
    from lgrep.server import remove_project

    monkeypatch.setattr(lifecycle, "MAX_PROJECTS", 1)
    owner = tmp_path / "owner"
    await _ensure_project_initialized(context, owner)
    context._indexing_events[str(owner)] = asyncio.Event()
    try:
        snapshot = lifecycle.owner_store_snapshot(context)
        assert snapshot["busy"] == 1 and snapshot["idle"] == 0
        assert remove_project(context, str(owner))["removed"] is False
        refused = await _ensure_project_initialized(context, tmp_path / "peer")
        assert refused["code"] == "project_capacity_busy"
    finally:
        context._indexing_events.pop(str(owner))
        await _shutdown(context)


@pytest.mark.parametrize("failure_site", ["window", "base"])
@pytest.mark.parametrize("stop_fails", [False, True])
async def test_failed_auto_index_cannot_orphan_a_watcher(
    context, lightweight, tmp_path, monkeypatch, failure_site, stop_fails
):
    path = tmp_path / "owner"
    state = await _ensure_project_initialized(context, path)
    state.indexer = SimpleNamespace(
        compute_pending_files=lambda: [],
        index_window=Mock(side_effect=RuntimeError("index failure")),
    )
    stop = Mock(side_effect=RuntimeError("observer stuck") if stop_fails else None)
    state.watcher = SimpleNamespace(stop=stop)
    state.watching = True
    monkeypatch.setattr(lifecycle, "AUTO_INDEX_MAX_ATTEMPTS", 1)
    if failure_site == "base":
        state.base_path = str(path)

        async def failed_base(*args, **kwargs):
            raise RuntimeError("base failure")

        monkeypatch.setattr(lifecycle, "_base_index_ready", failed_base)
    try:
        result = await lifecycle._auto_index_project_single_flight(context, str(path), path)
        assert "error" in result
        stop.assert_called_once()
        if stop_fails:
            assert context.projects[str(path)] is state
        else:
            assert str(path) not in context.projects
            assert state.watcher is None
    finally:
        await _shutdown(context)


async def test_remove_project_stops_a_watcher_after_failed_start(context, lightweight, tmp_path):
    from lgrep.server import remove_project

    path = tmp_path / "owner"
    state = await _ensure_project_initialized(context, path)
    state.db.for_checkout = lambda _: state.db
    stop = Mock()
    state.watcher = SimpleNamespace(stop=stop)
    state.watching = False
    assert remove_project(context, str(path))["removed"] is True
    stop.assert_called_once()
    assert state.watcher is None
    await _shutdown(context)
