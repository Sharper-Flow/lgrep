"""Native heap bounds: arena cap, idle trimming, shared Lance session, diagnostics."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import LgrepContext, mcp, memory
from lgrep.server.memory import IdleTrimmer
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.storage import _chunk_store
from lgrep.storage._chunk_store import (
    LANCE_INDEX_CACHE_BYTES,
    LANCE_METADATA_CACHE_BYTES,
    ChunkStore,
    lance_session,
)


@pytest.fixture
def no_glibc(monkeypatch):
    monkeypatch.setattr(memory, "_libc", None)
    monkeypatch.setattr(memory, "_libc_loaded", True)
    monkeypatch.setattr(memory, "_arena_max", None)
    monkeypatch.setattr(memory, "_trim_count", 0)


@pytest.fixture
def fake_glibc(monkeypatch):
    libc = MagicMock()
    libc.mallopt.return_value = 1
    monkeypatch.setattr(memory, "_libc", libc)
    monkeypatch.setattr(memory, "_libc_loaded", True)
    monkeypatch.setattr(memory, "_arena_max", None)
    monkeypatch.setattr(memory, "_trim_count", 0)
    return libc


class TestAllocatorHooks:
    def test_noop_without_glibc(self, no_glibc):
        assert memory.cap_malloc_arenas() is False
        assert memory.trim_heap() is False
        assert memory.allocator_info() == (None, 0)

    def test_cap_sets_m_arena_max(self, fake_glibc):
        assert memory.cap_malloc_arenas(3) is True
        fake_glibc.mallopt.assert_called_once_with(-8, 3)
        assert memory.allocator_info() == (3, 0)

    def test_rejected_cap_is_not_reported(self, fake_glibc):
        fake_glibc.mallopt.return_value = 0
        assert memory.cap_malloc_arenas() is False
        assert memory.allocator_info()[0] is None

    def test_trim_counts(self, fake_glibc):
        assert memory.trim_heap() is True
        assert memory.trim_heap() is True
        fake_glibc.malloc_trim.assert_called_with(0)
        assert memory.allocator_info() == (None, 2)

    def test_real_libc_probe_is_safe(self, monkeypatch):
        monkeypatch.setattr(memory, "_libc_loaded", False)
        monkeypatch.setattr(memory, "_libc", None)
        monkeypatch.setattr(memory, "_trim_count", 0)
        # glibc on Linux CI; None elsewhere. Either way trimming must not raise.
        ran = memory.trim_heap()
        assert ran is (memory._glibc() is not None)

    def test_run_server_caps_before_serving(self, monkeypatch):
        from lgrep.server import bootstrap

        calls = []
        monkeypatch.setattr(memory, "cap_malloc_arenas", lambda: calls.append("cap"))
        monkeypatch.setattr(mcp, "run", lambda transport: calls.append(transport))
        monkeypatch.setattr(bootstrap, "configure_logging", lambda: None)
        bootstrap.run_server("stdio")
        assert calls == ["cap", "stdio"]


class TestIdleTrimmer:
    def _wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_first_idle_trims_off_caller_thread(self):
        threads = []
        trimmer = IdleTrimmer(
            lambda: True, trim=lambda: threads.append(threading.current_thread()) or True
        )
        trimmer.notify_idle()
        assert self._wait(lambda: threads)
        assert threads == [threads[0]] and threads[0] is not threading.current_thread()
        trimmer.close()

    def test_throttles_to_one_deferred_trim(self):
        now = [100.0]
        trims = []
        trimmer = IdleTrimmer(
            lambda: True, interval_s=0.2, trim=lambda: trims.append(1) or True, clock=lambda: now[0]
        )
        trimmer.notify_idle()
        assert self._wait(lambda: len(trims) == 1)
        for _ in range(5):
            trimmer.notify_idle()
        time.sleep(0.05)
        assert len(trims) == 1, "trims within the interval must wait for the boundary"
        assert self._wait(lambda: len(trims) == 2)
        time.sleep(0.3)
        assert len(trims) == 2, "repeated notifications schedule one trim"
        trimmer.close()

    def test_deferred_trim_skipped_when_busy(self):
        idle = [True]
        trims = []
        trimmer = IdleTrimmer(lambda: idle[0], interval_s=0.1, trim=lambda: trims.append(1) or True)
        trimmer.notify_idle()
        assert self._wait(lambda: len(trims) == 1)
        trimmer.notify_idle()
        idle[0] = False
        time.sleep(0.25)
        assert len(trims) == 1
        trimmer.close()

    def test_close_cancels_pending_trim(self):
        trims = []
        trimmer = IdleTrimmer(lambda: True, interval_s=0.1, trim=lambda: trims.append(1) or True)
        trimmer.notify_idle()
        assert self._wait(lambda: len(trims) == 1)
        trimmer.notify_idle()
        trimmer.close()
        time.sleep(0.25)
        assert len(trims) == 1


class TestRuntimeIdleHook:
    @pytest.mark.asyncio
    async def test_trim_notified_when_last_job_finishes(self):
        import asyncio

        supervisor = RuntimeSupervisor(max_workers=2, history_limit=10)
        recorder = MagicMock()
        recorder.notify_idle.side_effect = lambda: idle_calls.append(supervisor.is_idle())
        idle_calls = []
        supervisor._idle_trimmer = recorder
        release = threading.Event()

        slow = asyncio.create_task(
            supervisor.run_blocking("slow", "test", None, lambda: release.wait(2))
        )
        while not supervisor.snapshot_active_jobs():
            await asyncio.sleep(0.001)
        await supervisor.run_blocking("fast", "test", None, lambda: None)
        assert idle_calls == [], "a finish with other work active is not idle"
        release.set()
        await slow
        assert idle_calls == [True]
        supervisor.shutdown()
        recorder.close.assert_called_once()


class TestSharedLanceSession:
    def test_session_is_process_wide_and_bounded(self, tmp_path, monkeypatch):
        created = []
        real = _chunk_store.lancedb.Session

        def record(**kwargs):
            created.append(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(_chunk_store, "_lance_session", None)
        monkeypatch.setattr(_chunk_store.lancedb, "Session", record)
        connects = []
        real_connect = _chunk_store.lancedb.connect

        def spy_connect(uri, **kwargs):
            connects.append(kwargs.get("session"))
            return real_connect(uri, **kwargs)

        monkeypatch.setattr(_chunk_store.lancedb, "connect", spy_connect)

        a = ChunkStore(tmp_path / "a")
        b = ChunkStore(tmp_path / "b")
        assert a.table.count_rows() == 0 and b.table.count_rows() == 0
        assert created == [
            {
                "index_cache_size_bytes": LANCE_INDEX_CACHE_BYTES,
                "metadata_cache_size_bytes": LANCE_METADATA_CACHE_BYTES,
            }
        ]
        assert len(connects) == 2
        assert connects[0] is connects[1] is lance_session()


class TestDiagnosticsProcessMemory:
    @pytest.mark.asyncio
    async def test_reports_process_memory_separately(self, fake_glibc):
        memory.cap_malloc_arenas(4)
        memory.trim_heap()
        app_ctx = LgrepContext()
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context.lifespan_context = app_ctx
        fn = next(t.fn for t in mcp._tool_manager.list_tools() if t.name == "lgrep_diagnostics")
        result = await fn(ctx=mock_ctx)
        pm = result["process_memory"]
        assert set(pm) == {
            "rss_bytes",
            "swap_bytes",
            "lance_cache_bytes",
            "lance_cache_limit_bytes",
            "lance_index_cache_limit_bytes",
            "lance_metadata_cache_limit_bytes",
            "malloc_arena_max",
            "heap_trims",
        }
        assert pm["rss_bytes"] is None or pm["rss_bytes"] > 0
        assert pm["lance_cache_bytes"] == lance_session().size_bytes
        assert pm["lance_cache_limit_bytes"] == LANCE_INDEX_CACHE_BYTES + LANCE_METADATA_CACHE_BYTES
        assert pm["malloc_arena_max"] == 4
        assert pm["heap_trims"] == 1
        assert "process_memory" not in result["symbol_cache"]
        app_ctx.runtime.shutdown()

    def test_rss_swap_unreadable_is_none(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no proc")

        monkeypatch.setattr("builtins.open", boom)
        assert memory.process_rss_swap_bytes() == (None, None)
