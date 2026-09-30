"""Runtime supervision for blocking daemon work."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from structlog.testing import capture_logs

from lgrep.server.runtime import JobStatus, RuntimeSupervisor, call_id_var


def _job_events(logs: list[dict]) -> list[dict]:
    return [entry for entry in logs if entry["event"] == "runtime_job_finished"]


@pytest.mark.asyncio
async def test_timed_out_blocking_job_is_marked_abandoned_then_terminal():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)
    release = threading.Event()

    def slow_work() -> str:
        release.wait(timeout=2)
        return "done"

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            supervisor.run_blocking(
                kind="index",
                caller="test",
                project="/tmp/project",
                fn=slow_work,
            ),
            timeout=0.05,
        )

    active = supervisor.snapshot_active_jobs()
    assert len(active) == 1
    assert active[0]["status"] == JobStatus.ABANDONED.value
    assert active[0]["project"] == "/tmp/project"

    release.set()
    await asyncio.sleep(0.05)

    assert supervisor.snapshot_active_jobs() == []
    recent = supervisor.snapshot_recent_jobs()
    assert recent[-1]["status"] == JobStatus.FINISHED_AFTER_ABANDON.value
    assert recent[-1]["abandoned"] is True

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_worker_limit_and_recent_history_are_bounded():
    supervisor = RuntimeSupervisor(max_workers=2, history_limit=2)

    assert supervisor.max_workers == 2

    for index in range(3):
        result = await supervisor.run_blocking(
            kind="status",
            caller="test",
            project=f"/tmp/project-{index}",
            fn=lambda value=index: value,
        )
        assert result == index

    recent = supervisor.snapshot_recent_jobs()
    assert len(recent) == 2
    assert [job["project"] for job in recent] == ["/tmp/project-1", "/tmp/project-2"]

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_sync_exception_is_terminal_and_summarized():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)

    def explode() -> None:
        raise ValueError("boom with details")

    with pytest.raises(ValueError):
        await supervisor.run_blocking(
            kind="search", caller="test", project="/tmp/project", fn=explode
        )

    recent = supervisor.snapshot_recent_jobs()
    assert recent[-1]["status"] == JobStatus.FAILED.value
    assert recent[-1]["error"] == "ValueError: boom with details"

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_finished_job_emits_timing_event():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)

    with capture_logs() as logs:
        await supervisor.run_blocking(
            kind="index_all", caller="test", project="/tmp/project", fn=lambda: "ok"
        )

    events = _job_events(logs)
    assert len(events) == 1
    event = events[0]
    assert event["job_id"].startswith("job-")
    assert event["kind"] == "index_all"
    assert event["caller"] == "test"
    assert event["project"] == "/tmp/project"
    assert event["status"] == "finished"
    assert event["queue_ms"] >= 0
    assert event["run_ms"] >= 0
    assert event["total_ms"] >= event["queue_ms"]
    assert event["abandoned"] is False
    assert event["error"] is None

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_job_event_carries_call_id_from_context():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)

    token = call_id_var.set("call-abc123")
    try:
        with capture_logs() as logs:
            await supervisor.run_blocking(
                kind="search_vector", caller="test", project="/tmp/project", fn=lambda: "ok"
            )
    finally:
        call_id_var.reset(token)

    events = _job_events(logs)
    assert len(events) == 1
    assert events[0]["call_id"] == "call-abc123"

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_job_event_without_call_context_has_null_call_id():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)

    with capture_logs() as logs:
        await supervisor.run_blocking(kind="status", caller="test", project=None, fn=lambda: "ok")

    event = _job_events(logs)[0]
    assert event["call_id"] is None
    assert event["project"] is None

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_never_started_job_reports_null_queue_and_run_ms():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)
    job = supervisor._create_job(kind="index_all", caller="test", project="/tmp/project")

    with capture_logs() as logs:
        supervisor._finish_job(job.id, JobStatus.CANCELLED)

    event = _job_events(logs)[0]
    assert event["queue_ms"] is None
    assert event["run_ms"] is None
    assert event["total_ms"] >= 0
    assert event["status"] == "cancelled"

    supervisor.shutdown(cancel_futures=True)


# ---------------------------------------------------------------------------
# Build lane (LGREP-25): build-kind jobs run on a dedicated executor so a
# build window or prune sweep cannot occupy the threads a query needs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_lane_jobs_run_on_dedicated_executor():
    supervisor = RuntimeSupervisor(max_workers=2, max_build_workers=2, history_limit=10)

    build_thread = await supervisor.run_blocking(
        "index_window", "test", None, threading.current_thread, lane="build"
    )
    query_thread = await supervisor.run_blocking(
        "search_vector", "test", None, threading.current_thread
    )
    assert build_thread.name.startswith("lgrep-build")
    assert query_thread.name.startswith("lgrep-worker")

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_query_completes_while_build_lane_saturated():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)
    started = threading.Event()
    release = threading.Event()

    def hold_build():
        started.set()
        release.wait(timeout=5)

    first = asyncio.create_task(
        supervisor.run_blocking("index_window", "test", None, hold_build, lane="build")
    )
    deadline = time.monotonic() + 5
    while not started.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.001)
    assert started.is_set(), "build job never started"

    queued = asyncio.create_task(
        supervisor.run_blocking("index_all", "test", None, lambda: "built", lane="build")
    )
    await asyncio.sleep(0.05)
    assert not queued.done(), "queued build job should wait behind the running one"

    # The query lane owns its own thread: a query completes despite the
    # saturated build lane. On a single shared pool this timed out.
    result = await asyncio.wait_for(
        supervisor.run_blocking("search_vector", "test", None, lambda: "ok"),
        timeout=1.0,
    )
    assert result == "ok"

    release.set()
    assert await first is None
    assert await queued == "built"

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_lane_param_selects_executor():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)

    forced_build = await supervisor.run_blocking(
        "search_vector", "test", None, threading.current_thread, lane="build"
    )
    forced_query = await supervisor.run_blocking(
        "index_all", "test", None, threading.current_thread, lane="query"
    )
    assert forced_build.name.startswith("lgrep-build")
    assert forced_query.name.startswith("lgrep-worker")

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_unknown_lane_is_refused():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)
    with pytest.raises(ValueError, match="lane"):
        await supervisor.run_blocking("status", "test", None, lambda: 1, lane="gpu")
    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_job_event_carries_lane():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)

    with capture_logs() as logs:
        await supervisor.run_blocking(
            "index_all", "test", project="/tmp/project", fn=lambda: "ok", lane="build"
        )
    event = _job_events(logs)[0]
    assert event["lane"] == "build"

    with capture_logs() as logs:
        await supervisor.run_blocking("status", "test", project=None, fn=lambda: "ok")
    event = _job_events(logs)[0]
    assert event["lane"] == "query"

    supervisor.shutdown(cancel_futures=True)


@pytest.mark.asyncio
async def test_job_snapshot_includes_lane():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)
    release = threading.Event()
    task = asyncio.create_task(
        supervisor.run_blocking(
            "index_window", "test", None, lambda: release.wait(timeout=2), lane="build"
        )
    )
    await asyncio.sleep(0.05)

    active = supervisor.snapshot_active_jobs()
    assert active and active[0]["lane"] == "build"

    release.set()
    await task
    supervisor.shutdown(cancel_futures=True)


def test_build_thread_limit_from_env(monkeypatch):
    monkeypatch.delenv("LGREP_BUILD_MAX_THREADS", raising=False)
    assert RuntimeSupervisor(max_workers=1).max_build_workers == 1

    monkeypatch.setenv("LGREP_BUILD_MAX_THREADS", "3")
    assert RuntimeSupervisor(max_workers=1).max_build_workers == 3

    monkeypatch.setenv("LGREP_BUILD_MAX_THREADS", "0")
    assert RuntimeSupervisor(max_workers=1).max_build_workers == 1

    monkeypatch.setenv("LGREP_BUILD_MAX_THREADS", "notanumber")
    assert RuntimeSupervisor(max_workers=1).max_build_workers == 1


@pytest.mark.asyncio
async def test_shutdown_shuts_down_both_executors():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)
    supervisor.shutdown(cancel_futures=True)
    with pytest.raises(RuntimeError):
        supervisor._executor.submit(lambda: 1)
    with pytest.raises(RuntimeError):
        supervisor._build_executor.submit(lambda: 1)
