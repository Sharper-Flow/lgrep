"""Runtime supervision for blocking daemon work."""

from __future__ import annotations

import asyncio
import threading

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
