"""MCP queries must not own or duplicate a slow symbol refresh."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

import lgrep.server as server
import lgrep.tools.index_folder as folder
from lgrep.parser.extractor import SymbolExtractor
from lgrep.server import LgrepContext, mcp, tools_symbols
from lgrep.server.lifecycle import _shutdown
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.storage.index_store import IndexStore
from lgrep.tools.get_symbol import get_symbol
from lgrep.tools.index_folder import index_folder


@pytest.fixture
def slow_refresh(tmp_path, monkeypatch):
    from lgrep.storage import index_store

    root = tmp_path / "checkout"
    root.mkdir()
    store = tmp_path / "symbols"
    monkeypatch.setattr(index_store, "DEFAULT_SYMBOLS_DIR", store)
    for number in range(36):
        (root / f"m{number:02d}.ts").write_text(f"function before_{number}() {{ return 1; }}\n")
    index_folder(str(root), max_files=100)
    index_path = IndexStore()._index_path(str(root))
    old = time.time_ns() - 10_000_000_000
    os.utime(index_path, ns=(old, old))
    for number in range(36):
        target = root / f"m{number:02d}.ts"
        target.write_text(f"function after_{number}() {{ return 2; }}\n")
        os.utime(target, ns=(old + 1_000_000_000, old + 1_000_000_000))

    state = SimpleNamespace(
        root=root,
        started=threading.Event(),
        release=threading.Event(),
        done=threading.Event(),
        calls=0,
        finished=0,
        parses=0,
    )
    parse = SymbolExtractor.extract_full
    refresh = folder._index_folder_with_snapshot

    def held_parse(*args, **kwargs):
        state.parses += 1
        state.started.set()
        assert state.release.wait(30), "test did not release its physical refresh"
        return parse(*args, **kwargs)

    def observed_refresh(*args, **kwargs):
        state.calls += 1
        try:
            return refresh(*args, **kwargs)
        finally:
            state.finished += 1
            if state.finished == state.calls:
                state.done.set()

    monkeypatch.setattr(SymbolExtractor, "extract_full", held_parse)
    monkeypatch.setattr(folder, "_index_folder_with_snapshot", observed_refresh)
    monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 10.0)
    return state


def context(app):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


async def search(app, root, query, **kwargs):
    return await mcp._tool_manager.call_tool(
        "search_symbols", {"query": query, "path": str(root), **kwargs}, context=context(app)
    )


async def drain(state):
    state.release.set()
    assert await asyncio.to_thread(state.done.wait, 10), "physical refresh did not finish"


@pytest.mark.asyncio
async def test_two_queries_return_pending_while_one_observable_refresh_finishes(
    slow_refresh, monkeypatch
):
    state = slow_refresh
    app = LgrepContext()
    monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 0.2)
    try:
        first = await search(app, state.root, "after_0")
        second = await search(app, state.root, "after_35")
        assert await asyncio.to_thread(state.started.wait, 10)
        assert not state.release.is_set()
        assert [r.get("code") for r in (first, second)] == [
            "indexing_in_progress",
            "indexing_in_progress",
        ], (first, second)
        assert all(r["retryable"] and "results" not in r for r in (first, second))
        assert state.calls == 1
        diagnostics = await mcp._tool_manager.call_tool(
            "lgrep_diagnostics", {}, context=context(app)
        )
        jobs = diagnostics["active_jobs"]
        assert len(jobs) == 1
        assert jobs[0]["kind"] == "symbol_index"
        assert jobs[0]["lane"] == "query"
        assert jobs[0]["project"] == str(state.root)
        old_id = "m35.ts:function:before_35"
        assert old_id in IndexStore().load(str(state.root)).symbols
        stale = get_symbol(old_id, str(state.root))
        assert "error" in stale and "symbol" not in stale
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 10.0)
        await drain(state)
        found = await search(app, state.root, "after_35")
        assert [sym["name"] for sym in found["results"]] == ["after_35"]
        assert state.parses == 36
    finally:
        await drain(state)
        await _shutdown(app)


@pytest.mark.asyncio
async def test_client_cancellation_does_not_open_another_writer(slow_refresh, monkeypatch):
    state = slow_refresh
    app = LgrepContext()
    caller = asyncio.create_task(search(app, state.root, "after_0"))
    try:
        assert await asyncio.to_thread(state.started.wait, 2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 0.2)
        pending = await search(app, str(state.root) + "/.", "after_35")
        assert pending.get("code") == "indexing_in_progress", pending
        jobs = app.runtime.snapshot_active_jobs()
        assert len(jobs) == 1 and not jobs[0]["abandoned"]
        assert state.calls == 1
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 10.0)
        await drain(state)
        result = await search(app, state.root, "after_35")
        assert result["results"][0]["name"] == "after_35"
    finally:
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        await drain(state)
        await _shutdown(app)


@pytest.mark.asyncio
async def test_failed_acquisition_clears_ownership_and_records_failure(slow_refresh, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    state = slow_refresh
    app = LgrepContext()
    load = tools_symbols.load_symbol_index
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("fixture index read failed")
        return load(*args, **kwargs)

    monkeypatch.setattr(tools_symbols, "load_symbol_index", fail_once)
    try:
        with pytest.raises(ToolError, match="fixture index read failed"):
            await search(app, state.root, "after_35")
        assert app._symbol_index_tasks == {}
        failed = app.runtime.snapshot_recent_jobs()
        assert failed[-1]["status"] == "failed"
        assert "fixture index read failed" in failed[-1]["error"]
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 0.2)
        pending = await search(app, state.root, "after_35")
        assert pending["code"] == "indexing_in_progress"
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 10.0)
        await drain(state)
        found = await search(app, state.root, "after_35")
        assert found["results"][0]["name"] == "after_35"
    finally:
        if state.calls:
            await drain(state)
        await _shutdown(app)


@pytest.mark.asyncio
async def test_queued_acquisition_is_bounded_and_shutdown_cancels_it(slow_refresh, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    state = slow_refresh
    app = LgrepContext(runtime=RuntimeSupervisor(max_workers=1))
    occupied = threading.Event()
    release_query = threading.Event()

    def hold_query():
        occupied.set()
        assert release_query.wait(30)

    worker = asyncio.create_task(
        app.runtime.run_blocking("fixture_query", "fixture", None, hold_query)
    )
    try:
        assert await asyncio.to_thread(occupied.wait, 2)
        monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 0.2)
        pending = await search(app, state.root, "after_35")
        assert pending["code"] == "indexing_in_progress"
        jobs = [j for j in app.runtime.snapshot_active_jobs() if j["kind"] == "symbol_index"]
        assert len(jobs) == 1 and jobs[0]["status"] == "queued"
        assert not state.started.is_set()
        assert (
            await asyncio.wait_for(
                app.runtime.run_blocking(
                    "fixture_build", "fixture", None, lambda: 42, lane="build"
                ),
                2,
            )
            == 42
        )
        await _shutdown(app)
        assert app._symbol_index_tasks == {}
        assert state.calls == 0
        jobs = [j for j in app.runtime.snapshot_recent_jobs() if j["kind"] == "symbol_index"]
        assert len(jobs) == 1 and jobs[0]["status"] == "cancelled"
        with pytest.raises(ToolError, match="shutting down"):
            await search(app, state.root, "after_35")
    finally:
        release_query.set()
        state.release.set()
        await worker
        await _shutdown(app)


@pytest.mark.asyncio
async def test_fresh_symbols_are_available_while_semantic_build_lane_is_busy(slow_refresh):
    state = slow_refresh
    state.release.set()
    index_folder(str(state.root))
    app = LgrepContext()
    occupied = threading.Event()
    release_build = threading.Event()

    def hold_build():
        occupied.set()
        assert release_build.wait(30)

    build = asyncio.create_task(
        app.runtime.run_blocking("index_all", "index_semantic", None, hold_build, lane="build")
    )
    try:
        assert await asyncio.to_thread(occupied.wait, 2)
        result = await search(app, state.root, "after_35")
        assert not release_build.is_set()
        assert [sym["name"] for sym in result["results"]] == ["after_35"]
        assert result["index_refreshed"] is False
    finally:
        release_build.set()
        await build
        await _shutdown(app)


@pytest.mark.asyncio
async def test_shutdown_returns_an_error_without_cancelling_waiting_caller(slow_refresh):
    from mcp.server.fastmcp.exceptions import ToolError

    state = slow_refresh
    app = LgrepContext()
    caller = asyncio.create_task(search(app, state.root, "after_35"))
    shutdown = None
    try:
        assert await asyncio.to_thread(state.started.wait, 2)
        shutdown = asyncio.create_task(_shutdown(app))
        with pytest.raises(ToolError, match="shutting down"):
            await asyncio.wait_for(caller, 2)
        assert not caller.cancelled() and caller.cancelling() == 0
    finally:
        await drain(state)
        await asyncio.gather(caller, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        else:
            await _shutdown(app)


@pytest.mark.asyncio
async def test_concurrent_queries_match_their_own_request_after_shared_refresh(slow_refresh):
    state = slow_refresh
    app = LgrepContext()
    callers = [
        asyncio.create_task(search(app, state.root, "after_0", kind="function", limit=1)),
        asyncio.create_task(search(app, state.root, "after_35", kind="class")),
    ]
    try:
        assert await asyncio.to_thread(state.started.wait, 2)
        state.release.set()
        first, second = await asyncio.gather(*callers)
        assert [sym["name"] for sym in first["results"]] == ["after_0"]
        assert second["results"] == []
        assert state.calls == 1
    finally:
        await drain(state)
        await asyncio.gather(*callers, return_exceptions=True)
        await _shutdown(app)
