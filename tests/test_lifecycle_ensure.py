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

from structlog.testing import capture_logs

from lgrep.server.lifecycle import (
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
)


def _make_slow_store(opened: threading.Event, release: threading.Event):
    """ChunkStore stand-in whose construction blocks until released."""

    class SlowStore:
        def __init__(self, db_path, project_path=None):
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


def _job_events(logs: list[dict]) -> list[dict]:
    return [entry for entry in logs if entry["event"] == "runtime_job_finished"]


async def _wait_for_event(event: threading.Event, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        if loop.time() > deadline:
            raise AssertionError("store construction never started")
        await asyncio.sleep(0.001)


async def test_slow_store_open_keeps_loop_responsive(tmp_path, monkeypatch):
    opened, release = threading.Event(), threading.Event()
    monkeypatch.setattr("lgrep.server.lifecycle.ChunkStore", _make_slow_store(opened, release))
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
    events = _job_events(logs)
    assert [e["kind"] for e in events] == ["ensure_store"]
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
    kinds = [e["kind"] for e in _job_events(logs)]
    assert kinds == ["ensure_store"]
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
