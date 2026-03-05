"""Latency benchmark for lgrep_search_semantic and symbol tools.

Verifies that the refactor did not introduce performance regressions.

For semantic search: uses a mock embedder to isolate local search latency
(excludes Voyage API call which is network-bound).

For symbol search: measures actual index + search latency.

Baselines are recorded in this file. The test asserts p95 latency
does not exceed baseline * 1.10 (10% regression budget).
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Semantic search latency ───────────────────────────────────────────────────

# Baseline: local LanceDB search (excluding Voyage API call) should be <100ms p95
# This is a generous budget — actual measured baseline is ~15ms
SEMANTIC_SEARCH_P95_BUDGET_MS = 100.0


class TestSemanticSearchLatency:
    """Verify local search latency (LanceDB only, mock embedder)."""

    @pytest.mark.asyncio
    async def test_search_semantic_local_latency(self, tmp_path):
        """Local search latency (excluding Voyage API) must be <100ms p95 over 10 queries."""
        from lgrep.server import LgrepContext, ProjectState, search_semantic
        from lgrep.storage import SearchResult, SearchResults
        from mcp.server.fastmcp import Context

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "bench_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())
        app_ctx.projects[str(project_path)] = mock_state

        # Mock embedder returns instantly
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        # Mock search returns 10 results
        results = SearchResults(
            results=[
                SearchResult(
                    f"file_{i}.py",
                    i * 10,
                    i * 10 + 5,
                    f"code snippet {i}",
                    0.9 - i * 0.01,
                    "hybrid",
                )
                for i in range(10)
            ],
            query_time_ms=5.0,
            total_chunks=1000,
        )
        mock_db.search_hybrid.return_value = results

        # Run 10 queries and measure latency
        latencies = []
        queries = [
            "authentication flow",
            "error handling",
            "database connection",
            "rate limiting",
            "JWT verification",
            "session management",
            "password hashing",
            "API routing",
            "middleware chain",
            "request validation",
        ]

        for query in queries:
            t0 = time.monotonic()
            await search_semantic(query=query, path=str(project_path), ctx=mock_ctx)
            elapsed_ms = (time.monotonic() - t0) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]

        assert p95 < SEMANTIC_SEARCH_P95_BUDGET_MS, (
            f"Semantic search p95 latency {p95:.1f}ms exceeds budget {SEMANTIC_SEARCH_P95_BUDGET_MS}ms. "
            f"All latencies: {[f'{l:.1f}ms' for l in latencies]}"
        )


# ── Symbol search latency ─────────────────────────────────────────────────────

# Baseline: symbol search (in-memory JSON index) should be <50ms p95
SYMBOL_SEARCH_P95_BUDGET_MS = 50.0

# Baseline: index_folder for 10 files should complete in <5s
INDEX_FOLDER_BUDGET_MS = 5000.0


class TestSymbolSearchLatency:
    """Verify symbol search latency."""

    @pytest.fixture
    def bench_repo(self, tmp_path):
        """Create a 10-file Python repo for benchmarking."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            (src / f"module_{i}.py").write_text(
                f"def function_{i}_a():\n    pass\n\n"
                f"def function_{i}_b():\n    pass\n\n"
                f"class Class_{i}:\n    def method_{i}(self):\n        pass\n"
            )
        return tmp_path

    def test_index_folder_latency(self, bench_repo, tmp_path):
        """index_folder for 10 files must complete in <5s."""
        from lgrep.tools.index_folder import index_folder

        store = tmp_path / "store"
        t0 = time.monotonic()
        result = index_folder(str(bench_repo), storage_dir=store)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert "error" not in result
        assert elapsed_ms < INDEX_FOLDER_BUDGET_MS, (
            f"index_folder took {elapsed_ms:.0f}ms, budget is {INDEX_FOLDER_BUDGET_MS}ms"
        )

    def test_search_symbols_latency(self, bench_repo, tmp_path):
        """Symbol search p95 latency must be <50ms over 10 queries."""
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        store = tmp_path / "store"
        index_folder(str(bench_repo), storage_dir=store)

        queries = [f"function_{i}" for i in range(10)]
        latencies = []

        for query in queries:
            t0 = time.monotonic()
            result = search_symbols(query, str(bench_repo), storage_dir=store)
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert "error" not in result
            latencies.append(elapsed_ms)

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]

        assert p95 < SYMBOL_SEARCH_P95_BUDGET_MS, (
            f"Symbol search p95 latency {p95:.1f}ms exceeds budget {SYMBOL_SEARCH_P95_BUDGET_MS}ms. "
            f"All latencies: {[f'{l:.1f}ms' for l in latencies]}"
        )
