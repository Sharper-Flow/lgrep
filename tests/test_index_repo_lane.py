"""index_symbols_repo routes parse/persist work through the build lane (LGREP-25).

The remote fetch loop stays async (httpx); the synchronous tree-sitter
parsing and index persistence run through the caller's ``run_sync`` runner.
Under a supervised server the runner is a ``run_blocking`` call with
``lane="build"``; without a supervisor it still leaves the event loop via
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import threading

from lgrep.server.runtime import RuntimeSupervisor


class _FakeContext:
    """Minimal FastMCP context stand-in exposing a lifespan app context."""

    class _RequestContext:
        def __init__(self, lifespan_context):
            self.lifespan_context = lifespan_context

    def __init__(self, lifespan_context):
        self.request_context = self._RequestContext(lifespan_context)


def test_supervised_index_repo_runner_uses_build_lane():
    supervisor = RuntimeSupervisor(max_workers=1, max_build_workers=1, history_limit=10)
    ctx = _FakeContext(type("App", (), {"runtime": supervisor})())

    async def scenario():
        captured = {}

        async def fake_index_repo(*args, **kwargs):
            captured["run_sync"] = kwargs.get("run_sync")
            return {"repo": "o/r", "files_indexed": 0, "symbols_indexed": 0}

        import lgrep.server.tools_symbols as tools_symbols

        original = tools_symbols._index_repo
        tools_symbols._index_repo = fake_index_repo
        try:
            await tools_symbols.index_symbols_repo(
                "o/r", ref="HEAD", max_files=1, github_token=None, ctx=ctx
            )
        finally:
            tools_symbols._index_repo = original

        thread = await captured["run_sync"](threading.current_thread)
        thread_name = thread.name
        return thread_name

    try:
        thread_name = asyncio.run(scenario())
        assert thread_name.startswith("lgrep-build"), (
            f"parse/persist ran on {thread_name!r}, not the build lane"
        )
    finally:
        supervisor.shutdown(cancel_futures=True)
