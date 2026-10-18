"""Remote fetch and inline parse/persist stay on one dedicated explicit worker."""

from __future__ import annotations

import asyncio
import threading

from lgrep.server import LgrepContext, tools_symbols
from tests.test_explicit_symbol_jobs import context, settled


def test_supervised_index_repo_runner_uses_explicit_lane(monkeypatch):
    app = LgrepContext(transport="stdio")
    threads = []

    async def fake_index_repo(*args, **kwargs):
        threads.append(threading.current_thread())
        threads.append(await kwargs["run_sync"](threading.current_thread))
        return {
            "repo": "o/r",
            "ref": "HEAD",
            "files_indexed": 0,
            "symbols_indexed": 0,
            "truncated": False,
            "truncation_reason": None,
        }

    monkeypatch.setattr(tools_symbols, "_index_repo", fake_index_repo)

    async def scenario():
        submitted = await tools_symbols.index_symbols_repo("o/r", ctx=context(app))
        final = await settled(app.runtime, submitted["job_id"])
        assert final["state"] == "completed"

    try:
        asyncio.run(scenario())
        assert len(threads) == 2 and threads[0] is threads[1]
        assert threads[0].name.startswith("lgrep-explicit")
        jobs = app.runtime.snapshot_recent_jobs()
        assert len(jobs) == 1 and jobs[0]["kind"] == "explicit_symbol_index"
    finally:
        app.runtime.shutdown()
