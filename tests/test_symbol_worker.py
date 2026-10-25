"""Symbol work runs in the spawned symbol worker, off the daemon interpreter."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import pytest

from lgrep.exceptions import OperationCancelled
from lgrep.server import LgrepContext, tools_symbols
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.storage import index_store
from lgrep.symbol_worker import SymbolWorkerLost, SymbolWorkerProcess
from tests.test_explicit_symbol_jobs import context

FILES = 300
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository whose symbol build takes seconds of CPU, and its own index store."""
    # The worker reads the store location when it starts.
    monkeypatch.setattr(index_store, "DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(FILES):
        body = "\n".join(
            f"def f{i}_{j}(x):\n    return g{j}(x) + {j}\n\n"
            f"class C{i}_{j}:\n    def m(self):\n        return f{i}_{j}(self)\n"
            for j in range(40)
        )
        (root / f"m{i}.py").write_text(body)
    return root


@pytest.fixture
def worker():
    process = SymbolWorkerProcess()
    try:
        yield process
    finally:
        process.shutdown()


def in_thread(fn, *args, **kwargs) -> Future:
    future: Future = Future()

    def run():
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            future.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    return future


def test_search_stays_responsive_during_symbol_build(repo, worker):
    """The build spends the worker's CPU, not the daemon's, and search keeps answering."""
    app = LgrepContext(transport="stdio", runtime=RuntimeSupervisor(symbol_worker=worker))
    ctx = context(app)

    async def scenario():
        # Start the worker first, so its import time is not counted as build time.
        outline = await tools_symbols.get_file_outline(str(repo / "m0.py"), ctx=ctx)
        assert outline["symbol_count"] > 0
        submitted = await tools_symbols.index_symbols_folder(
            str(repo), max_files=FILES * 2, ctx=ctx
        )
        cpu0, wall0 = time.process_time(), time.monotonic()
        latencies = []
        while app.runtime.get_symbol_index_job(submitted["job_id"])["state"] in {
            "queued",
            "running",
        }:
            started = time.monotonic()
            found = await tools_symbols.search_text("def f7_3(", str(repo), ctx=ctx)
            latencies.append(time.monotonic() - started)
            assert found["results"]
            await asyncio.sleep(0.2)
            assert time.monotonic() - wall0 < 60, "symbol build did not finish"
        cpu, wall = time.process_time() - cpu0, time.monotonic() - wall0
        job = app.runtime.get_symbol_index_job(submitted["job_id"])
        return job, latencies, cpu, wall

    try:
        job, latencies, cpu, wall = asyncio.run(scenario())
    finally:
        app.runtime.shutdown()
    assert job["state"] == "completed", job
    assert job["result"]["files_indexed"] == FILES
    # In the daemon, a search waits out the whole build.
    assert max(latencies) < 1.0, latencies
    assert len(latencies) >= 3, "too few searches overlapped the build"
    # In the daemon, the build alone keeps this process near 100% CPU.
    assert cpu < 0.5 * wall, f"daemon used {cpu:.2f}s CPU over a {wall:.2f}s build"


def test_worker_writes_nothing_to_stdout(repo, tmp_path):
    """Stdout is the daemon's MCP channel; worker output and logs go to stderr."""
    script = tmp_path / "daemon.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            from lgrep.server.bootstrap import configure_logging
            from lgrep.storage import index_store
            from lgrep.symbol_worker import SymbolWorkerProcess

            def main():
                configure_logging()
                index_store.DEFAULT_SYMBOLS_DIR = Path({str(tmp_path / "symbols")!r})
                worker = SymbolWorkerProcess()
                result = worker.call("index_folder", {str(repo)!r}, max_files=10)
                print("files_indexed", result["files_indexed"], file=sys.stderr)
                worker.shutdown()

            if __name__ == "__main__":
                main()
            """
        )
    )
    env = {**os.environ, "PYTHONPATH": str(SRC), "LGREP_LOG_LEVEL": "INFO"}
    env.pop("LGREP_LOG_FILE", None)
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, env=env, timeout=60, check=False
    )
    stderr = done.stderr.decode()
    assert done.returncode == 0, stderr
    assert done.stdout == b""
    assert "files_indexed 10" in stderr
    # The worker's own log events reach the daemon's sinks, tagged with its pid.
    assert "index_folder_complete" in stderr
    assert "worker_pid" in stderr


def test_crashed_worker_fails_in_flight_calls_and_is_replaced(repo, worker):
    worker.call("get_file_outline", str(repo / "m0.py"))
    first = worker.pid
    assert first is not None and first != os.getpid()
    build = in_thread(worker.call, "index_folder", str(repo), max_files=FILES * 2)
    time.sleep(0.3)
    assert not build.done()
    os.kill(first, signal.SIGKILL)
    with pytest.raises(SymbolWorkerLost, match="retry the call"):
        build.result(timeout=10)
    outline = worker.call("get_file_outline", str(repo / "m1.py"))
    assert outline["symbol_count"] > 0
    assert worker.pid not in {None, first}


def test_stop_event_reaches_running_build(repo, worker):
    """The stop event passed at worker start stops an explicit build cooperatively."""
    worker.call("get_file_outline", str(repo / "m0.py"))
    build = in_thread(worker.call, "index_folder", str(repo), max_files=FILES * 2)
    time.sleep(0.3)
    worker.stop()
    with pytest.raises(OperationCancelled):
        build.result(timeout=10)
    assert worker.pid is not None, "a cooperative stop keeps the worker"


def test_shutdown_kills_worker_that_misses_grace_window(repo):
    worker = SymbolWorkerProcess(shutdown_grace_s=0.01)
    worker.call("get_file_outline", str(repo / "m0.py"))
    pid = worker.pid
    build = in_thread(worker.call, "index_folder", str(repo), max_files=FILES * 2)
    time.sleep(0.3)
    worker.shutdown()
    with pytest.raises((OperationCancelled, SymbolWorkerLost)):
        build.result(timeout=10)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(OperationCancelled):
        worker.call("get_file_outline", str(repo / "m0.py"))


class LostWorker:
    def call(self, op, *args, **kwargs):
        raise SymbolWorkerLost("The symbol worker exited (exit code -9); retry the call.")

    def release(self, token):
        pass

    def stop(self):
        pass

    def shutdown(self):
        pass


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_symbol", {"symbol_id": "m.py:function:f"}),
        ("get_symbols", {"symbol_ids": ["m.py:function:f"]}),
        ("get_file_outline", {}),
        ("get_repo_outline", {}),
        ("search_references", {"query": "f"}),
        ("search_symbols", {"query": "f"}),
    ],
)
def test_lost_worker_is_a_retryable_tool_error(tmp_path, tool, arguments):
    app = LgrepContext(transport="stdio", runtime=RuntimeSupervisor(symbol_worker=LostWorker()))

    async def scenario():
        fn = getattr(tools_symbols, tool)
        return await fn(path=str(tmp_path), ctx=context(app), **arguments)

    try:
        result = asyncio.run(scenario())
    finally:
        app.runtime.shutdown()
    assert result["code"] == "symbol_worker_restarted"
    assert result["retryable"] is True
    assert "retry the call" in result["error"]
