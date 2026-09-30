"""Per-call tool-call logging: call_id/path correlation on time_tool events.

Verifies that every ``{tool}_completed/_timeout/_cancelled/_failed`` event
carries a short-uuid ``call_id`` held in a ContextVar for the duration of the
call, plus the call's ``path`` argument when the tool takes one, and that the
same ``call_id`` reaches RuntimeSupervisor job events.
"""

from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

import lgrep.server as server_module
from lgrep.server import time_tool
from lgrep.server.runtime import RuntimeSupervisor, call_id_var


def _events(logs: list[dict], name: str) -> list[dict]:
    return [entry for entry in logs if entry["event"] == name]


@pytest.mark.asyncio
async def test_completed_event_carries_call_id_and_path():
    @time_tool
    async def demo(path: str | None = None):
        return {"ok": True}

    with capture_logs() as logs:
        result = await demo(path="/tmp/project")

    assert result == {"ok": True}
    completed = _events(logs, "demo_completed")
    assert len(completed) == 1
    event = completed[0]
    assert isinstance(event["call_id"], str)
    assert len(event["call_id"]) <= 16
    int(event["call_id"], 16)  # short uuid hex
    assert event["path"] == "/tmp/project"
    assert event["duration_ms"] >= 0
    assert call_id_var.get() is None  # the context var does not leak past the call


@pytest.mark.asyncio
async def test_event_without_path_argument_omits_path():
    @time_tool
    async def no_path_tool():
        return {"ok": True}

    with capture_logs() as logs:
        await no_path_tool()

    completed = _events(logs, "no_path_tool_completed")
    assert len(completed) == 1
    assert "path" not in completed[0]
    assert completed[0]["call_id"]


@pytest.mark.asyncio
async def test_timeout_event_carries_call_id_and_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server_module, "TOOL_TIMEOUT_S", 0.05)

    @time_tool
    async def slow(path: str | None = None):
        await asyncio.sleep(5)

    with capture_logs() as logs:
        await slow(path="/tmp/slow")

    timeout_events = _events(logs, "slow_timeout")
    assert len(timeout_events) == 1
    event = timeout_events[0]
    assert event["call_id"]
    assert event["path"] == "/tmp/slow"
    assert event["timeout_s"] == 0.05


@pytest.mark.asyncio
async def test_cancelled_event_carries_call_id():
    @time_tool
    async def doom():
        raise asyncio.CancelledError

    with capture_logs() as logs, pytest.raises(asyncio.CancelledError):
        await doom()

    cancelled = _events(logs, "doom_cancelled")
    assert len(cancelled) == 1
    assert cancelled[0]["call_id"]


@pytest.mark.asyncio
async def test_failed_event_carries_call_id_and_error():
    @time_tool
    async def broken():
        raise ValueError("boom")

    with capture_logs() as logs, pytest.raises(ValueError, match="boom"):
        await broken()

    failed = _events(logs, "broken_failed")
    assert len(failed) == 1
    assert failed[0]["call_id"]
    assert failed[0]["error"] == "boom"


@pytest.mark.asyncio
async def test_distinct_calls_get_distinct_call_ids():
    @time_tool
    async def echo(path: str | None = None):
        return path

    with capture_logs() as logs:
        await echo(path="/a")
        await echo(path="/b")

    completed = _events(logs, "echo_completed")
    assert len(completed) == 2
    assert completed[0]["call_id"] != completed[1]["call_id"]
    assert completed[0]["path"] == "/a"
    assert completed[1]["path"] == "/b"


@pytest.mark.asyncio
async def test_tool_call_id_flows_into_job_event():
    supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)

    @time_tool
    async def demo(path: str | None = None):
        return await supervisor.run_blocking(
            kind="probe", caller="demo", project=path, fn=lambda: "ok"
        )

    with capture_logs() as logs:
        await demo(path="/tmp/project")

    tool_events = _events(logs, "demo_completed")
    job_events = _events(logs, "runtime_job_finished")
    assert len(tool_events) == 1
    assert len(job_events) == 1
    assert tool_events[0]["call_id"] == job_events[0]["call_id"]

    supervisor.shutdown(cancel_futures=True)
