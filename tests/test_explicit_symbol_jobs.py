"""Explicit indexing owns one worker and survives the submitting request."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from lgrep.exceptions import OperationCancelled
from lgrep.server import LgrepContext, mcp, tools_symbols
from lgrep.server.lifecycle import _shutdown
from lgrep.server.runtime import RuntimeSupervisor


def context(app):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def counters(**extra):
    return {"files_indexed": 1, "symbols_indexed": 2, **extra}


async def settled(runtime, job_id):
    for _ in range(1000):
        job = runtime.get_symbol_index_job(job_id)
        if job["state"] in {"completed", "failed", "cancelled"}:
            return job
        await asyncio.sleep(0.002)
    pytest.fail("explicit job did not settle")


def submit(runtime, work, key="same", **kwargs):
    return runtime.submit_symbol_index(
        caller="index_symbols_folder", project="fixture", request_key=key, work=work, **kwargs
    )


@pytest.mark.asyncio
async def test_single_flight_and_all_other_lanes_stay_available():
    runtime = RuntimeSupervisor(max_workers=1, max_build_workers=1, max_symbol_workers=1)
    entered, release = threading.Event(), threading.Event()
    observed = []

    def held(deadline, stop_event):
        observed.append((threading.current_thread().name, deadline, stop_event))
        entered.set()
        assert release.wait(3)
        return counters()

    try:
        outcome, job = submit(runtime, held)
        assert outcome == "accepted"
        assert await asyncio.to_thread(entered.wait, 1)
        assert submit(runtime, held)[0:1] == ("duplicate",)
        assert submit(runtime, held)[1]["job_id"] == job["job_id"]
        assert submit(runtime, held, key="different")[0] == "busy"
        for lane in ("query", "build", "symbol"):
            assert (
                await asyncio.wait_for(
                    runtime.run_blocking("probe", "test", "unrelated", lambda: "ok", lane=lane), 0.5
                )
                == "ok"
            )
        assert len(observed) == 1
        assert observed[0][0].startswith("lgrep-explicit")
        assert observed[0][1] > time.monotonic()
        release.set()
        assert (await settled(runtime, job["job_id"]))["result"] == counters()
    finally:
        release.set()
        runtime.shutdown()


@pytest.mark.asyncio
async def test_explicit_history_survives_queries_and_has_its_own_bound():
    runtime = RuntimeSupervisor(history_limit=2)
    try:
        _, first = submit(runtime, lambda *_: counters())
        await settled(runtime, first["job_id"])
        for _ in range(200):
            await runtime.run_blocking("query", "test", None, lambda: None)
        assert runtime.get_symbol_index_job(first["job_id"])["state"] == "completed"
        assert runtime.get_symbol_index_job("job-00000001") is None
        for n in range(100):
            _, job = submit(runtime, lambda *_: counters(), key=str(n))
            await settled(runtime, job["job_id"])
        assert runtime.get_symbol_index_job(first["job_id"]) is None
        assert runtime.get_symbol_index_job(job["job_id"])["state"] == "completed"
        assert len(runtime._explicit_recent) == 100
        assert all(
            record.future is None and not record.credentials and record.request_key is None
            for record in runtime._explicit_recent
        )
    finally:
        runtime.shutdown()


@pytest.mark.asyncio
async def test_generation_is_unique_per_supervisor():
    first, second = RuntimeSupervisor(), RuntimeSupervisor()
    try:
        _, a = submit(first, lambda *_: counters())
        _, b = submit(second, lambda *_: counters())
        await settled(first, a["job_id"])
        await settled(second, b["job_id"])
        assert a["job_id"] != b["job_id"]
        assert second.get_symbol_index_job(a["job_id"]) is None
        assert first.get_symbol_index_job(b["job_id"]) is None
    finally:
        first.shutdown()
        second.shutdown()


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_budget_warns_once_per_supervisor(raw, monkeypatch):
    monkeypatch.setenv("LGREP_EXPLICIT_INDEX_BUDGET_S", raw)
    with capture_logs() as logs:
        runtime = RuntimeSupervisor()
        try:
            assert runtime.explicit_budget_s == 150
            runtime.snapshot_recent_jobs()
            assert len([event for event in logs if event["log_level"] == "warning"]) == 1
            assert raw not in {str(value) for value in logs[0].values()}
        finally:
            runtime.shutdown()


@pytest.mark.parametrize("raw", [None, "", "23.5"])
def test_budget_default_empty_and_override(raw, monkeypatch):
    if raw is None:
        monkeypatch.delenv("LGREP_EXPLICIT_INDEX_BUDGET_S", raising=False)
    else:
        monkeypatch.setenv("LGREP_EXPLICIT_INDEX_BUDGET_S", raw)
    with capture_logs() as logs:
        runtime = RuntimeSupervisor()
        try:
            assert runtime.explicit_budget_s == (23.5 if raw == "23.5" else 150)
            assert not logs
        finally:
            runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_stops_cooperatively_without_releasing_held_work():
    runtime = RuntimeSupervisor()
    entered, release = threading.Event(), threading.Event()
    events = []

    def held(_deadline, stop_event):
        events.append(stop_event)
        entered.set()
        assert release.wait(3)
        if stop_event.is_set():
            raise OperationCancelled("shutdown")
        return counters()

    try:
        _, job = submit(runtime, held)
        assert await asyncio.to_thread(entered.wait, 1)
        runtime.shutdown()
        assert events[0].is_set()
        assert runtime.get_symbol_index_job(job["job_id"])["state"] == "running"
        assert submit(runtime, held, key="later")[0] == "unavailable"
        release.set()
        assert (await settled(runtime, job["job_id"]))["state"] == "cancelled"
    finally:
        release.set()
        runtime.shutdown()


@pytest.mark.asyncio
async def test_queued_shutdown_never_enters_helper():
    runtime = RuntimeSupervisor()
    entered, release = threading.Event(), threading.Event()
    calls = []
    try:
        runtime._explicit_executor.submit(lambda: (entered.set(), release.wait(3)))
        assert await asyncio.to_thread(entered.wait, 1)
        _, job = submit(runtime, lambda *_: calls.append(True))
        runtime.shutdown()
        assert runtime.get_symbol_index_job(job["job_id"])["state"] == "cancelled"
        release.set()
        assert calls == []
    finally:
        release.set()
        runtime.shutdown()


def test_scheduling_failure_rolls_back_admission(monkeypatch):
    runtime = RuntimeSupervisor()
    try:

        def closed(_fn):
            raise RuntimeError("closed executor")

        monkeypatch.setattr(runtime._explicit_executor, "submit", closed)
        assert submit(runtime, lambda *_: counters()) == ("unavailable", None)
        assert runtime.snapshot_active_jobs() == []
    finally:
        runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", None, "unknown", "missing"])
async def test_transport_refuses_every_job_tool_before_helper(transport, monkeypatch):
    app = LgrepContext(transport=None if transport == "missing" else transport)
    calls = []

    def folder(*_args, **_kwargs):
        calls.append(True)
        return counters(
            files_skipped=0, files_deleted=0, occurrences_indexed=0, repo_path="fixture"
        )

    async def repo(*_args, **_kwargs):
        calls.append(True)
        return counters(repo="owner/repo")

    monkeypatch.setattr(tools_symbols, "_index_folder", folder)
    monkeypatch.setattr(tools_symbols, "_index_repo", repo)
    ctx = None if transport == "missing" else context(app)
    try:
        results = [
            await tools_symbols.index_symbols_folder("fixture", ctx=ctx),
            await tools_symbols.index_symbols_repo("owner/repo", ctx=ctx),
            await tools_symbols.get_symbol_index_job("not-a-job", ctx=ctx),
        ]
        for result in results:
            assert result["code"] == "symbol_index_jobs_unavailable"
            assert result["retryable"] is False
            assert "job_id" not in result
        assert not calls
        assert app.runtime.snapshot_active_jobs() == []
    finally:
        await _shutdown(app)


@pytest.mark.asyncio
async def test_submission_is_prompt_and_status_validates(tmp_path, monkeypatch):
    from lgrep.storage import index_store

    monkeypatch.setattr(index_store, "DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
    app = LgrepContext(transport="stdio")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def held(path, **kwargs):
        calls.append((path, kwargs))
        entered.set()
        assert release.wait(2)
        return counters(
            files_skipped=0,
            files_deleted=0,
            occurrences_indexed=0,
            repo_path=path,
            walk_truncated=False,
            truncation_reason=None,
        )

    monkeypatch.setattr(tools_symbols, "_index_folder", held)
    ctx = context(app)
    try:
        started = time.monotonic()
        result = await tools_symbols.index_symbols_folder(str(tmp_path), ctx=ctx)
        assert time.monotonic() - started < 1
        assert "job_id" in result and "files_indexed" not in result
        assert await asyncio.to_thread(entered.wait, 1)
        duplicate = await tools_symbols.index_symbols_folder(str(tmp_path), ctx=ctx)
        assert duplicate["duplicate"] and duplicate["job_id"] == result["job_id"]
        busy = await tools_symbols.index_symbols_folder(str(tmp_path), max_files=3, ctx=ctx)
        assert busy["code"] == "symbol_index_busy" and busy["retryable"]
        release.set()
        await settled(app.runtime, result["job_id"])
        tool = mcp._tool_manager.get_tool("get_symbol_index_job")
        await tool.run({"job_id": result["job_id"]}, context=ctx, convert_result=True)
        final = await tools_symbols.get_symbol_index_job(result["job_id"], ctx=ctx)
        assert final["state"] == "completed" and final["result"]["files_indexed"] == 1
        unknown = await tools_symbols.get_symbol_index_job("earlier-daemon", ctx=ctx)
        assert unknown["code"] == "unknown_symbol_index_job" and not unknown["retryable"]
        assert len(calls) == 1
        assert "deadline" in calls[0][1] and "cancel_event" in calls[0][1]
    finally:
        release.set()
        await _shutdown(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolution", "lock", "load", "parse", "save"])
async def test_real_folder_stages_never_hold_submission_or_status(stage, tmp_path, monkeypatch):
    from lgrep.storage import index_store

    folder = importlib.import_module("lgrep.tools.index_folder")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "hello.py").write_text("def hello(): pass\n")
    monkeypatch.setattr(index_store, "DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
    app = LgrepContext(transport="stdio")
    entered, release = threading.Event(), threading.Event()
    threads = []

    def hold():
        threads.append(threading.current_thread().name)
        entered.set()
        assert release.wait(3)

    if stage == "resolution":
        original = Path.resolve

        def resolve(self, *args, **kwargs):
            if self == root:
                hold()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
    elif stage == "lock":
        original = index_store.IndexStore.repo_lock

        @contextmanager
        def locked(self, key):
            hold()
            with original(self, key):
                yield

        monkeypatch.setattr(index_store.IndexStore, "repo_lock", locked)
    else:
        owner, name = (
            (type(folder._extractor), "extract_full")
            if stage == "parse"
            else (index_store.IndexStore, stage)
        )
        original = getattr(owner, name)

        def held(*args, **kwargs):
            hold()
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, held)

    try:
        started = time.monotonic()
        caller = asyncio.create_task(
            tools_symbols.index_symbols_folder(str(root), ctx=context(app))
        )
        submitted = await asyncio.wait_for(asyncio.shield(caller), 1)
        assert time.monotonic() - started < 1
        assert await asyncio.to_thread(entered.wait, 1)
        caller.cancel()  # Client disconnect after acknowledgement does not own the worker.
        started = time.monotonic()
        status = await tools_symbols.get_symbol_index_job(submitted["job_id"], ctx=context(app))
        assert time.monotonic() - started < 1
        assert status["state"] == "running" and status["result"] is None
        if stage == "save":
            app.runtime.stop_symbol_indexes()  # An already-started save completes normally.
        release.set()
        final = await settled(app.runtime, submitted["job_id"])
        assert final["state"] == "completed" and final["result"]["files_indexed"] == 1
        assert threads and all(name.startswith("lgrep-explicit") for name in threads)
        await mcp._tool_manager.get_tool("get_symbol_index_job").run(
            {"job_id": submitted["job_id"]}, context=context(app), convert_result=True
        )
    finally:
        release.set()
        await _shutdown(app)


@pytest.mark.asyncio
async def test_remote_credentials_and_deadline_are_captured_at_admission(monkeypatch):
    import lgrep.server.runtime as runtime_module

    monkeypatch.setenv("LGREP_GITHUB_TOKEN", "original-private-token")
    clock = [10.0]
    monkeypatch.setattr(
        runtime_module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
    )
    app = LgrepContext(transport="stdio")
    entered, release = threading.Event(), threading.Event()
    captured = []

    async def remote(*args, **kwargs):
        captured.append(kwargs)
        return {
            "repo": "owner/repo",
            "ref": "HEAD",
            "files_indexed": 0,
            "symbols_indexed": 0,
            "truncated": False,
            "truncation_reason": None,
        }

    monkeypatch.setattr(tools_symbols, "_index_repo", remote)
    try:
        app.runtime._explicit_executor.submit(lambda: (entered.set(), release.wait(3)))
        assert await asyncio.to_thread(entered.wait, 1)
        result = await tools_symbols.index_symbols_repo("owner/repo", ctx=context(app))
        duplicate = await tools_symbols.index_symbols_repo("owner/repo", ctx=context(app))
        assert duplicate["duplicate"] and duplicate["job_id"] == result["job_id"]
        monkeypatch.setenv("LGREP_GITHUB_TOKEN", "rotated-private-token")
        busy = await tools_symbols.index_symbols_repo("owner/repo", ctx=context(app))
        assert busy["code"] == "symbol_index_busy"
        clock[0] = 30.0
        release.set()
        assert (await settled(app.runtime, result["job_id"]))["state"] == "completed"
        assert captured[0]["github_token"] == "original-private-token"
        assert captured[0]["deadline"] == 160.0
    finally:
        release.set()
        await _shutdown(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_explicit_metadata_and_exception_redaction_precede_projection(failure):
    secret = "private-credential-sentinel"
    runtime = RuntimeSupervisor()
    entered, release = threading.Event(), threading.Event()

    def work(*_):
        entered.set()
        assert release.wait(3)
        if failure:
            raise ValueError("x" * 490 + secret)
        return counters(
            repo=secret,
            ref=secret,
            source_text="must-not-retain",
            index=object(),
            truncated=False,
            truncation_reason=None,
        )

    try:
        with capture_logs() as logs:
            _, job = runtime.submit_symbol_index(
                caller=secret,
                project=secret,
                request_key="private-fingerprint",
                credentials=(secret,),
                work=work,
            )
            assert await asyncio.to_thread(entered.wait, 1)
            active = runtime.snapshot_active_jobs()
            release.set()
            final = await settled(runtime, job["job_id"])
            recent = runtime.snapshot_recent_jobs()
        public = json.dumps([job, active, final, recent, logs])
        assert secret not in public and "private-fingerprint" not in public
        assert "source_text" not in public and "must-not-retain" not in public
        if failure:
            assert final["state"] == "failed" and len(final["error"]) <= 500
            assert secret[:8] not in final["error"]
        else:
            assert final["state"] == "completed" and final["result"]["repo"] == "[redacted]"
    finally:
        release.set()
        runtime.shutdown()


@pytest.mark.asyncio
async def test_credential_in_local_target_is_not_logged(monkeypatch):
    token = "private-target-token"
    monkeypatch.setenv("LGREP_GITHUB_TOKEN", token)
    app = LgrepContext(transport="stdio")
    try:
        with capture_logs() as logs:
            result = await tools_symbols.index_symbols_folder(
                path=f"/repo/{token}", ctx=context(app)
            )
        assert "job_id" not in result and "error" in result
        assert token not in json.dumps([result, logs])
        assert not app.runtime.snapshot_active_jobs()
    finally:
        await _shutdown(app)


@pytest.mark.asyncio
async def test_credentials_do_not_cross_result_error_diagnostics_or_logs(monkeypatch):
    from lgrep.server.tools_diagnostics import lgrep_diagnostics

    token = "credential-for-indexing-only"
    monkeypatch.setenv("LGREP_GITHUB_TOKEN", token)
    app = LgrepContext(transport="stdio")

    async def failed(*_args, **_kwargs):
        return {"error": "x" * 490 + token + " fetch failed"}

    monkeypatch.setattr(tools_symbols, "_index_repo", failed)
    try:
        with capture_logs() as logs:
            result = await tools_symbols.index_symbols_repo(
                "owner/repo", github_token=token, ctx=context(app)
            )
            job = await settled(app.runtime, result["job_id"])
            assert job["state"] == "failed" and len(job["error"]) <= 500
            diagnostics = await lgrep_diagnostics(ctx=context(app))
        assert token not in json.dumps([result, job, diagnostics, logs])
        assert token[:12] not in job["error"]
    finally:
        await _shutdown(app)


@pytest.mark.asyncio
async def test_empty_token_argument_uses_deployment_token(monkeypatch):
    monkeypatch.setenv("LGREP_GITHUB_TOKEN", "deployment-token")
    app = LgrepContext(transport="stdio")
    captured = []

    async def remote(*_args, **kwargs):
        captured.append(kwargs["github_token"])
        return {
            "repo": "owner/repo",
            "ref": "HEAD",
            "files_indexed": 0,
            "symbols_indexed": 0,
            "truncated": False,
            "truncation_reason": None,
        }

    monkeypatch.setattr(tools_symbols, "_index_repo", remote)
    try:
        result = await tools_symbols.index_symbols_repo(
            "owner/repo", github_token="", ctx=context(app)
        )
        assert (await settled(app.runtime, result["job_id"]))["state"] == "completed"
        assert captured == ["deployment-token"]
    finally:
        await _shutdown(app)
