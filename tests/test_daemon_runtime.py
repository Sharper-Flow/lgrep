"""Runtime supervision for blocking daemon work."""

from __future__ import annotations

import asyncio
import threading

import pytest

from lgrep.server.runtime import JobStatus, RuntimeSupervisor


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
